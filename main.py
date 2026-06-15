from urllib.parse import quote, unquote
import os
from datetime import date
from typing import Annotated, Literal
from sqlalchemy.sql import func
from sqlalchemy import select
from send2anywhere import Notifier_factory
from services import ai_session, daily_morning_task 
from contextlib import asynccontextmanager
import uvicorn
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import TextClause, text
from fastapi import Cookie, FastAPI, File, Form, Depends, HTTPException, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from fastapi.middleware.cors import CORSMiddleware
from database import db_add_record, db_connection_check, engine, SessionDep, check_user, create_all_tables, get_massages_from_db, user_exists, new_session
from database import background_checks, make_message_read_liked, how_much_messages
from models import AttachmentsOrm, Comments, CommentsOrm, DeadlineEdit, DocsNotes, MessId, Message, MessageOrm, NewUser, NotifyInfo, TaskAttachmentsOrm, TaskEdit, TaskState, Tasks, TasksOrm, User, UserInfo, Docs, DocsOrm, UserProps 
from sheduler import AsyncPeriodicTask, AsyncDailyTask
from tokens import create_access_token, get_current_user 
from config import BASE_DIR, UPLOAD_DIR, settings, logger, ERROR_MESSAGES_EN, ERROR_MESSAGES_RU
from services import ProtectedStaticFiles, create_new_user, delete_file_from_disk, get_err_message, load_internationalization_data, makeFileResponse, notify_all, notify_new_comment, notify_task_closing, personal, save_user_file_to_disk
from ai import aiModel


os.makedirs(UPLOAD_DIR, exist_ok=True)

templates: Jinja2Templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# Lifespan function to perform startup and shutdown tasks, such as checking the database connection, 
# creating tables, starting background tasks for checking users activity and 
# closing the database connection pool at shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_connection_check()
    await create_all_tables()
    # Background task for checking users activity every XX minutes 
    periodic_task = AsyncPeriodicTask(interval=settings.users_activity_check_interval, task_func=lambda: background_checks(new_session))
    periodic_task.start()
    # Daily task for notifying about deadlines (every day at 8:00 AM)
    daily_task = AsyncDailyTask(target_hour=8, target_minute=0, task_func=lambda: daily_morning_task(new_session))
    daily_task.start()

    yield

    await periodic_task.stop()
    await daily_task.stop()
    # Closing the database connection pool at shutdown
    await engine.dispose()


app = FastAPI(lifespan=lifespan)


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/uploads", ProtectedStaticFiles(directory=UPLOAD_DIR), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint to verify that the application is running and can connect to the database
@app.get("/health")
async def health_check() -> JSONResponse:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse(content={"status": "ok"})   
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service Unavailable")


# Get the authorization page with the form for entering username and password, and also with the flash message if there is an error during the previous authorization attempt 
@app.get("/", tags=["Communicator", "auth"], summary="Communicator auth page") 
async def auth_page(request: Request, flash_msg: str | None = Cookie(None), i18n_data: dict = Depends(lambda: load_internationalization_data(BASE_DIR, settings.language))):
    decoded_msg = unquote(flash_msg) if flash_msg else None
    data: dict = {"flash_msg": decoded_msg} if decoded_msg else {}
    response = templates.TemplateResponse("index.html", {"request": request, **i18n_data, **data})
    if flash_msg:
        response.delete_cookie(key="flash_msg")
    return response


# Get the registration page
@app.get("/users/reg", tags=["Communicator", "new user"], summary="Communicator new user registration page")
async def regstration_page(request: Request, i18n_data: dict = Depends(lambda: load_internationalization_data(BASE_DIR, settings.language))):
    return templates.TemplateResponse("reg.html", {"request": request, **i18n_data})


