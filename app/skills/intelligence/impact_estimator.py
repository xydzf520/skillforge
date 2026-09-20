"""变更影响预估服务。

Publish 前自动跑历史数据对比新旧版本输出差异，
帮助用户评估改动的实际影响范围。
"""

from __future__ import annotations

from datetime import timedelta

from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time_utils import now_bjt, parse_bjt_datetime
from app.execution.models import ExecutionRun


async def estimate_impact(
    db: AsyncSession,
    skill_id: str,
    *,
    date_from: str = "",
    date_to: str = "",
    days: int = 7,
) -> dict:
    """对比当前版本与历史执行数据，预估改动影响。

    Returns:
        {
            "skill_id": str,
            "period": {"from": str, "to": str},
            "total_executions": int,
            "decision_distribution": {"green": N, "yellow": N, "red": N, ...},
            "estimated_changes": int,
            "change_rate": float,
            "details": [...]
        }
    """
    now = now_bjt()
    if date_to:
        end = parse_bjt_datetime(date_to)
    else:
        end = now
    if date_from:
        start = parse_bjt_datetime(date_from)
    else:
        start = end - timedelta(days=days)

    # 查询历史执行记录
    stmt = (
        select(ExecutionRun)
        .where(ExecutionRun.skill_id == skill_id)
        .where(ExecutionRun.created_at >= start)
        .where(ExecutionRun.created_at <= end)
        .order_by(ExecutionRun.created_at.desc())
        .limit(200)
    )
    result = await db.execute(stmt)
    runs = result.scalars().all()

    # 统计决策分布
    distribution: dict[str, int] = {}
    for run in runs:
        output = run.output_json or {}
        decision = output.get("conclusion") or output.get("decision") or output.get("result") or "unknown"
        if isinstance(decision, str):
            distribution[decision] = distribution.get(decision, 0) + 1

    # 读取当前（未发布）SKILL.md 的参数变化
    import asyncio as _aio
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser

    current_md = await _aio.to_thread(git_service.read_file, skill_id, "SKILL.md")
    head_md = await _aio.to_thread(git_service.get_file_at_commit, skill_id, "SKILL.md", "HEAD~1")

    current_steps = []
    old_steps = []
    if current_md:
        current_parsed = skill_parser.parse(current_md)
        current_steps = current_parsed.steps
    if head_md:
        old_parsed = skill_parser.parse(head_md)
        old_steps = old_parsed.steps

    # 计算条件变化数
    changed_conditions = 0
    current_conditions = {
        (s.id, b.condition) for s in current_steps for b in s.branches
    }
    old_conditions = {
        (s.id, b.condition) for s in old_steps for b in s.branches
    }
    added = current_conditions - old_conditions
    removed = old_conditions - current_conditions
    changed_conditions = len(added) + len(removed)

    # 粗略估算影响执行数（条件变化比率 × 总执行数）
    total_conditions = max(len(current_conditions), 1)
    change_ratio = min(changed_conditions / total_conditions, 1.0)
    estimated_changes = int(len(runs) * change_ratio)

    return {
        "skill_id": skill_id,
        "period": {
            "from": start.isoformat()[:10],
            "to": end.isoformat()[:10],
        },
        "total_executions": len(runs),
        "decision_distribution": distribution,
        "condition_changes": {
            "added": len(added),
            "removed": len(removed),
            "total_current": len(current_conditions),
        },
        "estimated_affected_executions": estimated_changes,
        "change_rate": round(change_ratio, 3),
    }
