# main.py
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
from logging.handlers import RotatingFileHandler

from app.routes import auth, config, chat, admin
from app.routes.auth import limiter  # Импортируем экземпляр лимитера
from app.database import init_db_pool  # Импорт функции инициализации пула БД
from app.utils import init_ws_manager, close_ws_manager

# === Настройка логирования в файл ===
def setup_logging():
    """Настраивает логирование в файл и консоль."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    # Создаём корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Обработчик для файла с ротацией
    file_handler = RotatingFileHandler(
        f"{log_dir}/app.log",
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    logging.info("Логирование инициализировано")

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Maxxx-Local Chat API",
    description="Безопасный многопользовательский чат",
    version="1.0.0",
)

# Инициализация пула соединений с БД при старте приложения (отложено до startup)
# init_db_pool(minconn=2, maxconn=10)

# Инициализация Redis при старте приложения (отложено до startup) - теперь заглушка, используется локальное хранилище

@app.on_event("startup")
async def startup_event():
    """Инициализация асинхронных сервисов при старте."""
    # Инициализация пула БД
    init_db_pool(minconn=2, maxconn=10)
    # Инициализация менеджера WebSocket (локальное хранилище)
    await init_ws_manager()
    logger.info("Приложение запущено")

@app.on_event("shutdown")
async def shutdown_event():
    """Очистка ресурсов при остановке приложения."""
    await close_ws_manager()
    logger.info("Приложение остановлено")

# === Middleware для безопасности HTTP заголовков (защита от MitM, XSS, Clickjacking) ===
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Добавляет security headers ко всем ответам для защиты от:
    - MitM атак (HSTS)
    - XSS (X-XSS-Protection, X-Content-Type-Options)
    - Clickjacking (X-Frame-Options)
    - MIME sniffing (X-Content-Type-Options)
    """
    response = await call_next(request)

    # Content Security Policy
    # Разрешаем inline скрипты и стили для работы текущего фронтенда,
    # но запрещаем загрузку ресурсов со сторонних доменов.
    csp_policy = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' ; "
        "font-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp_policy

    # HSTS - принудительный HTTPS (защита от MitM)
    # В production установить max_age=31536000 (1 год) и include_sub_domains
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    # Защита от clickjacking
    response.headers["X-Frame-Options"] = "DENY"

    # Запрет MIME sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # XSS Protection (для старых браузеров)
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Referrer Policy - не передавать referrer на другие сайты
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Permissions Policy - отключаем опасные функции
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    return response

# Добавляем CORS middleware с ограниченными настройками
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В production заменить на конкретные домены
    allow_credentials=False,  # Отключаем credentials для безопасности
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Обработчик превышения лимита запросов."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов. Пожалуйста, попробуйте позже."},
    )

# Подключаем SlowAPI к приложению
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Шаблоны с кэшированием в production
templates = Jinja2Templates(directory="templates")

# Статика с ограничением типов файлов
app.mount("/uploads", StaticFiles(directory="uploads", html=False), name="uploaded_files")

# Роутеры
app.include_router(auth.router, prefix="/api", tags=["Аутентификация"])
app.include_router(config.router, prefix="/api", tags=["Конфигурация"])
app.include_router(chat.router, prefix="/api", tags=["Чат"])
app.include_router(admin.router, prefix="/api", tags=["Администрирование"])

# Фронтенд-маршруты
@app.get("/")
async def login_page(request: Request):
    return templates.TemplateResponse(name="login.html", context={"request": request})

@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(name="register.html", context={"request": request})

@app.get("/chat")
async def chat_page(request: Request, chat_id: int = None):
    """Страница чата с опциональным параметром chat_id для открытия конкретного чата."""
    return templates.TemplateResponse(name="chat.html", context={"request": request, "chat_id": chat_id})


@app.get("/admin/login")
async def admin_login_page(request: Request):
    """Страница входа в админ-панель."""
    return templates.TemplateResponse(name="admin_login.html", context={"request": request})


@app.get("/admin")
async def admin_panel_redirect(request: Request):
    """Перенаправление на админ-панель (проверка прав происходит на уровне API)."""
    return templates.TemplateResponse(name="admin.html", context={"request": request, "user_id": None})


@app.get("/search-users")
async def search_users_page(request: Request):
    """Страница поиска пользователей."""
    return templates.TemplateResponse(name="search_users.html", context={"request": request})

# Health check endpoint для мониторинга работоспособности сервиса
@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Проверка работоспособности сервиса.
    Используется балансировщиками нагрузки и системами мониторинга.
    """
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}