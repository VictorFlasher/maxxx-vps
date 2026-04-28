"""
Модуль аутентификации: регистрация, вход, извлечение пользователя из токена.

Функции модуля:
- Регистрация новых пользователей с валидацией email
- Аутентификация и выдача JWT-токенов
- Проверка валидности токенов и статуса пользователей
- Вспомогательные функции для WebSocket аутентификации
- Безопасное управление паролями в памяти
- Логирование событий безопасности
"""

import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from ..models.user import create_user, get_user_by_email, get_user_by_id
from ..models.chat import log_connection_event
import bcrypt
import os

# === Rate Limiting (ограничение частоты запросов) ===
# Инициализация SlowAPI для защиты от brute-force атак
limiter = Limiter(key_func=get_remote_address)

# === Конфигурация логирования ===
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# === Конфигурация JWT ===
# Секретный ключ для подписи JWT-токенов (обязательно использовать переменную окружения в production)
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("Переменная окружения SECRET_KEY не установлена. Это критическая уязвимость безопасности!")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))  # Время жизни токена в минутах

# === FastAPI компоненты ===
router = APIRouter()
oauth2_scheme = HTTPBearer()


# === Модели запросов ===

class UserRegister(BaseModel):
    """Данные для регистрации нового пользователя."""
    username: str
    email: str
    password: str
    
    @field_validator('email')
    @classmethod
    def validate_email_format(cls, v):
        """
        Проверяет формат email.
        
        Args:
            v: строка email
            
        Returns:
            email в нижнем регистре
            
        Raises:
            ValueError: если формат email некорректен
        """
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError('Некорректный формат email')
        return v.lower()


class UserLogin(BaseModel):
    """Данные для входа в систему."""
    email: str
    password: str


# === Вспомогательные функции безопасности ===

def secure_hash_password(password: str) -> str:
    """
    Хеширует пароль и безопасно очищает его из памяти.
    
    Использует bytearray для возможности перезаписи чувствительных данных.
    
    Args:
        password: Plain-текст пароля
        
    Returns:
        Хеш пароля
    """
    password_bytes = None
    try:
        # Преобразуем в изменяемый bytearray для безопасной очистки
        password_bytes = bytearray(password.encode('utf-8'))
        # Конвертируем в bytes для bcrypt (требуется bytes, не bytearray)
        password_for_hash = bytes(password_bytes)
        hash_result = bcrypt.hashpw(password_for_hash, bcrypt.gensalt())
        return hash_result.decode('utf-8')
    finally:
        # Очищаем память от пароля
        if password_bytes is not None:
            for i in range(len(password_bytes)):
                password_bytes[i] = 0
            del password_bytes

def secure_verify_password(plain_password: str, stored_hash: str) -> bool:
    """
    Проверяет пароль против хеша и безопасно очищает plain-текст из памяти.
    
    Args:
        plain_password: Plain-текст пароля для проверки
        stored_hash: Сохранённый хеш пароля
        
    Returns:
        True если пароль верный, False иначе
    """
    password_bytes = None
    try:
        password_bytes = bytearray(plain_password.encode('utf-8'))
        # bcrypt требует bytes, конвертируем bytearray в bytes
        is_valid = bcrypt.checkpw(bytes(password_bytes), stored_hash.encode('utf-8'))
        return is_valid
    finally:
        # Очищаем память от пароля
        if password_bytes is not None:
            for i in range(len(password_bytes)):
                password_bytes[i] = 0
            del password_bytes

