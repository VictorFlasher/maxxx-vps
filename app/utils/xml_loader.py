# app/utils/xml_loader.py
import xml.etree.ElementTree as ET
from defusedxml.ElementTree import parse as defused_parse
import os


def load_mail_config(config_type="incoming"):
    """
    Загружает конфигурацию почты из XML-файла с защитой от XXE атак.
    
    Args:
        config_type: "incoming" или "outgoing"
        
    Returns:
        Словарь с параметрами конфигурации
        
    Raises:
        FileNotFoundError: если файл конфигурации не найден
        ValueError: если указан неверный тип конфигурации
    """
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.xml")
    
    # Используем defusedxml для защиты от XXE атак
    try:
        tree = defused_parse(config_path)
        root = tree.getroot()
    except Exception as e:
        raise FileNotFoundError(f"Не удалось загрузить конфигурацию: {e}")
    
    if config_type == "incoming":
        node = root.find("incoming")
        if node is None:
            raise ValueError("Конфигурация incoming не найдена")
        return {
            "hostname": node.find("hostname").text if node.find("hostname") is not None else "",
            "username": node.find("username").text if node.find("username") is not None else "",
            "port": int(node.find("port").text) if node.find("port") is not None else 993,
            "server": node.find("server").text if node.find("server") is not None else "",
            "ssl": node.find("ssl").text.lower() == "true" if node.find("ssl") is not None else True
        }
    elif config_type == "outgoing":
        node = root.find("outgoing")
        if node is None:
            raise ValueError("Конфигурация outgoing не найдена")
        return {
            "smtp_host": node.find("smtp_host").text if node.find("smtp_host") is not None else "",
            "smtp_user": node.find("smtp_user").text if node.find("smtp_user") is not None else "",
            "smtp_password": node.find("smtp_password").text if node.find("smtp_password") is not None else "",
            "ssl": node.find("ssl").text.lower() == "true" if node.find("ssl") is not None else True
        }
    else:
        raise ValueError(f"Неверный тип конфигурации: {config_type}")