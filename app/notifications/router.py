"""通知中心 API 路由"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.database import get_db
from app.notifications import service

router = APIRouter()


@router.get("/")
async def list_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的通知列表"""
    return await service.list_notifications(db, current_user.id, unread_only, page, page_size)


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """标记单条通知已读"""
    await service.mark_read(db, notification_id, current_user.id)
    return {"read": True}


@router.post("/read-all")
async def mark_all_read(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """标记全部已读"""
    count = await service.mark_all_read(db, current_user.id)
    return {"marked": count}
