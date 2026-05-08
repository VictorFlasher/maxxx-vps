"""
Модуль для работы с чатами: создание, проверка участия, получение истории.
Использует единую таблицу 'chats' для всех типов чатов.

Структура БД:
- chats: chat_id, name, is_group, created_at, created_by
- chat_members: chat_id, user_id, joined_at, role
- messages: message_id, chat_id, sender_id, content, encrypted_key, iv, created_at, is_edited, edited_at
- connection_logs: log_id, user_id, event_type, ip_address, created_at
- last_read_messages: (предполагается) user_id, chat_id, last_read_message_id, updated_at

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
        # Сначала проверяем тип чата
        cur.execute("SELECT is_group, created_by FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return []

        is_group, created_by = row

        if not is_group:
            # Личный чат - участники это создатель и второй пользователь
            # Для личных чатов created_by = user1_id, а user2_id хранится в chat_members
            cur.execute("SELECT user_id FROM maxxx_local.chat_members WHERE chat_id = %s", (chat_id,))
            members = [r[0] for r in cur.fetchall()]
            if created_by and created_by not in members:
                members.insert(0, created_by)
            return members
        else:
            # Групповой чат - все участники из chat_members
            cur.execute("SELECT user_id FROM maxxx_local.chat_members WHERE chat_id = %s", (chat_id,))
            return [r[0] for r in cur.fetchall()]
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

    Личный чат создаётся так:
    - chats: is_group=False, created_by=user1_id
    - chat_members: добавляется user2_id (user1 уже считается создателем)

    Args:
        user1_id: ID первого пользователя (создатель)
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
        # Ищем существующий личный чат, где оба пользователя являются участниками
        # Проверяем через chat_members + created_by
        cur.execute("""
            SELECT c.chat_id FROM maxxx_local.chats c
            WHERE c.is_group = FALSE
              AND c.created_by IN (%s, %s)
              AND EXISTS (
                  SELECT 1 FROM maxxx_local.chat_members cm 
                  WHERE cm.chat_id = c.chat_id 
                    AND cm.user_id IN (%s, %s)
              )
        """, (user1_id, user2_id, user1_id, user2_id))

        row = cur.fetchone()
        if row:
            # Дополнительно проверим, что в чате ровно 2 участника (с учётом created_by)
            chat_id = row[0]
            cur.execute("SELECT COUNT(*) FROM maxxx_local.chat_members WHERE chat_id = %s", (chat_id,))
            member_count = cur.fetchone()[0]
            # created_by + члены из chat_members должны дать 2 уникальных пользователя
            if member_count == 1:  # один в chat_members + created_by = 2
                return chat_id

        # Создаём новый чат
        cur.execute("""
            INSERT INTO chats (is_group, created_by)
            VALUES (FALSE, %s)
            RETURNING chat_id
        """, (user1_id,))

        chat_id = cur.fetchone()[0]

        # Добавляем второго пользователя как участника
        cur.execute("""
            INSERT INTO chat_members (chat_id, user_id)
            VALUES (%s, %s)
        """, (chat_id, user2_id))

        conn.commit()
        return chat_id

    except psycopg2.IntegrityError:
        conn.rollback()
        # Повторная попытка найти чат (гонка условий)
        cur.execute("""
            SELECT c.chat_id FROM maxxx_local.chats c
            WHERE c.is_group = FALSE
              AND c.created_by IN (%s, %s)
              AND EXISTS (
                  SELECT 1 FROM maxxx_local.chat_members cm 
                  WHERE cm.chat_id = c.chat_id 
                    AND cm.user_id IN (%s, %s)
              )
        """, (user1_id, user2_id, user1_id, user2_id))
        row = cur.fetchone()
        if row:
            return row[0]
        raise ValueError("Не удалось создать чат")
    finally:
        cur.close()
        release_db_connection(conn)

def create_group_chat(name: str, owner_id: int) -> int:
    """
    Создаёт новый групповой чат.

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
            INSERT INTO chats (name, is_group, created_by)
            VALUES (%s, TRUE, %s)
            RETURNING chat_id
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
        cur.execute("SELECT is_group, created_by FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return False

        is_group, created_by = row

        if not is_group:
            # Личный чат: пользователь имеет доступ если он создатель ИЛИ второй участник
            # Второй участник определяется по chat_members
            if user_id == created_by:
                return True
            
            # Проверяем, есть ли пользователь в chat_members для этого чата
            cur.execute(
                "SELECT 1 FROM maxxx_local.chat_members WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id)
            )
            return cur.fetchone() is not None
        else:
            # Групповой чат
            cur.execute(
                "SELECT 1 FROM maxxx_local.chat_members WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id)
            )
            return cur.fetchone() is not None
    finally:
        cur.close()
        release_db_connection(conn)

def get_chat_history(chat_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Сначала определим тип чата
        cur.execute("SELECT is_group FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return []
        
        is_group = row[0]
        
        cur.execute("""
            SELECT m.message_id, m.sender_id, u.username, m.content, m.created_at, m.is_edited, m.edited_at, m.file_path, m.file_type, m.original_filename
            FROM maxxx_local.messages m
            JOIN maxxx_local.users u ON m.sender_id = u.user_id
            WHERE m.chat_id = %s
            ORDER BY m.created_at ASC
            LIMIT %s
        """, (chat_id, limit))

        rows = cur.fetchall()
        result = []
        for row in rows:
            message_id, sender_id, username, content, created_at, is_edited, edited_at, file_path, file_type, original_filename = row
            
            # Если file_path не заполнен, но content содержит формат "[Файл]: URL", извлекаем путь
            if not file_path and content and content.startswith('[Файл]: '):
                file_path = content.replace('[Файл]: ', '').strip()
                # Определяем тип файла из расширения
                if not file_type:
                    _, ext = os.path.splitext(file_path)
                    file_type = ext.lower() if ext else ''
                content = None  # Очищаем content, так как файл теперь в file_path
            
            msg = {
                "message_id": message_id,
                "sender_id": sender_id,
                "sender_username": username,
                "text": content,
                "timestamp": created_at.isoformat() if created_at else None,
                "is_edited": is_edited,
                "edited_at": edited_at.isoformat() if edited_at else None,
                "chat_type": "group" if is_group else "private",
                "file_path": file_path,
                "file_type": file_type,
                "original_filename": original_filename
            }

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

        # Личные чаты: где пользователь - создатель или участник в chat_members
        cur.execute("""
            SELECT c.chat_id, c.created_by, cm.user_id as member_id, 
                   u1.username as creator_name, u2.username as member_name
            FROM maxxx_local.chats c
            LEFT JOIN maxxx_local.chat_members cm ON c.chat_id = cm.chat_id
            LEFT JOIN maxxx_local.users u1 ON c.created_by = u1.user_id
            LEFT JOIN maxxx_local.users u2 ON cm.user_id = u2.user_id
            WHERE c.is_group = FALSE 
              AND (c.created_by = %s OR cm.user_id = %s)
        """, (user_id, user_id))

        processed_chat_ids = set()
        for row in cur.fetchall():
            chat_id, created_by, member_id, creator_name, member_name = row
            
            if chat_id in processed_chat_ids:
                continue
            processed_chat_ids.add(chat_id)
            
            # Определяем "другого" пользователя
            if created_by == user_id:
                # Пользователь - создатель, второй пользователь - это member_id
                other_id = member_id
                other_name = member_name
            else:
                # Пользователь - второй участник, создатель - created_by
                other_id = created_by
                other_name = creator_name

            chats.append({
                "chat_id": chat_id,
                "type": "private",
                "name": f"Чат с {other_name}" if other_name else "Личный чат",
                "other_user_id": other_id
            })

        # Групповые чаты
        cur.execute("""
            SELECT c.chat_id, c.name
            FROM maxxx_local.chats c
            JOIN maxxx_local.chat_members cm ON c.chat_id = cm.chat_id
            WHERE c.is_group = TRUE AND cm.user_id = %s
        """, (user_id,))

        for row in cur.fetchall():
            chats.append({
                "chat_id": row[0],
                "type": "group",
                "name": row[1] or "Групповой чат"
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
            SELECT 1 FROM maxxx_local.chats c
            JOIN maxxx_local.chat_members cm ON c.chat_id = cm.chat_id
            WHERE c.chat_id = %s AND c.is_group = TRUE AND cm.user_id = %s
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
    Также удаляет файлы сообщений с диска при удалении чата.
    
    Returns:
        True если успешно, False если ошибка
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что чат групповой
        cur.execute("SELECT created_by FROM maxxx_local.chats WHERE chat_id = %s AND is_group = TRUE", (chat_id,))
        row = cur.fetchone()
        if not row:
            return False

        owner_id = row[0]
        # Разрешено: владелец/создатель удаляет кого угодно, или пользователь удаляет себя
        if remover_id != owner_id and remover_id != user_id:
            return False

        # Удаляем участника
        cur.execute("DELETE FROM maxxx_local.chat_members WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        
        # Проверяем, остались ли участники
        cur.execute("SELECT COUNT(*) FROM maxxx_local.chat_members WHERE chat_id = %s", (chat_id,))
        remaining_count = cur.fetchone()[0]
        
        # Если участников не осталось - удаляем чат и все сообщения
        if remaining_count == 0:
            # Получаем все файлы перед удалением сообщений
            cur.execute("SELECT file_path FROM maxxx_local.messages WHERE chat_id = %s AND file_path IS NOT NULL", (chat_id,))
            file_paths = [row[0] for row in cur.fetchall()]
            
            cur.execute("DELETE FROM maxxx_local.messages WHERE chat_id = %s", (chat_id,))
            cur.execute("DELETE FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
            
            conn.commit()
            
            # Удаляем файлы с диска после успешной транзакции
            import os
            for file_path in file_paths:
                try:
                    # Убираем ведущий слэш если есть
                    clean_path = file_path.lstrip('/')
                    full_path = os.path.join(os.getenv('UPLOAD_DIR', '/workspace'), clean_path)
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        logger.info(f"Удалён файл {full_path}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить файл {file_path}: {e}")
        else:
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
    Также удаляет все файлы сообщений с диска.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что чат существует, личный и пользователь — участник
        cur.execute("""
            SELECT created_by FROM maxxx_local.chats
            WHERE chat_id = %s AND is_group = FALSE
        """, (chat_id,))
        row = cur.fetchone()
        if not row:
            return False
        
        created_by = row[0]
        
        # Проверяем, что пользователь - создатель или участник
        cur.execute("SELECT 1 FROM maxxx_local.chat_members WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        is_member = cur.fetchone() is not None
        
        if user_id != created_by and not is_member:
            return False

        # 1. Получаем все файлы перед удалением сообщений
        cur.execute("SELECT file_path FROM maxxx_local.messages WHERE chat_id = %s AND file_path IS NOT NULL", (chat_id,))
        file_paths = [row[0] for row in cur.fetchall()]
        
        # 2. Удаляем все сообщения в чате
        cur.execute("DELETE FROM maxxx_local.messages WHERE chat_id = %s", (chat_id,))
        
        # 3. Удаляем сам чат
        cur.execute("DELETE FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
        
        conn.commit()
        
        # 4. Удаляем файлы с диска после успешной транзакции
        for file_path in file_paths:
            try:
                # Убираем ведущий слэш если есть
                clean_path = file_path.lstrip('/')
                full_path = os.path.join(os.getenv('UPLOAD_DIR', '/workspace'), clean_path)
                if os.path.exists(full_path):
                    os.remove(full_path)
                    logger.info(f"Удалён файл {full_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить файл {file_path}: {e}")
        
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
        cur.execute("SELECT is_group FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return "group" if row[0] else "private"
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
        logger.error(f"Ошибка логирования события подключения: {e}")
    finally:
        cur.close()
        release_db_connection(conn)


def update_last_read_message(user_id: int, chat_id: int, message_id: int) -> None:
    """
    Обновляет последнее прочитанное сообщение для пользователя в чате.
    Заглушка - таблица last_read_messages отсутствует.
    """
    # Функция не используется, так как таблица last_read_messages отсутствует в БД
    pass


def get_unread_count(user_id: int) -> Dict[int, int]:
    """
    Возвращает количество непрочитанных сообщений для каждого чата пользователя.
    Упрощённая версия без таблицы last_read_messages.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Получаем все чаты пользователя
        cur.execute("""
            SELECT c.chat_id FROM maxxx_local.chats c
            WHERE c.is_group = FALSE AND (c.created_by = %s OR EXISTS (
                SELECT 1 FROM maxxx_local.chat_members cm WHERE cm.chat_id = c.chat_id AND cm.user_id = %s
            ))
            UNION
            SELECT c.chat_id FROM maxxx_local.chats c
            JOIN maxxx_local.chat_members cm ON c.chat_id = cm.chat_id
            WHERE c.is_group = TRUE AND cm.user_id = %s
        """, (user_id, user_id, user_id))
        
        chat_ids = [row[0] for row in cur.fetchall()]
        result = {}
        
        for chat_id in chat_ids:
            # Считаем все сообщения от других пользователей как непрочитанные
            # (упрощённая версия без отслеживания прочитанных)
            cur.execute("""
                SELECT COUNT(*) FROM maxxx_local.messages
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
            SELECT message_id FROM maxxx_local.messages
            WHERE chat_id = %s
            ORDER BY message_id DESC
            LIMIT 1
        """, (chat_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        release_db_connection(conn)


def delete_group_chat(chat_id: int, user_id: int) -> bool:
    """
    Удаляет групповой чат. Только создатель может удалить.
    Также удаляет все файлы сообщений с диска.
    
    Args:
        chat_id: ID чата
        user_id: ID пользователя (должен быть создателем)
    
    Returns:
        True если успешно, False если ошибка
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что чат существует, групповой и пользователь - создатель
        cur.execute("""
            SELECT created_by FROM maxxx_local.chats
            WHERE chat_id = %s AND is_group = TRUE
        """, (chat_id,))
        row = cur.fetchone()
        if not row:
            return False
        
        created_by = row[0]
        
        if user_id != created_by:
            return False

        # 1. Получаем все файлы перед удалением сообщений
        cur.execute("SELECT file_path FROM maxxx_local.messages WHERE chat_id = %s AND file_path IS NOT NULL", (chat_id,))
        file_paths = [row[0] for row in cur.fetchall()]
        
        # 2. Удаляем всех участников из chat_members
        cur.execute("DELETE FROM maxxx_local.chat_members WHERE chat_id = %s", (chat_id,))
        
        # 3. Удаляем все сообщения в чате
        cur.execute("DELETE FROM maxxx_local.messages WHERE chat_id = %s", (chat_id,))
        
        # 4. Удаляем сам чат
        cur.execute("DELETE FROM maxxx_local.chats WHERE chat_id = %s", (chat_id,))
        
        conn.commit()
        
        # 5. Удаляем файлы с диска после успешной транзакции
        for file_path in file_paths:
            try:
                # Убираем ведущий слэш если есть
                clean_path = file_path.lstrip('/')
                full_path = os.path.join(os.getenv('UPLOAD_DIR', '/workspace'), clean_path)
                if os.path.exists(full_path):
                    os.remove(full_path)
                    logger.info(f"Удалён файл {full_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить файл {file_path}: {e}")
        
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка удаления группового чата {chat_id}: {e}")
        return False
    finally:
        cur.close()
        release_db_connection(conn)