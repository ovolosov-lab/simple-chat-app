from datetime import datetime, timedelta
from functools import lru_cache
import json
import os
import re
import shutil
from typing_extensions import Annotated
import uuid
import pathlib
from fastapi.responses import FileResponse, RedirectResponse
from fastapi import File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy import TextClause, delete, text
from sqlalchemy import select, cast, Date
from models import AI_Sessions, MessageOrm, NewUser, TasksOrm, UserOrm
from database import SessionDep, async_sessionmaker, check_user, create_ai_history, db_add_record, user_exists
from config import ERROR_MESSAGES_EN, ERROR_MESSAGES_RU, UPLOAD_DIR, settings, logger
from personal import PersonalMessages
from tokens import create_access_token, get_current_user
from groq.types.chat import ChatCompletionMessageParam
from ai import aiModel


personal: PersonalMessages = PersonalMessages()


async def create_new_user(new_user: Annotated[NewUser, Form()], session: SessionDep) -> RedirectResponse:
    newUserOrm = UserOrm(username=new_user.username, password=new_user.password1, active=False, fio=new_user.fio)
    session.add(newUserOrm)
    await session.commit() 
 
    userid: int = await check_user(new_user.username, new_user.password1, session) 
    token: str = create_access_token(data={"username": new_user.username, "userid": str(userid)})
    response: RedirectResponse = RedirectResponse(url="/messages", status_code=303)
    response.set_cookie(key="access_token", value=token, httponly=True)
    return response


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


def get_err_message(key: str, default: str) -> str:
    if default == "": 
        default = key
    return ERROR_MESSAGES_EN.get(key, default) if settings.language == "en" else ERROR_MESSAGES_RU.get(key, default) 


async def daily_morning_task(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        today = datetime.now().date()
        task_past_date = today - timedelta(days=settings.hold_closed_tasks_days)
        mess_past_date = today - timedelta(days=settings.hold_messages_days)

        query = select(TasksOrm).where(
            cast(TasksOrm.deadline, Date) == today,
            TasksOrm.status != 'closed'
        )
        result = await session.execute(query)
        tasks = result.scalars().all()
        
        for task in tasks:
            mess_text = f"Задача {task.title} сегодня должна быть завершена" if settings.language == "ru" else f"Task {task.title} must be completed today"     
            personal.add_message(to=task.respons, sender='System', messtext=mess_text) 
            
        try:
            query = delete(TasksOrm).where( TasksOrm.status == 'closed', TasksOrm.deadline < task_past_date)        
            result = await session.execute(query)
            await session.commit()
            logger.success("Deleting long-completed tasks was successful")
        except Exception as e:
            await session.rollback()
            logger.error(f"An error occurred while deleting long-completed tasks: {e}")
        """ 
        deleting old messages (and attached files) deleting where messages.created_at < today - settings.hold_messages_days 
        !!!! to do: need to delete from disk all the files attached to that messages   
        """
        try:
            sql: TextClause = text("SELECT m.id, a.filename FROM  attachments a INNER JOIN messages m ON a.mess_id=m.id WHERE m.created_at < :past")
            result = await session.execute(sql, {"past": mess_past_date})
            rows = result.fetchall()   
            if rows:  
                for row in rows:
                    delete_file_from_disk(row.filename, UPLOAD_DIR)
                    # теперь удалим записи в базе данных   
                    sql: TextClause = text("DELETE FROM attachments WHERE mess_id=:id") 
                    result = await session.execute(sql, {"id": row.id})     
            # теперь можем удалить сами слишком старые сообщения
            query = delete(MessageOrm).where( MessageOrm.created_at < mess_past_date )        
            result = await session.execute(query)
            await session.commit()
            logger.success("Deleting too old messages was successful")
        except Exception as e:
            await session.rollback()
            logger.error(f"An error occurred while deleting too old messages: {e}")

        """ удаляем из памяти персональные сообщения, не востребованные в течение суток """
        personal.clear_expired()


async def notify_task_closing(session: SessionDep, task_id: int) -> None:
    sql: TextClause = text("SELECT title, respons FROM tasks WHERE id=:id LIMIT 1") 
    result = await session.execute(sql, {"id": task_id})
    task = result.first() 
    if task: 
        mess_text = f"Задача {task.title} закрывается" if settings.language == "ru" else f"Task {task.title} is being closed"     
        personal.add_message(to=task.respons, sender='System', messtext=mess_text) 


async def notify_all(session: SessionDep, message: str, exclude_user: int = 0) -> None:
    sql = select(UserOrm)
    if exclude_user > 0:
        sql = sql.where(UserOrm.userid != exclude_user)
    result = await session.execute(sql)
    users = result.scalars().all()
    for usr in users:
        personal.add_message(to=usr.userid, sender='System', messtext=message) 


async def notify_new_comment(session: SessionDep, task_id: int, creator: int) -> None:
    sql: TextClause = text( "SELECT t.title FROM tasks t WHERE t.id=:id LIMIT 1")
    result = await session.execute(sql, {"id": task_id})
    row = result.first()
    if row:
        message: str = f"Создан комментарий к задаче {row.title}" if settings.language == "ru" else f"A comment has been created for the task {row.title}"  
        await notify_all(session, message, creator)


async def ask_ai_get_response(session: SessionDep, question: str) -> dict:
    response: str | None = await aiModel.ask_me(question)
    if response:
        if not await user_exists("ai", session):
            user_orm = UserOrm(username="ai", password="911!!_l,sdfg0367>", active=False, fio="Artificial Intelligence")
            await db_add_record(session, user_orm, "User")
        userId = await check_user("ai", "911!!_l,sdfg0367>", session, True)    
        message_orm = MessageOrm(userid=userId, messtext=response)
        await db_add_record(session, message_orm, log_label=f"new message from ai")      
    return {"result": "ok"}


async def ai_session(session: SessionDep, question: str, userid: int) -> dict:
    session_history: list[ChatCompletionMessageParam] = await create_ai_history(session, question, userid)

    response: str | None = await aiModel.send_session_history(sess_history=session_history)

    if response:
        if not await user_exists("ai", session):
            await db_add_record(session, UserOrm(username="ai", password="911!!_l,sdfg0367>", active=False, fio="Artificial Intelligence", avatar="&#129302;"), "User")
        ai_userId = await check_user("ai", "911!!_l,sdfg0367>", session, True)

        await db_add_record(session, MessageOrm(userid=ai_userId, messtext=response), log_label=f"new message from ai") 

        await db_add_record(session, AI_Sessions(userid=userid, messtext=question, role='user'), log_label=f"new question for ai to session")  
        await db_add_record(session, AI_Sessions(userid=userid, messtext=response, role='assistant'), log_label=f"new response from ai to session")  
    return {"result": "ok"}