# Get the messages page (a single-page application that receives all the data for a given application via asynchronous requests to the FastApi backend and updates the page dynamically without reloading) 
@app.get("/messages", tags=["Communicator", "home page", "messages list"], summary="Welcome to the our communicator home page")
async def messages_page(session: SessionDep, request: Request, current_user: UserInfo = Depends(get_current_user), i18n_data: dict = Depends(lambda: load_internationalization_data(BASE_DIR, settings.language))):
    how_much: int = await how_much_messages(session)
    data = {"userid": current_user.userid, "username": current_user.username, "messages_check_interval": settings.client_messages_check_interval, "users_check_interval": settings.client_users_check_interval, "msg_count": how_much}
    return templates.TemplateResponse("messages.html", {"request": request, **data, **i18n_data})


# Get all messages with their authors and info about how many users read and liked each message
@app.get("/messages/get_messages/{id}", tags=["Communicator", "get messages"], summary="Get all the messages")
async def messages(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    return await get_massages_from_db(id, session, history=False)


# Add a new message to the messages list
@app.post("/messages/add",  tags=["Communicator", "new message"], summary="Add a new message")
async def add_message(new_message: Message, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    result: dict = {"result": "error"}
    message_orm = MessageOrm(**new_message.model_dump(), checked=0)
    result = await db_add_record(session, message_orm, log_label=f"new message from user id={new_message.userid}")    
    if result["result"] == "ok" and aiModel.active:
        if (len(new_message.messtext)>3) and ("AI" in new_message.messtext[:4].upper() or "ИИ" in new_message.messtext[:4].upper()):
            result = await ai_session(session, new_message.messtext, new_message.userid)
    return result    


# Create a new user with the registration form data, checking the password confirmation and the uniqueness of the username, 
# setting error message in cookie and redirecting to the auth page if something is wrong, otherwise - creating a new user, his token and redirecting to the messages page
@app.post("/users/add",  tags=["Communicator", "new user"], summary="Add a new user")
async def add_user(new_user: Annotated[NewUser, Form()], session: SessionDep) -> RedirectResponse:
    if (new_user.password1 == new_user.password2):
        if (await user_exists(new_user.username, session)):
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(key="flash_msg", value=quote(get_err_message("username_taken","This name was already taken")), httponly=True)
            return response
        else:
            if new_user.secret == settings.friend_reference:  
                return await create_new_user(new_user, session)
            else:  # введенное секретное слово не совпадает с правильным из настроек 
                response = RedirectResponse(url="/", status_code=303)
                response.set_cookie(key="flash_msg", value=quote(get_err_message("secret_word","Секретное слово неверное")), httponly=True)
                return response
    else:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=quote(get_err_message("password_mismatch","Пароли не совпадают")), httponly=True)
        return response


# User authorization and token generation, setting http-only cookie with the token and redirecting to the messages page if authorization is successful, otherwise - redirecting to the auth page with error message in cookie
@app.post("/users/auth",  tags=["Communicator", "user authorization"], summary="๊")
async def user_auth(user: Annotated[User, Form()], session: SessionDep) -> RedirectResponse:
    userid: int = await check_user(user.username, user.password, session) 
    if (userid > 0):
        token: str = create_access_token(data={"username": user.username, "userid": str(userid)})
        # set http-only token in cookie and redirect to messages page
        response = RedirectResponse(url="/messages", status_code=303)
        response.set_cookie(key="access_token", value=token, httponly=True)
        return response
    else:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=quote(get_err_message("authorization_error","authorization error")), httponly=True)
        return response


