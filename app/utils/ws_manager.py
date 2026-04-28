"""
Модуль для управления состояниями WebSocket и кэшированием в памяти.

Этот модуль предоставляет:
- Локальное хранение активных WebSocket-соединений
- Кэширование часто запрашиваемых данных в памяти
- Механизм rate-limiting для WebSocket подключений
- Отслеживание онлайн-статусов пользователей

Используется только встроенная память Python с асинхронными замками для потокобезопасности.
"""

import json
import logging
import asyncio
from typing import Optional, Dict, Any, List, Set
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

# === Глобальные хранилища в памяти ===

# Хранилище WebSocket соединений: {chat_id: {user_id: instance_id}}
ws_connections: Dict[int, Dict[int, str]] = defaultdict(dict)
ws_lock = asyncio.Lock()

# Хранилище онлайн пользователей: {user_id: set of chat_ids}
online_users: Dict[int, Set[int]] = defaultdict(set)
online_lock = asyncio.Lock()

# Rate limiting: {user_id: {"count": int, "reset_at": datetime}}
rate_limits: Dict[int, Dict[str, Any]] = {}
rate_limit_lock = asyncio.Lock()

# Кэш данных: {key: {"value": Any, "expires_at": datetime}}
cache: Dict[str, Dict[str, Any]] = {}
cache_lock = asyncio.Lock()

# Уникальный ID экземпляра приложения
INSTANCE_ID = f"instance-{id(asyncio.get_event_loop())}"


async def init_ws_manager():
    """Инициализация менеджера WebSocket."""
    logger.info("Менеджер WebSocket инициализирован (локальное хранилище)")


async def close_ws_manager():
    """Очистка ресурсов менеджера WebSocket."""
    logger.info("Менеджер WebSocket остановлен")


# === Управление WebSocket соединениями ===

async def add_connection(chat_id: int, user_id: int, instance_id: str) -> bool:
    """
    Добавляет WebSocket соединение в локальное хранилище.
    
    Args:
        chat_id: ID чата
        user_id: ID пользователя
        instance_id: Уникальный ID экземпляра приложения
        
    Returns:
        True если успешно
    """
    async with ws_lock:
        ws_connections[chat_id][user_id] = instance_id
    return True


async def remove_connection(chat_id: int, user_id: int) -> bool:
    """
    Удаляет WebSocket соединение из локального хранилища.
    
    Args:
        chat_id: ID чата
        user_id: ID пользователя
        
    Returns:
        True если успешно
    """
    async with ws_lock:
        if chat_id in ws_connections and user_id in ws_connections[chat_id]:
            del ws_connections[chat_id][user_id]
            if not ws_connections[chat_id]:
                del ws_connections[chat_id]
    return True


async def get_chat_connections(chat_id: int) -> Dict[str, str]:
    """
    Получает все соединения для чата.
    
    Args:
        chat_id: ID чата
        
    Returns:
        Dict {user_id: instance_id}
    """
    async with ws_lock:
        connections = ws_connections.get(chat_id, {})
        return {str(k): v for k, v in connections.items()}


# === Управление статусами пользователей ===

async def add_user_online(user_id: int, chat_id: int) -> bool:
    """
    Добавляет пользователя в список онлайн в чате.
    
    Args:
        user_id: ID пользователя
        chat_id: ID чата
        
    Returns:
        True если успешно
    """
    async with online_lock:
        online_users[user_id].add(chat_id)
    return True


async def remove_user_online(user_id: int, chat_id: int) -> bool:
    """
    Удаляет пользователя из списка онлайн в чате.
    
    Args:
        user_id: ID пользователя
        chat_id: ID чата
        
    Returns:
        True если успешно
    """
    async with online_lock:
        if user_id in online_users:
            online_users[user_id].discard(chat_id)
            if not online_users[user_id]:
                del online_users[user_id]
    return True


async def get_user_online_chats(user_id: int) -> Set[int]:
    """
    Получает список чатов, где пользователь онлайн.
    
    Args:
        user_id: ID пользователя
        
    Returns:
        Set of chat_ids
    """
    async with online_lock:
        return online_users.get(user_id, set()).copy()


async def is_user_online(user_id: int) -> bool:
    """
    Проверяет, онлайн ли пользователь (есть ли активные чаты).
    
    Args:
        user_id: ID пользователя
        
    Returns:
        True если онлайн
    """
    chats = await get_user_online_chats(user_id)
    return len(chats) > 0


# === Rate Limiting для WebSocket ===

async def check_ws_rate_limit(user_id: int, max_connections: int = 5) -> bool:
    """
    Проверяет лимит подключений для пользователя.
    
    Args:
        user_id: ID пользователя
        max_connections: Максимум одновременных подключений
        
    Returns:
        True если можно подключиться
    """
    now = datetime.now(timezone.utc)
    async with rate_limit_lock:
        if user_id not in rate_limits:
            rate_limits[user_id] = {"count": 1, "reset_at": now}
            return True
        
        limit_info = rate_limits[user_id]
        
        # Сброс счётчика если прошла минута
        if now >= limit_info["reset_at"]:
            rate_limits[user_id] = {"count": 1, "reset_at": now}
            return True
        
        if limit_info["count"] >= max_connections:
            return False
        
        rate_limits[user_id]["count"] += 1
        return True


async def increment_ws_limit(user_id: int) -> bool:
    """Увеличивает счётчик подключений пользователя."""
    now = datetime.now(timezone.utc)
    async with rate_limit_lock:
        if user_id not in rate_limits:
            rate_limits[user_id] = {"count": 1, "reset_at": now}
        else:
            rate_limits[user_id]["count"] += 1
            rate_limits[user_id]["reset_at"] = now
    return True


async def decrement_ws_limit(user_id: int) -> bool:
    """Уменьшает счётчик подключений пользователя."""
    async with rate_limit_lock:
        if user_id in rate_limits and rate_limits[user_id]["count"] > 0:
            rate_limits[user_id]["count"] -= 1
    return True


# === Кэширование данных ===

async def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """
    Сохраняет значение в локальный кэш.
    
    Args:
        key: Ключ кэша
        value: Значение
        ttl: Время жизни в секундах
        
    Returns:
        True если успешно
    """
    expires_at = datetime.now(timezone.utc).timestamp() + ttl
    async with cache_lock:
        cache[key] = {"value": value, "expires_at": expires_at}
    return True


async def cache_get(key: str) -> Optional[Any]:
    """
    Получает значение из локального кэша.
    
    Args:
        key: Ключ кэша
        
    Returns:
        Значение или None если не найдено или истёк срок
    """
    async with cache_lock:
        if key not in cache:
            return None
        
        entry = cache[key]
        if datetime.now(timezone.utc).timestamp() >= entry["expires_at"]:
            del cache[key]
            return None
        
        return entry["value"]


async def cache_delete(key: str) -> bool:
    """Удаляет значение из кэша."""
    async with cache_lock:
        if key in cache:
            del cache[key]
    return True


def get_instance_id() -> str:
    """Возвращает уникальный ID текущего экземпляра приложения."""
    return INSTANCE_ID
