import asyncio
from datetime import datetime
from functools import lru_cache
import json
import os
import re
import shutil
from fastapi.responses import FileResponse, RedirectResponse
from typing_extensions import Annotated
import uuid

import pathlib
from fastapi import File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from database import SessionDep, async_sessionmaker, check_user
from config import settings, logger
from models import NewUser, TasksOrm, UserOrm
from tokens import create_access_token, get_current_user
from sqlalchemy import select, cast, Date


personal = list()


async def create_new_user(new_user: Annotated[NewUser, Form()], session: SessionDep) -> RedirectResponse:
    newUserOrm = UserOrm(username=new_user.username, password=new_user.password1, active=False, fio=new_user.fio)
    session.add(newUserOrm)
    await session.commit() 
 
    userid: int = await check_user(new_user.username, new_user.password1, session) 
    token: str = create_access_token(data={"username": new_user.username, "userid": str(userid)})
    response: RedirectResponse = RedirectResponse(url="/messages", status_code=303)
    response.set_cookie(key="access_token", value=token, httponly=True)
    return response


async def background_checks(session_factory: async_sessionmaker) -> None:
    logger.info("Activity check started")
    async with session_factory() as session:
        sql = text("""
            UPDATE users SET active = CASE WHEN EXISTS(
	            SELECT M.id FROM messages M WHERE M.userid=users.userid AND (EXTRACT(EPOCH FROM (now() - M.created_at)) < 300) 
	        ) OR EXISTS(
	            SELECT R.mess_id FROM mess_read R WHERE R.userid=users.userid AND (EXTRACT(EPOCH FROM (now() - R.read_time)) < 300)
	        ) THEN true ELSE false END; 
               """) 
        await session.execute(sql)
        await session.commit()
        logger.success("Activity check completed successfully")


class ProtectedStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def get_response(self, path: str, scope) -> Response:
        try:
            current_user = await get_current_user(Request(scope))
            if current_user.userid > 0:
                return await super().get_response(path, scope)
            else:
                logger.warning(f"Attempting to access the file page without authorization")
                raise HTTPException(status_code=401, detail="Authorization error")  
        except:    
            raise HTTPException(status_code=401, detail="Authorization error")  
        

def no_have_such_message(addr: int, sender: str, messtext: str) -> bool: 
    no_found: bool = True   
    for mess in personal:
        if (mess['to'] == addr) and (mess['from'] == sender):
            if ((datetime.now() - mess['created_at']).total_seconds() < 4) or (mess['messtext'] == messtext):
                no_found = False
                break 
    return no_found       


def get_personal_messages(userid: int) -> list | dict:
    for i in range(0, len(personal)):
        mess = personal[i]
        if mess['to'] == userid:
            sender = mess['from']
            mess_text = mess['messtext']
            del personal[i]
            return [{'from': sender, 'messtext': mess_text}] 
    return {}       


# Функции для всего блока работы с файлами

def save_file_sync(file_obj, path):
    with open(path, "wb") as buffer:
        shutil.copyfileobj(file_obj, buffer)


async def save_user_file_to_disk(userName: str, UPLOAD_DIR: str, file: UploadFile = File(...)) -> dict: 
    if file.filename is None or len(file.filename) > 255:
        logger.warning(f"FileName is missing for user: {userName}")
        return {"error": "File name missing or invalid" if settings.language == "en" else "Недопустимое имя файла"}
  
    origFileName: str = file.filename.replace(" ", "_")
    origFileName = re.sub(r'(?u)[^-\w.]', '', origFileName)
    extension = pathlib.Path(origFileName).suffix.lower()
    
    if extension.replace(".", "") not in settings.allowed_extensions:
        logger.warning(f"Invalid file format {origFileName} for user: {userName}")
        return {"error": "Invalid file format: "+extension if settings.language == "en" else "Недопустимый формат файла "+extension}
    if file.size > settings.max_upload_file_size * 1024 * 1024:     # type: ignore
        logger.warning(f"File size exceeds the limit for {origFileName} for user: {userName}")
        return {"error": "File size exceeds the limit "+str(settings.max_upload_file_size)+"Mb" if settings.language == "en" else "Слишком большой файл, больше "+str(settings.max_upload_file_size)+"Mb"}
    
    # Generate a save path (with a unique UUID in the file name)
    stem = pathlib.Path(origFileName).stem.lower().replace(".","_")
    shortFileName: str = stem if len(stem) < 31 else stem[:30]
    unique_filename = f"{uuid.uuid4()}_{shortFileName + extension}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)    
    # Сохраняем файл на диск    
    try:
        # waiting for the file to be saved in a thread to avoid blocking the event loop
        await run_in_threadpool(save_file_sync, file.file, file_path)
    except OSError as e:
        logger.error(f"File save error: {e}")
        # trying to remove the file if it was partially saved before the error occurred
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail="An error occurred while saving the file" if settings.language == "en" else "При сохранении файла возникла ошибка")
    return {"error": "OK", "ext": extension, "unique_filename": unique_filename, "orig_filename": origFileName}   


def delete_file_from_disk(filename: str, upload_dir: str) -> bool:
    file_path = os.path.join(upload_dir, filename)   
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.success(f"File {file_path} successfully deleted")
            return True
        except OSError as e:
            logger.error(f"Error occurred while deleting the file: {e.strerror}")
            return False
    else:
        logger.warning(f"File {file_path} not found for deletion")
        return False

def makeFileResponse(savedName: str, realName: str, upload_dir: str) -> FileResponse | dict:
    file_path = os.path.join(upload_dir, savedName)   
    if not os.path.exists(file_path):
        return {"error": "File not found" if settings.language == "en" else "Файл не найден"}
    return FileResponse(
        path=file_path, 
        filename=realName,   
        media_type='application/octet-stream'
    )



@lru_cache()
def load_internationalization_data(BASE_DIR: str, language: str) -> dict:
    i18n_file = os.path.join(BASE_DIR, f"locales/i18n_{language}.json")
    try:
        with open(i18n_file, "r", encoding="utf-8") as f:
            i18n_data = json.load(f)
        return i18n_data
    except OSError as e:
        logger.error(f"i18n file read error: {e}")
        raise HTTPException(status_code=500, detail="Error occurred while loading internationalization data")


async def how_much_messages(session: SessionDep) -> int:
    sql = text("SELECT COUNT(*) FROM messages")
    result = await session.execute(sql)
    return result.scalar() or 0


async def notify_deadlines(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        today = datetime.now().date()
        sql = select(TasksOrm).where(
            cast(TasksOrm.deadline, Date) == today,
            TasksOrm.completed == 0
        )
        result = await session.execute(sql)
        tasks = result.scalars().all()
        
        for task in tasks:
            mess_text = f"Задача {task.title} сегодня должна быть завершена" if settings.language == "ru" else f"Task {task.title} must be completed today"     
            personal.append({
                'to': task.respons,
                'from': 'System',
                'created_at': datetime.now(),
                'messtext': mess_text
            })