#  mark the message read
@app.post("/messages/check_read", tags=["Communicator", "messages", "check_read"], summary="mark the message have been read")
async def message_check_read(mess_read: MessId, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    resultOnly: bool = (mess_read.username == current_user.username) # True if user tries to mark his own message as read
    return await make_message_read_liked(session, mess_read.id, current_user, "mess_read", resultOnly)


# like
@app.post("/messages/like", tags=["Communicator", "likes"], summary="get likes for the message")
async def message_like(like: MessId, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    resultOnly: bool = (like.username == current_user.username) # True if user tries to like his own message
    return await make_message_read_liked(session, like.id, current_user, "mess_likes", resultOnly)


# Get the list of all users with their activity status (active/inactive) and fio for the personal messages page
@app.get("/users/get_activity", tags=["Communicator", "users", "activity"], summary="Get list of the activ users")
async def get_users_activity(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql: TextClause = text("SELECT u.userid, u.username, u.active, u.fio, u.avatar, u.email, u.ai_role FROM users u ORDER BY u.username") 
    result = await session.execute(sql) 
    return result.mappings().all()


# Get the first message id in the database to set the starting point for loading messages on the client side
@app.get("/messages/first_id", tags=["Communicator", "messages", "first id"], summary="the first message id")
async def first_id(session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    query = select(func.min(MessageOrm.id))
    result = await session.execute(query)
    first_id_value: int | None = result.scalar_one_or_none()
    return {"first_id": first_id_value or 1}


# Upload the attachment file and add the message with the file link to the messages list
@app.post("/message-attachment/", tags=["Communicator", "files", "upload"], summary="attachment files uploading")
async def upload_file(session: SessionDep, file: UploadFile = File(...), current_user: UserInfo = Depends(get_current_user)) -> dict:
    result: dict = await save_user_file_to_disk(current_user.username, UPLOAD_DIR, file)
    
    #  Writing file info to the database and linking it to the message   
    if result['error'] == 'OK':
        extension:str = str(result["ext"]).lower()
        shortFileName:str = result["orig_filename"]
    
        if ((extension == ".png") or (extension == ".jpg") or (extension == ".jpeg")):
            shortFileName = f"<img src='/uploads/{result['unique_filename']}' width='90%' />"
        else:    
            if len(shortFileName) > 30:
                shortFileName = shortFileName[:22] + '... ' + extension
            shortFileName = "&#128206; " + shortFileName + " &#128206;"       
        new_messageOrm: MessageOrm = MessageOrm(userid=current_user.userid, messtext=shortFileName, checked=0)
        session.add(new_messageOrm)    
        await session.flush() 
        attachmentObj: AttachmentsOrm = AttachmentsOrm(mess_id=new_messageOrm.id, filename=result["unique_filename"], origname=result["orig_filename"])
        await db_add_record(session, attachmentObj, "new attachment")
        logger.success(f"File {file.filename} successfully uploaded")
        return {"filename": file.filename, "status": "saved"}
    else: 
        return result 


# Upload the new attachment file for a task
@app.post("/task-attachment/{task_id}", tags=["Communicator", "files", "upload"], summary="attachment files uploading")
async def upload_task_attachment(task_id: int, session: SessionDep, file: UploadFile = File(...), current_user: UserInfo = Depends(get_current_user)) -> dict:
    result: dict = await save_user_file_to_disk(current_user.username, UPLOAD_DIR, file)
    
    #  Writing file info to the database and linking it to the message   
    if result['error'] == 'OK':
        extension:str = result["ext"]
        extension:str = extension.lower()
        shortFileName:str = result["orig_filename"]   
        if len(shortFileName) > 40:
            shortFileName = shortFileName[:32] + '... ' + extension
            
        new_attachment: TaskAttachmentsOrm = TaskAttachmentsOrm(task_id=task_id, filename=result["unique_filename"], origname=shortFileName)
        await db_add_record(session, new_attachment, f"new attachment file for task id={task_id} ")
        logger.success(f"File {file.filename} successfully uploaded")
        return {"filename": file.filename, "status": "saved"}
    else: 
        return result 


# Download the attachment file (message)
@app.get("/download-attachment/{id}", tags=["Communicator", "files", "download"], summary="download the attachment file")
async def download_file(session: SessionDep, id: int, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT B.origname, B.filename FROM attachments B WHERE B.mess_id=:id LIMIT 1")
    result = await session.execute(sql, {"id": id})
    row = result.first() 
    if row:
        file_path: str = os.path.join(UPLOAD_DIR, row.filename)   
        if not os.path.exists(file_path):
            return {"error": get_err_message("file_not_found","File not found")}
        return FileResponse(
            path=file_path, 
            filename=row.origname,  
            media_type='application/octet-stream'
        )
    else:
        return {"error": get_err_message("file_not_found","File not found")}
    

# Download the task - attachment file
@app.get("/tasks/download-file/{id}", tags=["Communicator", "files", "download"], summary="download the attachment file")
async def download_task_attachment(session: SessionDep, id: int, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT B.origname, B.filename FROM task_attachments B WHERE B.id=:id LIMIT 1")
    result = await session.execute(sql, {"id": id})
    row = result.first() 
    if row:
        file_path: str = os.path.join(UPLOAD_DIR, row.filename)   
        if not os.path.exists(file_path):
            return {"error": get_err_message("file_not_found","File not found")}
        return FileResponse(
            path=file_path, 
            filename=row.origname,  
            media_type='application/octet-stream'
        )
    else:
        return {"error": get_err_message("file_not_found","File not found")}


# Get the previous messages based on the message id (for infinite scroll implementation on the client side)
@app.get("/messages/get_prev/{id}", tags=["Communicator", "messages history"], summary="Get all the previous messages")
async def prev_messages(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    return await get_massages_from_db(id, session, history=True) 


# Get the number of reads and likes for the messages and also the information if the message is unread for the current user to warn that user (unread - if the message was created in the last 24 hours, user is not the author of the message and user did not read this message)
@app.get("/messages/conditions", tags=["Communicator", "mess. conditions"], summary="Get all the reads and likes")
async def conditions(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql: TextClause = text("""
        SELECT m.id, (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as reads, (SELECT count(*) FROM mess_likes L WHERE L.mess_id=m.id) as likes, 
        CASE 
        WHEN NOT EXISTS(SELECT 1 FROM mess_read Z WHERE Z.mess_id=m.id AND Z.userid=:userid) AND (m.userid <> :userid2) AND (m.created_at >= CURRENT_DATE - INTERVAL '1 day') THEN 1 
        ELSE 0 END as unread        
        FROM messages m ORDER BY m.id desc LIMIT :max_mess_count 
    """) 
    result = await session.execute(sql, {"userid": current_user.userid, "userid2": current_user.userid, "max_mess_count": settings.current_messages_max_count}) 
    return result.mappings().all()  


# Get the list of all users to fill the select element with the users in the new task form
@app.get("/users/get_users", tags=["Communicator", "users"], summary="Get list of the users")
async def get_userslist(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql: TextClause = text("SELECT u.userid, u.username FROM users u ORDER BY u.username") 
    result = await session.execute(sql) 
    return result.mappings().all()  


# Get all active tasks with all their comments and info about expired tasks
@app.get("/tasks/get_tasks/{include_closed}", tags=["Communicator", "tasks"], summary="Get all active tasks")
async def get_tasks(session: SessionDep, include_closed: int=0, current_user: UserInfo = Depends(get_current_user)):
    sql: TextClause = text("""
        SELECT t.id, u1.username as creator, t.title, t.description, to_char(t.created_at, 'DD.MM.YYYY') as created_at, to_char(t.start_date, 'DD.MM.YYYY') as start_date, u2.username as respons, 
        CASE WHEN t.status='closed' THEN 'closed' WHEN DATE(t.start_date) > CURRENT_DATE THEN 'planned' ELSE 'started' END as status,                    
        to_char(t.deadline, 'DD.MM.YYYY') as deadline, CASE WHEN t.deadline < LOCALTIMESTAMP AND t.status<>'closed' THEN 1 else 0 END as expired, (
            SELECT COALESCE(json_agg(json_build_object('c_id', c.id, 'username', u.username, 'comment', c.comment, 'created_at', to_char(c.created_at, 'DD.MM.YYYY HH24:MI')) ORDER BY c.id ASC), '[]'::json) 
            FROM comments c INNER JOIN users u ON c.creator = u.userid
            WHERE c.task_id=t.id 
        ) as comments, (
            SELECT COALESCE(json_agg(json_build_object('att_id', a.id, 'filename', a.origname) ORDER BY a.id ASC), '[]'::json)  
            FROM task_attachments a WHERE a.task_id=t.id                              
        ) as attac           
        FROM tasks t  
        INNER JOIN users u1 ON t.creator=u1.userid 
        INNER JOIN users u2 ON t.respons=u2.userid   
        WHERE t.status <> 'closed' OR :include_closed=1       
        ORDER BY t.created_at
    """) 
    result = await session.execute(sql, {'include_closed': include_closed}) 
    return result.mappings().all()  


# Get data for Gantt diagram
@app.get("/tasks/gant/{include_closed}", tags=["Communicator", "tasks"], summary="Get all active tasks")
async def get_diagram_data(session: SessionDep, include_closed: int=0, current_user: UserInfo = Depends(get_current_user)):
    sql: TextClause = text("""SELECT t.id, DATE(t.start_date) - CURRENT_DATE as startcol, DATE(t.deadline) - CURRENT_DATE as endcol, t.title, to_char(t.deadline, 'DD.MM.YYYY') as deadline, 
                    t.respons, CASE WHEN u.fio='' THEN u.username ELSE u.fio END as executor, CASE WHEN CURRENT_DATE > t.deadline AND t.status<>'closed' THEN 1 ELSE 0 END as expired, 
                    CASE WHEN t.status='closed' THEN 'closed' WHEN DATE(t.start_date) > CURRENT_DATE THEN 'planned' ELSE 'started' END as status
                    FROM tasks t INNER JOIN users u ON t.respons=u.userid 
                    WHERE t.status <> 'closed' OR :include_closed = 1       
                    ORDER BY t.start_date""") 
    result = await session.execute(sql, {'include_closed': include_closed}) 
    return result.mappings().all()  


# Add a new task based on the message text 
@app.post("/tasks/add",  tags=["Communicator", "new task"], summary="Add a new task")
async def add_task(new_task: Tasks, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    logger.info("Adding new task with id: " + str(new_task.id))
    sql: TextClause = text("SELECT m.messtext FROM messages m WHERE m.id=:id") 
    result = await session.execute(sql, {"id": new_task.id}) 
    row = result.first()
    if row:   
        task_description: str = row.messtext
        sql: TextClause = text("SELECT t.id FROM tasks t WHERE t.title=:title") 
        result = await session.execute(sql, {"title": new_task.title}) 
        row = result.first()
        if row:
            return {"result": "error", "details": "Task with the same title already exists" if settings.language == "en" else "Задача с таким названием уже существует"}  
         
        if new_task.start_date <= date.today():
            new_task.status = TaskState.started
        else: 
            new_task.status = TaskState.planned     
        newTaskOrm: TasksOrm = TasksOrm(creator=new_task.creator, respons=new_task.respons, start_date=new_task.start_date, deadline=new_task.deadline, title=new_task.title, description=task_description, status=new_task.status)
        try:
            session.add(newTaskOrm)
            await session.commit()
            logger.success(f"New task id: {new_task.id} successfully added")    
            message: str = f"Создана новая задача: {new_task.title}" if settings.language == "ru" else f"New task was created: {new_task.title}"
            await notify_all(session, message, current_user.userid)
            return {"result": "ok"}
        except IntegrityError as e:
            await session.rollback()
            logger.error("Error occurred while trying to add task: this message is already added to 'Tasks'")
            return {"result": "error", "details": "Task already exists" if settings.language == "en" else "Задача с таким сообщением уже существует"}
        except Exception as e:
            await session.rollback()
            logger.error(f"Error occurred while trying to add task: {e}")    
            return {"result": "error", "details": "An error occurred while adding the task" }    
    else:
        return {"result": "error", "details": "Source message was not found"}


# Close the task (only for the task creator)
@app.delete("/tasks/close/{id}", tags=["Communicator", "tasks", "close"], summary="close the task")
async def close_task(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    try:
        # Найти приаттаченные файлы в task_attachments и удалить их с диска перед удалением задачи !!! 
        sql: TextClause = text("SELECT a.filename FROM task_attachments a WHERE a.task_id=:task_id")
        result = await session.execute(sql, {"task_id": id})
        rows = result.fetchall()   
        if rows:  
            for row in rows:
                delete_file_from_disk(row.filename, UPLOAD_DIR)
            # теперь удалим записи в базе данных   
            sql: TextClause = text("DELETE FROM task_attachments WHERE task_id=:id") 
            result = await session.execute(sql, {"id": id}) 
        # Шлем оповещение о закрытии задачи     
        await notify_task_closing(session, id)       
        #  Удаляем комментарии к задаче и саму задачу закрываем
        sql: TextClause = text("DELETE FROM comments WHERE task_id=:id") 
        result = await session.execute(sql, {"id": id}) 
        sql: TextClause = text("UPDATE tasks SET status='closed' WHERE id=:id") 
        result = await session.execute(sql, {"id": id}) 
        await session.commit()
        return {"result": "ok"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to close task: {e}")    
        return {"result": "error", "details": "An error occurred while closing the task" }    
    

# Edit the task description (only for the task creator)
@app.post("/tasks/edit", tags=["Communicator", "tasks", "close"], summary="edit the task")
async def edit_task(task: TaskEdit, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    if current_user.userid == task.userid:
        sql: TextClause = text("UPDATE tasks SET description=:messtext WHERE id=:id") 
        result = await session.execute(sql, {"messtext": task.messtext, "id": task.id}) 
        await session.commit()
        return {"result": "ok"}
    else:
        return {"result": "error", "details": get_err_message("you are not the task creator", "")}


# Edit the task deadline (only for the task creator)
@app.post("/tasks/edit/{datefield}", tags=["Communicator", "tasks", "deadline"], summary="edit task deadline")
async def edit_deadline(datefield: Literal["deadline", "start_date"], deadline: DeadlineEdit, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    if (current_user.userid == deadline.userid) and (datefield in ("deadline", "start_date")):
        sql: TextClause = text(f"UPDATE tasks SET {datefield}=:deadline WHERE id=:id AND :deadline >= CURRENT_DATE") 
        await session.execute(sql, {"deadline": deadline.deadline, "id": deadline.id}) 
        await session.commit()
        return {"result": "ok"}
    else:
        return {"result": "error", "details": get_err_message("you are not the task creator", "")}


# Add a new personal message to the personal messages list for the current user
@app.post("/messages/send_personal",  tags=["Communicator", "personal"], summary="send personal message")
async def add_personal_message(message: Message, current_user: UserInfo = Depends(get_current_user)) -> dict:
    if personal.no_have_such_message(message.userid, current_user.username, message.messtext):
        personal.add_message(to=message.userid, sender=current_user.username, messtext=message.messtext)  
        return {"result": "ok"}
    else:
        return {"result": "error", "details": get_err_message("duplicated message","") }
    

# Get all personal messages for the current user
@app.get("/messages/get_personal", tags=["Communicator", "personal"], summary="get personal message")    
async def get_personal_message(current_user: UserInfo = Depends(get_current_user)):
    return personal.pop(current_user.userid)


# Delete a message and its associated files
@app.delete("/messages/delete/{id}", tags=["Communicator", "message", "delete"], summary="delete message")
async def del_message(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict: 
    sql: TextClause = text("SELECT a.filename FROM documents a WHERE a.mess_id=:mess_id LIMIT 1")
    result = await session.execute(sql, {"mess_id": id})
    row = result.first()   
    # if the file is listed in the important documents - we must not delete it from the messaages and from the disk!
    if row:  
        return {"result": "error", "details": "This message is listed in important documents"}
    else:    
        sql: TextClause = text("SELECT a.filename FROM attachments a WHERE a.mess_id=:mess_id LIMIT 1")
        result = await session.execute(sql, {"mess_id": id})
        row = result.first()   
        # if the file from attachments is not listed in the important documents - delete it from disk
        if row:  
            delete_file_from_disk(row.filename, UPLOAD_DIR)
            # теперь удалим записи в базе данных   
            sql: TextClause = text("DELETE FROM attachments WHERE mess_id=:id") 
            result = await session.execute(sql, {"id": id})         

        sql = text("DELETE FROM mess_likes WHERE mess_id=:id") 
        result = await session.execute(sql, {"id": id})         
        sql = text("DELETE FROM mess_read WHERE mess_id=:id") 
        result = await session.execute(sql, {"id": id})         
        sql = text("DELETE FROM messages WHERE id=:id") 
        result = await session.execute(sql, {"id": id}) 
        await session.commit()
        return {"result": "ok"}


# Delete a document from the important documents list
@app.delete("/documents/delete/{id}", tags=["Communicator", "documents", "delete"], summary="delete document")
async def del_document(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    sql: TextClause = text("SELECT a.savedname as filename FROM documents a WHERE a.mess_id=:mess_id AND NOT EXISTS(SELECT 1 FROM attachments b WHERE b.mess_id=a.mess_id) LIMIT 1")
    result = await session.execute(sql, {"mess_id": id})
    row = result.first()   
    # if the file from documents is not listed in the attachments - delete it from disk, because it is not used in messages anymore and is not listed in important documents
    if row:  
        file_path: str = os.path.join(UPLOAD_DIR, row.filename)   
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.success(f"File {file_path} successfully deleted")
            except OSError as e:
                logger.error(f"Error occurred while deleting file {file_path}: {e.strerror}")
    # now delete the database records            
    sql = text("DELETE FROM documents WHERE mess_id=:id") 
    result = await session.execute(sql, {"id": id}) 
    await session.commit()
    return {"result": "ok"}


# Get all the important documents list
@app.get("/documents/get", tags=["Communicator", "documents", "get all"], summary="get all the documents")
async def get_ducuments(session: SessionDep, currnet_user: UserInfo = Depends(get_current_user)):
    result = await session.execute(text('SELECT mess_id, filename, notes FROM documents ORDER BY created_at'))
    return result.mappings().all()


# Add a new document to the important documents list
@app.post("/documents/add",  tags=["Communicator", "documents", "add"], summary="Add a new document")
async def add_document(new_doc: Docs, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    sql: TextClause = text("SELECT A.filename, A.origname FROM attachments A WHERE A.mess_id=:mess_id LIMIT 1")
    result = await session.execute(sql, {"mess_id": new_doc.mess_id})
    row = result.first()   
    if row:  
        try:
            new_document = DocsOrm(mess_id=new_doc.mess_id, filename=row.origname, savedname=row.filename, notes='')
            session.add(new_document)
            await session.commit()
            logger.success(f"Document {row.origname} successfully added")
            return {"result": "ok"}
        except IntegrityError as e:
            await session.rollback()
            logger.error(f"Error occurred while trying to add already added document. {e}")
            return {"result": "error", "details": f"Document {row.origname} is already added" if settings.language == "en" else f"Документ {row.origname} уже существует"}
        except Exception as e:
            await session.rollback()
            logger.error(f"Error occurred while trying to add document: {e}")
            return {"result": "error", "details": "An error occurred while adding the document" if settings.language == "en" else "Произошла ошибка при добавлении документа"}
    else:
        return {"result": "error", "details": "Source file was not found in attachments"}    


# Download the file from important document lists\s item  
@app.get("/documents/download/{id}", tags=["Communicator", "files", "download"], summary="download the attachment file")
async def get_document_file(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql: TextClause = text("SELECT A.filename, A.savedname FROM documents A WHERE A.mess_id=:mess_id LIMIT 1")
    result = await session.execute(sql, {"mess_id": id})
    row = result.first() 
    if row:
        return makeFileResponse(row.savedname, row.filename, UPLOAD_DIR)
    else:
        return {"result": "Document not found"}


# add the document\s description\notes, which will be visible in the important documents list
@app.post("/documents/add_notes", tags=["Communicator", "documents", "add_notes"], summary="add notes to the document")
async def add_doc_description(descr: DocsNotes, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    sql: TextClause = text("""UPDATE documents SET notes=:notes WHERE mess_id=:mess_id""")
    try:
        await session.execute(sql, {"notes": descr.notes, "mess_id": descr.mess_id}) 
        await session.commit()
        return {"result": "OK"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to add document description: {e}")    
        return {"result": "error", "details": "An error occurred while adding the document description" if settings.language == "en" else "Произошла ошибка при добавлении описания документа"}               


# add / update user's full name
@app.post("/users/fio", tags=["Communicator", "users", "fio"], summary="add first / last names")
async def add_fio(userProps: UserProps, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    sql: TextClause = text("""UPDATE users SET fio=:fio, avatar=:avatar, email=:email, ai_role=:ai_role WHERE userid=:userid""")
    try:
        await session.execute(sql, {"fio": userProps.fio, "avatar": userProps.avatar, "userid": userProps.userid, "email": userProps.email, "ai_role": userProps.ai_role}) 
        await session.commit()
        return {"result": "OK"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to update user's full name: {e}")    
        return {"result": "error", "details": "An error occurred while updating the user's full name" if settings.language == "en" else "Произошла ошибка при обновлении полного имени пользователя"}


# notify a user
@app.post("/users/notify", tags=["Communicator", "users", "email", "sms"], summary="notify a user by email or sms message")
async def send_notifocation(notifyMessage: NotifyInfo, current_user: UserInfo = Depends(get_current_user)) -> dict:
    notify = Notifier_factory.create(notifyMessage.messType, notifyMessage.envelope) 
    return await notify.send(notifyMessage.messtext)


# add new comment to the task
@app.post("/comments/add",  tags=["Communicator", "Comments", "new message"], summary="Add a new comment to the task")
async def add_comment(new_comment: Comments, session: SessionDep, current_user: UserInfo = Depends(get_current_user)) -> dict:
    newComment_record = CommentsOrm(task_id=new_comment.task_id, creator=new_comment.creator, comment=new_comment.comment)
    result: dict = await db_add_record(session, newComment_record, f"new comment for task id={new_comment.task_id} user={new_comment.creator}")
    await notify_new_comment(session, new_comment.task_id, new_comment.creator)               
    return result


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(conn: ConnectionAbortedError, exc: HTTPException):
    if exc.status_code == 401:
        logger.info(exc.detail if exc.detail else "Error occurred during user authentication. Redirecting to '/'")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=exc.detail if exc.detail else "Please login first", httponly=True)
        return response 


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse | RedirectResponse:
    readable_errors = []
    
    for error in exc.errors():
        err_type = error['type']
        err_field = None
        template = ""

        if settings.language == "ru":
            template = ERROR_MESSAGES_RU.get(err_type, error['msg'] if error['msg'] else " Ошибка валидации данных")
        else:
            template = ERROR_MESSAGES_EN.get(err_type, error['msg'] if error['msg'] else "Validation error")

        if err_type.startswith("value_error") or err_type.startswith("type_error"):
                err_field = ",".join(str(loc) for loc in error['loc'])
        err_field = "" if err_field is None else err_field.replace("body,","").replace("query,","").replace("path,","").replace("header,","").replace("cookie,","")

        readable_errors.append(template + (f" ({err_field})" if err_field != ""  else ""))

    final_msg = " | ".join(readable_errors)

    accept_header = request.headers.get("accept", "")
    
    if "application/json" in accept_header:
        return JSONResponse(
            status_code=422,
            content={"result": "error", "details": final_msg}
        )
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="flash_msg", value=quote(final_msg), httponly=True)  
    return response


if __name__ == "__main__":    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)