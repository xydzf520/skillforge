"""
效果看板统计服务：采纳率、执行次数、业务影响、趋势图表、周报、治理概览。
"""

from datetime import datetime, timedelta

from sqlalchemy import select, func, case, cast, Date, desc, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep, OpenClawInstance
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime

# 每次执行预估节省的分钟数（可在后续版本中改为配置项）
AVG_MINUTES_SAVED_PER_EXECUTION = 15


async def get_overview(db: AsyncSession, days: int = 30, department: str | None = None) -> dict:
    """运行总览（含省时估算）"""
    since = now_bjt() - timedelta(days=days)

    # 执行统计
    runs_stmt = select(
        func.count(ExecutionRun.id).label("total"),
        func.count(case((ExecutionRun.status == "completed", 1))).label("success"),
        func.count(case((ExecutionRun.status == "failed", 1))).label("failed"),
    ).where(ExecutionRun.started_at >= since)
    if department:
        runs_stmt = runs_stmt.join(
            DecisionLog, DecisionLog.run_id == ExecutionRun.id
        ).join(Skill, Skill.id == DecisionLog.skill_id).where(Skill.department == department)
    runs_result = await db.execute(runs_stmt)
    runs = runs_result.one()

    # Skill数量
    skills_stmt = select(func.count(Skill.id)).where(Skill.status == "active")
    if department:
        skills_stmt = skills_stmt.where(Skill.department == department)
    skills_result = await db.execute(skills_stmt)
    active_skills = skills_result.scalar() or 0

    # 省时估算：成功执行次数 × 每次预估节省分钟数，转换为小时
    time_saved_hours = round(runs.success * AVG_MINUTES_SAVED_PER_EXECUTION / 60, 1)

    return {
        "period_days": days,
        "executions": {"total": runs.total, "success": runs.success, "failed": runs.failed},
        "active_skills": active_skills,
        "time_saved_hours": time_saved_hours,
    }


async def get_adoption(db: AsyncSession, days: int = 30, department: str | None = None) -> list[dict]:
    """
    各Skill的采纳率统计。
    采纳率 = user_action='completed' 的比例。
    """
    since = now_bjt() - timedelta(days=days)

    stmt = (
        select(
            DecisionLog.skill_id,
            func.count(DecisionLog.id).label("total"),
            func.count(case((DecisionLog.user_action == "completed", 1))).label("adopted"),
            func.count(case((DecisionLog.user_action == "rejected", 1))).label("rejected"),
        )
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
    )
    if department:
        stmt = stmt.join(Skill, Skill.id == DecisionLog.skill_id).where(Skill.department == department)
    stmt = stmt.group_by(DecisionLog.skill_id).order_by(func.count(DecisionLog.id).desc())
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for row in rows:
        rate = round(row.adopted / row.total * 100, 1) if row.total > 0 else 0
        items.append({
            "skill_id": row.skill_id,
            "total": row.total,
            "adopted": row.adopted,
            "rejected": row.rejected,
            "adoption_rate": rate,
        })

    return items


async def get_trends(db: AsyncSession, days: int = 30, department: str | None = None) -> dict:
    """每日趋势数据（执行次数 + 采纳次数），用于折线图"""
    since = now_bjt() - timedelta(days=days)

    # 每日执行次数
    exec_stmt = (
        select(
            cast(ExecutionRun.started_at, Date).label("day"),
            func.count(ExecutionRun.id).label("cnt"),
        )
        .where(ExecutionRun.started_at >= since)
        .group_by(cast(ExecutionRun.started_at, Date))
        .order_by(cast(ExecutionRun.started_at, Date))
    )
    if department:
        exec_stmt = exec_stmt.join(
            DecisionLog, DecisionLog.run_id == ExecutionRun.id
        ).join(Skill, Skill.id == DecisionLog.skill_id).where(Skill.department == department)
    exec_result = await db.execute(exec_stmt)
    exec_rows = exec_result.all()

    # 每日采纳次数（user_action='completed' 且非沙箱）
    adopt_stmt = (
        select(
            cast(DecisionLog.created_at, Date).label("day"),
            func.count(DecisionLog.id).label("cnt"),
        )
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.user_action == "completed")
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .group_by(cast(DecisionLog.created_at, Date))
        .order_by(cast(DecisionLog.created_at, Date))
    )
    if department:
        adopt_stmt = adopt_stmt.join(Skill, Skill.id == DecisionLog.skill_id).where(
            Skill.department == department
        )
    adopt_result = await db.execute(adopt_stmt)
    adopt_rows = adopt_result.all()

    # 构造完整日期序列（包含没有数据的日期，填0）
    exec_map = {row.day.isoformat(): row.cnt for row in exec_rows}
    adopt_map = {row.day.isoformat(): row.cnt for row in adopt_rows}

    dates = []
    executions = []
    adoptions = []
    current = (now_bjt() - timedelta(days=days)).date()
    end = now_bjt().date()
    while current <= end:
        d = current.isoformat()
        dates.append(d)
        executions.append(exec_map.get(d, 0))
        adoptions.append(adopt_map.get(d, 0))
        current += timedelta(days=1)

    return {"dates": dates, "executions": executions, "adoptions": adoptions}


