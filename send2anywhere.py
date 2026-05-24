
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

import bleach
import httpx
from config import Settings, logger

from models import EmailEnvelope, Envelope, SmsEnvelope


class Notifycation(ABC):
    @abstractmethod
    async def send(self, messtext: str) -> dict:
        pass

class EmailNotification(Notifycation):
    def __init__(self, envelope: EmailEnvelope):
        self.envelope = envelope

    async def send(self, messtext: str) -> dict:
        if Settings.email_notifications_url != "":
            requestParams = {"login": Settings.email_notifications_login, "password": Settings.email_notifications_password, "address": self.envelope.address, "subject": self.envelope.subject, "messtext": messtext}
            await http_request(Settings.email_notifications_url, requestParams)
            logger.success(f"Отправлено Email сообщение на адрес ", self.envelope.address)  
            return {"result": "ok"}
        else:
            return {"result": "error", "details": "configure connection parameters with the email service"}         


class SmsNotification(Notifycation):
    def __init__(self, envelope: SmsEnvelope):
        self.recipient = envelope

    async def send(self, messtext: str) -> dict:
        if Settings.sms_notifications_url != "":
            requestParams = {"login": Settings.sms_notifications_login, "password": Settings.sms_notifications_password, "phone": self.recipient.phone, "sender": self.recipient.sender, "messtext": messtext}
            await http_request(Settings.sms_notifications_url, requestParams)
            logger.success(f"Отправлено Смс сообщение на адрес ", self.recipient.phone)  
            return {"result": "ok"}
        else:
            return {"result": "error", "details": "configure connection parameters with the sms service"}   


class Notifier_factory:
    @staticmethod
    def create(messType: str, envelope: Envelope):
        if messType == "sms" and isinstance(envelope, SmsEnvelope):
            return SmsNotification(envelope)
        elif messType == "email" and isinstance(envelope, EmailEnvelope):
            return EmailNotification(envelope)
        else:
            logger.error(f"Неизвестный тип сообщения: {messType}") 
            raise ValueError(f"Неизвестный тип сообщения: {messType}") 


async def http_request(url: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=json_data,  
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.error(f"Ошибка запроса: {exc}")
            raise
