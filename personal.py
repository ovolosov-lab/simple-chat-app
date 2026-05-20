from dataclasses import dataclass, field
from datetime import datetime
from typing import List
from config import logger


@dataclass
class PersonalMessage:
    to: int
    sender: str
    messtext: str
    created_at: datetime = field(default_factory=datetime.now)


class PersonalMessages:
    def __init__(self):
        self._messages: List[PersonalMessage] = []

    def add_message(self, to: int, sender: str, messtext: str) -> None:
        """Создает объект PersonalMessage и добавляет его в список."""
        new_message = PersonalMessage(to=to, sender=sender, messtext=messtext)
        self._messages.append(new_message)
        logger.success(f"Добавлено сообщение пользователю userid={to} от {sender}")

    def get_all(self) -> List[PersonalMessage]:
        """Возвращает весь список объектов."""
        return self._messages

    def get(self, to: int) -> List[PersonalMessage]:
        """Возвратить сообщения по адресату"""
        return [mess for mess in self._messages if mess.to == to]
    
    def pop(self, to: int) -> List[PersonalMessage]:
        """Вернуть сообщения по адресату и удалить найденные"""
        result: List[PersonalMessage] = []
        remaining_messages: List[PersonalMessage] = []

        for mess in self._messages:
            if mess.to == to:
                result.append(mess)
            else:
                remaining_messages.append(mess)
                
        self._messages = remaining_messages  
        return result                
    
    def no_have_such_message(self, to: int, sender: str, messtext: str) -> bool: 
        no_found: bool = True   
        for mess in self._messages:
            if (mess.to == to) and (mess.sender == sender):
                if ((datetime.now() - mess.created_at).total_seconds() < 4) or (mess.messtext == messtext):
                    no_found = False
                    break 
        return no_found   
    
    def clear_expired(self, max_age_hours: int = 24) -> int:
        """
        Удаляет сообщения старше заданного времени (по умолчанию - сутки).
        Возвращает количество удаленных сообщений.
        """
        now = datetime.now()
        remaining_messages: List[PersonalMessage] = []
        deleted_count = 0

        for mess in self._messages:
            age_hours = (now - mess.created_at).total_seconds() // 3600  # в часы
            
            if age_hours <= max_age_hours:
                remaining_messages.append(mess)
            else:
                deleted_count += 1

        if deleted_count > 0:
            self._messages = remaining_messages
            logger.info(f"Очистка: удалено {deleted_count} устаревших сообщений.")
            
        return deleted_count