async def get_impact(db: AsyncSession, days: int = 30, department: str | None = None) -> dict:
    """
    业务影响统计（从decision_log的business_impact字段汇总）。
    返回最近影响记录列表 + 按Skill聚合的影响金额汇总。
    """
    since = now_bjt() - timedelta(days=days)

    stmt = (
        select(DecisionLog)
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.business_impact.isnot(None))
    )
    if department:
        stmt = stmt.join(Skill, Skill.id == DecisionLog.skill_id).where(Skill.department == department)
    stmt = stmt.order_by(DecisionLog.created_at.desc()).limit(100)
    result = await db.execute(stmt)
    logs = result.scalars().all()

    # 明细列表（取最近50条展示）
    items = [
        {
            "skill_id": l.skill_id,
            "date": isoformat_bjt(l.created_at)[:10] if l.created_at else "",
            "impact": l.business_impact,
        }
        for l in logs
    ]

    # 按Skill聚合影响金额（business_impact中可能含 amount 字段）
    skill_totals: dict[str, float] = {}
    total_amount = 0.0
    for l in logs:
        bi = l.business_impact or {}
        amount = 0.0
        # 兼容多种数据结构：{amount: 1000} 或 {value: 1000} 或直接数字
        if isinstance(bi, dict):
            amount = float(bi.get("amount", 0) or bi.get("value", 0) or 0)
        elif isinstance(bi, (int, float)):
            amount = float(bi)
        if amount:
            skill_totals[l.skill_id] = skill_totals.get(l.skill_id, 0) + amount
            total_amount += amount

    # 按影响金额降序排列的Skill排行
    top_skills = sorted(
        [{"skill_id": sid, "total_amount": round(amt, 2)} for sid, amt in skill_totals.items()],
        key=lambda x: x["total_amount"],
        reverse=True,
    )

    return {
        "items": items[:50],
        "total_amount": round(total_amount, 2),
        "top_skills": top_skills[:10],
    }


