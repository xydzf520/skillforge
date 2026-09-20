"""Inbox reports router."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.common.cache import cached
from app.config import settings
from app.database import get_db
from app.todos.overview_service import get_overview
from app.todos.schemas import InboxOverviewResponse

from .schemas import InboxReportDetailResponse, InboxReportListResponse
from . import report_designs
from .service import inbox_service
from .user_prefs_service import get_unread_count, mark_reports_read

router = APIRouter()


class MarkReportsReadRequest(BaseModel):
    until: datetime | None = Field(None, description="标记到指定时刻止，缺省为当前时间")

_CACHE_KEY_EXCLUDES = frozenset(
    {"db", "session", "request", "background_tasks", "current_user"}
)


def _detail_cache_key(*args, **kwargs) -> str:
    card_id = kwargs.get("card_id")
    if card_id is None and args:
        card_id = args[0]
    return f"{card_id}:{int(bool(kwargs.get('include_debug')))}"


@router.get("/reports", response_model=InboxReportListResponse)
@cached(
    "inbox:reports:list",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    skill_id: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    tag: str | None = Query(None),
    channel: str | None = Query(None),
    trigger_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await inbox_service.list_reports(
        db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        skill_id=skill_id,
        date_from=date_from,
        date_to=date_to,
        tag=tag,
        channel=channel,
        trigger_type=trigger_type,
    )


@router.get("/overview", response_model=InboxOverviewResponse)
@cached(
    "inbox:overview",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    """GAP-2：收件中心 KPI + SLA 达成率 + 积压分桶 + 近 7 天聚合。"""
    return await get_overview(db, current_user=current_user)


# GAP-5：服务端未读红点，不缓存（要实时反映）
# 注意：必须在 /reports/{card_id} 之前注册，否则会被当成 card_id 参数
@router.get("/reports/unread-count")
async def reports_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await get_unread_count(db, current_user=current_user)


# GAP-6：mark-read，写入 user_inbox_preferences + 审计
@router.post("/reports/mark-read")
async def reports_mark_read(
    body: MarkReportsReadRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    ip_address = request.client.host if request.client else None
    return await mark_reports_read(
        db,
        current_user=current_user,
        until=body.until,
        ip_address=ip_address,
    )


@router.get("/report-design-templates")
async def list_report_design_templates(
    q: str | None = Query(None),
    source_type: str | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_state_active),
):
    return report_designs.list_report_design_templates(
        q=q,
        source_type=source_type,
        category=category,
        page=page,
        page_size=page_size,
    )


@router.get("/report-design-templates/{template_id}")
async def get_report_design_template(
    template_id: str,
    current_user: User = Depends(require_state_active),
):
    return report_designs.get_report_design_template(template_id, include_standard=True)


# /reports/{card_id} 必须放最后：它会吃任何未命中的 /reports/* 路径
@router.get("/reports/{card_id}", response_model=InboxReportDetailResponse)
@cached(
    "inbox:reports:detail",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=("current_user.id",),
    key_builder=_detail_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_report_detail(
    card_id: str,
    include_debug: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_state_active),
):
    return await inbox_service.get_report_detail(
        db,
        card_id=card_id,
        current_user=current_user,
        include_debug=include_debug,
    )
