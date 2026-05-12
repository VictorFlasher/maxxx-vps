"""
Маршрут для получения параметров почтового сервера из XML-конфигурации.
"""

from fastapi import APIRouter
from ..utils.xml_loader import load_mail_config

router = APIRouter()


@router.get("/config/{config_type}", summary="Получить конфигурацию почты")
def get_config(config_type: str):
    """
    Возвращает настройки почтового сервера из config.xml.

    Args:
        config_type: "incoming" или "outgoing"

    Returns:
        Словарь с параметрами (hostname, port, ssl и т.д.)
    """
    return load_mail_config(config_type)