async def generate_weekly_report(db: AsyncSession, department: str | None = None) -> dict:
    """
    生成本周汇总报告数据。
    可按部门过滤（普通用户仅看本部门）。
    Returns:
        period, total_executions, success_rate, adoption_rate,
        time_saved_hours, active_skills, top_skills, highlights, trend_summary
    """
    now = now_bjt()
    week_ago = now - timedelta(days=7)
    period = f"{week_ago.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}"
    prev_week_start = week_ago - timedelta(days=7)

    # ── 本周执行统计 ──
    runs_stmt = select(
        func.count(ExecutionRun.id).label("total"),
        func.count(case((ExecutionRun.status == "completed", 1))).label("success"),
    ).where(ExecutionRun.started_at >= week_ago)
    if department:
        runs_stmt = runs_stmt.join(
            DecisionLog, DecisionLog.run_id == ExecutionRun.id
        ).join(Skill, Skill.id == DecisionLog.skill_id).where(Skill.department == department)
    runs_result = await db.execute(runs_stmt)
    runs = runs_result.one()
    total_executions = runs.total or 0
    success_count = runs.success or 0
    success_rate = round(success_count / total_executions * 100, 1) if total_executions > 0 else 0

    # ── 本周采纳率 ──
    decision_base = (
        select(
            func.count(DecisionLog.id).label("total"),
            func.count(case((DecisionLog.user_action == "completed", 1))).label("adopted"),
        )
        .where(DecisionLog.created_at >= week_ago)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
    )
    if department:
        decision_base = decision_base.join(
            Skill, Skill.id == DecisionLog.skill_id
        ).where(Skill.department == department)
    dec_result = await db.execute(decision_base)
    dec = dec_result.one()
    dec_total = dec.total or 0
    dec_adopted = dec.adopted or 0
    adoption_rate = round(dec_adopted / dec_total * 100, 1) if dec_total > 0 else 0

    # ── 上周采纳率（用于趋势对比）──
    prev_dec_stmt = (
        select(
            func.count(DecisionLog.id).label("total"),
            func.count(case((DecisionLog.user_action == "completed", 1))).label("adopted"),
        )
        .where(DecisionLog.created_at >= prev_week_start)
        .where(DecisionLog.created_at < week_ago)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
    )
    if department:
        prev_dec_stmt = prev_dec_stmt.join(
            Skill, Skill.id == DecisionLog.skill_id
        ).where(Skill.department == department)
    prev_dec_result = await db.execute(prev_dec_stmt)
    prev_dec = prev_dec_result.one()
    prev_dec_total = prev_dec.total or 0
    prev_adopted = prev_dec.adopted or 0
    prev_adoption_rate = round(prev_adopted / prev_dec_total * 100, 1) if prev_dec_total > 0 else 0

    # ── 上周执行次数（趋势对比）──
    prev_runs_stmt = select(
        func.count(ExecutionRun.id).label("total"),
    ).where(
        ExecutionRun.started_at >= prev_week_start,
        ExecutionRun.started_at < week_ago,
    )
    if department:
        prev_runs_stmt = prev_runs_stmt.join(
            DecisionLog, DecisionLog.run_id == ExecutionRun.id
        ).join(Skill, Skill.id == DecisionLog.skill_id).where(Skill.department == department)
    prev_runs_result = await db.execute(prev_runs_stmt)
    prev_total_executions = (prev_runs_result.scalar() or 0)

    # ── 活跃Skill数量 ──
    skills_stmt = select(func.count(Skill.id)).where(Skill.status == "active")
    if department:
        skills_stmt = skills_stmt.where(Skill.department == department)
    active_skills = (await db.execute(skills_stmt)).scalar() or 0

    # ── 省时估算 ──
    time_saved_hours = round(success_count * AVG_MINUTES_SAVED_PER_EXECUTION / 60, 1)

    # ── Top Skills（本周执行次数排名）──
    top_stmt = (
        select(
            DecisionLog.skill_id,
            func.count(DecisionLog.id).label("total"),
            func.count(case((DecisionLog.user_action == "completed", 1))).label("adopted"),
        )
        .where(DecisionLog.created_at >= week_ago)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
    )
    if department:
        top_stmt = top_stmt.join(
            Skill, Skill.id == DecisionLog.skill_id
        ).where(Skill.department == department)
    top_stmt = top_stmt.group_by(DecisionLog.skill_id).order_by(desc("total")).limit(5)
    top_result = await db.execute(top_stmt)
    top_rows = top_result.all()
    top_skills = []
    for row in top_rows:
        rate = round(row.adopted / row.total * 100, 1) if row.total > 0 else 0
        top_skills.append({
            "skill_id": row.skill_id,
            "executions": row.total,
            "adoption_rate": rate,
        })

    # ── 亮点摘要（自动生成）──
    highlights = []

    # 检查本周是否有新上线的Skill
    new_skills_stmt = (
        select(Skill.id)
        .where(Skill.status == "active")
        .where(Skill.updated_at >= week_ago)
        .where(Skill.created_at >= week_ago)
    )
    if department:
        new_skills_stmt = new_skills_stmt.where(Skill.department == department)
    new_skills_result = await db.execute(new_skills_stmt)
    new_skill_ids = [r[0] for r in new_skills_result.all()]
    for sid in new_skill_ids[:3]:
        highlights.append(f"本周新上线{sid}")

    # 采纳率变化
    adoption_diff = round(adoption_rate - prev_adoption_rate, 1)
    if adoption_diff > 0:
        highlights.append(f"采纳率较上周提升{adoption_diff}%")
    elif adoption_diff < 0:
        highlights.append(f"采纳率较上周下降{abs(adoption_diff)}%")

    if not highlights:
        highlights.append("本周运行平稳")

    # ── 趋势简述 ──
    def _trend_arrow(current, previous):
        if current > previous:
            return "↗"
        elif current < previous:
            return "↘"
        return "→"

    trend_summary = (
        f"执行量{_trend_arrow(total_executions, prev_total_executions)} "
        f"采纳率{_trend_arrow(adoption_rate, prev_adoption_rate)} "
        f"省时{_trend_arrow(time_saved_hours, prev_total_executions * AVG_MINUTES_SAVED_PER_EXECUTION / 60)}"
    )

    return {
        "period": period,
        "total_executions": total_executions,
        "success_rate": success_rate,
        "adoption_rate": adoption_rate,
        "time_saved_hours": time_saved_hours,
        "active_skills": active_skills,
        "top_skills": top_skills,
        "highlights": highlights,
        "trend_summary": trend_summary,
    }


