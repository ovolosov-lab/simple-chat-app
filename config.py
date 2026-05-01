from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from loguru import logger


ERROR_MESSAGES_RU = {
    "missing": "Поле обязательно для заполнения",
    "value_error.email": "Некорректный адрес электронной почты",
    "string_too_short": "Минимум символов: {min_length}",
    "string_too_long": "Максимум символов: {max_length}",
    "less_than_equal": "Значение должно быть <= {le}",
    "greater_than_equal": "Значение должно быть >= {ge}",
    "value_error": "Некорректное значение",
    "type_error": "Некорректный тип",
    "token_invalid": "Недействительный токен",
    "token_expired": "Срок действия токена истек",
    "authorization_error": "Ошибка авторизации",
    "file_too_big": "Размер файла превышает допустимый лимит",
    "file_format_invalid": "Недопустимый формат файла",
    "file_name_missing": "Имя файла отсутствует",
    "file_save_error": "Произошла ошибка при сохранении файла на сервере",
    "file_delete_error": "Произошла ошибка при удалении файла на сервере",
    "file_not_found": "Файл не найден",
    "comment_add_error": "Произошла ошибка при попытке добавить комментарий",
    "username_taken": "Имя пользователя уже занято",
    "secret_word": "Секретное слово неверно",
    "password_mismatch": "Пароли не совпадают",
    "user_created": "Пользователь успешно создан",
    "user_deleted": "Пользователь успешно удален",
    "user_not_found": "Пользователь не найден",
    "message_sent": "Сообщение успешно отправлено",
    "message_deleted": "Сообщение успешно удалено",
    "message_not_found": "Сообщение не найдено",
    "task_not_found": "Задача не найдена",
    "task_created": "Задача успешно создана",
    "task_deleted": "Задача успешно удалена",
    "comment_added": "Комментарий успешно добавлен",
    "comment_deleted": "Комментарий успешно удален",
    "comment_not_found": "Комментарий не найден",
    "user_info_updated": "Информация о пользователе успешно обновлена",
    "user_info_update_error": "Произошла ошибка при попытке обновить информацию о пользователе",
    "You are not authorized": "Вы не авторизованы", 
    "date_in_past": "Дата не может быть в прошлом"
}

ERROR_MESSAGES_EN = {
    "missing": "Field is required",
    "value_error.email": "Invalid email address",
    "string_too_short": "Minimum characters: {min_length}",
    "string_too_long": "Maximum characters: {max_length}",
    "less_than_equal": "Value must be <= {le}",
    "greater_than_equal": "Value must be >= {ge}",
    "value_error": "Invalid value",
    "type_error": "Invalid type",
    "token_invalid": "Token is invalid",
    "token_expired": "Token has expired",
    "authorization_error": "Authorization error",
    "file_too_big": "File size exceeds the limit",
    "file_format_invalid": "Invalid file format",
    "file_name_missing": "FileName is missing",
    "file_save_error": "Error occurred while saving the file on the server",
    "file_delete_error": "Error occurred while deleting the file on the server",
    "file_not_found": "File not found",
    "comment_add_error": "Error occurred while trying to add comment",
    "username_taken": "Username is already taken",
    "secret_word": "Secret word is incorrect",
    "password_mismatch": "Passwords do not match",
    "user_created": "User successfully created",
    "user_deleted": "User successfully deleted",
    "user_not_found": "User not found",
    "message_sent": "Message successfully sent",
    "message_deleted": "Message successfully deleted",
    "message_not_found": "Message not found",
    "task_not_found": "Task not found",
    "task_created": "Task successfully created",
    "task_deleted": "Task successfully deleted",
    "comment_added": "Comment successfully added",
    "comment_deleted": "Comment successfully deleted",
    "comment_not_found": "Comment not found",
    "user_info_updated": "User info successfully updated",
    "user_info_update_error": "Error occurred while trying to update user info",
    "You are not authorized": "You are not authorized",
    "date_in_past": "Date cannot be in the past"
}   


class Settings(BaseSettings):
    # Описываем параметры и их типы
    db_user: str = Field(...)
    db_password: str = Field(...)
    db_host: str = Field(...)
    db_port: str = Field(...)
    db_name: str = Field(...)
    jwt_secret: str = Field(...)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 240  
    debug: bool = False
    friend_reference: str = Field(...)
    current_messages_max_count: int = 100
    pool_size: int = 5
    max_overflow: int = 5
    log_file: str = "logs/communicator.log"
    log_rotation: str = "12 MB" 
    log_retention: str = "3 days"
    users_activity_check_interval: int = 350
    max_upload_file_size: int = 20 # 20 MB
    allowed_extensions: list = ["jpg", "jpeg", "png", "pdf", "doc", "docx", "xls", "xlsx", "txt", "zip", "z7", "rar"]
    client_messages_check_interval: int = 2000
    client_users_check_interval: int = 2000
    largest_number_of_messages: int = 10000
    largest_number_of_users: int = 10000
    language: str = "en" 
   


    # Указываем, откуда брать данные
    model_config = SettingsConfigDict(env_file=".env")

@lru_cache()
def get_settings():
    """Используем lru_cache, чтобы настройки считывались из файла только один раз"""
    return Settings() # type: ignore

settings = get_settings()


logger.add(settings.log_file, rotation=settings.log_rotation, retention=settings.log_retention, compression="zip", enqueue=True)