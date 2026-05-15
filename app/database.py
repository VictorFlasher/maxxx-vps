import os
import psycopg2
from psycopg2 import pool, sql
from typing import Optional
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# === Конфигурация подключения ===
# Параметры подключения берутся из переменных окружения или используются значения по умолчанию
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

# Имя схемы берётся из переменной окружения
SCHEMA_NAME = os.getenv("DB_SCHEMA")


def get_schema_name() -> str:
    """
    Возвращает имя текущей схемы для использования в запросах.
    
    Returns:
        str: Имя схемы
    """
    return SCHEMA_NAME


# === Пул соединений ===
# Глобальный пул соединений для производительности
db_pool: Optional[pool.ThreadedConnectionPool] = None


def init_db_pool(minconn: int = 5, maxconn: int = 50):
    """
    Инициализирует пул соединений с базой данных.
    
    Args:
        minconn: Минимальное количество соединений в пуле
        maxconn: Максимальное количество соединений в пуле
    """
    global db_pool
    try:
        # Добавляем options для установки search_path при создании каждого соединения
        pool_config = DB_CONFIG.copy()
        pool_config["options"] = f"-c search_path={SCHEMA_NAME}"
        
        db_pool = pool.ThreadedConnectionPool(
            minconn=minconn,
            maxconn=maxconn,
            **pool_config
        )
        logger.info(f"Пул соединений к БД инициализирован (min={minconn}, max={maxconn})")
    except Exception as e:
        logger.error(f"Ошибка инициализации пула соединений: {e}")
        raise


@contextmanager
def get_db_connection():
    """
    Контекстный менеджер для получения соединения из пула.
    Автоматически возвращает соединение в пул после использования.
    Search_path устанавливается при создании соединения через options.
    
    Yields:
        psycopg2.connection: активное соединение с БД
    
    Raises:
        RuntimeError: если не удаётся получить соединение с базой данных
    """
    conn = None
    if db_pool is not None:
        try:
            conn = db_pool.getconn()
            yield conn
        except Exception as e:
            logger.error(f"Ошибка работы с соединением из пула: {e}")
            if conn:
                conn.rollback()
            raise RuntimeError(f"Не удалось получить соединение из пула: {e}") from e
        finally:
            if conn:
                try:
                    db_pool.putconn(conn)
                except Exception as e:
                    logger.error(f"Ошибка возврата соединения в пул: {e}")
    else:
        # Fallback: создаём новое соединение (для тестов или если пул не инициализирован)
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            yield conn
        except Exception as e:
            raise RuntimeError(f"Не удалось подключиться к базе данных: {e}") from e
        finally:
            if conn:
                conn.close()