# ===== P2: 治理概览 =====


async def get_governance_overview(db: AsyncSession, department: str | None = None) -> dict:
    """
    治理概览 — 真实数据聚合。

    汇总审批效率、成本归因、数据治理、Skill 资产健康和近期治理事件，
    供治理看板页面一次性拉取使用。按 department 可选过滤。
    """
    from app.reviews.models import Review
    from app.datasources.models import DataSource, DataIngestionLog
    from app.common.audit import AuditLog

    now = now_bjt()
    thirty_days_ago = now - timedelta(days=30)
    twenty_four_hours_ago = now - timedelta(hours=24)

    # ── 1. Worker（OpenClaw 实例）状态 ──
    workers = await _aggregate_workers(db, department)

    # ── 2. 审批效率 ──
    approval_efficiency = await _aggregate_approval_efficiency(db, department, now, thirty_days_ago, twenty_four_hours_ago)

    # ── 3. 待审批计数 ──
    pending_stmt = select(func.count(Review.id)).where(Review.status == "pending")
    if department:
        pending_stmt = pending_stmt.join(Skill, Skill.id == Review.skill_id).where(Skill.department == department)
    approvals_pending = (await db.execute(pending_stmt)).scalar() or 0

    # ── 4. 成本归因 ──
    cost_attribution = await _aggregate_cost_attribution(db, department, thirty_days_ago)

    # ── 5. 数据治理 ──
    data_governance = await _aggregate_data_governance(db, department, now)

    # ── 6. Skill 资产健康 ──
    skill_health = await _aggregate_skill_health(db, department, thirty_days_ago)

    # ── 7. 近期治理事件 ──
    recent_events = await _aggregate_recent_events(db, department)

    return {
        # 基础计数
        "workers": workers,
        "approvals_pending": approvals_pending,
        # 新增聚合
        "approval_efficiency": approval_efficiency,
        "cost_attribution": cost_attribution,
        "data_governance": data_governance,
        "skill_health": skill_health,
        "recent_events": recent_events,
    }


async def _aggregate_workers(db: AsyncSession, department: str | None) -> dict:
    """聚合 OpenClaw 实例在线/离线状态"""
    stale_cutoff = now_bjt() - timedelta(minutes=5)

    base = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
    if department:
        base = base.where(OpenClawInstance.department == department)
    result = await db.execute(base)
    instances = result.scalars().all()

    total = len(instances)
    online = sum(
        1 for i in instances
        if i.last_heartbeat and i.last_heartbeat >= stale_cutoff
    )
    stale = total - online

    return {"total": total, "online": online, "stale": stale}


