from typing import Annotated

from annotated_types import Gt, MaxLen, MinLen
from fastapi import types
from pydantic import AfterValidator, BaseModel, field_validator
from sqlalchemy.orm import declarative_base
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import date, datetime
from sqlalchemy import DateTime, func

Base = declarative_base()

class UserOrm(Base):
    __tablename__ = "users"
    userid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(20), index=True)
    password: Mapped[str] = mapped_column(String(20), index=True)
    active: Mapped[bool] = mapped_column(default=False)
    fio: Mapped[str] = mapped_column(String(255), nullable=True) 
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now(),
        index=True
    )    

class MessageOrm(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    userid: Mapped[int] = mapped_column(ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    messtext: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now(),
        index=True
    )
    checked: Mapped[int] = mapped_column(Integer, default=0)

class MessReadsOrm(Base):
    __tablename__ = "mess_read"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)   
    mess_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete="CASCADE"), index=True)
    userid: Mapped[int] = mapped_column(ForeignKey('users.userid', ondelete="CASCADE"), index=True) 
    read_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now()
    )

class LikesOrm(Base):
    __tablename__ = "mess_likes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)   
    mess_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete="CASCADE"), index=True)
    userid: Mapped[int] = mapped_column(ForeignKey('users.userid', ondelete="CASCADE"), index=True) 
    
class AttachmentsOrm(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)    
    mess_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete="CASCADE"), index=True) 
    filename: Mapped[str] = mapped_column(String(255), nullable=False)     
    origname: Mapped[str] = mapped_column(String(255), nullable=False)

class TasksOrm(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    respons: Mapped[int] = mapped_column(Integer, ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now()
    )
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=True)
    completed: Mapped[int] = mapped_column(Integer, server_default="0")
    title: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True) 
    description: Mapped[str] = mapped_column(String(4000), nullable=False) 

class CommentsOrm(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)      
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey('tasks.id', ondelete="CASCADE"), index=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    comment: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now()
    )

class DocsOrm(Base):
    __tablename__ = "documents"    
    mess_id: Mapped[int] = mapped_column(Integer, ForeignKey('messages.id', ondelete="CASCADE"), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  
    savedname: Mapped[str] = mapped_column(String(255), nullable=False) 
    notes: Mapped[str] = mapped_column(String(1000), nullable=True) 
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now(),
        index=True
    )


class Message(BaseModel):
    userid: Annotated[int, Gt(0)]
    messtext: Annotated[str, AfterValidator(str.strip), MinLen(1), MaxLen(2000)]

class User(BaseModel):
    username: Annotated[str, AfterValidator(str.strip), MinLen(3), MaxLen(20)]   
    password: Annotated[str, AfterValidator(str.strip), MinLen(6), MaxLen(20)]         

class NewUser(BaseModel):
    username: Annotated[str, AfterValidator(str.strip), MinLen(3), MaxLen(20)]  
    fio: Annotated[str | None, AfterValidator(lambda v: v.strip().title() if v else v), MaxLen(100)] = None   
    secret: str  
    password1: Annotated[str, AfterValidator(str.strip), MinLen(6), MaxLen(20)]     
    password2: Annotated[str, AfterValidator(str.strip), MinLen(6), MaxLen(20)]     
    
class UserInfo(BaseModel):
    userid: int
    username: str

class MessId(BaseModel):
    id: Annotated[int, Gt(0)]
    username: Annotated[str, MinLen(3), MaxLen(20)] 

class Tasks(BaseModel):
    id: Annotated[int, Gt(0)]
    creator: Annotated[int, Gt(0)]
    respons: Annotated[int, Gt(0)]
    deadline: date
    title: Annotated[str, MinLen(3), MaxLen(255)] 

    @field_validator('deadline')
    @classmethod
    def prevent_past_dates(cls, v: date) -> date:
        if v < date.today():
            raise ValueError('date_in_past')
        return v 

class TaskEdit(BaseModel):
    id: Annotated[int, Gt(0)]
    userid: Annotated[int, Gt(0)]
    messtext: Annotated[str, AfterValidator(str.strip), MinLen(11), MaxLen(2000)]

class DeadlineEdit(BaseModel):
    id: Annotated[int, Gt(0)]
    userid: Annotated[int, Gt(0)]
    deadline: date

    @field_validator('deadline')
    @classmethod
    def prevent_past_dates(cls, v: date) -> date:
        if v < date.today():
            raise ValueError('date_in_past')
        return v    

class UserFio(BaseModel):
    userid: Annotated[int, Gt(0)]  
    fio: Annotated[str, AfterValidator(lambda v: v.strip().title()), MinLen(3), MaxLen(100)]

class Docs(BaseModel):
    mess_id: Annotated[int, Gt(0)]

class DocsNotes(Docs):
    notes: Annotated[str, AfterValidator(str.strip), MinLen(3), MaxLen(1000)]     

class Comments(BaseModel):
    task_id: Annotated[int, Gt(0)]
    creator: Annotated[int, Gt(0)]
    comment: Annotated[str, AfterValidator(str.strip), MinLen(1), MaxLen(2000)]        

    


