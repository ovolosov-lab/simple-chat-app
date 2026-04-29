from fastapi import HTTPException, Request
import jwt
from datetime import datetime, timedelta
from config import ERROR_MESSAGES_EN, ERROR_MESSAGES_RU, settings
from models import UserInfo



#SECRET_KEY = "927_C,m03856_,shfcgfnm_12spar!001_&90"
#ALGORITHM = "HS256"

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


# 1. Функция-зависимость для проверки токена
async def get_current_user(request: Request):
    # Достаем токен из куки 'access_token'
    token = request.cookies.get("access_token")
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail="You are not authorized"
        )
    
    try:
        # Декодируем и проверяем срок действия (exp)
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        username: str = str(payload.get("username", ""))
        userid: int = int(payload.get("userid", 0)) 
        if (username is None) or (username == "") or (userid == 0):
            raise HTTPException(status_code=401, detail=ERROR_MESSAGES_EN["token_invalid"] if settings.language == "en" else ERROR_MESSAGES_RU["token_invalid"])
        return UserInfo(userid=userid, username=username)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES_EN["token_expired"] if settings.language == "en" else ERROR_MESSAGES_RU["token_expired"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES_EN["authorization_error"] if settings.language == "en" else ERROR_MESSAGES_RU["authorization_error"])