async def _aggregate_approval_efficiency(
    db: AsyncSession,
    department: str | None,
    now: datetime,
    thirty_days_ago: datetime,
    twenty_four_hours_ago: datetime,
) -> dict:
    """审批效率：平均耗时、超时数、通过率、驳回率"""
    from app.reviews.models import Review

    # 平均审批耗时（小时），近 30 天内已决策的审核
    avg_stmt = select(
        func.avg(
            extract("epoch", Review.decided_at - Review.created_at) / 3600.0
        )
    ).where(
        Review.decided_at.isnot(None),
        Review.created_at >= thirty_days_ago,
    )
    if department:
        avg_stmt = avg_stmt.join(Skill, Skill.id == Review.skill_id).where(Skill.department == department)
    avg_hours_raw = (await db.execute(avg_stmt)).scalar()
    avg_decision_hours = round(float(avg_hours_raw), 1) if avg_hours_raw is not None else 0.0

    # 超过 24h 未处理的审批
    pending_over_stmt = select(func.count(Review.id)).where(
        Review.status == "pending",
        Review.created_at < twenty_four_hours_ago,
    )
    if department:
        pending_over_stmt = pending_over_stmt.join(Skill, Skill.id == Review.skill_id).where(Skill.department == department)
    pending_over_24h = (await db.execute(pending_over_stmt)).scalar() or 0

    # 近 30 天通过率 / 驳回率
    decided_stmt = select(
        func.count(Review.id).label("total"),
        func.count(case((Review.status == "approved", 1))).label("approved"),
        func.count(case((Review.status == "rejected", 1))).label("rejected"),
    ).where(
        Review.decided_at.isnot(None),
        Review.created_at >= thirty_days_ago,
    )
    if department:
        decided_stmt = decided_stmt.join(Skill, Skill.id == Review.skill_id).where(Skill.department == department)
    decided = (await db.execute(decided_stmt)).one()
    total_decided = decided.total or 0
    approval_rate = round(decided.approved / total_decided * 100, 1) if total_decided > 0 else 0.0
    rejection_rate = round(decided.rejected / total_decided * 100, 1) if total_decided > 0 else 0.0

    return {
        "avg_decision_hours": avg_decision_hours,
        "pending_over_24h": pending_over_24h,
        "approval_rate_30d": approval_rate,
        "rejection_rate_30d": rejection_rate,
    }


async def _aggregate_cost_attribution(
    db: AsyncSession,
    department: str | None,
    thirty_days_ago: datetime,
) -> dict:
    """成本归因：近 30 天总执行数、按部门、Top 5 Skill"""

    # 总执行次数
    total_stmt = select(func.count(ExecutionRun.id)).where(
        ExecutionRun.started_at >= thirty_days_ago,
    )
    total_executions = (await db.execute(total_stmt)).scalar() or 0

    # 按部门执行量（通过 ExecutionStep → Skill）
    dept_stmt = (
        select(
            Skill.department,
            func.count(ExecutionStep.id).label("count"),
            func.count(case((ExecutionStep.status == "completed", 1))).label("success"),
        )
        .join(Skill, Skill.id == ExecutionStep.skill_id)
        .where(ExecutionStep.started_at >= thirty_days_ago)
        .group_by(Skill.department)
        .order_by(desc("count"))
    )
    if department:
        dept_stmt = dept_stmt.where(Skill.department == department)
    dept_result = await db.execute(dept_stmt)
    by_department = []
    for row in dept_result:
        rate = round(row.success / row.count * 100, 1) if row.count > 0 else 0.0
        by_department.append({
            "department": row.department,
            "count": row.count,
            "success_rate": rate,
        })

    # 执行量 Top 5 Skill（按 ExecutionStep 聚合）
    top_stmt = (
        select(
            ExecutionStep.skill_id,
            Skill.name,
            func.count(ExecutionStep.id).label("count"),
            func.avg(ExecutionStep.duration_ms).label("avg_duration_ms"),
        )
        .join(Skill, Skill.id == ExecutionStep.skill_id)
        .where(ExecutionStep.started_at >= thirty_days_ago)
        .group_by(ExecutionStep.skill_id, Skill.name)
        .order_by(desc("count"))
        .limit(5)
    )
    if department:
        top_stmt = top_stmt.where(Skill.department == department)
    top_result = await db.execute(top_stmt)
    top_skills = []
    for row in top_result:
        avg_s = round(float(row.avg_duration_ms) / 1000, 2) if row.avg_duration_ms else 0.0
        top_skills.append({
            "skill_id": row.skill_id,
            "name": row.name,
            "count": row.count,
            "avg_duration_s": avg_s,
        })

    return {
        "total_executions_30d": total_executions,
        "by_department": by_department,
        "top_skills": top_skills,
    }


