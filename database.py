import asyncio

from fastapi import Depends
from sqlalchemy import URL, Result, TextClause, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import Annotated, Any
from models import Base
from config import settings, logger


DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.db_user,
    password=settings.db_password,
    host=settings.db_host,
    port=int(settings.db_port),
    database=settings.db_name,
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=settings.pool_size, max_overflow=settings.max_overflow)
new_session = async_sessionmaker(engine, expire_on_commit=False)

async def get_session():
    async with new_session() as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]

async def db_connection_check() -> None:
    """Database connection check at application startup. If the connection fails, the application will not start."""
    retries: int = 5
    while retries > 0:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.success("Database connection check successful")
            return
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            retries -= 1
            if retries > 0:
                logger.info(f"Retrying database connection check... ({5 - retries}/5)")
                await asyncio.sleep(5)  # Wait 5 seconds before the next attempt

    raise RuntimeError("Failed to connect to the database. Application startup aborted.")


async def user_exists(username: str, session: SessionDep) -> bool:
    sql = text("SELECT userid FROM users WHERE username = :uname LIMIT 1")
    result = await session.execute(sql, {"uname": username}) 
    row = result.first()
    if row:
        return True
    else:
        return False   


async def check_user(username: str, password: str, session: SessionDep) -> int:
    sql = text("SELECT userid, password FROM users WHERE username = :uname LIMIT 1")
    result = await session.execute(sql, {"uname": username}) 
    row = result.first()
    if row:
        #if verify_password(password, row.password):
        if password == row.password:
            return row.userid
        else:
            return 0
    else:
        return 0
    

async def create_all_tables():
    """ DB: Create all tables if they do not exist yet. This function should be called at the start of the application. """
    async with engine.begin() as conn:
        if settings.in_development and settings.force_recreate_db:
            await conn.run_sync(Base.metadata.drop_all)
            logger.success("All previous database tables have been dropped.")
        
        await conn.run_sync(Base.metadata.create_all)
        logger.success("Database tables were created successfully")        
        


async def db_add_record(session: AsyncSession, model_instance: Base, log_label: str = "Record") -> dict:
    try:
        session.add(model_instance)
        await session.commit()
        logger.success(f"{log_label} successfully added")
        return {"result": "ok"}
    except Exception as e:
        await session.rollback()
        logger.error(f"Error occurred while trying to add {log_label.lower()}: {e}")
        return {"result": "error"}
    

async def get_massages_from_db(id: int, session: AsyncSession, history: bool = False):   
    result: Result[Any]
    if history == 1:
        sql: TextClause = text("""
            SELECT m.id, u.username, m.messtext, to_char(m.created_at, 'DD.MM.YYYY HH24:MI') as created_at, 
            (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as checked, u.avatar  
            FROM messages m INNER JOIN users u ON m.userid=u.userid 
            WHERE m.id < :mess_id  
            ORDER BY m.id DESC LIMIT :max_mess_count 
        """) 
        result = await session.execute(sql, {"mess_id": id, "max_mess_count": settings.current_messages_max_count}) 
    elif id <= 0:
        sql: TextClause = text("""
           SELECT t.id, t.username, t.userid, t.messtext, t.created_at, t.checked, t.likes, t.task, t.avatar 
           FROM (
             SELECT m.id, u.userid, u.username, u.avatar, m.messtext, to_char(m.created_at, 'DD.MM.YYYY HH24:MI') as created_at, 
               (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as checked, 
               (SELECT count(*) FROM mess_likes R WHERE R.mess_id=m.id) as likes, 0 as task          
             FROM messages m INNER JOIN users u ON m.userid=u.userid 
             ORDER BY m.id DESC LIMIT :max_mess_count 
           ) t ORDER BY t.id
        """) 
        result = await session.execute(sql, {"max_mess_count": settings.current_messages_max_count}) 
    else: 
        sql: TextClause = text(""" 
            SELECT m.id, u.userid, u.username, u.avatar, m.messtext, to_char(m.created_at, 'DD.MM.YYYY HH24:MI') as created_at, 
            (SELECT count(*) FROM mess_read R WHERE R.mess_id=m.id) as checked, 
            (SELECT count(*) FROM mess_likes R WHERE R.mess_id=m.id) as likes, 0 as task                                  
            FROM messages m INNER JOIN users u ON m.userid=u.userid 
            WHERE m.id > :mess_id       
            ORDER BY m.id
        """)    
        result = await session.execute(sql, {"mess_id": id}) 
    return result.mappings().all()