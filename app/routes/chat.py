
"""
Модуль для работы с чатами: создание, проверка участия, получение истории.
Использует единую таблицу 'chats' для всех типов чатов.

Функции модуля:
- Создание личных и групповых чатов
- Проверка участия пользователя в чате
- Получение истории сообщений
- Управление участниками групповых чатов
- Удаление чатов
- Логирование событий подключения
"""

import logging
import os
from typing import List, Dict, Any, Optional
import psycopg2
from ..database import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def _get_chat_members(chat_id: int) -> List[int]:
    """
    Возвращает список ID всех участников чата (личного или группового).

    Эта функция используется внутри модуля routes для получения списка участников.

    Args:
        chat_id: ID чата

    Returns:
        Список ID участников чата
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT type, user1_id, user2_id FROM chats WHERE id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
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


def get_chat_members(chat_id: int) -> List[int]:
    """
    Публичная функция для получения списка участников чата.

    Args:
        chat_id: ID чата

    Returns:
        Список ID участников чата
    """
    return _get_chat_members(chat_id)

def create_private_chat(user1_id: int, user2_id: int) -> int:
    """
    Создаёт или возвращает существующий личный чат между двумя пользователями.

    Args:
        user1_id: ID первого пользователя
        user2_id: ID второго пользователя

    Returns:
        ID чата

    Raises:
        ValueError: если user1_id == user2_id или чат не удалось создать
    """
    if user1_id == user2_id:
        raise ValueError("Нельзя создать чат с самим собой")

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Ищем существующий чат
        cur.execute("""
            SELECT id FROM chats
            WHERE type = 'private'
              AND (
                  (user1_id = %s AND user2_id = %s)
                  OR
                  (user1_id = %s AND user2_id = %s)
              )
        """, (user1_id, user2_id, user2_id, user1_id))

        row = cur.fetchone()
        if row:
            return row[0]

        # Создаём новый чат
        cur.execute("""
            INSERT INTO chats (type, user1_id, user2_id)
            VALUES ('private', %s, %s)
            RETURNING id
        """, (user1_id, user2_id))

        chat_id = cur.fetchone()[0]
        conn.commit()
        return chat_id

    except psycopg2.IntegrityError:
        conn.rollback()
        # Повторная попытка найти чат (гонка условий)
        cur.execute("""
            SELECT id FROM chats
            WHERE type = 'private'
              AND (
                  (user1_id = %s AND user2_id = %s)
                  OR
                  (user1_id = %s AND user2_id = %s)
              )
        """, (user1_id, user2_id, user2_id, user1_id))
        row = cur.fetchone()
        if row:
            return row[0]
        raise ValueError("Не удалось создать чат")
    finally:
        cur.close()
        release_db_connection(conn)

def create_group_chat(name: str, owner_id: int) -> int:
    """
    Создает новый групповой чат.

    Args:
        name: название чата
        owner_id: ID создателя

    Returns:
        ID чата
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO chats (type, name, owner_id)
            VALUES ('group', %s, %s)
            RETURNING id
        """, (name, owner_id))
        chat_id = cur.fetchone()[0]

        # Добавляем владельца как первого участника
        cur.execute("""
            INSERT INTO chat_members (chat_id, user_id)
            VALUES (%s, %s)
        """, (chat_id, owner_id))

        conn.commit()
        return chat_id
    finally:
        cur.close()
        release_db_connection(conn)

def is_user_in_chat(chat_id: int, user_id: int) -> bool:
    """
    Проверяет, состоит ли пользователь в указанном чате.

    Args:
        chat_id: ID чата
        user_id: ID пользователя

    Returns:
        True, если пользователь — участник чата
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Сначала определим тип чата
        cur.execute("SELECT type, user1_id, user2_id FROM chats WHERE id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return False

        chat_type, user1, user2 = row

        if chat_type == 'private':
            return user_id in (user1, user2)
        elif chat_type == 'group':
            cur.execute(
                "SELECT 1 FROM chat_members WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id)
            )
            return cur.fetchone() is not None
        else:
            return False
    finally:
        cur.close()
        release_db_connection(conn)

def get_chat_history(chat_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT m.message_id, m.sender_id, u.username, m.text, m.file_path, m.created_at, c.type
            FROM messages m
            JOIN users u ON m.sender_id = u.user_id
            JOIN chats c ON m.chat_id = c.id
            WHERE m.chat_id = %s
            ORDER BY m.created_at ASC
            LIMIT %s
        """, (chat_id, limit))

        rows = cur.fetchall()
        result = []
        for row in rows:
            chat_type = row[6]
            msg = {
                "message_id": row[0],
                "sender_id": row[1],
                "text": row[3],
                "file_path": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
                "chat_type": chat_type
            }

            # Определяем тип файла
            if row[4]:  # есть file_path
                _, ext = os.path.splitext(row[4])
                msg["file_type"] = ext.lower()

            # Только для групповых чатов добавляем имя
            if chat_type == "group":
                msg["sender_username"] = row[2]
            result.append(msg)

        return result
    finally:
        cur.close()
        release_db_connection(conn)

def get_user_chats(user_id: int) -> List[Dict[str, Any]]:
    """
    Возвращает список чатов для конкретного пользователя.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        chats = []

        # Личные чаты
        cur.execute("""
            SELECT c.id, u1.username AS user1_name, u2.username AS user2_name,
                   c.user1_id, c.user2_id
            FROM chats c
            JOIN users u1 ON c.user1_id = u1.id
            JOIN users u2 ON c.user2_id = u2.id
            WHERE c.type = 'private' AND (c.user1_id = %s OR c.user2_id = %s)
        """, (user_id, user_id))

        for row in cur.fetchall():
            chat_id, user1_name, user2_name, user1_id, user2_id = row

            # Определяем, кто "другой" пользователь
            if user1_id == user_id:
                other_name = user2_name
                other_id = user2_id
            else:
                other_name = user1_name
                other_id = user1_id

            chats.append({
                "chat_id": chat_id,
                "type": "private",
                "name": f"Чат с {other_name}",
                "other_user_id": other_id
            })

        # Групповые чаты (без изменений)
        cur.execute("""
            SELECT c.id, c.name
            FROM chats c
            JOIN chat_members cm ON c.id = cm.chat_id
            WHERE c.type = 'group' AND cm.user_id = %s
        """, (user_id,))

        for row in cur.fetchall():
            chats.append({
                "chat_id": row[0],
                "type": "group",
                "name": row[1]
            })

        return chats
    finally:
        cur.close()
        release_db_connection(conn)

def add_user_to_group_chat(chat_id: int, user_id: int, inviter_id: int) -> bool:
    """
    Добавляет пользователя в групповой чат.
    Любой участник чата может приглашать других.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что чат групповой и inviter является участником
        cur.execute("""
            SELECT 1 FROM chats c
            JOIN chat_members cm ON c.id = cm.chat_id
            WHERE c.id = %s AND c.type = 'group' AND cm.user_id = %s
        """, (chat_id, inviter_id))
        row = cur.fetchone()
        if not row:
            return False

        # Добавляем участника
        cur.execute("""
            INSERT INTO chat_members (chat_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (chat_id, user_id))
        conn.commit()
        return True
    finally:
        cur.close()
        release_db_connection(conn)

def remove_user_from_group_chat(chat_id: int, user_id: int, remover_id: int) -> bool:
    """
    Удаляет пользователя из группового чата.
    Может сделать владелец/создатель или сам пользователь (покинуть чат).
    Если после удаления не осталось участников - чат автоматически удаляется.

    Returns:
        True если успешно, False если ошибка
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что чат групповой
        cur.execute("SELECT owner_id FROM chats WHERE id = %s AND type = 'group'", (chat_id,))
        row = cur.fetchone()
        if not row:
            return False

        owner_id = row[0]
        # Разрешено: владелец/создатель удаляет кого угодно, или пользователь удаляет себя
        if remover_id != owner_id and remover_id != user_id:
            return False

        # Сначала удаляем записи из last_read_messages для этого пользователя и чата
        # Это нужно сделать ДО удаления участника, чтобы избежать нарушения FK
        cur.execute("""
            DELETE FROM last_read_messages
            WHERE user_id = %s AND chat_id = %s
        """, (user_id, chat_id))

        # Удаляем участника
        cur.execute("DELETE FROM chat_members WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))

        # Проверяем, остались ли участники
        cur.execute("SELECT COUNT(*) FROM chat_members WHERE chat_id = %s", (chat_id,))
        remaining_count = cur.fetchone()[0]

        # Если участников не осталось - удаляем чат и все сообщения
        if remaining_count == 0:
            # Сначала удаляем записи из last_read_messages (чтобы избежать нарушения FK)
            cur.execute("""
                DELETE FROM last_read_messages
                WHERE chat_id = %s OR last_read_message_id IN (
                    SELECT message_id FROM messages WHERE chat_id = %s
                )
            """, (chat_id, chat_id))

            cur.execute("DELETE FROM messages WHERE chat_id = %s", (chat_id,))
            cur.execute("DELETE FROM chats WHERE id = %s", (chat_id,))

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при выходе из группового чата {chat_id}: {e}")
        return False
    finally:
        cur.close()
        release_db_connection(conn)

def delete_private_chat(chat_id: int, user_id: int) -> bool:
    """
    Удаляет личный чат. Любой из участников может удалить.
    Сначала удаляет записи из last_read_messages, затем сообщения и сам чат.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что чат существует, личный и пользователь — участник
        cur.execute("""
            SELECT user1_id, user2_id FROM chats
            WHERE id = %s AND type = 'private'
        """, (chat_id,))
        row = cur.fetchone()
        if not row or user_id not in (row[0], row[1]):
            return False

        # 1. Сначала удаляем записи из last_read_messages (чтобы избежать нарушения FK)
        cur.execute("""
            DELETE FROM last_read_messages
            WHERE chat_id = %s OR last_read_message_id IN (
                SELECT message_id FROM messages WHERE chat_id = %s
            )
        """, (chat_id, chat_id))

        # 2. Удаляем все сообщения в чате
        cur.execute("DELETE FROM messages WHERE chat_id = %s", (chat_id,))

        # 3. Удаляем сам чат
        cur.execute("DELETE FROM chats WHERE id = %s", (chat_id,))

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка удаления чата {chat_id}: {e}")
        return False
    finally:
        cur.close()
        release_db_connection(conn)

def get_chat_type(chat_id: int) -> str:
    """Возвращает тип чата: 'private' или 'group'."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT type FROM chats WHERE id = %s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        release_db_connection(conn)


def get_chat_type_cached(chat_id: int) -> Optional[str]:
    """
    Возвращает тип чата с кэшированием.

    Args:
        chat_id: ID чата

    Returns:
        Тип чата ('private' или 'group') или None если не найден
    """
    from ..utils.ws_manager import cache_get, cache_set

    cache_key = f"chat_type:{chat_id}"

    # Проверяем кэш
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Получаем из БД
    chat_type = get_chat_type(chat_id)

    # Сохраняем в кэш
    if chat_type:
        cache_set(cache_key, chat_type, ttl=600)

    return chat_type


def log_connection_event(user_id: int, event_type: str) -> None:
    """
    Логирует событие подключения или отключения пользователя.

    Args:
        user_id: ID пользователя
        event_type: Тип события ('connect', 'disconnect')
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO connection_logs (user_id, event_type, created_at)
            VALUES (%s, %s, NOW())
        """, (user_id, event_type))
        conn.commit()
    except Exception as e:
        conn.rollback()
        # Не выбрасываем ошибку, чтобы не ломать основной функционал
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка логирования события подключения: {e}")
    finally:
        cur.close()
        release_db_connection(conn)


def update_last_read_message(user_id: int, chat_id: int, message_id: int) -> None:
    """
    Обновляет последнее прочитанное сообщение для пользователя в чате.

    Args:
        user_id: ID пользователя
        chat_id: ID чата
        message_id: ID последнего прочитанного сообщения
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO last_read_messages (user_id, chat_id, last_read_message_id, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id, chat_id)
            DO UPDATE SET last_read_message_id = %s, updated_at = NOW()
        """, (user_id, chat_id, message_id, message_id))
        conn.commit()
    finally:
        cur.close()
        release_db_connection(conn)


def get_unread_count(user_id: int) -> Dict[int, int]:
    """
    Возвращает количество непрочитанных сообщений для каждого чата пользователя.

    Args:
        user_id: ID пользователя

    Returns:
        Словарь {chat_id: unread_count}
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Получаем все чаты пользователя
        cur.execute("""
            SELECT c.id FROM chats c
            WHERE c.type = 'private' AND (c.user1_id = %s OR c.user2_id = %s)
            UNION
            SELECT c.id FROM chats c
            JOIN chat_members cm ON c.id = cm.chat_id
            WHERE c.type = 'group' AND cm.user_id = %s
        """, (user_id, user_id, user_id))

        chat_ids = [row[0] for row in cur.fetchall()]
        result = {}

        for chat_id in chat_ids:
            # Получаем ID последнего прочитанного сообщения
            cur.execute("""
                SELECT last_read_message_id FROM last_read_messages
                WHERE user_id = %s AND chat_id = %s
            """, (user_id, chat_id))
            row = cur.fetchone()
            last_read_id = row[0] if row else None

            # Считаем непрочитанные сообщения
            if last_read_id:
                cur.execute("""
                    SELECT COUNT(*) FROM messages
                    WHERE chat_id = %s AND message_id > %s AND sender_id != %s
                """, (chat_id, last_read_id, user_id))
            else:
                # Если никогда не читал, считаем все сообщения кроме своих
                cur.execute("""
                    SELECT COUNT(*) FROM messages
                    WHERE chat_id = %s AND sender_id != %s
                """, (chat_id, user_id))

            count = cur.fetchone()[0]
            if count > 0:
                result[chat_id] = count

        return result
    finally:
        cur.close()
        release_db_connection(conn)


def get_chat_last_message_id(chat_id: int) -> Optional[int]:
    """
    Возвращает ID последнего сообщения в чате.

    Args:
        chat_id: ID чата

    Returns:
        ID последнего сообщения или None
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT message_id FROM messages
            WHERE chat_id = %s
            ORDER BY message_id DESC
            LIMIT 1
        """, (chat_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        release_db_connection(conn)