async def _aggregate_data_governance(
    db: AsyncSession,
    department: str | None,
    now: datetime,
) -> dict:
    """数据治理：数据源总数、过期数据源、待处理导入"""
    from app.datasources.models import DataSource, DataIngestionLog

    # 总数据源数
    total_stmt = select(func.count(DataSource.id)).where(DataSource.is_active == True)  # noqa: E712
    if department:
        total_stmt = total_stmt.where(DataSource.department == department)
    total_sources = (await db.execute(total_stmt)).scalar() or 0

    # 过期数据源：比对每个数据源最新成功导入时间与其 stale_threshold_hours
    # 先取所有活跃数据源及其最新成功导入时间，在 Python 侧判断是否过期
    latest_ingestion = (
        select(
            DataIngestionLog.source_id,
            func.max(DataIngestionLog.created_at).label("last_ingested"),
        )
        .where(DataIngestionLog.status == "success")
        .group_by(DataIngestionLog.source_id)
        .subquery()
    )
    stale_check_stmt = (
        select(
            DataSource.id,
            DataSource.stale_threshold_hours,
            latest_ingestion.c.last_ingested,
        )
        .outerjoin(latest_ingestion, latest_ingestion.c.source_id == DataSource.id)
        .where(DataSource.is_active == True)  # noqa: E712
    )
    if department:
        stale_check_stmt = stale_check_stmt.where(DataSource.department == department)
    stale_rows = (await db.execute(stale_check_stmt)).all()
    stale_sources = 0
    for row in stale_rows:
        if row.last_ingested is None:
            # 从未成功导入过视为过期
            stale_sources += 1
        else:
            threshold = timedelta(hours=row.stale_threshold_hours or 24)
            if now - row.last_ingested > threshold:
                stale_sources += 1

    # 待处理的数据导入
    pending_import_stmt = select(func.count(DataIngestionLog.id)).where(
        DataIngestionLog.status == "processing",
    )
    pending_imports = (await db.execute(pending_import_stmt)).scalar() or 0

    return {
        "total_sources": total_sources,
        "stale_sources": stale_sources,
        "pending_imports": pending_imports,
    }


async def _aggregate_skill_health(
    db: AsyncSession,
    department: str | None,
    thirty_days_ago: datetime,
) -> dict:
    """Skill 资产健康：按状态分布、平均健康分、低分 Skill 数、无测试 Skill 数"""
    from loguru import logger

    # 按状态分布
    status_stmt = (
        select(
            Skill.status,
            func.count(Skill.id).label("cnt"),
        )
        .group_by(Skill.status)
    )
    if department:
        status_stmt = status_stmt.where(Skill.department == department)
    status_result = await db.execute(status_stmt)
    by_status: dict[str, int] = {}
    total = 0
    for row in status_result:
        by_status[row.status] = row.cnt
        total += row.cnt

    # 计算每个活跃/影子 Skill 的健康分（复用 get_skill_health_score）
    # 为避免 N+1 查询过慢，仅对 active + shadow Skill 采样
    active_stmt = select(Skill.id).where(Skill.status.in_(("active", "shadow")))
    if department:
        active_stmt = active_stmt.where(Skill.department == department)
    active_result = await db.execute(active_stmt)
    active_ids = [r[0] for r in active_result.all()]

    scores: list[float] = []
    low_health_count = 0
    no_test_count = 0

    for sid in active_ids:
        try:
            health = await get_skill_health_score(db, sid)
            score = health["score"]
            scores.append(score)
            if score < 60:
                low_health_count += 1
            if health["breakdown"]["test_coverage"] == 0:
                no_test_count += 1
        except Exception as e:
            logger.warning("治理看板：健康分计算失败 skill={}: {}", sid, e)

    avg_health_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    return {
        "total": total,
        "by_status": by_status,
        "avg_health_score": avg_health_score,
        "low_health_count": low_health_count,
        "no_test_cases": no_test_count,
    }


