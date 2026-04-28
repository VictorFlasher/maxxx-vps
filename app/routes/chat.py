"""
Маршруты чата: WebSocket в реальном времени, создание чатов, история, загрузка файлов.

Этот модуль обрабатывает:
- WebSocket соединения для обмена сообщениями в реальном времени
- HTTP endpoints для управления чатами (создание, удаление, приглашение)
- Загрузку файлов в чаты
- Отслеживание статуса пользователей "в сети"
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    HTTPException,
    Query,
    UploadFile,
    File,
    BackgroundTasks,
)
from pydantic import BaseModel

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

# Проверка типа файла по magic numbers (кроссплатформенная версия)
# Используем простую реализацию для Windows без зависимости от libmagic
def get_file_type(file_bytes: bytes) -> str:
    """
    Определяет тип файла по первым байтам (magic numbers).
    
    Args:
        file_bytes: Первые байты файла (минимум 8-14 байт)
    
    Returns:
        Строка с MIME-типом или 'application/octet-stream' если не определено
    """
    if len(file_bytes) < 4:
        return 'application/octet-stream'
    
    # Проверка по сигнатурам файлов
    if file_bytes[:4] == b'\x89PNG':
        return 'image/png'
    elif file_bytes[:2] == b'\xFF\xD8':
        return 'image/jpeg'
    elif file_bytes[:6] in [b'GIF87a', b'GIF89a']:
        return 'image/gif'
    elif file_bytes[:8] == b'\x89HDF\r\n\x1a\n':
        return 'application/x-hdf'
    elif file_bytes[:2] == b'MZ':
        return 'application/x-msdownload'
    elif file_bytes[:4] == b'%PDF':
        return 'application/pdf'
    elif file_bytes[:5] == b'%!PS-':
        return 'application/postscript'
    elif file_bytes[:6] == b'\xD0\xCF\x11\xE0\xA1\xB1':
        return 'application/msword'
    elif file_bytes[:6] == b'PK\x03\x04':
        return 'application/zip'
    elif file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WAVE':
        return 'audio/wav'
    elif file_bytes[:4] == b'ID3' or file_bytes[:2] == b'\xFF\xFB':
        return 'audio/mpeg'
    elif file_bytes[:4] == b'fLaC':
        return 'audio/flac'
    elif file_bytes[:8] == b'ftypisom' or file_bytes[:8] == b'ftypmp42':
        return 'video/mp4'
    elif file_bytes[:8] == b'ftypM4V ':
        return 'video/x-m4v'
    elif file_bytes[:4] == b'\x00\x00\x00\x18':
        return 'video/mp4'
    elif file_bytes[:4] == b'ftyp':
        return 'video/quicktime'
    elif file_bytes[:2] == b'\x1F\x8B':
        return 'application/gzip'
    elif file_bytes[:3] == b'\xEF\xBB\xBF':
        return 'text/plain'
    elif file_bytes[:2] == b'\xFE\xFF':
        return 'text/plain'
    elif file_bytes[:2] == b'\xFF\xFE':
        return 'text/plain'
    
    # Проверка на текстовые файлы (простая эвристика)
    try:
        text = file_bytes[:1024].decode('utf-8')
        if all(c.isprintable() or c in '\n\r\t' for c in text):
            return 'text/plain'
    except:
        pass
    
    return 'application/octet-stream'

from ..database import get_db_connection, release_db_connection
from .auth import get_current_user, get_current_user_from_header
from ..models.chat import (
    create_private_chat,
    create_group_chat,
    is_user_in_chat,
    get_chat_history,
    add_user_to_group_chat,
    remove_user_from_group_chat,
    get_user_chats,
    delete_private_chat,
    get_chat_type,
)
from ..models.user import get_username, get_all_users, search_users
from ..utils.ws_manager import (
    add_connection,
    remove_connection,
    add_user_online,
    remove_user_online,
    get_user_online_chats,
    is_user_online,
    check_ws_rate_limit,
    increment_ws_limit,
    decrement_ws_limit,
    cache_set,
    cache_get,
    get_instance_id,
)

# === Конфигурация ===
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Разрешённые расширения файлов для загрузки
ALLOWED_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".txt", ".pdf"}
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", 2 * 1024 * 1024))  # 2 МБ по умолчанию

# Соответствие MIME-типов и расширений для валидации по magic numbers
ALLOWED_MIME_TYPES = {
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png": [".png"],
    "image/gif": [".gif"],
    "text/plain": [".txt"],
    "application/pdf": [".pdf"],
}

# === Глобальное хранилище WebSocket-соединений (локальное хранилище) ===
# Структура: {chat_id: {user_id: websocket}}
active_connections: Dict[int, Dict[int, WebSocket]] = {}

# === Глобальное хранилище статусов пользователей "в сети" ===
# Структура: {user_id: set of chat_ids where user is online}
online_users: Dict[int, Set[int]] = {}

# === Уникальный ID экземпляра приложения ===
INSTANCE_ID = os.getenv("INSTANCE_ID", f"instance-{os.getpid()}")

router = APIRouter()


# === Модели запросов/ответов ===

class CreatePrivateChatRequest(BaseModel):
    """Запрос на создание личного чата между двумя пользователями."""
    user1_id: int
    user2_id: int


class CreateGroupChatRequest(BaseModel):
    """Запрос на создание группового чата."""
    name: str


class InviteUserRequest(BaseModel):
    """Запрос на приглашение пользователя в групповой чат по email или username."""
    user_email_or_username: str

# === Вспомогательные функции ===
def _get_chat_members(chat_id: int) -> List[int]:
    """
    Возвращает список ID всех участников чата (личного или группового).

    Args:
        chat_id: ID чата

    Returns:
        Список ID участников. Пустой список если чат не найден.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT type, user1_id, user2_id FROM chats WHERE id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
            # Чат не найден - возвращаем пустой список (неявное поведение)
            return []

        chat_type, user1, user2 = row

        if chat_type == 'private':
            return [user1, user2]
        elif chat_type == 'group':
            cur.execute("SELECT user_id FROM chat_members WHERE chat_id = %s", (chat_id,))
            return [r[0] for r in cur.fetchall()]
        else:
            return []
    finally:
        cur.close()
        release_db_connection(conn)


