import enum
import re
from typing import Annotated, Literal

from annotated_types import Gt
import bleach
from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field, field_validator
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import date, datetime
from sqlalchemy import DateTime, func
from config import settings


class Base(DeclarativeBase):
    pass

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
    avatar: Mapped[str] = mapped_column(String(10), nullable=True, default='&#129489')  
    email: Mapped[str] = mapped_column(String(100), nullable=True, default='')  
    ai_role: Mapped[str] = mapped_column(String(2000), nullable=True, default=settings.ai_role)

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

class TaskState(str, enum.Enum):
    planned = "planned"
    started = "started"
    closed = "closed"

class TasksOrm(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    respons: Mapped[int] = mapped_column(Integer, ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now()
    )
    start_date:  Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now(), index=True
    )
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True) 
    description: Mapped[str] = mapped_column(String(4000), nullable=False) 
    status: Mapped[TaskState] = mapped_column(Enum(TaskState, name="task_states"), default=TaskState.started, index=True)

class TaskAttachmentsOrm(Base):
    __tablename__ = "task_attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id', ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)     
    origname: Mapped[str] = mapped_column(String(255), nullable=False)    
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=False), 
        server_default=func.now()
    )

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

class AI_Sessions(Base):    
    __tablename__ = "ai_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    userid: Mapped[int] = mapped_column(ForeignKey('users.userid', ondelete="CASCADE"), index=True)
    messtext: Mapped[str] = mapped_column(String(6000), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default='user')

# --------------------------- Pydantic classes -----------------------

clean_before = BeforeValidator(lambda v: bleach.clean(str(v or ''), strip=True).strip())
clean_before_bi = BeforeValidator(lambda v: bleach.clean(str(v or ''), tags=['b', 'i'], strip=True).strip())

def validate_not_past(v: date) -> date:
    if v < date.today():
        raise ValueError('date_in_past')
    return v

FutureDate = Annotated[date, AfterValidator(validate_not_past)]


class Message(BaseModel):
    userid: Annotated[int, Gt(0)]
    messtext: Annotated[str, clean_before_bi, Field(min_length=1, max_length=2000)]

class User(BaseModel):
    username: Annotated[str, clean_before, Field(min_length=3, max_length=20)]   
    password: Annotated[str, AfterValidator(str.strip), Field(min_length=6, max_length=20)]         

class NewUser(BaseModel):
    username: Annotated[str, clean_before, Field(min_length=3, max_length=20)]  
    fio: Annotated[str | None, clean_before, AfterValidator(lambda v: v.title()), Field(max_length=100)] = None   
    secret: Annotated[str, AfterValidator(str.strip), Field(min_length=2, max_length=100)]   
    password1: Annotated[str, AfterValidator(str.strip), Field(min_length=6, max_length=20)]     
    password2: Annotated[str, AfterValidator(str.strip), Field(min_length=6, max_length=20)]     
    
class UserInfo(BaseModel):
    userid: Annotated[int, Gt(0)]
    username: Annotated[str, clean_before, Field(min_length=2, max_length=20)] 

class MessId(BaseModel):
    id: Annotated[int, Gt(0)]
    username: Annotated[str, clean_before, Field(min_length=2, max_length=20)] 

class Tasks(BaseModel):
    id: Annotated[int, Gt(0)]
    creator: Annotated[int, Gt(0)]
    respons: Annotated[int, Gt(0)]
    start_date: FutureDate
    deadline: FutureDate
    title: Annotated[str, clean_before, Field(min_length=3, max_length=255)] 
    status: TaskState

class TaskEdit(BaseModel):
    id: Annotated[int, Gt(0)]
    userid: Annotated[int, Gt(0)]
    messtext: Annotated[str, clean_before_bi, Field(min_length=11, max_length=2000)] 

class DeadlineEdit(BaseModel):
    id: Annotated[int, Gt(0)]
    userid: Annotated[int, Gt(0)]
    deadline: FutureDate

class UserProps(BaseModel):
    userid: Annotated[int, Gt(0)]  
    fio: Annotated[str, clean_before, Field(min_length=3, max_length=100)] 
    avatar: Annotated[str, AfterValidator(str.strip), Field(pattern=r"^[0-9&#;axAF-f]+$", min_length=3, max_length=10)]   
    email: Annotated[str, EmailStr] 
    ai_role: Annotated[str, Field(max_length=2000)]

class Docs(BaseModel):
    mess_id: Annotated[int, Gt(0)]

class DocsNotes(Docs):
    notes: Annotated[str, clean_before, Field(min_length=3, max_length=1000)]    

class Comments(BaseModel):
    task_id: Annotated[int, Gt(0)]
    creator: Annotated[int, Gt(0)]
    comment: Annotated[str, clean_before_bi, Field(min_length=3, max_length=2000)]         


# Базовый класс для контактных данных получателя и отправителя
class Envelope(BaseModel):
    pass

# Специализированный тип для Email с авто-валидацией
class EmailEnvelope(Envelope):
    address: EmailStr  
    subject: Annotated[str, clean_before, Field(min_length=1, max_length=200)]  

# Специализированный тип для SMS с кастомной валидацией телефона
class SmsEnvelope(Envelope):
    phone: str
    sender: Annotated[str, clean_before, Field(max_length=11)] 

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        clean_phone = re.sub(r'\D', '', v)
        if not (10 <= len(clean_phone) <= 15):
            raise ValueError("Not a phone number!")
        return f"+{clean_phone}"
    
class NotifyInfo(BaseModel):
    messType: Literal['email', 'sms']
    envelope: EmailEnvelope | SmsEnvelope 
    messtext: Annotated[str, clean_before_bi, Field(min_length=3, max_length=4000)] 


