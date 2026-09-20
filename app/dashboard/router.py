"""效果看板 + 审计日志 API路由"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import Integer, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.common.cache import invalidate_dashboard
from app.common.cache_facade import NamespaceCache
from app.config import settings
from app.database import get_db
from app.dashboard import service
from app.datasources import service as ds_service
from app.execution.models import ContractDrift
from app.platform_settings import SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON, get_platform_security_settings
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime

router = APIRouter()

OVERVIEW_CACHE = NamespaceCache("dashboard:overview", ttl=settings.CACHE_TTL_DASHBOARD)
ADOPTION_CACHE = NamespaceCache("dashboard:adoption", ttl=settings.CACHE_TTL_DASHBOARD)
TRENDS_CACHE = NamespaceCache("dashboard:trends", ttl=settings.CACHE_TTL_DASHBOARD)
IMPACT_CACHE = NamespaceCache("dashboard:impact", ttl=settings.CACHE_TTL_DASHBOARD)
DATA_HEALTH_CACHE = NamespaceCache("dashboard:data_health", ttl=settings.CACHE_TTL_DASHBOARD)
WEEKLY_CACHE = NamespaceCache("dashboard:weekly", ttl=settings.CACHE_TTL_DASHBOARD)
GOVERNANCE_CACHE = NamespaceCache("dashboard:governance", ttl=settings.CACHE_TTL_DASHBOARD)
HEALTH_CACHE = NamespaceCache("dashboard:health", ttl=settings.CACHE_TTL_DASHBOARD)
CHANGE_HEATMAP_CACHE = NamespaceCache("dashboard:change_heatmap", ttl=settings.CACHE_TTL_DASHBOARD)
COSTS_CACHE = NamespaceCache("dashboard:costs", ttl=60)
CONTRACT_DRIFT_CACHE = NamespaceCache("dashboard:contract_drift", ttl=60)


def _user_cache_scope(user: User) -> str:
    return (
        f"user={user.id}|role={user.role}|dept={user.department}|"
        f"view_all={bool(user.can_view_all)}"
    )


# v2.8.2 D4：Skill 创建漏斗 —— 直接读 telemetry 内存快照（测试 /
# 本地观测够用；prom 端另走 /metrics）
@router.get("/skill-creation-funnel")
async def skill_creation_funnel(
    current_user: User = Depends(require_state_active),
):
    """返回 Skill 创建漏斗各步骤的累计计数 + 按 source 分组 + 转化率估算。

    response 格式：
    ```
    {
      "steps": {"describe": 120, "interview": 100, "synthesize": 80, "preview": 75, "commit": 60, "abort": 45},
      "by_source": {"architect": {...}, "swarm": {...}, "fork": {...}},
      "conversion_rate": {"describe_to_commit": 0.5, "synthesize_to_commit": 0.75}
    }
    ```
    """
    from app.common.telemetry import get_memory_snapshot

    snap = get_memory_snapshot()
    steps_total: dict[str, int] = {}
    by_source: dict[str, dict[str, int]] = {}
    for k, v in (snap.get("counters") or {}).items():
        if not k.startswith("creation_step:"):
            continue
        parts = k.split(":")
        if len(parts) < 3:
            continue
        _, step, source = parts[0], parts[1], ":".join(parts[2:])
        steps_total[step] = steps_total.get(step, 0) + int(v)
        by_source.setdefault(source, {})
        by_source[source][step] = by_source[source].get(step, 0) + int(v)

    def _ratio(num: int, den: int) -> float:
        return round(num / den, 3) if den else 0.0

    commit_count = steps_total.get("commit", 0)
    conversion = {
        "describe_to_commit": _ratio(commit_count, steps_total.get("describe", 0)),
        "synthesize_to_commit": _ratio(commit_count, steps_total.get("synthesize", 0)),
        "abort_rate": _ratio(
            steps_total.get("abort", 0),
            max(steps_total.get("describe", 0), 1),
        ),
    }

    return {
        "steps": steps_total,
        "by_source": by_source,
        "conversion_rate": conversion,
    }


# ===== 效果看板 =====

@router.get("/overview")
async def overview(
    days: int = Query(30, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """运行总览（带缓存）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await OVERVIEW_CACHE.get(days, department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.get_overview(db, days, department=department)

    try:
        await OVERVIEW_CACHE.set(days, department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/adoption")
async def adoption(
    days: int = Query(30, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """采纳率统计（带缓存）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await ADOPTION_CACHE.get(days, department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.get_adoption(db, days, department=department)

    try:
        await ADOPTION_CACHE.set(days, department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/trends")
async def trends(
    days: int = Query(30, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """每日趋势数据（执行次数 + 采纳次数，带缓存）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await TRENDS_CACHE.get(days, department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.get_trends(db, days, department=department)

    try:
        await TRENDS_CACHE.set(days, department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/impact")
async def impact(
    days: int = Query(30, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """业务影响统计（带缓存）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await IMPACT_CACHE.get(days, department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.get_impact(db, days, department=department)

    try:
        await IMPACT_CACHE.set(days, department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/data-health")
async def data_health(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据源健康状态总览（部门隔离，带缓存）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await DATA_HEALTH_CACHE.get(department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    sources_result = await ds_service.list_sources(db, department=department, page=1, page_size=500)
    sources = sources_result.get("items", [])
    from datetime import datetime, timedelta
    health = []
    for s in sources:
        last = s.get("last_updated")
        threshold = s.get("stale_threshold_hours", 24)
        is_stale = False
        hours_since = None
        if last:
            last_dt = parse_bjt_datetime(last)
            hours_since = round((now_bjt() - last_dt).total_seconds() / 3600, 1)
            is_stale = hours_since > threshold
        health.append({
            "id": s["id"],
            "name": s["name"],
            "department": s.get("department"),
            "is_stale": is_stale,
            "hours_since_update": hours_since,
            "threshold_hours": threshold,
        })
    result = {
        "total": len(health),
        "stale_count": sum(1 for h in health if h["is_stale"]),
        "sources": health,
    }

    try:
        await DATA_HEALTH_CACHE.set(department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


@router.get("/weekly-report")
async def weekly_report(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取本周汇总报告（带缓存）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await WEEKLY_CACHE.get(department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.generate_weekly_report(db, department=department)

    try:
        await WEEKLY_CACHE.set(department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


# ===== P2: 治理概览 =====

@router.get("/governance")
async def governance_overview(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """治理概览：审批效率、成本归因、数据治理、Skill 健康、近期事件"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department

    try:
        cached_val = await GOVERNANCE_CACHE.get(department or 'all')
        if cached_val is not None:
            return cached_val
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    result = await service.get_governance_overview(db, department=department)

    try:
        await GOVERNANCE_CACHE.set(department or 'all', value=result)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    return result


# ===== P3: 健康排行榜 =====

@router.get("/health")
async def health_ranking(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """全局 Skill 健康排行榜"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department
    scope = _user_cache_scope(current_user)
    cached_val = await HEALTH_CACHE.get(scope, department or "all")
    if cached_val is not None:
        return cached_val
    result = await service.get_health_ranking(db, department=department)
    await HEALTH_CACHE.set(scope, department or "all", value=result)
    return result


# ===== P3: 变更频率热力图 =====

@router.get("/change-heatmap")
async def change_heatmap(
    days: int = Query(30, le=365),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """变更频率热力图数据（部门隔离）"""
    department = None
    if current_user.role not in ("admin", "ai_engineer") and not current_user.can_view_all:
        department = current_user.department
    scope = _user_cache_scope(current_user)
    cached_val = await CHANGE_HEATMAP_CACHE.get(scope, days, department or "all")
    if cached_val is not None:
        return cached_val
    result = await service.get_change_heatmap(db, days=days, department=department)
    await CHANGE_HEATMAP_CACHE.set(scope, days, department or "all", value=result)
    return result


# 审计日志已迁移到独立路由 /api/audit/（app/audit/router.py）
# 保留此注释以说明变更历史


# ===== F4: AI 成本看板 =====

@router.get("/costs/today")
async def costs_today(
    current_user: User = Depends(require_state_active),
):
    """今日 LLM 成本总览：总调用 / 总成本 / token 用量 / 按模型 + call_source 分桶"""
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from datetime import datetime, timedelta
    from app.common.cost_tracker import cost_tracker

    today = now_bjt().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    cached_val = await COSTS_CACHE.get("today", today.date().isoformat(), current_user.role)
    if cached_val is not None:
        return cached_val
    result = await cost_tracker.summary(date_from=today, date_to=tomorrow)
    await COSTS_CACHE.set("today", today.date().isoformat(), current_user.role, value=result)
    return result


@router.get("/costs/summary")
async def costs_summary(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_state_active),
):
    """指定时间范围内的 LLM 成本聚合"""
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from datetime import datetime, timedelta
    from app.common.cost_tracker import cost_tracker

    date_from = now_bjt() - timedelta(days=days)
    cached_val = await COSTS_CACHE.get("summary", days, current_user.role)
    if cached_val is not None:
        return cached_val
    result = await cost_tracker.summary(date_from=date_from)
    await COSTS_CACHE.set("summary", days, current_user.role, value=result)
    return result


@router.get("/costs/top-skills")
async def costs_top_skills(
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(require_state_active),
):
    """按总成本排序的 Top N Skill"""
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from app.common.cost_tracker import cost_tracker
    cached_val = await COSTS_CACHE.get("top-skills", days, limit, current_user.role)
    if cached_val is not None:
        return cached_val
    items = await cost_tracker.top_skills(limit=limit, days=days)
    result = {"items": items, "days": days}
    await COSTS_CACHE.set("top-skills", days, limit, current_user.role, value=result)
    return result


@router.get("/costs/top-users")
async def costs_top_users(
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(require_state_active),
):
    """按总成本排序的 Top N 用户（仅 admin 可见，避免泄露其他人的消耗）"""
    if not role_matches_any(current_user, ("admin",)):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from app.common.cost_tracker import cost_tracker
    cached_val = await COSTS_CACHE.get("top-users", days, limit, current_user.role)
    if cached_val is not None:
        return cached_val
    items = await cost_tracker.top_users(limit=limit, days=days)
    result = {"items": items, "days": days}
    await COSTS_CACHE.set("top-users", days, limit, current_user.role, value=result)
    return result


@router.get("/costs/by-day")
async def costs_by_day(
    days: int = Query(14, ge=1, le=90),
    current_user: User = Depends(require_state_active),
):
    """最近 N 天的每日成本曲线（用于 ECharts 折线）"""
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from app.common.cost_tracker import cost_tracker
    cached_val = await COSTS_CACHE.get("by-day", days, current_user.role)
    if cached_val is not None:
        return cached_val
    items = await cost_tracker.daily_costs(days=days)
    result = {"items": items, "days": days}
    await COSTS_CACHE.set("by-day", days, current_user.role, value=result)
    return result


@router.get("/costs/report")
async def costs_report(
    days: int = Query(7, ge=1, le=365),
    offset_days: int = Query(0, ge=0, le=365, description="向前偏移的天数，用于拉取上一个周期"),
    department: str | None = Query(None),
    model: str | None = Query(None),
    call_source: str | None = Query(None),
    group_by: str = Query("user_id"),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_state_active),
):
    """详细成本报表（admin 专用）。

    支持按 user_id / department / skill_id / model / call_source 分组，
    可叠加 department / model / call_source 过滤。
    offset_days 向前偏移：offset_days=days 时拉到上一个周期（用于环比精准对比）。

    group_by 必须在白名单内（Codex MEDIUM 修复：fail fast）。
    """
    if not role_matches_any(current_user, ("admin",)):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from datetime import datetime, timedelta
    from app.common.cost_tracker import cost_tracker, CostTracker
    from app.common.exceptions import AppError

    if group_by not in CostTracker.REPORT_GROUP_BY_FIELDS:
        raise AppError(
            "INVALID_GROUP_BY",
            422,
            {
                "field": "group_by",
                "value": group_by,
                "allowed": list(CostTracker.REPORT_GROUP_BY_FIELDS),
            },
        )

    now = now_bjt()
    date_to = now - timedelta(days=offset_days) if offset_days else None
    date_from = now - timedelta(days=days + offset_days)
    cached_val = await COSTS_CACHE.get(
        "report",
        days,
        offset_days,
        department or "all",
        model or "all",
        call_source or "all",
        group_by,
        limit,
    )
    if cached_val is not None:
        return cached_val
    result = await cost_tracker.report(
        date_from=date_from,
        date_to=date_to,
        department=department,
        model=model,
        call_source=call_source,
        group_by=group_by,
        limit=limit,
    )
    await COSTS_CACHE.set(
        "report",
        days,
        offset_days,
        department or "all",
        model or "all",
        call_source or "all",
        group_by,
        limit,
        value=result,
    )
    return result


@router.get("/costs/call-sources")
async def costs_call_sources(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_state_active),
):
    """列出最近 N 天出现过的 call_source（前端筛选下拉用）

    Codex MEDIUM-D4 修复：避免前端硬编码 call_source 列表落后于真实埋点。
    """
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from app.common.cost_tracker import cost_tracker
    cached_val = await COSTS_CACHE.get("call-sources", days, current_user.role)
    if cached_val is not None:
        return cached_val
    items = await cost_tracker.list_call_sources(days=days)
    result = {"items": items, "days": days}
    await COSTS_CACHE.set("call-sources", days, current_user.role, value=result)
    return result


# ===== 契约偏离（Contract Drift）看板 =====

@router.get("/contract-drift")
async def contract_drift_overview(
    days: int = Query(7, ge=1, le=90),
    acknowledged: bool | None = Query(None, description="None=全部, True=已处理, False=未处理"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """契约偏离看板：按 skill 聚合最近 N 天的 drift。

    返回 {total, items: [{skill_id, name, drift_count, ack_count, latest: {run_id, detected_at, first_error}}]}
    """
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    cached_val = await CONTRACT_DRIFT_CACHE.get(days, acknowledged if acknowledged is not None else "all")
    if cached_val is not None:
        return cached_val

    since = now_bjt() - timedelta(days=days)

    # 聚合：每个 skill 最近 N 天 drift 总数 + ack 数 + 最新一条
    base_q = select(
        ContractDrift.skill_id,
        func.count(ContractDrift.id).label("drift_count"),
        func.sum(cast(ContractDrift.acknowledged, Integer)).label("ack_count"),
        func.max(ContractDrift.detected_at).label("latest_at"),
    ).where(ContractDrift.detected_at >= since)
    if acknowledged is not None:
        base_q = base_q.where(ContractDrift.acknowledged == acknowledged)
    base_q = base_q.group_by(ContractDrift.skill_id).order_by(func.count(ContractDrift.id).desc()).limit(100)

    agg_rows = (await db.execute(base_q)).all()
    items: list[dict] = []
    for row in agg_rows:
        # 拿最新一条 drift 详情
        latest = (await db.execute(
            select(ContractDrift)
            .where(ContractDrift.skill_id == row.skill_id)
            .order_by(ContractDrift.detected_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        skill = (await db.execute(select(Skill).where(Skill.id == row.skill_id))).scalar_one_or_none()
        first_error = None
        if latest and latest.schema_errors:
            first_error = latest.schema_errors[0] if isinstance(latest.schema_errors, list) else None
        items.append({
            "skill_id": row.skill_id,
            "name": skill.name if skill else row.skill_id,
            "department": skill.department if skill else None,
            "drift_count": int(row.drift_count or 0),
            "ack_count": int(row.ack_count or 0),
            "latest": {
                "run_id": latest.run_id if latest else None,
                "drift_id": latest.id if latest else None,
                "detected_at": isoformat_bjt(latest.detected_at) if latest else None,
                "first_error": first_error,
                "error_count": len(latest.schema_errors) if latest and latest.schema_errors else 0,
            },
        })

    result = {"total": len(items), "items": items, "days": days}
    await CONTRACT_DRIFT_CACHE.set(days, acknowledged if acknowledged is not None else "all", value=result)
    return result


@router.get("/contract-drift/{drift_id}")
async def contract_drift_detail(
    drift_id: int,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """单条 drift 详情：含完整的 schema_errors 列表"""
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    drift = (await db.execute(select(ContractDrift).where(ContractDrift.id == drift_id))).scalar_one_or_none()
    if not drift:
        raise HTTPException(404, "drift not found")

    return {
        "id": drift.id,
        "skill_id": drift.skill_id,
        "run_id": drift.run_id,
        "schema_errors": drift.schema_errors,
        "schema_hash": drift.schema_hash,
        "detected_at": isoformat_bjt(drift.detected_at),
        "acknowledged": drift.acknowledged,
        "acknowledged_by": drift.acknowledged_by,
        "acknowledged_at": isoformat_bjt(drift.acknowledged_at),
    }


@router.post("/contract-drift/{drift_id}/acknowledge")
async def contract_drift_acknowledge(
    drift_id: int,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """承认 drift（收到提醒、已开始处理）"""
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    drift = (await db.execute(select(ContractDrift).where(ContractDrift.id == drift_id))).scalar_one_or_none()
    if not drift:
        raise HTTPException(404, "drift not found")

    drift.acknowledged = True
    drift.acknowledged_by = current_user.id
    drift.acknowledged_at = now_bjt()
    await db.commit()
    await CONTRACT_DRIFT_CACHE.invalidate()
    return {"ok": True, "drift_id": drift_id}


@router.post("/contract-drift/{skill_id}/propose-schema")
async def contract_drift_propose_schema(
    skill_id: str,
    sample_size: int = Query(10, ge=3, le=50),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """从最近 N 次非沙箱 output 反推保守 jsonschema，创建一条审核单（不直接改文件）。

    典型场景：skill 持续 drift，owner 判断"实际 output 是对的、schema 写歪了"，
    提交一个"反向覆盖"审核单,走 /reviews 人审 → 审核人批准后才真正改 skill 文件。

    返回 review_id;前端跳转 /reviews 展示。
    """
    if not role_matches_any(current_user, ("admin", "ai_engineer")):
        from app.common.exceptions import AppError
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    from app.common.contract_schema import infer_schema_from_samples
    from app.execution.models import DecisionLog
    from app.reviews import service as review_service
    from app.skills.core.git_service import git_service
    import json

    logs = (await db.execute(
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .order_by(DecisionLog.created_at.desc())
        .limit(sample_size)
    )).scalars().all()
    samples = [log.output_result for log in logs if isinstance(log.output_result, dict)]
    if len(samples) < 3:
        raise HTTPException(400, f"需要至少 3 条非沙箱执行样本，当前仅 {len(samples)} 条")

    inferred = infer_schema_from_samples(samples)

    contract_raw = git_service.read_file(skill_id, "contract.json")
    if not contract_raw:
        raise HTTPException(404, "contract.json not found")
    try:
        contract = json.loads(contract_raw)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"contract.json 解析失败: {e}")
    old_schema = contract.get("output_schema")

    # 统计该 skill 当前未处理 drift 数(给审核人看上下文)
    drift_count = (await db.execute(
        select(func.count(ContractDrift.id))
        .where(ContractDrift.skill_id == skill_id, ContractDrift.acknowledged == False)  # noqa: E712
    )).scalar_one() or 0

    security_settings = await get_platform_security_settings(db)

    # 创建审核单。不 touch git,由 approve_review 时根据 diff_content.reason 实际落盘。
    review = await review_service.create_review(
        db,
        skill_id=skill_id,
        submitter=current_user.id,
        change_type="update",
        diff_summary=f"反向覆盖 output_schema（基于最近 {len(samples)} 次执行采样，当前未处理 drift {drift_count} 条）",
        diff_content={
            "reason": "contract_drift_apply",
            "old_schema": old_schema,
            "inferred_schema": inferred,
            "sample_count": len(samples),
            "pending_drift_count": int(drift_count),
        },
        reason="契约偏离反向覆盖：由平台从实际 output 反推 schema",
        force_submit=security_settings.bypass_review_direct_publish,
        force_reason=SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON if security_settings.bypass_review_direct_publish else "",
    )
    await db.commit()

    approve_result = None
    if security_settings.bypass_review_direct_publish and review.get("review_id"):
        approve_result = await review_service.approve_review_as_system(
            db,
            int(review["review_id"]),
            verify_after_sync=True,
        )
        await db.commit()

    response = {
        "ok": True,
        "review_id": review.get("review_id"),
        "skill_id": skill_id,
        "sample_count": len(samples),
        "pending_drift_count": int(drift_count),
        "old_schema": old_schema,
        "inferred_schema": inferred,
    }
    if approve_result is not None:
        response.update(
            {
                "status": "approved",
                "auto_publish": {"ok": True},
                "review": approve_result,
            }
        )
    return response