def create_access_token(data: Dict[str, Any]) -> str:
    """
    Создаёт JWT-токен с заданными данными и временем жизни.

    Args:
        data: полезная нагрузка (например, {"user_id": 123})

    Returns:
        Подписанный JWT-токен
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    data.update({"exp": expire})
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


# === Роутеры ===
@router.post("/register", summary="Регистрация нового пользователя")
@limiter.limit("5/minute")  # Максимум 5 регистраций в минуту с одного IP
async def register(request: Request, user: UserRegister):
    """
    Создаёт нового пользователя. Пароль хешируется безопасно.
    
    Логирует попытки регистрации (успешные и неудачные).
    Защищено от brute-force атак (rate limiting).
    """
    try:
        # Используем безопасное хеширование с очисткой памяти
        create_user(user.username, user.email, user.password, secure_hash=True)
        logger.info(f"Успешная регистрация пользователя: {user.email}")
        return {"message": "Пользователь создан"}
    except ValueError as e:
        logger.warning(f"Неудачная регистрация: {user.email} - {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при регистрации: {user.email} - {str(e)}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.post("/login", summary="Вход в систему")
@limiter.limit("10/minute")  # Максимум 10 попыток входа в минуту с одного IP
def login(request: Request, user: UserLogin):
    """
    Аутентифицирует пользователя и возвращает JWT-токен.
    
    Логирует попытки входа (успешные и неудачные).
    Использует безопасную проверку пароля с очисткой памяти.
    Защищено от brute-force атак (rate limiting).
    Проверяет бан пользователя перед выдачей токена.
    """
    db_user = get_user_by_email(user.email)
    if not db_user:
        logger.warning(f"Попытка входа с несуществующим email: {user.email}")
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    stored_hash = db_user[2]
    
    # Безопасная проверка пароля с очисткой памяти
    if not secure_verify_password(user.password, stored_hash):
        logger.warning(f"Неудачная попытка входа: {user.email}")
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    # Проверяем, не забанен ли пользователь, ДО выдачи токена
    user_id = db_user[0]
    try:
        user_data = get_user_by_id(user_id)
        if user_data["is_banned"]:
            logger.warning(f"Попытка входа забаненного пользователя: {user.email} (ID: {user_id})")
            raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован. Обратитесь к администрации.")
    except ValueError:
        logger.error(f"Ошибка при проверке статуса пользователя: {user.email}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

    token = create_access_token({"sub": db_user[1], "user_id": db_user[0]})
    logger.info(f"Успешный вход пользователя: {user.email} (ID: {db_user[0]})")
    
    # Логируем событие подключения
    try:
        log_connection_event(db_user[0], 'connect')
    except Exception as e:
        logger.error(f"Ошибка логирования события входа: {str(e)}")
    
    return {"access_token": token, "token_type": "bearer"}


def get_current_user_from_header(
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme)
) -> int:
    """
    Извлекает user_id из токена и проверяет:
    - валидность токена (включая срок действия exp)
    - существование пользователя
    - статус бана
    """
    try:
        # jwt.decode автоматически проверяет exp и другие стандартные claims
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Неверный токен")
        user_id = int(user_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Срок действия токена истёк")
    except jwt.JWTClaimsError as e:
        raise HTTPException(status_code=401, detail=f"Ошибка claims токена: {str(e)}")
    except Exception:
        raise HTTPException(status_code=401, detail="Неверный токен")

    # Проверяем, существует ли пользователь и не забанен ли
    try:
        user = get_user_by_id(user_id)
        if user["is_banned"]:
            raise HTTPException(status_code=403, detail="Вы забанены")
    except ValueError:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    return user_id


# === Утилита для WebSocket (не использует заголовки) ===
def get_current_user(token: str) -> int:
    """
    Извлекает user_id из строки токена (для WebSocket).
    Проверяет срок действия токена и другие claims.
    Выбрасывает ValueError при ошибке — обрабатывается вручную.
    """
    try:
        # jwt.decode автоматически проверяет exp и другие стандартные claims
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise ValueError("Нет user_id в токене")
        return int(user_id)
    except jwt.ExpiredSignatureError:
        raise ValueError("Срок действия токена истёк")
    except jwt.JWTClaimsError as e:
        raise ValueError(f"Ошибка claims токена: {str(e)}")
    except Exception as e:
        raise ValueError("Неверный токен") from e