"""
Модуль для работы с пользователями: регистрация, аутентификация, проверка прав.
Работает с таблицей users в схеме "maxxx-local".

Функции модуля:
- Регистрация новых пользователей с хешированием паролей
- Аутентификация по email
- Поиск пользователей по username/email
- Проверка административных прав и банов
- Управление статусом пользователей
"""

import re
import bcrypt
import psycopg2
from typing import Optional, Tuple, List
from ..database import get_db_connection, release_db_connection


def create_user(username: str, email: str, password: str, secure_hash: bool = True) -> None:
    """
    Регистрирует нового пользователя с хешированным паролем.

    Args:
        username: уникальное имя пользователя
        email: уникальный email (должен содержать @ и домен)
        password: пароль в открытом виде (будет захеширован)
        secure_hash: если True, использует безопасное хеширование с очисткой памяти

    Raises:
        ValueError: если пользователь с таким email или username уже существует,
                    или email некорректен
    """
    # Проверка формата email
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        raise ValueError("Некорректный формат email")

    email = email.lower()  # Приводим к нижнему регистру

    # Безопасное хеширование пароля с очисткой памяти
    password_bytes = None
    hashed = None
    try:
        # Преобразуем строку пароля в bytes для bcrypt
        password_bytes = bytearray(password.encode('utf-8'))
        # bcrypt.hashpw принимает bytes или bytearray, но для надёжности явно конвертируем
        hashed = bcrypt.hashpw(bytes(password_bytes), bcrypt.gensalt()).decode("utf-8")
    finally:
        # Очищаем память от пароля
        if password_bytes is not None:
            for i in range(len(password_bytes)):
                password_bytes[i] = 0
            del password_bytes

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO users (username, email, password_hash)
            VALUES (%s, %s, %s)
            """,
            (username, email, hashed),
        )
        conn.commit()
    except psycopg2.IntegrityError as e:
        conn.rollback()
        raise ValueError("Пользователь с таким email или username уже существует") from e
    finally:
        cur.close()
        release_db_connection(conn)


def get_user_by_email(email: str) -> Optional[Tuple[int, str, str]]:
    """
    Возвращает данные пользователя по email.

    Args:
        email: email пользователя

    Returns:
        Кортеж (user_id, email, password_hash) или None, если не найден
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, email, password_hash FROM users WHERE email = %s",
            (email,),
        )
        return cur.fetchone()
    finally:
        cur.close()
        release_db_connection(conn)


def get_user_by_email_or_username(email_or_username: str) -> Optional[Tuple[int, str, str]]:
    """
    Возвращает данные пользователя по email или username.

    Args:
        email_or_username: email или username пользователя

    Returns:
        Кортеж (user_id, email, username) или None, если не найден
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, email, username FROM users
               WHERE email = %s OR username = %s""",
            (email_or_username, email_or_username),
        )
        return cur.fetchone()
    finally:
        cur.close()
        release_db_connection(conn)


