"""§6.2 数据漂移检测 — KL 散度对比当前输入分布 vs 基线。

适用场景：
  - 数值字段分布偏移（ROI、金额、时长等）
  - 类别字段比例偏移（部门、类目）
  - 检测输入数据特征是否发生显著变化
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import math

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import DecisionLog
from app.common.time_utils import now_bjt


@dataclass
class DriftReport:
    """字段漂移报告。"""
    field: str
    field_type: str              # numeric / categorical
    kl_divergence: float
    baseline_size: int
    current_size: int
    top_shifts: list[dict] = field(default_factory=list)  # 分布变化最大的桶
    severity: str = "low"        # low / medium / high
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _kl_divergence(p: dict, q: dict, epsilon: float = 1e-6) -> float:
    """计算离散分布 P 相对 Q 的 KL 散度 D_KL(P || Q)。

    P 和 Q 都是 {bucket: count} 格式，内部归一化为概率。
    使用 epsilon 平滑避免 log(0)。
    """
    all_keys = set(p.keys()) | set(q.keys())
    if not all_keys:
        return 0.0
    p_total = sum(p.values()) or 1
    q_total = sum(q.values()) or 1

    kl = 0.0
    for key in all_keys:
        p_prob = (p.get(key, 0) + epsilon) / (p_total + epsilon * len(all_keys))
        q_prob = (q.get(key, 0) + epsilon) / (q_total + epsilon * len(all_keys))
        if p_prob > 0:
            kl += p_prob * math.log(p_prob / q_prob)
    return max(kl, 0.0)


def _bucket_numeric(value: float, bucket_size: int = 10) -> str:
    """数值分桶：按 bucket_size 粒度落桶。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "invalid"
    if math.isnan(v) or math.isinf(v):
        return "invalid"
    bucket = int(v // bucket_size) * bucket_size
    return f"[{bucket},{bucket + bucket_size})"


def _kl_severity(kl: float) -> str:
    """根据 KL 散度大小判断严重度。"""
    if kl >= 0.5:
        return "high"
    if kl >= 0.2:
        return "medium"
    return "low"


async def detect_drift(
    db: AsyncSession,
    skill_id: str,
    *,
    window_hours: int = 24,
    baseline_days: int = 14,
    min_samples: int = 30,
) -> list[DriftReport]:
    """检测 Skill 输入数据的字段漂移。

    从 DecisionLog.input_snapshot 采样前后两个窗口的输入，对每个字段计算 KL 散度。
    """
    reports: list[DriftReport] = []

    now = now_bjt()
    window_start = now - timedelta(hours=window_hours)
    baseline_start = now - timedelta(days=baseline_days)

    try:
        stmt = (
            select(DecisionLog.input_snapshot, DecisionLog.created_at)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .where(DecisionLog.created_at >= baseline_start)
            .limit(3000)
        )
        rows = (await db.execute(stmt)).all()
    except Exception as e:
        logger.debug("drift 查询失败 skill_id={}: {}", skill_id, e)
        return reports

    if len(rows) < min_samples:
        return reports

    # 按字段分桶：{field_name: {"cur": {bucket: count}, "base": {...}, "type": "numeric|categorical"}}
    field_dists: dict[str, dict] = {}

    for snapshot, created_at in rows:
        if not isinstance(snapshot, dict) or not created_at:
            continue
        is_current = created_at >= window_start

        for field_name, value in snapshot.items():
            if value is None or isinstance(value, (dict, list)):
                continue

            # 推断字段类型并分桶
            try:
                float_val = float(value)
                is_numeric = not isinstance(value, bool)
            except (TypeError, ValueError):
                is_numeric = False

            if is_numeric:
                bucket = _bucket_numeric(float_val)
                ftype = "numeric"
            else:
                bucket = str(value)[:30]
                ftype = "categorical"

            if field_name not in field_dists:
                field_dists[field_name] = {"cur": {}, "base": {}, "type": ftype}
            elif field_dists[field_name]["type"] != ftype:
                # 类型冲突 → 降级为 categorical
                field_dists[field_name]["type"] = "categorical"

            target = field_dists[field_name]["cur"] if is_current else field_dists[field_name]["base"]
            target[bucket] = target.get(bucket, 0) + 1

    # 对每个字段计算 KL 散度
    for field_name, data in field_dists.items():
        cur = data["cur"]
        base = data["base"]
        cur_size = sum(cur.values())
        base_size = sum(base.values())

        # 双方都要有足够样本才有意义
        if cur_size < 10 or base_size < 10:
            continue

        kl = _kl_divergence(cur, base)
        if kl < 0.1:
            continue  # 太小忽略

        # 找出差异最大的 3 个桶
        all_buckets = set(cur.keys()) | set(base.keys())
        shifts = []
        for b in all_buckets:
            cur_prob = cur.get(b, 0) / max(cur_size, 1)
            base_prob = base.get(b, 0) / max(base_size, 1)
            shifts.append({
                "bucket": b,
                "baseline_ratio": round(base_prob, 3),
                "current_ratio": round(cur_prob, 3),
                "delta": round(cur_prob - base_prob, 3),
            })
        shifts.sort(key=lambda s: -abs(s["delta"]))

        reports.append(DriftReport(
            field=field_name,
            field_type=data["type"],
            kl_divergence=round(kl, 4),
            baseline_size=base_size,
            current_size=cur_size,
            top_shifts=shifts[:3],
            severity=_kl_severity(kl),
            description=f"字段 {field_name} 分布偏移 KL={round(kl, 3)}",
        ))

    # 按 KL 大小排序
    reports.sort(key=lambda r: -r.kl_divergence)
    return reports