def _get_online_users_in_chat(chat_id: int) -> List[int]:
    """
    Возвращает список ID пользователей, которые сейчас онлайн в данном чате.

    Args:
        chat_id: ID чата

    Returns:
        Список ID онлайн-пользователей
    """
    members = _get_chat_members(chat_id)
    return [user_id for user_id in members if user_id in online_users and len(online_users[user_id]) > 0]


async def _notify_users(chat_id: int, message: dict) -> None:
    """Рассылает сообщение всем активным участникам чата."""
    from fastapi.websockets import WebSocketState
    
    members = _get_chat_members(chat_id)
    for user_id in members:
        ws = active_connections.get(chat_id, {}).get(user_id)
        if ws and ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.send_json(message)
            except Exception:
                # Соединение закрылось во время отправки
                pass


async def _broadcast_status_to_all_chats(user_id: int, status: str) -> None:
    """
    Рассылает уведомление об изменении статуса пользователя во все его чаты.

    Args:
        user_id: ID пользователя
        status: "online" или "offline"
    """
    from fastapi.websockets import WebSocketState
    
    # Находим все чаты, где состоит пользователь
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Личные чаты
        cur.execute("""
            SELECT id FROM chats
            WHERE type = 'private' AND (user1_id = %s OR user2_id = %s)
        """, (user_id, user_id))
        private_chats = [row[0] for row in cur.fetchall()]
        
        # Групповые чаты
        cur.execute("""
            SELECT c.id FROM chats c
            JOIN chat_members cm ON c.id = cm.chat_id
            WHERE c.type = 'group' AND cm.user_id = %s
        """, (user_id,))
        group_chats = [row[0] for row in cur.fetchall()]
        
        all_chats = private_chats + group_chats
    finally:
        cur.close()
        release_db_connection(conn)
    
    # Отправляем уведомление во все чаты с проверкой состояния WebSocket
    for chat_id in all_chats:
        members = _get_chat_members(chat_id)
        for member_id in members:
            ws = active_connections.get(chat_id, {}).get(member_id)
            if ws and ws.client_state == WebSocketState.CONNECTED:
                try:
                    await ws.send_json({
                        "type": "status",
                        "user_id": user_id,
                        "status": status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    # Соединение закрылось во время отправки
                    pass


async def _notify_file_upload(chat_id: int, user_id: int, file_url: str, file_type: str) -> None:
    """
    Фоновая задача: сохраняет файл в БД и уведомляет участников.

    Args:
        chat_id: ID чата
        user_id: ID отправителя
        file_url: URL файла
        file_type: Тип файла (расширение)
    """
    # 1. Сохраняем в БД
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO messages (chat_id, sender_id, file_path, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (chat_id, user_id, file_url, datetime.now(timezone.utc)),
        )
        conn.commit()
    finally:
        cur.close()
        release_db_connection(conn)

    # 2. Уведомляем через WebSocket с кэшированием
    # Кэшируем тип чата
    from ..utils.ws_manager import cache_get, cache_set
    chat_type_cache_key = f"chat_type:{chat_id}"
    chat_type_cached = await cache_get(chat_type_cache_key)
    if chat_type_cached:
        chat_type = chat_type_cached
    else:
        chat_type = get_chat_type(chat_id)
        await cache_set(chat_type_cache_key, chat_type, ttl=600)
    
    sender_username = None
    if chat_type == "group":
        # Кэшируем username
        cache_key = f"username:{user_id}"
        cached = await cache_get(cache_key)
        if cached:
            sender_username = cached
        else:
            sender_username = get_username(user_id)
            await cache_set(cache_key, sender_username, ttl=600)

    await _notify_users(
        chat_id,
        {
            "type": "message",
            "chat_id": chat_id,
            "sender_id": user_id,
            "sender_username": sender_username,
            "chat_type": chat_type,
            "text": None,
            "file_path": file_url,
            "file_type": file_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

# === WebSocket-маршрут ===
@router.websocket("/ws/{chat_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, last_message_id: Optional[int] = Query(None, alias="last_message_id")):
    """
    Обрабатывает WebSocket-соединение. Токен передаётся как ?token=...
    
    Args:
        chat_id: ID чата
        last_message_id: (optional) ID последнего полученного сообщения для восстановления при reconnect
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Токен не указан")
        return

    try:
        user_id = get_current_user(token)
    except ValueError:
        await websocket.close(code=4002, reason="Неверный токен")
        return

    if not is_user_in_chat(chat_id, user_id):
        await websocket.close(code=4003, reason="Нет доступа к чату")
        return

    # === Rate Limiting: проверка лимита подключений ===
    if not await check_ws_rate_limit(user_id, max_connections=5):
        await websocket.close(code=4004, reason="Превышен лимит подключений")
        return

    await websocket.accept()
    
    logger.info(f"WebSocket подключён: user_id={user_id}, chat_id={chat_id}")

    # === Добавляем соединение в Redis и локальный кэш ===
    await add_connection(chat_id, user_id, INSTANCE_ID)
    await increment_ws_limit(user_id)
    
    # Локальный кэш (для текущего инстанса)
    if user_id not in online_users:
        online_users[user_id] = set()
    online_users[user_id].add(chat_id)
    
    if chat_id not in active_connections:
        active_connections[chat_id] = {}
    active_connections[chat_id][user_id] = websocket

    # === Добавляем пользователя в онлайн в Redis ===
    await add_user_online(user_id, chat_id)

    # Логируем событие подключения WebSocket
    try:
        from ..models.chat import log_connection_event
        log_connection_event(user_id, 'connect')
    except Exception as e:
        logger.error(f"Ошибка логирования connect: {str(e)}")

    # === Восстановление сообщений при reconnect ===
    if last_message_id is not None:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT message_id, sender_id, text, file_path, created_at 
                    FROM messages 
                    WHERE chat_id = %s AND message_id > %s 
                    ORDER BY message_id ASC
                """, (chat_id, last_message_id))
                missed_messages = cur.fetchall()
                
                for msg in missed_messages:
                    msg_id, sender_id, text, file_path, created_at = msg
                    # Кэшируем тип чата
                    chat_type_cache_key = f"chat_type:{chat_id}"
                    chat_type_cached = await cache_get(chat_type_cache_key)
                    if chat_type_cached:
                        chat_type = chat_type_cached
                    else:
                        chat_type = get_chat_type(chat_id)
                        await cache_set(chat_type_cache_key, chat_type, ttl=600)
                    
                    sender_username = None
                    if chat_type == "group":
                        # Используем кэширование
                        cache_key = f"username:{sender_id}"
                        cached = await cache_get(cache_key)
                        if cached:
                            sender_username = cached
                        else:
                            sender_username = get_username(sender_id)
                            await cache_set(cache_key, sender_username, ttl=600)
                    
                    await websocket.send_json({
                        "type": "message",
                        "chat_id": chat_id,
                        "sender_id": sender_id,
                        "sender_username": sender_username,
                        "chat_type": chat_type,
                        "text": text,
                        "file_path": file_path,
                        "timestamp": created_at.isoformat() if created_at else None,
                    })
            finally:
                cur.close()
                release_db_connection(conn)
        except Exception as e:
            logger.error(f"Ошибка восстановления сообщений: {e}")

    # Уведомление о входе (онлайн) - рассылаем во ВСЕ чаты пользователя
    await _broadcast_status_to_all_chats(user_id, "online")

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                text = payload.get("text", "").strip()
                if not text:
                    continue

                # === 1. Сохраняем сообщение в БД ===
                conn = get_db_connection()
                cur = conn.cursor()
                try:
                    cur.execute(
                        """
                        INSERT INTO messages (chat_id, sender_id, text, created_at)
                        VALUES (%s, %s, %s, %s)
                        RETURNING message_id
                        """,
                        (chat_id, user_id, text, datetime.now(timezone.utc)),
                    )
                    message_id = cur.fetchone()[0]
                    conn.commit()
                finally:
                    cur.close()
                    release_db_connection(conn)

                # === 2. Готовим данные для рассылки с кэшированием ===
                # Кэшируем тип чата
                chat_type_cache_key = f"chat_type:{chat_id}"
                chat_type_cached = await cache_get(chat_type_cache_key)
                if chat_type_cached:
                    chat_type = chat_type_cached
                else:
                    chat_type = get_chat_type(chat_id)
                    await cache_set(chat_type_cache_key, chat_type, ttl=600)
                
                sender_username = None
                if chat_type == "group":
                    # Кэшируем username
                    cache_key = f"username:{user_id}"
                    cached = await cache_get(cache_key)
                    if cached:
                        sender_username = cached
                    else:
                        sender_username = get_username(user_id)
                        await cache_set(cache_key, sender_username, ttl=600)

                # === 3. Рассылаем сообщение ===
                await _notify_users(
                    chat_id,
                    {
                        "type": "message",
                        "chat_id": chat_id,
                        "sender_id": user_id,
                        "sender_username": sender_username,
                        "chat_type": chat_type,
                        "text": text,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # === 4. Обновляем последнее прочитанное сообщение для отправителя ===
                from ..models.chat import update_last_read_message
                update_last_read_message(user_id, chat_id, message_id)

            except json.JSONDecodeError:
                await websocket.send_json({"error": "Неверный JSON"})

    except WebSocketDisconnect:
        pass
    finally:
        # Логируем событие отключения WebSocket
        try:
            from ..models.chat import log_connection_event
            log_connection_event(user_id, 'disconnect')
        except Exception as e:
            logger.error(f"Ошибка логирования disconnect: {str(e)}")
        
        # === Удаляем соединение из Redis и локального кэша ===
        await remove_connection(chat_id, user_id)
        await decrement_ws_limit(user_id)
        await remove_user_online(user_id, chat_id)
        
        # Локальный кэш
        active_connections.get(chat_id, {}).pop(user_id, None)
        
        # Удаляем чат из списка активных чатов пользователя
        if user_id in online_users:
            online_users[user_id].discard(chat_id)
            # Если у пользователя больше нет активных чатов, удаляем его из онлайн
            if len(online_users[user_id]) == 0:
                del online_users[user_id]
                # Проверяем через Redis перед уведомлением об оффлайне
                is_online = await is_user_online(user_id)
                if not is_online:
                    # Уведомляем об оффлайне только если пользователь полностью оффлайн
                    await _broadcast_status_to_all_chats(user_id, "offline")

# === HTTP-маршруты ===
@router.post("/chats/private", summary="Создать личный чат")
def create_private_chat_endpoint(
    request: CreatePrivateChatRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    if current_user_id not in (request.user1_id, request.user2_id):
        raise HTTPException(status_code=403, detail="Вы не участник этого чата")
    chat_id = create_private_chat(request.user1_id, request.user2_id)
    return {"chat_id": chat_id}

@router.post("/chats/group", summary="Создать групповой чат")
def create_group_chat_endpoint(
    request: CreateGroupChatRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Создаёт новый групповой чат. Инициатор автоматически становится владельцем и участником.
    """
    chat_id = create_group_chat(request.name, current_user_id)
    return {"chat_id": chat_id}

@router.get("/chats/{chat_id}/messages", summary="Получить историю сообщений")
def get_messages(
    chat_id: int,
    limit: int = Query(50, le=100, description="Максимум 100 сообщений"),
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Возвращает последние N сообщений из чата.
    Доступно только участникам чата.
    """
    if not is_user_in_chat(chat_id, current_user_id):
        raise HTTPException(
            status_code=403,
            detail="У вас нет доступа к этому чату"
        )

    history = get_chat_history(chat_id, limit)
    
    # Обновляем последнее прочитанное сообщение при загрузке истории
    from ..models.chat import update_last_read_message, get_chat_last_message_id
    last_msg_id = get_chat_last_message_id(chat_id)
    if last_msg_id:
        update_last_read_message(current_user_id, chat_id, last_msg_id)
    
    return {"messages": history}

@router.post("/chats/{chat_id}/upload", summary="Загрузить файл в чат")
async def upload_file(  # ← ОБЯЗАТЕЛЬНО async
    chat_id: int,
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_from_header),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Загружает файл в указанный чат. Поддерживаются только безопасные форматы.
    Максимальный размер файла: 2 МБ.
    Проверяет расширение и MIME-тип файла (magic numbers) для безопасности.
    """
    # Проверка участия в чате
    if not is_user_in_chat(chat_id, current_user_id):
        raise HTTPException(status_code=403, detail="Нет доступа к чату")

    # Проверка размера (для UploadFile нужно читать содержимое)
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 2 МБ)")

    # Возвращаемся к началу файла
    await file.seek(0)

    # Проверка расширения
    _, ext = os.path.splitext(file.filename or "")
    ext_lower = ext.lower()
    if ext_lower not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(ALLOWED_FILE_EXTENSIONS)
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый тип файла. Разрешены: {allowed}"
        )

    # === Валидация по magic numbers (MIME-тип) ===
    try:
        mime_type = get_file_type(contents[:1024])
        
        # Проверяем, что MIME-тип разрешён
        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Небезопасный тип файла (MIME: {mime_type})"
            )
        
        # Проверяем соответствие расширения и MIME-типа
        if ext_lower not in ALLOWED_MIME_TYPES[mime_type]:
            raise HTTPException(
                status_code=400,
                detail=f"Несоответствие расширения и типа файла (расширение: {ext_lower}, MIME: {mime_type})"
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка проверки файла: {str(e)}")

    # Сохранение с защитой от path traversal
    safe_filename = f"{uuid.uuid4().hex}{ext_lower}"
    filepath = os.path.join(UPLOAD_DIR, safe_filename)
    
    # Дополнительная проверка: убеждаемся, что путь не выходит за пределы UPLOAD_DIR
    real_upload_dir = os.path.realpath(UPLOAD_DIR)
    real_filepath = os.path.realpath(filepath)
    if not real_filepath.startswith(real_upload_dir):
        raise HTTPException(status_code=400, detail="Небезопасное имя файла")

    with open(filepath, "wb") as f:
        f.write(contents)  # ← используем уже прочитанные данные

    # Фоновая задача
    background_tasks.add_task(
        _notify_file_upload,
        chat_id=chat_id,
        user_id=current_user_id,
        file_url=f"/uploads/{safe_filename}",
        file_type=ext_lower
    )

    return {"file_url": f"/uploads/{safe_filename}"}

@router.get("/chats/me", summary="Получить список моих чатов")
def get_my_chats(current_user_id: int = Depends(get_current_user_from_header)):
    """Возвращает все чаты текущего пользователя."""
    from ..models.chat import get_unread_count
    chats = get_user_chats(current_user_id)
    unread_counts = get_unread_count(current_user_id)
    
    # Добавляем информацию о непрочитанных сообщениях к каждому чату
    for chat in chats:
        chat["unread_count"] = unread_counts.get(chat["chat_id"], 0)
    
    return {"chats": chats}


@router.post("/chats/{chat_id}/invite", summary="Пригласить пользователя в групповой чат")
def invite_user_to_chat(
    chat_id: int,
    request: InviteUserRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Приглашает пользователя в групповой чат по email или username.
    Доступно только владельцу чата.
    """
    from app.models.user import get_user_by_email_or_username
    
    # Находим пользователя по email или username
    user_data = get_user_by_email_or_username(request.user_email_or_username)
    if not user_data:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    invited_user_id = user_data[0]
    
    if not add_user_to_group_chat(chat_id, invited_user_id, current_user_id):
        raise HTTPException(status_code=403, detail="Недостаточно прав или чат не найден")
    return {"status": "success"}


@router.delete("/chats/{chat_id}/leave", summary="Покинуть групповой чат")
def leave_group_chat(
    chat_id: int,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """Позволяет пользователю покинуть групповой чат."""
    if not remove_user_from_group_chat(chat_id, current_user_id, current_user_id):
        raise HTTPException(status_code=403, detail="Невозможно покинуть чат")
    return {"status": "success"}


@router.delete("/chats/{chat_id}", summary="Удалить личный чат")
def delete_private_chat_endpoint(
    chat_id: int,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Удаляет личный чат. Доступно любому из участников.
    Групповые чаты нельзя удалять этим методом.
    """
    if not delete_private_chat(chat_id, current_user_id):
        raise HTTPException(status_code=403, detail="Нет доступа к чату или чат не найден")
    return {"status": "success", "message": "Чат удалён"}


@router.get("/users", summary="Получить список всех пользователей")
def get_users_list(
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Возвращает список всех пользователей (кроме текущего).
    Используется для создания новых чатов.
    """
    users = get_all_users(exclude_user_id=current_user_id)
    return {"users": users}


@router.get("/users/search", summary="Поиск пользователей")
def search_users_endpoint(
    q: str,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Ищет пользователей по имени или email.
    Требует параметр поиска 'q'.
    """
    if not q or len(q.strip()) == 0:
        raise HTTPException(status_code=400, detail="Введите строку поиска")
    
    users = search_users(query=q.strip(), exclude_user_id=current_user_id)
    return {"users": users}


@router.post("/chats/private/with-user", summary="Создать личный чат с пользователем")
def create_private_chat_with_user(
    user2_id: int,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Создаёт личный чат между текущим пользователем и указанным.
    Если чат уже существует, возвращает его ID.
    """
    if user2_id == current_user_id:
        raise HTTPException(status_code=400, detail="Нельзя создать чат с самим собой")
    
    chat_id = create_private_chat(current_user_id, user2_id)
    return {"chat_id": chat_id}


@router.get("/users/status", summary="Получить статусы пользователей (онлайн/оффлайн)")
def get_users_status(
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Возвращает список всех пользователей с их статусами (онлайн, админ, забанен).
    Используется в том числе для проверки прав администратора.
    """
    from ..models.chat import get_chat_members
    from ..models.user import get_user_by_id
    
    # Получаем данные текущего пользователя
    current_user = get_user_by_id(current_user_id)
    
    # Получаем все чаты пользователя
    user_chats = get_user_chats(current_user_id)
    
    # Собираем всех уникальных пользователей из этих чатов
    all_user_ids = set()
    for chat in user_chats:
        members = get_chat_members(chat["chat_id"])
        all_user_ids.update(members)
    
    # Исключаем текущего пользователя
    all_user_ids.discard(current_user_id)
    
    # Формируем результат с расширенной информацией
    users_info = []
    for user_id in all_user_ids:
        try:
            user_data = get_user_by_id(user_id)
            users_info.append({
                "id": user_data["id"],
                "username": user_data["username"],
                "email": user_data["email"],
                "is_admin": user_data["is_admin"],
                "is_banned": user_data["is_banned"],
                "online": user_id in online_users
            })
        except ValueError:
            continue
    
    # Добавляем текущего пользователя
    users_info.append({
        "id": current_user["id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "is_admin": current_user["is_admin"],
        "is_banned": current_user["is_banned"],
        "online": True
    })
    
    return users_info


@router.get("/chats/unread", summary="Получить количество непрочитанных сообщений")
def get_unread_counts(
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Возвращает количество непрочитанных сообщений для каждого чата.
    """
    from ..models.chat import get_unread_count
    unread = get_unread_count(current_user_id)
    return {"unread_counts": unread}


# === Маршруты для управления сообщениями (редактирование, удаление, жалобы) ===

@router.put("/messages/{message_id}", summary="Редактировать сообщение")
def edit_message(
    message_id: int,
    request: dict,
    current_user_id: int = Depends(get_current_user_from_header),
    background_tasks: BackgroundTasks = None,
):
    """
    Редактирует текст сообщения.
    Можно редактировать только свои текстовые сообщения (не файлы).
    После редактирования отправляет обновление всем участникам чата через WebSocket.
    """
    text = request.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, существует ли сообщение и принадлежит ли оно пользователю
        cur.execute(
            """
            SELECT sender_id, file_path, chat_id FROM messages WHERE message_id = %s
            """,
            (message_id,)
        )
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        
        sender_id, file_path, chat_id = row
        
        if file_path:
            raise HTTPException(status_code=400, detail="Нельзя редактировать файлы")
        
        if sender_id != current_user_id:
            raise HTTPException(status_code=403, detail="Можно редактировать только свои сообщения")
        
        # Обновляем сообщение
        edited_at = datetime.now(timezone.utc)
        cur.execute(
            """
            UPDATE messages 
            SET text = %s, edited_at = %s 
            WHERE message_id = %s
            """,
            (text, edited_at, message_id)
        )
        conn.commit()
        
        # Отправляем уведомление всем участникам чата через WebSocket
        from ..models.chat import _get_chat_members
        members = _get_chat_members(chat_id)
        # Кэшируем тип чата и username
        from ..utils.ws_manager import cache_get, cache_set
        chat_type_cache_key = f"chat_type:{chat_id}"
        chat_type_cached = cache_get(chat_type_cache_key)
        if chat_type_cached:
            chat_type = chat_type_cached
        else:
            from ..models.chat import get_chat_type
            chat_type = get_chat_type(chat_id)
            cache_set(chat_type_cache_key, chat_type, ttl=600)
        
        sender_username = None
        if chat_type == "group":
            cache_key = f"username:{sender_id}"
            cached = cache_get(cache_key)
            if cached:
                sender_username = cached
            else:
                from ..models.user import get_username
                sender_username = get_username(sender_id)
                cache_set(cache_key, sender_username, ttl=600)
        
        # Рассылаем обновление
        import asyncio
        from fastapi.websockets import WebSocketState
        global active_connections
        for user_id in members:
            ws = active_connections.get(chat_id, {}).get(user_id)
            if ws and ws.client_state == WebSocketState.CONNECTED:
                try:
                    asyncio.create_task(ws.send_json({
                        "type": "message_edited",
                        "message_id": message_id,
                        "chat_id": chat_id,
                        "sender_id": sender_id,
                        "sender_username": sender_username,
                        "text": text,
                        "edited_at": edited_at.isoformat(),
                        "chat_type": chat_type
                    }))
                except Exception:
                    pass
        
        return {"message": "Сообщение отредактировано"}
    except HTTPException:
        raise
    finally:
        cur.close()
        release_db_connection(conn)


@router.delete("/messages/{message_id}", summary="Удалить сообщение")
def delete_message(
    message_id: int,
    current_user_id: int = Depends(get_current_user_from_header),
    background_tasks: BackgroundTasks = None,
):
    """
    Удаляет сообщение.
    Можно удалить только своё сообщение или если пользователь - администратор.
    После удаления отправляет уведомление всем участникам чата через WebSocket.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, существует ли сообщение и является ли пользователь админом или владельцем
        cur.execute(
            """
            SELECT sender_id, chat_id FROM messages WHERE message_id = %s
            """,
            (message_id,)
        )
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        
        sender_id, chat_id = row[0], row[1]
        
        # Проверяем права (владелец или админ)
        cur.execute("SELECT is_admin FROM users WHERE id = %s", (current_user_id,))
        is_admin = cur.fetchone()[0]
        
        if sender_id != current_user_id and not is_admin:
            raise HTTPException(status_code=403, detail="Нет прав на удаление этого сообщения")
        
        # Сначала удаляем ссылки на сообщение из last_read_messages (чтобы не нарушить FK)
        cur.execute("DELETE FROM last_read_messages WHERE last_read_message_id = %s", (message_id,))
        
        # Удаляем сообщение из БД
        cur.execute("DELETE FROM messages WHERE message_id = %s", (message_id,))
        conn.commit()
        
        # Отправляем уведомление всем участникам чата через WebSocket
        from ..models.chat import _get_chat_members
        members = _get_chat_members(chat_id)
        # Кэшируем тип чата и username
        from ..utils.ws_manager import cache_get, cache_set
        chat_type_cache_key = f"chat_type:{chat_id}"
        chat_type_cached = cache_get(chat_type_cache_key)
        if chat_type_cached:
            chat_type = chat_type_cached
        else:
            from ..models.chat import get_chat_type
            chat_type = get_chat_type(chat_id)
            cache_set(chat_type_cache_key, chat_type, ttl=600)
        
        # Рассылаем обновление
        import asyncio
        from fastapi.websockets import WebSocketState
        global active_connections
        for user_id in members:
            ws = active_connections.get(chat_id, {}).get(user_id)
            if ws and ws.client_state == WebSocketState.CONNECTED:
                try:
                    asyncio.create_task(ws.send_json({
                        "type": "message_deleted",
                        "message_id": message_id,
                        "chat_id": chat_id,
                        "deleted_by": current_user_id,
                        "chat_type": chat_type
                    }))
                except Exception:
                    pass
        
        return {"message": "Сообщение удалено"}
    except HTTPException:
        raise
    finally:
        cur.close()
        release_db_connection(conn)


class ReportMessageRequest(BaseModel):
    """Запрос на жалобу к сообщению."""
    message_id: int
    reason: str


@router.post("/messages/report", summary="Пожаловаться на сообщение")
def report_message(
    request: ReportMessageRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Создаёт жалобу на сообщение.
    Жалоба сохраняется в БД для последующей модерации.
    
    Нельзя пожаловаться:
    - На своё сообщение
    - На сообщение админа (админы модерируются только другими админами)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, существует ли сообщение и получаем данные о чате и авторе
        cur.execute(
            """
            SELECT m.message_id, m.sender_id, m.chat_id, c.type, u.is_admin
            FROM messages m
            JOIN chats c ON m.chat_id = c.id
            JOIN users u ON m.sender_id = u.user_id
            WHERE m.message_id = %s
            """,
            (request.message_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        
        message_id, sender_id, chat_id, chat_type, sender_is_admin = row
        
        # Для личных чатов - нельзя пожаловаться на себя
        if chat_type == 'private' and sender_id == current_user_id:
            raise HTTPException(status_code=400, detail="Нельзя пожаловаться на своё сообщение")
        
        # Нельзя пожаловаться на сообщение админа (если текущий пользователь не админ)
        if sender_is_admin:
            # Проверяем, является ли текущий пользователь админом
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (current_user_id,))
            current_user_is_admin = cur.fetchone()[0]
            if not current_user_is_admin:
                raise HTTPException(
                    status_code=403, 
                    detail="Нельзя пожаловаться на сообщение администратора"
                )
        
        # Сохраняем жалобу
        cur.execute(
            """
            INSERT INTO message_reports (message_id, reporter_id, reason, created_at)
            VALUES (%s, %s, %s, NOW())
            """,
            (request.message_id, current_user_id, request.reason)
        )
        conn.commit()
        
        return {"message": "Жалоба отправлена"}
    except HTTPException:
        raise
    finally:
        cur.close()
        release_db_connection(conn)