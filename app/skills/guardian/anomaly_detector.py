"""分群异常检测 — 多维度检测 Skill 运行偏离基线。

master plan §6.2 维度：
  - 误判率突增（整体 + 分群）
  - 分支命中异常（某分支命中率突变）
  - 参数漂移（见 drift_detector.py 的 KL 散度）
  - 字段缺失率（通过 input_snapshot 空值比例）
  - 下游反馈（通过 user_action = rejected 比例）
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import defaultdict
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import DecisionLog, ExecutionStep
from app.common.time_utils import now_bjt


@dataclass
class Anomaly:
    """检测到的异常。"""
    skill_id: str
    dimension: str                   # overall / branch / rejection / missing_data
    segment: str                     # 具体分群值
    metric: str                      # failure_rate / hit_rate / rejection_rate
    baseline: float                  # 基线值
    current: float                   # 当前值
    z_score: float
    severity: str = "medium"         # low/medium/high/critical
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _relative_severity(current: float, base: float) -> str:
    """根据当前 vs 基线比值决定严重度。"""
    if base <= 0:
        return "high" if current > 0.1 else "medium"
    ratio = current / base
    if ratio >= 3.0:
        return "critical"
    if ratio >= 2.0:
        return "high"
    if ratio >= 1.5:
        return "medium"
    return "low"


async def _detect_overall_failure_rate(
    db: AsyncSession,
    skill_id: str,
    window_start: datetime,
    baseline_start: datetime,
) -> list[Anomaly]:
    """维度 1：整体失败率检测（基于 ExecutionStep）。"""
    out: list[Anomaly] = []

    failed_case = case((ExecutionStep.status == "failed", 1), else_=0)
    current_filter = ExecutionStep.started_at >= window_start
    baseline_filter = (ExecutionStep.started_at >= baseline_start) & (ExecutionStep.started_at < window_start)

    try:
        stmt = (
            select(
                func.sum(case((current_filter, 1), else_=0).cast(Integer)).label("cur_total"),
                func.sum(case((current_filter, failed_case), else_=0).cast(Integer)).label("cur_failed"),
                func.sum(case((baseline_filter, 1), else_=0).cast(Integer)).label("base_total"),
                func.sum(case((baseline_filter, failed_case), else_=0).cast(Integer)).label("base_failed"),
            )
            .where(ExecutionStep.skill_id == skill_id)
            .where(ExecutionStep.started_at >= baseline_start)
        )
        row = (await db.execute(stmt)).first()
    except Exception as e:
        logger.debug("detect_overall_failure_rate 查询失败 skill_id={}: {}", skill_id, e)
        return out

    if not row:
        return out

    cur_total = row[0] or 0
    cur_failed = row[1] or 0
    base_total = row[2] or 0
    base_failed = row[3] or 0

    if cur_total == 0 or base_total == 0:
        return out

    cur_rate = cur_failed / cur_total
    base_rate = base_failed / base_total

    if base_rate > 0 and cur_rate > base_rate * 2.0:
        out.append(Anomaly(
            skill_id=skill_id,
            dimension="overall",
            segment="全部",
            metric="failure_rate",
            baseline=round(base_rate * 100, 2),
            current=round(cur_rate * 100, 2),
            z_score=round((cur_rate - base_rate) / max(base_rate, 0.01), 2),
            severity=_relative_severity(cur_rate, base_rate),
            description=f"失败率从 {base_rate*100:.1f}% 上升到 {cur_rate*100:.1f}%",
        ))
    elif base_rate == 0 and cur_rate > 0.1:
        out.append(Anomaly(
            skill_id=skill_id,
            dimension="overall",
            segment="全部",
            metric="failure_rate",
            baseline=0.0,
            current=round(cur_rate * 100, 2),
            z_score=round(cur_rate * 10, 2),
            severity="high",
            description=f"基线零失败，当前失败率 {cur_rate*100:.1f}%",
        ))

    return out


async def _detect_branch_hit_rate(
    db: AsyncSession,
    skill_id: str,
    window_start: datetime,
    baseline_start: datetime,
) -> list[Anomaly]:
    """维度 2：分支命中率突变（基于 DecisionLog.suggested_action 中的 step/branch 信息）。

    从 suggested_action JSONB 中提取 step_id 或 branch_id，
    按分支统计当前 vs 基线命中比例，差异 >50% 且绝对占比 >5% 的视为异常。
    """
    out: list[Anomaly] = []

    try:
        stmt = (
            select(DecisionLog.suggested_action, DecisionLog.created_at)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .where(DecisionLog.created_at >= baseline_start)
            .limit(2000)  # 硬性上限，避免拉太多
        )
        rows = (await db.execute(stmt)).all()
    except Exception as e:
        logger.debug("branch_hit 查询失败 skill_id={}: {}", skill_id, e)
        return out

    if not rows:
        return out

    # 统计当前窗口和基线窗口各分支的命中
    cur_counts: dict[str, int] = defaultdict(int)
    base_counts: dict[str, int] = defaultdict(int)

    for action_json, created_at in rows:
        if not isinstance(action_json, dict):
            continue
        # 尝试从 suggested_action 的常见字段中拿到分支标识
        branch_id = (
            action_json.get("branch_id")
            or action_json.get("step_id")
            or action_json.get("step")
            or action_json.get("conclusion")
        )
        if not branch_id:
            continue
        branch_id = str(branch_id)[:60]  # 防止超长
        if created_at and created_at >= window_start:
            cur_counts[branch_id] += 1
        else:
            base_counts[branch_id] += 1

    cur_total = sum(cur_counts.values()) or 1
    base_total = sum(base_counts.values()) or 1

    all_branches = set(cur_counts.keys()) | set(base_counts.keys())
    for branch_id in all_branches:
        cur_rate = cur_counts[branch_id] / cur_total
        base_rate = base_counts[branch_id] / base_total

        # 只关注命中率 >5% 的分支（忽略边角情况）
        if max(cur_rate, base_rate) < 0.05:
            continue
        if base_rate <= 0:
            continue
        diff_ratio = abs(cur_rate - base_rate) / base_rate
        if diff_ratio < 0.5:
            continue  # 变化 <50% 不算异常

        out.append(Anomaly(
            skill_id=skill_id,
            dimension="branch",
            segment=f"branch={branch_id}",
            metric="hit_rate",
            baseline=round(base_rate * 100, 2),
            current=round(cur_rate * 100, 2),
            z_score=round(diff_ratio, 2),
            severity="high" if diff_ratio > 2.0 else "medium",
            description=(
                f"分支 {branch_id} 命中率从 {base_rate*100:.1f}% 变化到 {cur_rate*100:.1f}%"
                f"（{'上升' if cur_rate > base_rate else '下降'}）"
            ),
        ))

    return out


async def _detect_segmented_failure_rate(
    db: AsyncSession,
    skill_id: str,
    window_start: datetime,
    baseline_start: datetime,
) -> list[Anomaly]:
    """维度 4：按分群检测 rejection 率异常。

    §6.2 要求：按部门/类目/时段分群。
    从 DecisionLog.input_snapshot 尝试抽取 category/department/region 字段。
    按时段做粗粒度（work_hour / off_hour）。
    """
    out: list[Anomaly] = []

    try:
        stmt = (
            select(DecisionLog.input_snapshot, DecisionLog.user_action, DecisionLog.created_at)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .where(DecisionLog.created_at >= baseline_start)
            .limit(2000)
        )
        rows = (await db.execute(stmt)).all()
    except Exception as e:
        logger.debug("segmented_failure 查询失败: {}", e)
        return out

    if len(rows) < 20:
        return out

    segments: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"cur_total": 0, "cur_rejected": 0, "base_total": 0, "base_rejected": 0}
    )

    for snapshot, action, created_at in rows:
        is_current = created_at and created_at >= window_start
        rejected = action == "rejected"

        if isinstance(snapshot, dict):
            for field_name in ("category", "department", "region", "account_type", "channel"):
                val = snapshot.get(field_name)
                if val is None or val == "":
                    continue
                seg = (field_name, str(val)[:40])
                bucket = segments[seg]
                if is_current:
                    bucket["cur_total"] += 1
                    if rejected:
                        bucket["cur_rejected"] += 1
                else:
                    bucket["base_total"] += 1
                    if rejected:
                        bucket["base_rejected"] += 1

        if created_at:
            is_offhour = created_at.hour < 8 or created_at.hour >= 20
            time_seg = ("time_window", "off_hour" if is_offhour else "work_hour")
            bucket = segments[time_seg]
            if is_current:
                bucket["cur_total"] += 1
                if rejected:
                    bucket["cur_rejected"] += 1
            else:
                bucket["base_total"] += 1
                if rejected:
                    bucket["base_rejected"] += 1

    for (dim_name, seg_value), stats in segments.items():
        cur_total = stats["cur_total"]
        base_total = stats["base_total"]
        if cur_total < 5 or base_total < 10:
            continue
        cur_rate = stats["cur_rejected"] / cur_total
        base_rate = stats["base_rejected"] / base_total

        if base_rate <= 0:
            if cur_rate > 0.2:
                out.append(Anomaly(
                    skill_id=skill_id,
                    dimension=f"segment:{dim_name}",
                    segment=seg_value,
                    metric="rejection_rate",
                    baseline=0.0,
                    current=round(cur_rate * 100, 2),
                    z_score=round(cur_rate * 5, 2),
                    severity="high",
                    description=f"{dim_name}={seg_value} 基线无驳回，当前驳回率 {cur_rate*100:.1f}%",
                ))
            continue

        ratio = cur_rate / base_rate
        if ratio >= 1.5 and cur_rate > 0.1:
            out.append(Anomaly(
                skill_id=skill_id,
                dimension=f"segment:{dim_name}",
                segment=seg_value,
                metric="rejection_rate",
                baseline=round(base_rate * 100, 2),
                current=round(cur_rate * 100, 2),
                z_score=round((cur_rate - base_rate) / max(base_rate, 0.01), 2),
                severity=_relative_severity(cur_rate, base_rate),
                description=(
                    f"{dim_name}={seg_value} 驳回率从 {base_rate*100:.1f}% 升至 "
                    f"{cur_rate*100:.1f}%（{int((ratio-1)*100)}% 相对上升）"
                ),
            ))

    return out


async def _detect_rejection_rate(
    db: AsyncSession,
    skill_id: str,
    window_start: datetime,
    baseline_start: datetime,
) -> list[Anomaly]:
    """维度 3：下游反馈 — user_action=rejected 比例突增。"""
    out: list[Anomaly] = []

    try:
        stmt = (
            select(DecisionLog.user_action, DecisionLog.created_at)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .where(DecisionLog.created_at >= baseline_start)
            .limit(2000)
        )
        rows = (await db.execute(stmt)).all()
    except Exception as e:
        logger.debug("rejection_rate 查询失败: {}", e)
        return out

    cur_total = 0
    cur_rejected = 0
    base_total = 0
    base_rejected = 0
    for action, created_at in rows:
        if created_at and created_at >= window_start:
            cur_total += 1
            if action == "rejected":
                cur_rejected += 1
        else:
            base_total += 1
            if action == "rejected":
                base_rejected += 1

    if cur_total < 10 or base_total < 10:
        return out

    cur_rate = cur_rejected / cur_total
    base_rate = base_rejected / base_total

    if base_rate > 0 and cur_rate > base_rate * 1.5 and cur_rate > 0.1:
        out.append(Anomaly(
            skill_id=skill_id,
            dimension="rejection",
            segment="下游反馈",
            metric="rejection_rate",
            baseline=round(base_rate * 100, 2),
            current=round(cur_rate * 100, 2),
            z_score=round((cur_rate - base_rate) / max(base_rate, 0.01), 2),
            severity=_relative_severity(cur_rate, base_rate),
            description=f"人工驳回率从 {base_rate*100:.1f}% 升至 {cur_rate*100:.1f}%",
        ))
    elif base_rate == 0 and cur_rate > 0.2:
        out.append(Anomaly(
            skill_id=skill_id,
            dimension="rejection",
            segment="下游反馈",
            metric="rejection_rate",
            baseline=0.0,
            current=round(cur_rate * 100, 2),
            z_score=round(cur_rate * 5, 2),
            severity="high",
            description=f"基线零驳回，当前驳回率 {cur_rate*100:.1f}%",
        ))

    return out


async def detect_anomalies(
    db: AsyncSession,
    skill_id: str,
    *,
    window_hours: int = 24,
    baseline_days: int = 30,
) -> list[Anomaly]:
    """检测 Skill 的运行异常。

    §6.2 多维度并行检测：
    1. overall failure rate
    2. branch hit rate
    3. rejection rate (下游反馈)
    """
    import asyncio

    now = now_bjt()
    window_start = now - timedelta(hours=window_hours)
    baseline_start = now - timedelta(days=baseline_days)

    # 并行四个维度检测
    results = await asyncio.gather(
        _detect_overall_failure_rate(db, skill_id, window_start, baseline_start),
        _detect_branch_hit_rate(db, skill_id, window_start, baseline_start),
        _detect_rejection_rate(db, skill_id, window_start, baseline_start),
        _detect_segmented_failure_rate(db, skill_id, window_start, baseline_start),
        return_exceptions=True,
    )

    anomalies: list[Anomaly] = []
    for r in results:
        if isinstance(r, Exception):
            logger.debug("anomaly dimension 检测失败: {}", r)
            continue
        anomalies.extend(r or [])

    # 按严重度排序
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    anomalies.sort(key=lambda a: severity_order.get(a.severity, 4))
    return anomalies
