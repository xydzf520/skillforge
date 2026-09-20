"""资源配额与使用量服务。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.models import ResourceQuota
from app.common.exceptions import AppError
from app.common.models import UsageLog
from app.execution.models import ExecutionRun
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt


def _period_start(period: str) -> datetime:
    now = now_bjt()
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _serialize_quota(item: ResourceQuota) -> dict:
    return {
        "id": item.id,
        "org_unit_id": item.org_unit_id,
        "resource_type": item.resource_type,
        "period": item.period,
        "quota_limit": item.quota_limit,
        "burst_limit": item.burst_limit,
        "enabled": item.enabled,
        "created_at": isoformat_bjt(item.created_at),
        "updated_at": isoformat_bjt(item.updated_at),
    }


async def list_quotas(db: AsyncSession, *, org_unit_id: str | None = None, resource_type: str | None = None) -> dict:
    stmt = select(ResourceQuota).order_by(ResourceQuota.org_unit_id, ResourceQuota.resource_type)
    if org_unit_id:
        stmt = stmt.where(ResourceQuota.org_unit_id == org_unit_id)
    if resource_type:
        stmt = stmt.where(ResourceQuota.resource_type == resource_type)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize_quota(row) for row in rows], "total": len(rows)}


async def create_quota(
    db: AsyncSession,
    *,
    org_unit_id: str,
    resource_type: str,
    period: str,
    quota_limit: int,
    burst_limit: int | None,
    enabled: bool = True,
) -> dict:
    exists = (
        await db.execute(
            select(ResourceQuota).where(
                ResourceQuota.org_unit_id == org_unit_id,
                ResourceQuota.resource_type == resource_type,
                ResourceQuota.period == period,
            )
        )
    ).scalars().first()
    if exists:
        raise AppError("PARAM_INVALID", 409, {"detail": "quota 已存在"})
    item = ResourceQuota(
        id=f"rq-{uuid4().hex[:12]}",
        org_unit_id=org_unit_id,
        resource_type=resource_type,
        period=period,
        quota_limit=quota_limit,
        burst_limit=burst_limit,
        enabled=enabled,
    )
    db.add(item)
    await db.flush()
    return _serialize_quota(item)


async def update_quota(
    db: AsyncSession,
    *,
    quota_id: str,
    quota_limit: int | None = None,
    burst_limit: int | None = None,
    enabled: bool | None = None,
) -> dict:
    item = await db.get(ResourceQuota, quota_id)
    if not item:
        raise AppError("NOT_FOUND", 404, {"resource": "resource_quota", "id": quota_id})
    if quota_limit is not None:
        item.quota_limit = quota_limit
    if burst_limit is not None:
        item.burst_limit = burst_limit
    if enabled is not None:
        item.enabled = enabled
    item.updated_at = now_bjt()
    await db.flush()
    return _serialize_quota(item)


async def _usage_for_quota(db: AsyncSession, quota: ResourceQuota) -> int:
    since = _period_start(quota.period)
    if quota.resource_type == "llm_tokens":
        stmt = select(func.coalesce(func.sum(UsageLog.input_tokens + UsageLog.output_tokens), 0)).where(
            UsageLog.department == quota.org_unit_id,
            UsageLog.ts >= since,
        )
        return int((await db.execute(stmt)).scalar() or 0)
    if quota.resource_type == "executions":
        stmt = (
            select(func.count(ExecutionRun.id))
            .join(Skill, Skill.id == ExecutionRun.playbook_id, isouter=True)
        )
        # current schema lacks org on run; use decision log via skill table indirectly unavailable here, fallback to skill ownership by org
        stmt = (
            select(func.count(ExecutionRun.id))
            .select_from(ExecutionRun)
            .join(
                __import__("app.execution.models", fromlist=["DecisionLog"]).DecisionLog,
                __import__("app.execution.models", fromlist=["DecisionLog"]).DecisionLog.run_id == ExecutionRun.id,
            )
            .join(Skill, Skill.id == __import__("app.execution.models", fromlist=["DecisionLog"]).DecisionLog.skill_id)
            .where(Skill.org_unit_id == quota.org_unit_id, ExecutionRun.started_at >= since)
        )
        return int((await db.execute(stmt)).scalar() or 0)
    if quota.resource_type == "browser_minutes":
        return 0
    return 0


async def get_quota_usage(db: AsyncSession, *, org_unit_id: str | None = None) -> dict:
    stmt = select(ResourceQuota).where(ResourceQuota.enabled == True)  # noqa: E712
    if org_unit_id:
        stmt = stmt.where(ResourceQuota.org_unit_id == org_unit_id)
    quotas = (await db.execute(stmt)).scalars().all()
    items = []
    for quota in quotas:
        used = await _usage_for_quota(db, quota)
        limit_value = quota.quota_limit or 0
        usage_pct = round((used / limit_value) * 100, 2) if limit_value else 0
        alert_level = "normal"
        if usage_pct >= 100:
            alert_level = "throttled"
        elif usage_pct >= 80:
            alert_level = "warning"
        items.append(
            {
                **_serialize_quota(quota),
                "used": used,
                "usage_pct": usage_pct,
                "alert_level": alert_level,
                "throttled": usage_pct >= 100,
            }
        )
    return {"items": items, "total": len(items)}
