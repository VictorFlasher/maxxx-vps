"""
Административные маршруты: бан пользователей, просмотр и обработка жалоб.
Требуется авторизация и права администратора.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from ..models.user import (
    is_user_admin, 
    ban_user_with_reason, 
    unban_user, 
    get_ban_history, 
    get_active_bans,
    get_db_connection, 
    release_db_connection,
    get_user_by_id
)
from .auth import get_current_user_from_header


router = APIRouter()
templates = Jinja2Templates(directory="templates")


class BanUserRequest(BaseModel):
    """Запрос на блокировку пользователя с причиной."""
    user_id: int
    reason: str


class UnbanUserRequest(BaseModel):
    """Запрос на разблокировку пользователя."""
    user_id: int


class ReviewReportRequest(BaseModel):
    """Запрос на обработку жалобы."""
    report_id: int
    action: str  # 'ban', 'dismiss'
    ban_reason: Optional[str] = None  # Причина бана, если action='ban'


@router.get("/admin", summary="Админ-панель (HTML)")
async def admin_panel(request: Request, current_user_id: int = Depends(get_current_user_from_header)):
    """
    HTML страница админ-панели. Доступна только администраторам.
    """
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    
    return templates.TemplateResponse("admin.html", {"request": request, "user_id": current_user_id})


@router.post("/admin/ban", summary="Забанить пользователя с причиной")
def ban_user_endpoint(
    request: BanUserRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Блокирует пользователя с указанием причины. Доступно только администраторам.
    
    - Нельзя забанить самого себя
    - Нельзя забанить админа
    - Причина бана обязательна и записывается в историю
    """
    # Проверка прав администратора
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")

    target_id = request.user_id
    reason = request.reason.strip()
    
    if not reason:
        raise HTTPException(status_code=400, detail="Причина бана обязательна")

    # Выполнение бана с причиной
    if not ban_user_with_reason(target_id, current_user_id, reason):
        # Проверяем, почему не удалось
        try:
            target_user = get_user_by_id(target_id)
            if target_user["is_admin"]:
                raise HTTPException(status_code=403, detail="Нельзя забанить администратора")
        except ValueError:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        raise HTTPException(status_code=400, detail="Не удалось забанить пользователя")

    return {"status": "success", "banned_user_id": target_id, "reason": reason}


@router.post("/admin/unban", summary="Разбанить пользователя")
def unban_user_endpoint(
    request: UnbanUserRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Разблокирует пользователя. Доступно только администраторам.
    Запись о разбане сохраняется в истории.
    """
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")

    target_id = request.user_id
    
    if not unban_user(target_id, current_user_id):
        raise HTTPException(status_code=400, detail="Не удалось разбанить (пользователь не найден или не забанен)")

    return {"status": "success", "unbanned_user_id": target_id}


@router.get("/admin/bans", summary="Получить список активных банов")
def get_bans_list(
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Получает список всех активных банов с причинами.
    """
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    
    return get_active_bans()


@router.get("/admin/ban-history", summary="История банов")
def get_history(
    user_id: Optional[int] = None,
    limit: int = 50,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Получает историю банов для конкретного пользователя или всех пользователей.
    """
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    
    return get_ban_history(user_id=user_id, limit=limit)


@router.get("/admin/reports", summary="Получить список жалоб")
def get_reports(
    status: Optional[str] = "pending",
    limit: int = 50,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Получает список жалоб для модерации.
    
    - status: фильтр по статусу ('pending', 'reviewed', 'resolved')
    - limit: максимальное количество записей
    """
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT 
                r.report_id, r.message_id, r.reporter_id, r.reason, 
                r.status, r.created_at, r.reviewed_by, r.reviewed_at,
                m.text as message_text, m.sender_id, m.chat_id
            FROM message_reports r
            JOIN messages m ON r.message_id = m.message_id
        """
        params = []
        
        if status:
            query += " WHERE r.status = %s"
            params.append(status)
        
        query += " ORDER BY r.created_at DESC LIMIT %s"
        params.append(limit)
        
        cur.execute(query, params)
        rows = cur.fetchall()
        
        reports = []
        for row in rows:
            reports.append({
                "report_id": row[0],
                "message_id": row[1],
                "reporter_id": row[2],
                "reason": row[3],
                "status": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
                "reviewed_by": row[6],
                "reviewed_at": row[7].isoformat() if row[7] else None,
                "message_text": row[8],
                "sender_id": row[9],
                "chat_id": row[10]
            })
        
        return reports
    finally:
        cur.close()
        release_db_connection(conn)


@router.post("/admin/reports/review", summary="Обработать жалобу")
def review_report(
    request: ReviewReportRequest,
    current_user_id: int = Depends(get_current_user_from_header),
):
    """
    Обрабатывает жалобу: банит автора или отклоняет жалобу.
    
    Действия:
    - ban: забанить автора сообщения (требуется ban_reason)
    - dismiss: отклонить жалобу (снять метку pending)
    """
    if not is_user_admin(current_user_id):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Проверяем существование жалобы и получаем данные
        cur.execute("""
            SELECT r.message_id, m.sender_id 
            FROM message_reports r
            JOIN messages m ON r.message_id = m.message_id
            WHERE r.report_id = %s
        """, (request.report_id,))
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Жалоба не найдена")
        
        message_id, sender_id = row
        
        if request.action == 'ban':
            # Бан с причиной
            if not request.ban_reason or not request.ban_reason.strip():
                raise HTTPException(status_code=400, detail="Причина бана обязательна")
            
            # Проверяем, что sender_id не админ
            cur.execute("SELECT role FROM users WHERE id = %s", (sender_id,))
            admin_row = cur.fetchone()
            if admin_row and admin_row[0] == 'admin':
                raise HTTPException(status_code=403, detail="Нельзя забанить администратора")
            
            # Бан через нашу функцию с историей
            ban_user_with_reason(sender_id, current_user_id, request.ban_reason.strip())
            
            # Обновляем статус жалобы
            cur.execute("""
                UPDATE message_reports 
                SET status = 'actioned', reviewed_by = %s, reviewed_at = NOW()
                WHERE report_id = %s
            """, (current_user_id, request.report_id))
            
        elif request.action == 'dismiss':
            # Отклоняем жалобу
            cur.execute("""
                UPDATE message_reports 
                SET status = 'dismissed', reviewed_by = %s, reviewed_at = NOW()
                WHERE report_id = %s
            """, (current_user_id, request.report_id))
        else:
            raise HTTPException(status_code=400, detail="Неверное действие")
        
        conn.commit()
        return {"status": "success", "action": request.action}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {str(e)}")
    finally:
        cur.close()
        release_db_connection(conn)