async def _aggregate_recent_events(db: AsyncSession, department: str | None) -> list[dict]:
    """最近 10 条治理相关事件：审批决策 + 数据源导入"""
    from app.reviews.models import Review
    from app.datasources.models import DataIngestionLog

    events: list[dict] = []

    # 最近审批事件
    review_stmt = (
        select(Review)
        .where(Review.decided_at.isnot(None))
        .order_by(Review.decided_at.desc())
        .limit(10)
    )
    if department:
        review_stmt = review_stmt.join(Skill, Skill.id == Review.skill_id).where(Skill.department == department)
    review_result = await db.execute(review_stmt)
    for r in review_result.scalars().all():
        events.append({
            "type": "approval",
            "action": r.status,  # approved / rejected
            "skill_id": r.skill_id,
            "user": r.reviewer,
            "at": isoformat_bjt(r.decided_at),
        })

    # 最近数据导入事件
    ingest_stmt = (
        select(DataIngestionLog)
        .order_by(DataIngestionLog.created_at.desc())
        .limit(10)
    )
    ingest_result = await db.execute(ingest_stmt)
    for il in ingest_result.scalars().all():
        events.append({
            "type": "data_ingestion",
            "action": il.status,  # success / failed / processing
            "source_id": il.source_id,
            "user": il.uploaded_by,
            "at": isoformat_bjt(il.created_at),
        })

    # 按时间倒序取前 10
    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    return events[:10]


# ===== P3: Skill 健康评分 =====

async def get_skill_health_score(db: AsyncSession, skill_id: str) -> dict:
    """
    计算单个 Skill 的健康评分（0-100）。
    公式：采纳率30% + 成功率25% + 测试覆盖20% + 新鲜度15% + 数据健康10%
    """
    # 1. 采纳率（最近30天）
    adoption = await _calc_adoption_rate(db, skill_id)

    # 2. 成功率
    success = await _calc_success_rate(db, skill_id)

    # 3. 测试覆盖率
    coverage = _calc_test_coverage(skill_id)

    # 4. 新鲜度
    freshness = _calc_freshness(skill_id)

    # 5. 数据源健康度
    data_health = await _calc_data_health(db, skill_id)

    score = (
        adoption * 0.30
        + success * 0.25
        + coverage * 0.20
        + freshness * 0.15
        + data_health * 0.10
    )

    return {
        "skill_id": skill_id,
        "score": round(score, 1),
        "grade": _score_to_grade(score),
        "breakdown": {
            "adoption_rate": round(adoption, 1),
            "success_rate": round(success, 1),
            "test_coverage": round(coverage, 1),
            "freshness": round(freshness, 1),
            "data_health": round(data_health, 1),
        },
    }


async def get_health_ranking(db: AsyncSession, department: str | None = None) -> list[dict]:
    """全局健康排行榜"""
    stmt = select(Skill.id).where(Skill.status.in_(("active", "shadow")))
    if department:
        stmt = stmt.where(Skill.department == department)
    result = await db.execute(stmt)
    skill_ids = [r[0] for r in result.all()]

    rankings = []
    for sid in skill_ids:
        score = await get_skill_health_score(db, sid)
        rankings.append(score)
    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings


async def _calc_adoption_rate(db: AsyncSession, skill_id: str) -> float:
    since = now_bjt() - timedelta(days=30)
    total_r = await db.execute(
        select(func.count(DecisionLog.id))
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .where(DecisionLog.created_at >= since)
    )
    total = total_r.scalar() or 0
    if total == 0:
        return 0.0
    adopted_r = await db.execute(
        select(func.count(DecisionLog.id))
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.user_action == "completed")
    )
    adopted = adopted_r.scalar() or 0
    return adopted / total * 100


async def _calc_success_rate(db: AsyncSession, skill_id: str) -> float:
    since = now_bjt() - timedelta(days=30)
    total_r = await db.execute(
        select(func.count(DecisionLog.id))
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.created_at >= since)
    )
    total = total_r.scalar() or 0
    if total == 0:
        return 100.0  # 无执行记录默认满分
    # 成功 = 有 output_result 的
    success_r = await db.execute(
        select(func.count(DecisionLog.id))
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.output_result.isnot(None))
    )
    success = success_r.scalar() or 0
    return success / total * 100


