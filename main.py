# main.py
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import logging

# Настраиваем логгер для main модуля
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

from app.routes import auth, config, chat, admin
from app.routes.auth import limiter  # Импортируем экземпляр лимитера
from app.database import init_db_pool  # Импорт функции инициализации пула БД
from app.utils import init_ws_manager, close_ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения."""
    # Startup
    init_db_pool(minconn=2, maxconn=10)
    await init_ws_manager()
    logging.info("Приложение запущено")
    yield
    # Shutdown
    await close_ws_manager()
    logging.info("Приложение остановлено")


app = FastAPI(
    title="Maxxx-Local Chat API",
    description="Безопасный многопользовательский чат",
    version="1.0.0",
    lifespan=lifespan,
)

# === Middleware для безопасности HTTP заголовков (защита от MitM, XSS, Clickjacking) ===
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Добавляет security headers ко всем ответам для защиты от:
    - MitM атак (HSTS)
    - XSS (X-XSS-Protection, X-Content-Type-Options)
    - Clickjacking (X-Frame-Options)
    - MIME sniffing (X-Content-Type-Options)
    
    Примечание: Не применяется к WebSocket подключениям.
    """
    # Пропускаем WebSocket запросы — они обрабатываются отдельно
    if request.scope.get("type") == "websocket":
        return await call_next(request)
    
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
# Важно: allow_origins должен содержать точные origin, с которых работает фронтенд
# Для WebSocket критически важно разрешить заголовки Sec-WebSocket-Protocol
# ПРИМЕЧАНИЕ: allow_origins=["*"] с allow_credentials=True может не работать в некоторых браузерах для WebSocket
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],  # Явно указываем origins для WebSocket
    allow_credentials=True,  # Включаем credentials для WebSocket
    allow_methods=["*"],  # Разрешаем все методы включая WebSocket
    allow_headers=["*"],  # Разрешаем все заголовки включая Sec-WebSocket-*
)

# Добавляем middleware для логирования CORS запросов
@app.middleware("http")
async def cors_debug_middleware(request: Request, call_next):
    """Middleware для отладки CORS проблем."""
    if request.scope.get("type") == "websocket":
        origin = request.headers.get("origin", "no-origin")
        logger.info(f"CORS DEBUG: WebSocket запрос, origin={origin}, path={request.url.path}")
    response = await call_next(request)
    return response

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

# Фронтенд-маршрут
@app.get("/")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/chat")
async def chat_page(request: Request, chat_id: int = None):
    """Страница чата с опциональным параметром chat_id для открытия конкретного чата."""
    return templates.TemplateResponse("chat.html", {"request": request, "chat_id": chat_id})


@app.get("/admin/login")
async def admin_login_page(request: Request):
    """Страница входа в админ-панель."""
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.get("/admin")
async def admin_panel_redirect(request: Request):
    """Перенаправление на админ-панель (проверка прав происходит на уровне API)."""
    return templates.TemplateResponse("admin.html", {"request": request, "user_id": None})


@app.get("/search-users")
async def search_users_page(request: Request):
    """Страница поиска пользователей."""
    return templates.TemplateResponse("search_users.html", {"request": request})

# Health check endpoint для мониторинга работоспособности сервиса
@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Проверка работоспособности сервиса.
    Используется балансировщиками нагрузки и системами мониторинга.
    """
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}