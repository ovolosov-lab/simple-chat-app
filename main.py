from contextlib import asynccontextmanager
from datetime import datetime
import os
from typing import Annotated, Any
import bleach
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Result, text
import uvicorn
from fastapi import Cookie, FastAPI, File, Form, Depends, HTTPException, Header, Request, UploadFile
from database import db_connection_check, engine, SessionDep, check_user, create_all_tables, user_exists, new_session
from models import Base, Comments, CommentsOrm, DeadlineEdit, DocsNotes, MessId, Message, MessageOrm, NewUser, TaskEdit, Tasks, TasksOrm, User, UserFio, UserInfo, UserOrm, Docs, DocsOrm 
from sheduler import AsyncPeriodicTask
from tokens import create_access_token, get_current_user 
from sqlalchemy.exc import IntegrityError
from fastapi.middleware.cors import CORSMiddleware
from config import settings, logger, ERROR_MESSAGES_EN, ERROR_MESSAGES_RU
from services import ProtectedStaticFiles, create_new_user, delete_file_from_disk, load_internationalization_data, background_checks, makeFileResponse, no_have_such_message, get_personal_messages, personal, save_user_file_to_disk, how_much_messages


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_connection_check()
    await create_all_tables()
    # Background task for checking users activity every XX minutes (if there are messages read or sent in the last XX minutes - user is active, if not - user is inactive)
    periodic_task = AsyncPeriodicTask(interval=settings.users_activity_check_interval, task_func=lambda: background_checks(new_session))
    periodic_task.start()
    
    yield

    await periodic_task.stop()
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