def _calc_test_coverage(skill_id: str) -> float:
    """测试覆盖率：test_cases 数量 / 决策树叶子节点数"""
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        return 0.0
    parsed = skill_parser.parse(skill_md)
    leaf_count = sum(len(s.branches) for s in parsed.steps) or 1
    test_count = len(parsed.test_cases)
    return min(test_count / leaf_count * 100, 100.0)


def _calc_freshness(skill_id: str) -> float:
    """新鲜度：最后 commit 距今天数，越近越高"""
    from app.skills.core.git_service import git_service

    logs = git_service.log(skill_id=skill_id, max_count=1)
    if not logs:
        return 0.0
    last_date_str = logs[0].get("date", "")
    if not last_date_str:
        return 0.0
    try:
        last_date = parse_bjt_datetime(last_date_str)
        days_ago = (now_bjt() - last_date).days
        # 7天内100分，30天内线性衰减，超过30天0分
        if days_ago <= 7:
            return 100.0
        if days_ago <= 30:
            return max(0, (30 - days_ago) / 23 * 100)
        return 0.0
    except (ValueError, TypeError):
        return 0.0


async def _calc_data_health(db: AsyncSession, skill_id: str) -> float:
    """数据源健康度：关联数据源的漂移检测"""
    try:
        from app.skills.tooling.validation_service import check_data_drift
        drift = await check_data_drift(db, skill_id)
        if drift["total_inputs"] == 0:
            return 100.0  # 无数据源依赖
        error_count = sum(1 for d in drift["drifts"] if d["severity"] == "error")
        warning_count = sum(1 for d in drift["drifts"] if d["severity"] == "warning")
        # 每个 error 扣 30 分，每个 warning 扣 10 分
        return max(0, 100 - error_count * 30 - warning_count * 10)
    except Exception as e:
        from loguru import logger
        logger.warning(f"数据健康检测失败 skill={skill_id}: {e}")
        return 100.0  # 检测失败不扣分


def _score_to_grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


# ===== P3: 变更频率热力图 =====

async def get_change_heatmap(db: AsyncSession, days: int = 30, department: str | None = None) -> dict:
    """变更频率热力图数据：聚合 audit_log 中 skill.edit + review.reject"""
    from app.common.audit import AuditLog
    from app.reviews.models import Review

    cutoff = now_bjt() - timedelta(days=days)

    # 编辑频率
    edit_stmt = (
        select(AuditLog.target_id, func.count().label("edit_count"))
        .where(AuditLog.action == "skill.edit")
        .where(AuditLog.created_at >= cutoff)
        .where(AuditLog.target_id.isnot(None))
        .group_by(AuditLog.target_id)
    )
    if department:
        edit_stmt = edit_stmt.join(Skill, Skill.id == AuditLog.target_id).where(Skill.department == department)
    edit_result = await db.execute(edit_stmt)
    edits = {row.target_id: row.edit_count for row in edit_result}

    # 驳回次数
    reject_stmt = (
        select(Review.skill_id, func.count().label("reject_count"))
        .where(Review.status == "rejected")
        .where(Review.decided_at >= cutoff)
        .group_by(Review.skill_id)
    )
    if department:
        reject_stmt = reject_stmt.join(Skill, Skill.id == Review.skill_id).where(Skill.department == department)
    reject_result = await db.execute(reject_stmt)
    rejects = {row.skill_id: row.reject_count for row in reject_result}

    # 合并
    all_skills = set(edits.keys()) | set(rejects.keys())
    heatmap = []
    for sid in all_skills:
        edit_count = edits.get(sid, 0)
        reject_count = rejects.get(sid, 0)
        heatmap.append({
            "skill_id": sid,
            "edit_count": edit_count,
            "reject_count": reject_count,
            "reject_rate": round(reject_count / max(edit_count, 1) * 100, 1),
            "heat_score": edit_count + reject_count * 3,
        })

    heatmap.sort(key=lambda x: x["heat_score"], reverse=True)

    return {
        "days": days,
        "total_skills": len(heatmap),
        "data": heatmap,
    }
