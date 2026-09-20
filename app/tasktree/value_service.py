"""任务树 Phase 2 价值度量服务。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import case, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import bindparam

from app.common.ai import call_llm_cached
from app.common.exceptions import AppError
from app.common.metrics import tasktree_slow_query_total
from app.common.models import UsageLog
from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep
from app.skills.core.models import Skill
from app.common.time_utils import now_bjt

DEFAULT_BASELINE_MINUTES = 15.0
DEFAULT_HOURLY_COST_USD = 30.0

# ── 启动期列存在性快照 ───────────────────────────────────────────
# 旧实现每次请求都 db.run_sync(inspect) 读 pg_attribute，高频价值查询会打 DB。
# 现改为 main.py lifespan 启动时调用 warmup_value_column_flags() 填一次，
# 之后所有请求走内存字典。若启动期检查失败则置 None，请求 path 回退到动态探测。
_VALUE_COLUMNS_EXIST: dict[str, set[str]] | None = None


async def warmup_value_column_flags(db: AsyncSession) -> dict[str, set[str]]:
    """启动期调用，一次性缓存 execution_runs / skills 的列集合。

    幂等：失败不抛异常，只记录 warning；请求 path 会自动回退到动态探测。
    """
    global _VALUE_COLUMNS_EXIST
    try:
        exec_cols = await _table_columns(db, "execution_runs", force=True)
        skill_cols = await _table_columns(db, "skills", force=True)
        _VALUE_COLUMNS_EXIST = {
            "execution_runs": exec_cols,
            "skills": skill_cols,
        }
        logger.info(
            "value_service 列缓存预热完成: execution_runs={} skills={}",
            len(exec_cols), len(skill_cols),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("value_service 列缓存预热失败（运行时会自动降级）: {}", exc)
        _VALUE_COLUMNS_EXIST = None
    return _VALUE_COLUMNS_EXIST or {}


async def estimate_value(
    db: AsyncSession,
    skill_id: str,
    period_days: int = 30,
    *,
    hourly_cost_usd: float = DEFAULT_HOURLY_COST_USD,
    fallback_baseline_minutes: float = DEFAULT_BASELINE_MINUTES,
    include_ai: bool = True,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    """估算单个 Skill 的业务价值。"""

    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    since = now_bjt() - timedelta(days=period_days)
    # 优先用启动期缓存；未预热/失败时回退到动态探测（兼容测试直接 mock 的路径）
    table_columns = await _get_cached_columns(db, "execution_runs")
    skill_columns = await _get_cached_columns(db, "skills")

    runs = await _load_skill_runs(db, skill_id, since)
    decision_logs = await _load_skill_decision_logs(db, skill_id, since)
    token_cost_period = await _load_skill_token_cost(db, skill_id, since)

    manual_baselines = {}
    if "manual_baseline_minutes" in table_columns:
        manual_baselines = await _load_manual_baselines(db, [row.run_id for row in runs])

    skill_default_baseline = fallback_baseline_minutes
    if "default_baseline_minutes" in skill_columns:
        skill_default_baseline = await _load_skill_default_baseline(db, skill_id) or fallback_baseline_minutes

    completed_runs = 0
    failed_runs = 0
    total_actual_minutes = 0.0
    total_baseline_minutes = 0.0
    saved_minutes = 0.0

    for row in runs:
        actual_minutes = _run_minutes(row)
        baseline_minutes = _resolve_baseline_minutes(
            run_id=row.run_id,
            manual_baselines=manual_baselines,
            skill_default_baseline=skill_default_baseline,
            fallback_baseline_minutes=fallback_baseline_minutes,
        )

        if row.run_status == "completed":
            completed_runs += 1
            total_actual_minutes += actual_minutes
            total_baseline_minutes += baseline_minutes
            saved_minutes += max(baseline_minutes - actual_minutes, 0.0)
        elif row.run_status in {"failed", "timeout", "blocked"}:
            failed_runs += 1

    total_decisions = len(decision_logs)
    rejected_decisions = sum(
        1
        for log in decision_logs
        if (log.user_action == "rejected") or (log.approval_status == "rejected")
    )
    completed_decisions = sum(1 for log in decision_logs if log.user_action == "completed")
    human_takeover_rate = round(
        rejected_decisions / total_decisions, 3
    ) if total_decisions else 0.0

    saved_hours = round(saved_minutes / 60.0, 2)
    estimated_cost_saving = round((saved_minutes / 60.0) * float(hourly_cost_usd), 2)
    risk_events_prevented = failed_runs + rejected_decisions

    result = {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "period_days": period_days,
        "run_count": len(runs),
        "completed_runs": completed_runs,
        "failed_runs": failed_runs,
        "decision_count": total_decisions,
        "completed_decisions": completed_decisions,
        "human_takeover_rate": human_takeover_rate,
        "saved_hours": saved_hours,
        "estimated_cost_saving": estimated_cost_saving,
        "risk_events_prevented": risk_events_prevented,
        "token_cost_period": round(float(token_cost_period), 2),
        "baseline_minutes": round(total_baseline_minutes / completed_runs, 1) if completed_runs else round(skill_default_baseline, 1),
        "average_run_minutes": round(total_actual_minutes / completed_runs, 1) if completed_runs else 0.0,
        "recommendation": _fallback_recommendation(skill.name, {
            "run_count": len(runs),
            "completed_runs": completed_runs,
            "failed_runs": failed_runs,
            "human_takeover_rate": human_takeover_rate,
            "saved_hours": saved_hours,
            "estimated_cost_saving": estimated_cost_saving,
            "token_cost_period": float(token_cost_period),
        }, hourly_cost_usd),
    }

    if include_ai:
        recommendation = await _ai_recommendation(
            db,
            skill,
            result,
            user_id=user_id,
            department=department,
        )
        if recommendation:
            result["recommendation"] = recommendation

    return result


async def get_department_value_summary(
    db: AsyncSession,
    department: str,
    period_days: int = 30,
    *,
    hourly_cost_usd: float = DEFAULT_HOURLY_COST_USD,
    fallback_baseline_minutes: float = DEFAULT_BASELINE_MINUTES,
    include_ai: bool = True,
    user_id: str | None = None,
) -> dict:
    """部门维度价值驾驶舱数据。"""

    since = now_bjt() - timedelta(days=period_days)
    skill_rows = await db.execute(
        select(Skill.id, Skill.name)
        .where(Skill.department == department)
        .where(Skill.status != "deprecated")
        .order_by(Skill.name.asc())
    )
    skill_rows = skill_rows.all()

    # P1-1：消除 N+1 — 原实现每个 skill 跑 5 次查询（estimate_value），
    # 现在用一次 GROUP BY 聚合 saved_hours / takeover_count / cost 等指标。
    skill_values: list[dict[str, Any]] = []
    skill_id_list = [row.id for row in skill_rows]
    aggregated: dict[str, dict[str, Any]] | None = None
    if skill_id_list:
        aggregated = await _bulk_load_skill_metrics(
            db,
            skill_ids=skill_id_list,
            since=since,
            hourly_cost_usd=hourly_cost_usd,
            fallback_baseline_minutes=fallback_baseline_minutes,
        )

    # 如果聚合成功就走单次 SQL 路径；否则回退到老的 per-skill estimate_value
    # （保留回退路径以兼容数据库列缺失或极端情况）
    if aggregated is not None:
        for row in skill_rows:
            metrics = aggregated.get(row.id) or _empty_metrics(row.id)
            metrics.update({
                "skill_id": row.id,
                "skill_name": row.name,
                "period_days": period_days,
            })
            skill_values.append(metrics)
    else:
        try:
            tasktree_slow_query_total.labels(operation="value_summary_fallback").inc()
        except Exception as e:
            logger.debug("value_summary_fallback 指标上报失败: {}", e)
        for row in skill_rows:
            skill_values.append(
                await estimate_value(
                    db,
                    row.id,
                    period_days,
                    hourly_cost_usd=hourly_cost_usd,
                    fallback_baseline_minutes=fallback_baseline_minutes,
                    include_ai=False,
                    user_id=user_id,
                    department=department,
                )
            )

    total_decisions = await _count_decision_logs(db, department, since)
    rejected_decisions = await _count_rejected_decisions(db, department, since)
    token_cost_today = await _load_department_token_cost_today(db, department)

    saved_hours = round(sum(item["saved_hours"] for item in skill_values), 2)
    estimated_cost_saving = round(sum(item["estimated_cost_saving"] for item in skill_values), 2)
    risk_events_prevented = sum(item["risk_events_prevented"] for item in skill_values)
    completed_runs = sum(item["completed_runs"] for item in skill_values)
    failed_runs = sum(item["failed_runs"] for item in skill_values)
    human_takeover_rate = round(
        rejected_decisions / total_decisions, 3
    ) if total_decisions else 0.0
    roi = round(
        estimated_cost_saving / float(token_cost_today), 2
    ) if token_cost_today else None

    result = {
        "department": department,
        "period_days": period_days,
        "skill_count": len(skill_values),
        "run_count": sum(item["run_count"] for item in skill_values),
        "completed_runs": completed_runs,
        "failed_runs": failed_runs,
        "saved_hours": saved_hours,
        "estimated_cost_saving": estimated_cost_saving,
        "risk_events_prevented": risk_events_prevented,
        "human_takeover_rate": human_takeover_rate,
        "token_cost_today": round(float(token_cost_today), 2),
        "roi": roi,
        "skills": sorted(skill_values, key=lambda item: item["saved_hours"], reverse=True)[:20],
        "recommendation": _fallback_department_recommendation({
            "department": department,
            "skill_count": len(skill_values),
            "saved_hours": saved_hours,
            "estimated_cost_saving": estimated_cost_saving,
            "risk_events_prevented": risk_events_prevented,
            "human_takeover_rate": human_takeover_rate,
            "token_cost_today": float(token_cost_today),
            "roi": roi,
        }),
    }

    if include_ai and skill_values:
        recommendation = await _ai_department_recommendation(
            db,
            department,
            result,
            user_id=user_id,
        )
        if recommendation:
            result["recommendation"] = recommendation

    return result


async def _table_columns(db: AsyncSession, table_name: str, *, force: bool = False) -> set[str]:
    """读取表列集合。

    force=True 强制走 DB 探测（用于启动期 warmup）。
    其他路径统一通过 `_get_cached_columns`，只有启动期缓存未就绪时才回退到这里。
    """
    def _inspect(sync_session) -> set[str]:
        bind = sync_session.get_bind()
        return {column["name"] for column in inspect(bind).get_columns(table_name)}

    try:
        return await db.run_sync(_inspect)
    except Exception:
        return set()


async def _get_cached_columns(db: AsyncSession, table_name: str) -> set[str]:
    """优先返回启动期缓存结果；未预热时降级到动态探测（不影响请求）。"""
    cached = _VALUE_COLUMNS_EXIST
    if cached is not None and table_name in cached:
        return cached[table_name]
    return await _table_columns(db, table_name)


def _empty_metrics(skill_id: str) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "run_count": 0,
        "completed_runs": 0,
        "failed_runs": 0,
        "decision_count": 0,
        "completed_decisions": 0,
        "human_takeover_rate": 0.0,
        "saved_hours": 0.0,
        "estimated_cost_saving": 0.0,
        "risk_events_prevented": 0,
        "token_cost_period": 0.0,
        "baseline_minutes": 0.0,
        "average_run_minutes": 0.0,
        "recommendation": "",
    }


async def _bulk_load_skill_metrics(
    db: AsyncSession,
    *,
    skill_ids: list[str],
    since: datetime,
    hourly_cost_usd: float,
    fallback_baseline_minutes: float,
) -> dict[str, dict[str, Any]] | None:
    """单次 GROUP BY 聚合一批 Skill 的执行/决策/token 指标。

    返回 {skill_id: metrics_dict}。任何一个聚合失败都返回 None，调用方回退到 per-skill 路径。
    """
    if not skill_ids:
        return {}

    try:
        # 1) 执行聚合：run_count / completed_runs / failed_runs / duration / baseline
        exec_cols = await _get_cached_columns(db, "execution_runs")
        skill_cols = await _get_cached_columns(db, "skills")
        has_manual_baseline = "manual_baseline_minutes" in exec_cols
        has_skill_baseline = "default_baseline_minutes" in skill_cols

        completed_case = case((ExecutionRun.status == "completed", 1), else_=0)
        failed_case = case(
            (ExecutionRun.status.in_(["failed", "timeout", "blocked"]), 1),
            else_=0,
        )
        # [M5] 只把 completed 的 run 的 duration_ms 算进分母。
        # 失败 run 往往会因为卡主/超时/人工 abort 产生极端长的 duration，
        # 继续纳入平均会让 avg_actual_min 虚高 → saved_per_run_min 缩水。
        duration_completed_case = case(
            (ExecutionRun.status == "completed", ExecutionStep.duration_ms),
            else_=0,
        )
        # 每个 run 取 duration_ms 总和（一个 run 多步的情况下按 step 汇总，
        # 但只统计 completed run 的 step）
        exec_stmt = (
            select(
                ExecutionStep.skill_id.label("skill_id"),
                func.count(func.distinct(ExecutionRun.id)).label("run_count"),
                func.sum(completed_case).label("completed_runs"),
                func.sum(failed_case).label("failed_runs"),
                func.coalesce(func.sum(duration_completed_case), 0).label("duration_ms_sum"),
            )
            .select_from(ExecutionRun)
            .join(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
            .where(ExecutionStep.skill_id.in_(skill_ids))
            .where(ExecutionRun.started_at >= since)
            .group_by(ExecutionStep.skill_id)
        )
        exec_rows = (await db.execute(exec_stmt)).all()

        # 2) 决策聚合：total / rejected / completed
        decision_stmt = (
            select(
                DecisionLog.skill_id.label("skill_id"),
                func.count(DecisionLog.id).label("decision_count"),
                func.sum(
                    case(
                        (
                            (DecisionLog.user_action == "rejected")
                            | (DecisionLog.approval_status == "rejected"),
                            1,
                        ),
                        else_=0,
                    )
                ).label("rejected_count"),
                func.sum(
                    case((DecisionLog.user_action == "completed", 1), else_=0)
                ).label("completed_count"),
            )
            .where(DecisionLog.skill_id.in_(skill_ids))
            .where(DecisionLog.created_at >= since)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .group_by(DecisionLog.skill_id)
        )
        decision_rows = (await db.execute(decision_stmt)).all()

        # 3) token 成本聚合
        cost_stmt = (
            select(
                UsageLog.skill_id.label("skill_id"),
                func.coalesce(func.sum(UsageLog.cost_usd), 0).label("token_cost_period"),
            )
            .where(UsageLog.skill_id.in_(skill_ids))
            .where(UsageLog.ts >= since)
            .group_by(UsageLog.skill_id)
        )
        cost_rows = (await db.execute(cost_stmt)).all()

        # 4) baseline：manual（run 级）vs skill default（skill 级）
        manual_baseline_by_skill: dict[str, float] = {}
        if has_manual_baseline:
            mb_stmt = (
                select(
                    ExecutionStep.skill_id.label("skill_id"),
                    func.avg(ExecutionRun.manual_baseline_minutes).label("avg_manual"),
                )
                .select_from(ExecutionRun)
                .join(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
                .where(ExecutionStep.skill_id.in_(skill_ids))
                .where(ExecutionRun.started_at >= since)
                .where(ExecutionRun.status == "completed")
                .group_by(ExecutionStep.skill_id)
            )
            for row in (await db.execute(mb_stmt)).all():
                if row.avg_manual is not None:
                    manual_baseline_by_skill[row.skill_id] = float(row.avg_manual)

        skill_default_by_skill: dict[str, float] = {}
        if has_skill_baseline:
            sb_stmt = (
                select(Skill.id.label("skill_id"), Skill.default_baseline_minutes)
                .where(Skill.id.in_(skill_ids))
            )
            for row in (await db.execute(sb_stmt)).all():
                if row.default_baseline_minutes is not None:
                    skill_default_by_skill[row.skill_id] = float(row.default_baseline_minutes)

        # 汇总
        decision_map = {row.skill_id: row for row in decision_rows}
        cost_map = {row.skill_id: float(row.token_cost_period or 0) for row in cost_rows}
        metrics_map: dict[str, dict[str, Any]] = {}
        for row in exec_rows:
            skill_id = row.skill_id
            run_count = int(row.run_count or 0)
            completed_runs = int(row.completed_runs or 0)
            failed_runs = int(row.failed_runs or 0)
            duration_ms_sum = float(row.duration_ms_sum or 0)
            # baseline 选取：manual > skill_default > fallback
            baseline_min = (
                manual_baseline_by_skill.get(skill_id)
                or skill_default_by_skill.get(skill_id)
                or fallback_baseline_minutes
            )
            avg_actual_min = (
                round((duration_ms_sum / 60000.0) / completed_runs, 1)
                if completed_runs
                else 0.0
            )
            # 节省时长 = max(baseline - actual, 0) * completed_runs（转小时）
            saved_per_run_min = max(baseline_min - avg_actual_min, 0.0) if completed_runs else 0.0
            saved_hours = round((saved_per_run_min * completed_runs) / 60.0, 2)

            decision_row = decision_map.get(skill_id)
            decision_count = int(decision_row.decision_count) if decision_row else 0
            rejected_count = int(decision_row.rejected_count or 0) if decision_row else 0
            completed_decisions = int(decision_row.completed_count or 0) if decision_row else 0
            takeover_rate = (
                round(rejected_count / decision_count, 3) if decision_count else 0.0
            )

            metrics_map[skill_id] = {
                "skill_id": skill_id,
                "run_count": run_count,
                "completed_runs": completed_runs,
                "failed_runs": failed_runs,
                "decision_count": decision_count,
                "completed_decisions": completed_decisions,
                "human_takeover_rate": takeover_rate,
                "saved_hours": saved_hours,
                "estimated_cost_saving": round(saved_hours * float(hourly_cost_usd), 2),
                "risk_events_prevented": failed_runs + rejected_count,
                "token_cost_period": round(cost_map.get(skill_id, 0.0), 2),
                "baseline_minutes": round(baseline_min, 1),
                "average_run_minutes": avg_actual_min,
                "recommendation": "",
            }

        # 没跑过的 skill 也返回一条空记录，保证下游 skill_count / 排序逻辑一致
        for skill_id in skill_ids:
            metrics_map.setdefault(skill_id, _empty_metrics(skill_id))

        return metrics_map
    except Exception as exc:  # noqa: BLE001
        logger.warning("_bulk_load_skill_metrics 聚合失败，回退到 per-skill 路径: {}", exc)
        return None


async def _load_skill_runs(db: AsyncSession, skill_id: str, since: datetime):
    stmt = (
        select(
            ExecutionRun.id.label("run_id"),
            ExecutionRun.status.label("run_status"),
            ExecutionRun.started_at,
            ExecutionRun.completed_at,
            func.coalesce(func.sum(ExecutionStep.duration_ms), 0).label("duration_ms"),
        )
        .join(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
        .where(ExecutionStep.skill_id == skill_id)
        .where(ExecutionRun.started_at >= since)
        .group_by(
            ExecutionRun.id,
            ExecutionRun.status,
            ExecutionRun.started_at,
            ExecutionRun.completed_at,
        )
        .order_by(ExecutionRun.started_at.desc(), ExecutionRun.id.desc())
    )
    return list((await db.execute(stmt)).all())


async def _load_skill_decision_logs(db: AsyncSession, skill_id: str, since: datetime):
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _load_skill_token_cost(db: AsyncSession, skill_id: str, since: datetime) -> float:
    stmt = (
        select(func.coalesce(func.sum(UsageLog.cost_usd), 0))
        .where(UsageLog.skill_id == skill_id)
        .where(UsageLog.ts >= since)
    )
    result = await db.execute(stmt)
    return float(result.scalar() or 0)


async def _load_department_token_cost_today(db: AsyncSession, department: str) -> float:
    today = now_bjt().date()
    stmt = (
        select(func.coalesce(func.sum(UsageLog.cost_usd), 0))
        .where(UsageLog.department == department)
        .where(func.date(UsageLog.ts) == today)
    )
    result = await db.execute(stmt)
    return float(result.scalar() or 0)


async def _count_decision_logs(db: AsyncSession, department: str, since: datetime) -> int:
    stmt = (
        select(func.count(DecisionLog.id))
        .join(Skill, Skill.id == DecisionLog.skill_id)
        .where(Skill.department == department)
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
    )
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


async def _count_rejected_decisions(db: AsyncSession, department: str, since: datetime) -> int:
    stmt = (
        select(func.count(DecisionLog.id))
        .join(Skill, Skill.id == DecisionLog.skill_id)
        .where(Skill.department == department)
        .where(DecisionLog.created_at >= since)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .where((DecisionLog.user_action == "rejected") | (DecisionLog.approval_status == "rejected"))
    )
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


async def _load_manual_baselines(db: AsyncSession, run_ids: list[str]) -> dict[str, float]:
    if not run_ids:
        return {}
    stmt = text(
        "SELECT id, manual_baseline_minutes "
        "FROM execution_runs "
        "WHERE id IN :run_ids"
    ).bindparams(bindparam("run_ids", expanding=True))
    result = await db.execute(stmt, {"run_ids": run_ids})
    rows = result.mappings().all()
    baselines: dict[str, float] = {}
    for row in rows:
        value = row.get("manual_baseline_minutes")
        if value is not None:
            baselines[str(row["id"])] = float(value)
    return baselines


async def _load_skill_default_baseline(db: AsyncSession, skill_id: str) -> float | None:
    stmt = text("SELECT default_baseline_minutes FROM skills WHERE id = :skill_id")
    result = await db.execute(stmt, {"skill_id": skill_id})
    row = result.mappings().first()
    if not row:
        return None
    value = row.get("default_baseline_minutes")
    return float(value) if value is not None else None


def _run_minutes(row) -> float:
    duration_ms = float(row.duration_ms or 0)
    if duration_ms > 0:
        return round(duration_ms / 60000.0, 2)
    if row.started_at and row.completed_at:
        delta = row.completed_at - row.started_at
        return round(max(delta.total_seconds() / 60.0, 0.0), 2)
    return 0.0


def _resolve_baseline_minutes(
    *,
    run_id: str,
    manual_baselines: dict[str, float],
    skill_default_baseline: float,
    fallback_baseline_minutes: float,
) -> float:
    baseline = manual_baselines.get(run_id)
    if baseline is not None and baseline > 0:
        return float(baseline)
    if skill_default_baseline > 0:
        return float(skill_default_baseline)
    return float(fallback_baseline_minutes)


async def _ai_recommendation(
    db: AsyncSession,
    skill: Skill,
    value_result: dict,
    *,
    user_id: str | None = None,
    department: str | None = None,
) -> str | None:
    prompt_payload = {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "period_days": value_result["period_days"],
        "run_count": value_result["run_count"],
        "completed_runs": value_result["completed_runs"],
        "failed_runs": value_result["failed_runs"],
        "saved_hours": value_result["saved_hours"],
        "estimated_cost_saving": value_result["estimated_cost_saving"],
        "risk_events_prevented": value_result["risk_events_prevented"],
        "human_takeover_rate": value_result["human_takeover_rate"],
        "token_cost_period": value_result["token_cost_period"],
        "baseline_minutes": value_result["baseline_minutes"],
    }
    prompt_hash = hashlib.md5(
        json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    system = (
        "你是任务树价值分析助手。根据业务统计输出一句中文建议，"
        "重点说明是否继续投入、是否需要补 baseline、是否要先修复稳定性。"
        "不要分点，不要输出 JSON。"
    )
    user = (
        f"## 统计数据\n```json\n{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "请输出 1 段话，不超过 180 字。"
    )
    try:
        result = await call_llm_cached(
            f"tasktree:value:{skill.id}:{value_result['period_days']}:{prompt_hash}",
            system,
            user,
            cache_ttl=600,
            max_tokens=500,
            timeout=20,
            json_mode=False,
            call_source="tasktree.value_estimate",
            cost_context={
                "skill_id": skill.id,
                "department": department or skill.department,
                "user_id": user_id,
                "prompt_hash": prompt_hash,
            },
        )
    except Exception as e:
        logger.warning(
            "AI 诊断失败 (skill={}, period_days={}): {}",
            skill.id,
            value_result.get("period_days"),
            e,
            exc_info=True,
        )
        return None

    if isinstance(result, dict):
        return str(result.get("recommendation") or result.get("summary") or "").strip() or None
    if isinstance(result, str):
        return result.strip() or None
    return None


async def _ai_department_recommendation(
    db: AsyncSession,
    department: str,
    summary: dict,
    *,
    user_id: str | None = None,
) -> str | None:
    prompt_hash = hashlib.md5(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    system = (
        "你是任务树部门驾驶舱助手。根据部门价值汇总给出一句中文经营建议，"
        "要覆盖投入、稳定性、扩量和成本四个方向。不要分点。"
    )
    user = (
        f"## 部门汇总\n```json\n{json.dumps(summary, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        "请输出 1 段话，不超过 200 字。"
    )
    try:
        result = await call_llm_cached(
            f"tasktree:dept_value:{department}:{prompt_hash}",
            system,
            user,
            cache_ttl=900,
            max_tokens=600,
            timeout=25,
            json_mode=False,
            call_source="tasktree.department_value",
            cost_context={
                "department": department,
                "user_id": user_id,
                "prompt_hash": prompt_hash,
            },
        )
    except Exception as e:
        logger.warning(
            "AI 部门建议失败 (department={}): {}",
            department,
            e,
            exc_info=True,
        )
        return None

    if isinstance(result, dict):
        return str(result.get("recommendation") or result.get("summary") or "").strip() or None
    if isinstance(result, str):
        return result.strip() or None
    return None


def _fallback_recommendation(skill_name: str, metrics: dict[str, Any], hourly_cost_usd: float) -> str:
    if metrics["run_count"] <= 0:
        return f"{skill_name} 目前历史样本不足，先继续积累执行数据，再评估继续投入。"
    failure_rate = metrics["failed_runs"] / metrics["run_count"]
    if failure_rate >= 0.3 or metrics["human_takeover_rate"] >= 0.35:
        return f"{skill_name} 当前失败率或人工接管偏高，建议先修复稳定性和输入边界，再扩大使用。"
    if metrics["saved_hours"] >= 10 and metrics["estimated_cost_saving"] >= hourly_cost_usd * 10:
        return f"{skill_name} 已产生明确节省，建议继续投入，并复制到相似业务场景。"
    return f"{skill_name} 当前收益为正，建议继续投入，同时补齐 baseline 和业务影响标注。"


def _fallback_department_recommendation(summary: dict[str, Any]) -> str:
    if summary["skill_count"] <= 0:
        return f"{summary['department']} 部门暂无任务树样本，先补齐运行数据后再评估 ROI。"
    if summary["human_takeover_rate"] >= 0.3:
        return f"{summary['department']} 部门人工接管偏高，优先修复高风险 Skill，再继续扩量。"
    if summary["roi"] is not None and summary["roi"] >= 3:
        return f"{summary['department']} 部门价值回报较好，建议继续投入并横向复制成熟 Skill。"
    return f"{summary['department']} 部门当前处于可观测阶段，建议持续优化稳定性和价值归因。"
