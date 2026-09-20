"""Skill 大厅路由：跨部门浏览、发现、筛选"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.database import get_db
from app.skills import hall_service

router = APIRouter()


@router.get("/hall")
async def skill_hall(
    q: str | None = Query(None, description="搜索关键词"),
    department: str | None = Query(None, description="按部门筛选"),
    category: str | None = Query(None, description="按分类筛选"),
    status: str | None = Query(None, description="active/draft/all"),
    sort_by: str = Query("popularity", description="popularity/updated_at/name/health"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    include: str | None = Query(None, description="逗号分隔：profile 等扩展字段"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """Skill 大厅：跨部门浏览可见的所有 Skill。

    v2.7：?include=profile 附加 adoption_departments / data_sources / 等画像字段（5min Redis 缓存）。
    """
    include_list = [x.strip() for x in (include or "").split(",") if x.strip()] or None
    return await hall_service.list_hall_skills(
        db,
        current_user,
        q=q,
        department=department,
        category=category,
        status=status,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
        include=include_list,
    )


@router.get("/hall/stats")
async def skill_hall_stats(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """大厅统计摘要"""
    return await hall_service.get_hall_stats(db, current_user)


@router.get("/hall/filters")
async def skill_hall_filters(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """大厅筛选器选项（部门列表 + 分类列表）"""
    departments = await hall_service.get_departments_list(db, current_user)
    categories = await hall_service.get_categories_list(db, current_user)
    return {
        "departments": departments,
        "categories": categories,
    }