@app.get("/health")
async def health_check():
    """Health check endpoint to verify that the application is running and can connect to the database."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service Unavailable")


@app.get("/", tags=["Communicator", "auth"], summary="Communicator auth page")
async def auth_page(request: Request, flash_msg: str | None = Cookie(None), i18n_data: dict = Depends(lambda: load_internationalization_data(BASE_DIR, settings.language))):
    data = {"flash_msg": flash_msg} if flash_msg else {}
    response = templates.TemplateResponse("index.html", {"request": request, **i18n_data, **data})
    if flash_msg:
        response.delete_cookie(key="flash_msg")
    return response


@app.get("/users/reg", tags=["Communicator", "new user"], summary="Communicator new user registration page")
async def regstration_page(request: Request, i18n_data: dict = Depends(lambda: load_internationalization_data(BASE_DIR, settings.language))):
    return templates.TemplateResponse("reg.html", {"request": request, **i18n_data})


@app.get("/messages", tags=["Communicator", "home page", "messages list"], summary="Welcome to the our communicator home page")
async def messages_page(session: SessionDep, request: Request, current_user: UserInfo = Depends(get_current_user), i18n_data: dict = Depends(lambda: load_internationalization_data(BASE_DIR, settings.language))):
    how_much: int = await how_much_messages(session)
    data = {"userid": current_user.userid, "username": current_user.username, "messages_check_interval": settings.client_messages_check_interval, "users_check_interval": settings.client_users_check_interval, "msg_count": how_much}
    return templates.TemplateResponse("messages.html", {"request": request, **data, **i18n_data})


@app.get("/messages/get_messages/{id}", tags=["Communicator", "get messages"], summary="Get all the messages")
async def messages(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    result: Result[Any]
    if id <= 0:
        sql = text("""
           SELECT t.id, t.username, t.messtext, t.created_at, t.checked, t.likes, t.task 
           FROM (
             SELECT m.id, u.username, m.messtext, to_char(m.created_at, 'DD.MM.YYYY HH24:MI') as created_at, 
               (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as checked, 
               (SELECT count(*) FROM mess_likes R WHERE R.mess_id=m.id) as likes, 0 as task          
             FROM messages m INNER JOIN users u ON m.userid=u.userid 
             ORDER BY m.id DESC LIMIT :max_mess_count 
           ) t ORDER BY t.id
        """) 
        result = await session.execute(sql, {"max_mess_count": settings.current_messages_max_count}) 
    else: 
        sql = text(""" 
            SELECT m.id, u.username, m.messtext, to_char(m.created_at, 'DD.MM.YYYY HH24:MI') as created_at, 
            (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as checked, 
            (SELECT count(*) FROM mess_likes R WHERE R.mess_id=m.id) as likes, 0 as task                                  
            FROM messages m INNER JOIN users u ON m.userid=u.userid 
            WHERE m.id > :mess_id       
            ORDER BY m.id
        """)    
        result = await session.execute(sql, {"mess_id": id}) 
    return result.mappings().all()


@app.post("/messages/add",  tags=["Communicator", "new message"], summary="Add a new message")
async def add_message(new_message: Message, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
        clean_text = bleach.clean(new_message.messtext, tags=['b', 'i'], strip=True)
        try:
            new_message = MessageOrm(userid=new_message.userid, messtext=clean_text, checked=0)
            session.add(new_message)
            await session.commit()
            logger.success(f"User message {new_message.userid} successfully added")
            return {"result": "ok"}
        except Exception as e:
            await session.rollback()
            logger.error(f"Error occurred while trying to add message: {e}")    
            return {"result": "error"}       



@app.post("/users/add",  tags=["Communicator", "new user"], summary="Add a new user")
async def add_user(new_user: Annotated[NewUser, Form()], session: SessionDep):
    if (new_user.password1 == new_user.password2):
        if (await user_exists(new_user.username, session)):
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(key="flash_msg", value=ERROR_MESSAGES_EN.get("username_taken","This name was already taken") if settings.language == "en" else ERROR_MESSAGES_RU.get("username_taken","это имя уже занято"), httponly=True)
            return response
        else:
            if new_user.secret == settings.friend_reference:  
                return await create_new_user(new_user, session)
            else:  # введенное секретное слово не совпадает с правильным из настроек 
                response = RedirectResponse(url="/", status_code=303)
                response.set_cookie(key="flash_msg", value=ERROR_MESSAGES_EN.get("secret_word","Wrong secret word") if settings.language == "en" else ERROR_MESSAGES_RU.get("secret_word","Секретное слово неверное"), httponly=True)
                return response
    else:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=ERROR_MESSAGES_EN.get("password_mismatch","Passwords mismatch") if settings.language == "en" else ERROR_MESSAGES_RU.get("password_mismatch","Пароли не совпадают"), httponly=True)
        return response


@app.post("/users/auth",  tags=["Communicator", "user authorization"], summary="๊")
async def user_auth(user: Annotated[User, Form()], session: SessionDep):
    userid = await check_user(user.username, user.password, session) 
    if (userid > 0):
        token = create_access_token(data={"username": user.username, "userid": str(userid)})
        # set http-only token in cookie and redirect to messages page
        response = RedirectResponse(url="/messages", status_code=303)
        response.set_cookie(key="access_token", value=token, httponly=True)
        return response
    else:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=ERROR_MESSAGES_EN.get("authorization_error","authorization error") if settings.language == "en" else ERROR_MESSAGES_RU.get("authorization_error","пользователь не авторизован"), httponly=True)
        return response


#   отмечаем сообщение прочитанным
@app.post("/messages/check_read", tags=["Communicator", "messages", "check_read"], summary="mark the message have been read")
async def message_check_read(mess_read: MessId, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""
            INSERT INTO mess_read(mess_id, userid) 
            SELECT A.id, :user_id FROM messages A WHERE A.id=:id AND 
            NOT EXISTS(SELECT B.userid FROM mess_read B WHERE B.mess_id=:mess_id AND B.userid=:userid) 
            """) 
    result = await session.execute(sql, {"user_id": current_user.userid, "id": mess_read.id, "mess_id": mess_read.id, "userid": current_user.userid}) 
    await session.commit()
    sql = text("SELECT R.mess_id, count(*) as cnt FROM mess_read R WHERE R.mess_id=:id GROUP BY R.mess_id")
    result = await session.execute(sql, {"id": mess_read.id})
    dres = result.mappings().all()
    return dres


# ставим сообщению лайк
@app.post("/messages/like", tags=["Communicator", "likes"], summary="get likes for the message")
async def message_like(like: MessId, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    if (like.username != current_user.username) and (like.id > 0):
        sql = text("""
                INSERT INTO mess_likes(mess_id, userid) 
                SELECT A.id, :user_id FROM messages A WHERE A.id=:id AND 
                NOT EXISTS(SELECT B.userid FROM mess_likes B WHERE B.mess_id=:mess_id AND B.userid=:userid) 
               """) 
        result = await session.execute(sql, {"user_id": current_user.userid, "id": like.id, "mess_id": like.id, "userid": current_user.userid}) 
        await session.commit()
        sql = text("SELECT R.mess_id, count(*) as cnt FROM mess_likes R WHERE R.mess_id=:id GROUP BY R.mess_id")
        result = await session.execute(sql, {"id": like.id})
        dres = result.mappings().all()
        return dres
    else:
        return {"result": "User is not authorized"}


@app.get("/users/get_activity", tags=["Communicator", "users", "activity"], summary="Get list of the activ users")
async def get_users_activity(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT u.userid, u.username, u.active, u.fio FROM users u ORDER BY u.username") 
    result = await session.execute(sql) 
    return result.mappings().all()


@app.get("/messages/first_id", tags=["Communicator", "messages", "first id"], summary="the first message id")
async def first_id(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT MIN(m.id) as id FROM messages m") 
    result = await session.execute(sql) 
    row = result.first()
    if row:
        return {"first_id": row.id}   
    else:
        return {"first_id": 1}


@app.post("/upload-attachment/", tags=["Communicator", "files", "upload"], summary="attachment files uploading")
async def upload_file(session: SessionDep, file: UploadFile = File(...), current_user: UserInfo = Depends(get_current_user)):
    result = await save_user_file_to_disk(current_user.username, UPLOAD_DIR, file)
    
    #  Writing file info to the database and linking it to the message   
    if result['error'] == 'OK':
        extension:str = result["ext"]
        extension:str = extension.lower()
        shortFileName:str = result["orig_filename"]
    
        if ((extension == ".png") or (extension == ".jpg") or (extension == ".jpeg")):
            shortFileName = f"<img src='/uploads/{result['unique_filename']}' width='90%' />"
        else:    
            if len(shortFileName) > 30:
                shortFileName = shortFileName[:22] + '... ' + extension
            shortFileName = "&#128206; " + shortFileName + " &#128206;"    
            # mess_text = f"&#128206; <a href='/download-attachment/{unique_filename}'>{origFileName}</a> &#128206;"    
        new_message = MessageOrm(userid=current_user.userid, messtext=shortFileName, checked=0)
        session.add(new_message)    
        await session.flush() 
        sql = text("INSERT INTO attachments(mess_id, filename, origname) VALUES(:mess_id, :filename, :origname)") 
        result = await session.execute(sql, {"mess_id": new_message.id, "filename": result["unique_filename"], "origname": result["orig_filename"]}) 
        await session.commit()  
        logger.success(f"File {file.filename} successfully uploaded")
        return {"filename": file.filename, "status": "saved"}
    else: 
        return result 


# Скачать файл из сообщения (ранее загруженный файл)
@app.get("/download-attachment/{id}", tags=["Communicator", "files", "download"], summary="download the attachment file")
async def download_file(session: SessionDep, id: int, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT B.origname, B.filename FROM attachments B WHERE B.mess_id=:id LIMIT 1")
    result = await session.execute(sql, {"id": id})
    row = result.first() 
    if row:
        file_path = os.path.join(UPLOAD_DIR, row.filename)   
        if not os.path.exists(file_path):
            return {"error": ERROR_MESSAGES_EN.get("file_not_found","File not found") if settings.language == "en" else ERROR_MESSAGES_RU.get("file_not_found","Файл не найден")}
        return FileResponse(
            path=file_path, 
            filename=row.origname,  
            media_type='application/octet-stream'
        )
    else:
        return {"error": ERROR_MESSAGES_EN.get("file_not_found","File not found") if settings.language == "en" else ERROR_MESSAGES_RU.get("file_not_found","Файл не найден")}

@app.get("/messages/get_prev/{id}", tags=["Communicator", "messages history"], summary="Get all the previous messages")
async def prev_messages(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""
        SELECT m.id, u.username, m.messtext, to_char(m.created_at, 'DD.MM.YYYY HH24:MI') as created_at, 
        (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as checked  
        FROM messages m INNER JOIN users u ON m.userid=u.userid 
        WHERE m.id < :mess_id  
        ORDER BY m.id DESC LIMIT :max_mess_count 
    """) 
    result = await session.execute(sql, {"mess_id": id, "max_mess_count": settings.current_messages_max_count}) 
    return result.mappings().all()  


@app.get("/messages/conditions", tags=["Communicator", "mess. conditions"], summary="Get all the reads and likes")
async def conditions(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""
        SELECT m.id, (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as reads, (SELECT count(*) FROM mess_likes L WHERE L.mess_id=m.id) as likes, 
        CASE 
        WHEN NOT EXISTS(SELECT 1 FROM mess_read Z WHERE Z.mess_id=m.id AND Z.userid=:userid) AND (m.userid <> :userid2) AND (m.created_at >= CURRENT_DATE - INTERVAL '1 day') THEN 1 
        ELSE 0 END as unread        
        FROM messages m ORDER BY m.id desc LIMIT :max_mess_count 
    """) 
    result = await session.execute(sql, {"userid": current_user.userid, "userid2": current_user.userid, "max_mess_count": settings.current_messages_max_count}) 
    return result.mappings().all()  


@app.get("/users/get_users", tags=["Communicator", "users"], summary="Get list of the users")
async def get_userslist(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT u.userid, u.username FROM users u ORDER BY u.username") 
    result = await session.execute(sql) 
    return result.mappings().all()  


@app.get("/tasks/get_tasks", tags=["Communicator", "tasks"], summary="Get all active tasks")
async def get_tasks(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""
        SELECT t.id, u1.username as creator, t.title, t.description, to_char(t.created_at, 'DD.MM.YYYY') as created_at, u2.username as respons, 
        to_char(t.deadline, 'DD.MM.YYYY') as deadline, CASE WHEN t.deadline < LOCALTIMESTAMP THEN 1 else 0 END as expired, (
            SELECT COALESCE(json_agg(json_build_object('c_id', c.id, 'username', u.username, 'comment', c.comment, 'created_at', to_char(c.created_at, 'DD.MM.YYYY HH24:MI')) ORDER BY c.id ASC), '[]'::json) 
            FROM comments c INNER JOIN users u ON c.creator = u.userid
            WHERE c.task_id=t.id 
        ) as comments          
        FROM tasks t  
        INNER JOIN users u1 ON t.creator=u1.userid 
        INNER JOIN users u2 ON t.respons=u2.userid   
        WHERE t.completed=0       
        ORDER BY t.created_at
    """) 
    result = await session.execute(sql) 
    return result.mappings().all()  


@app.get("/tasks/gant", tags=["Communicator", "tasks"], summary="Get all active tasks")
async def get_diagram_data(session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""SELECT t.id, DATE(t.created_at) - CURRENT_DATE as startcol, DATE(t.deadline) - CURRENT_DATE as endcol, t.title, to_char(t.deadline, 'DD.MM.YYYY') as deadline, 
                    t.respons, CASE WHEN u.fio='' THEN u.username ELSE u.fio END as executor, CASE WHEN CURRENT_DATE > t.deadline THEN 1 ELSE 0 END as expired
                    FROM tasks t INNER JOIN users u ON t.respons=u.userid 
                    ORDER BY t.created_at""") 
    result = await session.execute(sql) 
    return result.mappings().all()  


@app.post("/tasks/add",  tags=["Communicator", "new task"], summary="Add a new task")
async def add_task(new_task: Tasks, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    logger.info("Adding new task with id: " + str(new_task.id))
    sql = text("SELECT m.messtext FROM messages m WHERE m.id=:id") 
    result = await session.execute(sql, {"id": new_task.id}) 
    row = result.first()
    if row:   
        newTaskOrm = TasksOrm(creator=new_task.creator, respons=new_task.respons, deadline=new_task.deadline, title=new_task.title, description=row.messtext)
        try:
            session.add(newTaskOrm)
            await session.commit()
            logger.success(f"New task id: {new_task.id} successfully added")    
            return {"result": "ok"}
        except IntegrityError as e:
            await session.rollback()
            logger.error("Error occurred while trying to add task: this message is already added to 'Tasks'")
            return {"result": "already exists"}
        except Exception as e:
            await session.rollback()
            logger.error(f"Error occurred while trying to add task: {e}")    
            return {"result": "error"}    
    else:
        return {"result": "Source message was not found"}


@app.delete("/tasks/close/{id}", tags=["Communicator", "tasks", "close"], summary="close the task")
async def close_task(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("DELETE FROM tasks WHERE id=:id") 
    result = await session.execute(sql, {"id": id}) 
    await session.commit()
    return {"result": "ok"}
    

@app.post("/tasks/edit", tags=["Communicator", "tasks", "close"], summary="close the task")
async def edit_task(task: TaskEdit, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    new_text = bleach.clean(task.messtext)
    if (current_user.userid == task.userid) and (len(new_text) > 10):
        sql = text("UPDATE tasks SET description=:messtext WHERE id=:id") 
        result = await session.execute(sql, {"messtext": new_text, "id": task.id}) 
        await session.commit()
        return {"result": "ok"}
    else:
        return {"result": "error", "details": "denied"}


@app.post("/tasks/deadline/edit", tags=["Communicator", "tasks", "close"], summary="close the task")
async def edit_deadline(deadline: DeadlineEdit, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    if (current_user.userid == deadline.userid):
        sql = text("UPDATE tasks SET deadline=:deadline WHERE id=:id") 
        result = await session.execute(sql, {"deadline": deadline.deadline, "id": deadline.id}) 
        await session.commit()
        return {"result": "ok"}
    else:
        return {"result": "error", "details": "denied"}


@app.post("/messages/send_personal",  tags=["Communicator", "personal"], summary="send personal message")
async def add_personal_message(message: Message, current_user: UserInfo = Depends(get_current_user)):
    messText = bleach.clean(message.messtext, tags=['b', 'i'], strip=True)
    if no_have_such_message(message.userid, current_user.username, messText):
        one_personal_message = {"to": message.userid, "from": current_user.username, "messtext": messText, "created_at": datetime.now()}
        personal.append(one_personal_message)
        logger.info("Created new personal message from " + current_user.username)
        return {"result": "ok"}
    else:
        return {"result": "duplicated message"}
    

@app.get("/messages/get_personal", tags=["Communicator", "personal"], summary="get personal message")    
async def get_personal_message(current_user: UserInfo = Depends(get_current_user)):
    return get_personal_messages(current_user.userid)


@app.delete("/messages/delete/{id}", tags=["Communicator", "message", "delete"], summary="delete message")
async def del_message(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT a.filename FROM attachments a WHERE a.mess_id=:mess_id AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.mess_id=a.mess_id) LIMIT 1")
    result = await session.execute(sql, {"mess_id": id})
    row = result.first()   
    # if the file from attachments is not listed in the important documents - delete it from disk
    if row:  
        delete_file_from_disk(row.filename, UPLOAD_DIR)
        # теперь удалим записи в базе данных  -  все дочерние записи должне быть удалены каскадно 
        # благодаря настройкам внешнего ключа, удалим только приаттаченные файлы к сообщению, которые не числятся в важных документах      
        sql = text("DELETE FROM attachments WHERE mess_id=:id") 
        result = await session.execute(sql, {"id": id})         
    sql = text("DELETE FROM tasks WHERE id=:id") 
    result = await session.execute(sql, {"id": id})         
    sql = text("DELETE FROM mess_likes WHERE mess_id=:id") 
    result = await session.execute(sql, {"id": id})         
    sql = text("DELETE FROM mess_read WHERE mess_id=:id") 
    result = await session.execute(sql, {"id": id})         
    sql = text("DELETE FROM messages WHERE id=:id") 
    result = await session.execute(sql, {"id": id}) 
    await session.commit()
    return {"result": "ok"}


@app.delete("/documents/delete/{id}", tags=["Communicator", "documents", "delete"], summary="delete document")
async def del_document(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT a.savedname as filename FROM documents a WHERE a.mess_id=:mess_id AND NOT EXISTS(SELECT 1 FROM attachments b WHERE b.mess_id=a.mess_id) LIMIT 1")
    result = await session.execute(sql, {"mess_id": id})
    row = result.first()   
    # if the file from documents is not listed in the attachments - delete it from disk, because it is not used in messages anymore and is not listed in important documents
    if row:  
        filename = str(row.filename) 
        file_path = os.path.join(UPLOAD_DIR, filename)   
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


@app.get("/documents/get", tags=["Communicator", "documents", "get all"], summary="get all the documents")
async def get_ducuments(session: SessionDep, currnet_user: UserInfo = Depends(get_current_user)):
    result = await session.execute(text('SELECT mess_id, filename, notes FROM documents ORDER BY created_at'))
    return result.mappings().all()


@app.post("/documents/add",  tags=["Communicator", "documents", "add"], summary="Add a new document")
async def add_document(new_doc: Docs, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT A.filename, A.origname FROM attachments A WHERE A.mess_id=:mess_id LIMIT 1")
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
            return {"result": f"Document {row.origname} is already added"}            
        except Exception as e:
            await session.rollback()
            logger.error(f"Error occurred while trying to add document: {e}")    
            return {"result": "error"}


# Скачать файл из хранилища важных документов
@app.get("/documents/download/{id}", tags=["Communicator", "files", "download"], summary="download the attachment file")
async def get_document_file(id: int, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("SELECT A.filename, A.savedname FROM documents A WHERE A.mess_id=:mess_id LIMIT 1")
    result = await session.execute(sql, {"mess_id": id})
    row = result.first() 
    if row:
        return makeFileResponse(row.savedname, row.filename, UPLOAD_DIR)
  


# добавляем описание документа
@app.post("/documents/add_notes", tags=["Communicator", "documents", "add_notes"], summary="add notes to the document")
async def add_doc_description(descr: DocsNotes, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""UPDATE documents SET notes=:notes WHERE mess_id=:mess_id""")
    try:
        await session.execute(sql, {"notes": descr.notes, "mess_id": descr.mess_id}) 
        await session.commit()
        return {"result": "OK"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to add document description: {e}")    
        return {"result": "error"}               


# add / update user's full name
@app.post("/users/fio", tags=["Communicator", "users", "fio"], summary="add first / last names")
async def add_fio(user_fio: UserFio, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    sql = text("""UPDATE users SET fio=:fio WHERE userid=:userid""")
    try:
        await session.execute(sql, {"fio": user_fio.fio, "userid": user_fio.userid}) 
        await session.commit()
        return {"result": "OK"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to update user's full name: {e}")    
        return {"result": "error"}               


# add new comment to the task
@app.post("/comments/add",  tags=["Communicator", "Comments", "new message"], summary="Add a new comment to the task")
async def add_comment(new_comment: Comments, session: SessionDep, current_user: UserInfo = Depends(get_current_user)):
    clean_text = bleach.clean(new_comment.comment, tags=['b', 'i'], strip=True)
    try:
        new_comment = CommentsOrm(task_id=new_comment.task_id, creator=new_comment.creator, comment=clean_text)
        session.add(new_comment)
        await session.commit()
        logger.success(f"User {new_comment.creator} comment successfully added to task {new_comment.task_id}")
        return {"result": "ok"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to add comment: {e}")
        return {"result": "error"}


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(conn: ConnectionAbortedError, exc: HTTPException):
    if exc.status_code == 401:
        logger.info(exc.detail if exc.detail else "Error occurred during user authentication. Redirecting to '/'")
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="flash_msg", value=exc.detail if exc.detail else "Please login first", httponly=True)
        return response 


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    readable_errors = []
    
    for error in exc.errors():
        err_type = error['type']
        if settings.language == "ru":
            template = ERROR_MESSAGES_RU.get(err_type, error['msg']) 
        else:
            template = ERROR_MESSAGES_EN.get(err_type, error['msg']) 

        readable_errors.append(template)

    final_msg = " | ".join(readable_errors)

    accept_header = request.headers.get("accept", "")
    
    if "application/json" in accept_header:
        return JSONResponse(
            status_code=422,
            content={"result": "error", "details": final_msg}
        )
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="flash_msg", value=final_msg, httponly=True)  
    return response


if __name__ == "__main__":    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)