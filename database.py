import asyncio

from fastapi import Depends
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import Annotated
from crpass import verify_password
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

async def db_connection_check():
    """Database connection check at application startup. If the connection fails, the application will not start."""
    retries = 5
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


async def user_exists(username: str, session: SessionDep):
    sql = text("SELECT userid FROM users WHERE username = :uname LIMIT 1")
    result = await session.execute(sql, {"uname": username}) 
    row = result.first()
    if row:
        return True
    else:
        return False   


async def check_user(username: str, password: str, session: SessionDep):
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
        await conn.run_sync(Base.metadata.create_all)
        logger.success("Database tables were created successfully")        
        