def is_user_admin(user_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором.

    Args:
        user_id: ID пользователя

    Returns:
        True, если is_admin = true, иначе False
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()[0] == True
    finally:
        cur.close()
        release_db_connection(conn)


def ban_user(target_user_id: int) -> bool:
    """
    Блокирует пользователя по ID.

    Args:
        target_user_id: ID пользователя для бана

    Returns:
        True, если пользователь был забанен, False — если не найден
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET is_banned = true WHERE id = %s",
            (target_user_id,),
        )
        updated = cur.rowcount > 0
        conn.commit()
        return updated
    finally:
        cur.close()
        release_db_connection(conn)


def get_user_by_id(user_id: int) -> dict:
    """
    Возвращает данные пользователя по ID.

    Raises:
        ValueError: если пользователь не найден
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, username, email, is_admin, is_banned
            FROM users
            WHERE id = %s
        """, (user_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Пользователь не найден")
        return {
            "id": row[0],
            "username": row[1],
            "email": row[2],
            "is_admin": row[3],
            "is_banned": row[4]
        }
    finally:
        cur.close()
        release_db_connection(conn)

def get_username(user_id: int) -> str:
    """Возвращает username по ID."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else f"Пользователь {user_id}"
    finally:
        cur.close()
        release_db_connection(conn)


def get_username_cached(user_id: int) -> str:
    """
    Возвращает username по ID с кэшированием.

    Args:
        user_id: ID пользователя

    Returns:
        Username или 'Пользователь {user_id}' если не найден
    """
    from ..utils.ws_manager import cache_get, cache_set

    cache_key = f"username:{user_id}"

    # Проверяем кэш
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Получаем из БД
    username = get_username(user_id)

    # Сохраняем в кэш
    cache_set(cache_key, username, ttl=600)

    return username


def get_all_users(exclude_user_id: Optional[int] = None) -> List[dict]:
    """
    Возвращает список всех пользователей (кроме текущего).

    Args:
        exclude_user_id: ID пользователя, которого нужно исключить из списка

    Returns:
        Список словарей с информацией о пользователях
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if exclude_user_id:
            cur.execute("""
                SELECT id, username, email
                FROM users
                WHERE id != %s
                ORDER BY username
            """, (exclude_user_id,))
        else:
            cur.execute("""
                SELECT id, username, email
                FROM users
                ORDER BY username
            """)

        rows = cur.fetchall()
        return [
            {"id": row[0], "username": row[1], "email": row[2]}
            for row in rows
        ]
    finally:
        cur.close()
        release_db_connection(conn)


def search_users(query: str, exclude_user_id: Optional[int] = None) -> List[dict]:
    """
    Ищет пользователей по имени или email.

    Args:
        query: Строка поиска
        exclude_user_id: ID пользователя, которого нужно исключить из результатов

    Returns:
        Список словарей с информацией о найденных пользователях
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        search_pattern = f"%{query}%"

        if exclude_user_id:
            cur.execute("""
                SELECT id, username, email
                FROM users
                WHERE (username ILIKE %s OR email ILIKE %s)
                  AND id != %s
                ORDER BY username
            """, (search_pattern, search_pattern, exclude_user_id))
        else:
            cur.execute("""
                SELECT id, username, email
                FROM users
                WHERE username ILIKE %s OR email ILIKE %s
                ORDER BY username
            """, (search_pattern, search_pattern))

        rows = cur.fetchall()
        return [
            {"id": row[0], "username": row[1], "email": row[2]}
            for row in rows
        ]
    finally:
        cur.close()
        release_db_connection(conn)


def ban_user_with_reason(target_user_id: int, admin_user_id: int, reason: str) -> bool:
    """
    Блокирует пользователя с указанием причины и записью в историю.
    Нельзя банить админов и себя.

    Args:
        target_user_id: ID пользователя для бана
        admin_user_id: ID администратора, выполняющего бан
        reason: Причина бана

    Returns:
        True, если пользователь был забанен, False — если ошибка (например, попытка забанить админа)
    """
    # Нельзя банить себя
    if target_user_id == admin_user_id:
        return False

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что целевой пользователь существует и не является админом
        cur.execute("SELECT is_admin FROM users WHERE id = %s", (target_user_id,))
        row = cur.fetchone()
        if not row or row[0]:  # Не найден или админ
            return False

        # Начинаем транзакцию
        # 1. Устанавливаем флаг бана
        cur.execute("UPDATE users SET is_banned = true WHERE id = %s", (target_user_id,))

        # 2. Создаём запись в таблице bans (бессрочный бан)
        # Используем ON CONFLICT для обновления существующей записи
        cur.execute("""
            INSERT INTO bans (user_id, banned_by, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                banned_by = EXCLUDED.banned_by,
                reason = EXCLUDED.reason,
                created_at = CURRENT_TIMESTAMP
        """, (target_user_id, admin_user_id, reason))

        # 3. Записываем в историю
        cur.execute("""
            INSERT INTO ban_history (user_id, action, performed_by, reason)
            VALUES (%s, 'ban', %s, %s)
        """, (target_user_id, admin_user_id, reason))

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка при бане пользователя {target_user_id}: {e}")
        return False
    finally:
        cur.close()
        release_db_connection(conn)


def unban_user(user_id: int, admin_user_id: int) -> bool:
    """
    Разбанивает пользователя с записью в историю.

    Args:
        user_id: ID пользователя для разбана
        admin_user_id: ID администратора, выполняющего разбан

    Returns:
        True, если успешно, False иначе
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем, что пользователь существует и забанен
        cur.execute("SELECT is_banned FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row or not row[0]:  # Не найден или не забанен
            return False

        # Начинаем транзакцию
        # 1. Снимаем флаг бана
        cur.execute("UPDATE users SET is_banned = false WHERE id = %s", (user_id,))

        # 2. Удаляем запись из таблицы активных банов
        cur.execute("DELETE FROM bans WHERE user_id = %s", (user_id,))

        # 3. Записываем в историю
        cur.execute("""
            INSERT INTO ban_history (user_id, action, performed_by)
            VALUES (%s, 'unban', %s)
        """, (user_id, admin_user_id))

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка при разбане пользователя {user_id}: {e}")
        return False
    finally:
        cur.close()
        release_db_connection(conn)


def get_ban_history(user_id: Optional[int] = None, limit: int = 50) -> List[dict]:
    """
    Получает историю банов для конкретного пользователя или всех пользователей.

    Args:
        user_id: ID пользователя (None для всей истории)
        limit: Максимальное количество записей

    Returns:
        Список записей истории банов
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if user_id:
            cur.execute("""
                SELECT h.history_id, h.user_id, u.username, h.action,
                       h.performed_by, bu.username as performed_by_username,
                       h.reason, h.created_at
                FROM ban_history h
                JOIN users u ON h.user_id = u.id
                LEFT JOIN users bu ON h.performed_by = bu.id
                WHERE h.user_id = %s
                ORDER BY h.created_at DESC
                LIMIT %s
            """, (user_id, limit))
        else:
            cur.execute("""
                SELECT h.history_id, h.user_id, u.username, h.action,
                       h.performed_by, bu.username as performed_by_username,
                       h.reason, h.created_at
                FROM ban_history h
                JOIN users u ON h.user_id = u.id
                LEFT JOIN users bu ON h.performed_by = bu.id
                ORDER BY h.created_at DESC
                LIMIT %s
            """, (limit,))

        rows = cur.fetchall()
        return [
            {
                "history_id": row[0],
                "user_id": row[1],
                "username": row[2],
                "action": row[3],
                "performed_by": row[4],
                "performed_by_username": row[5],
                "reason": row[6],
                "created_at": row[7].isoformat() if row[7] else None
            }
            for row in rows
        ]
    finally:
        cur.close()
        release_db_connection(conn)


def get_active_bans() -> List[dict]:
    """
    Получает список всех активных банов с причинами.

    Returns:
        Список активных банов
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT b.ban_id, b.user_id, u.username, b.banned_by,
                   bu.username as banned_by_username, b.reason,
                   b.created_at
            FROM bans b
            JOIN users u ON b.user_id = u.id
            LEFT JOIN users bu ON b.banned_by = bu.id
            ORDER BY b.created_at DESC
        """)

        rows = cur.fetchall()
        return [
            {
                "ban_id": row[0],
                "user_id": row[1],
                "username": row[2],
                "banned_by": row[3],
                "banned_by_username": row[4],
                "reason": row[5],
                "created_at": row[6].isoformat() if row[6] else None
            }
            for row in rows
        ]
    finally:
        cur.close()
        release_db_connection(conn)