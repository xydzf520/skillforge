"""批量参数调优服务。

选多个参数 → 设搜索范围 → 网格搜索最优组合。
通过历史回放对比不同参数组合的效果。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.testing.replay import replay_service


@dataclass
class ParamRange:
    """参数搜索范围"""
    name: str
    values: list  # 离散值列表


@dataclass
class TuningResult:
    """调优结果"""
    params: dict
    total_count: int = 0
    changed_count: int = 0
    improvement_score: float = 0.0


async def grid_search(
    db: AsyncSession,
    skill_id: str,
    param_ranges: list[ParamRange],
    *,
    date_from: str = "",
    date_to: str = "",
    days: int = 7,
    max_combinations: int = 50,
) -> dict:
    """网格搜索最优参数组合。

    Args:
        param_ranges: 每个参数的候选值列表
        max_combinations: 最大组合数限制（防止爆炸）

    Returns:
        {
            "skill_id": str,
            "combinations_tested": int,
            "best": {"params": {...}, "score": float},
            "results": [...]
        }
    """
    from datetime import timedelta
    from app.common.time_utils import now_bjt

    if not date_from:
        now = now_bjt()
        date_to = date_to or now.strftime("%Y-%m-%d")
        date_from = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # 生成参数组合
    names = [pr.name for pr in param_ranges]
    value_lists = [pr.values for pr in param_ranges]
    combinations = list(itertools.product(*value_lists))

    if len(combinations) > max_combinations:
        logger.warning(
            "参数组合数 {} 超过上限 {}，截断",
            len(combinations), max_combinations,
        )
        combinations = combinations[:max_combinations]

    results: list[TuningResult] = []

    for combo in combinations:
        params = dict(zip(names, combo))
        try:
            replay_result = await replay_service.run_replay(
                skill_id, date_from, date_to, new_params=params,
            )
            total = replay_result.get("total_count", 0)
            changed = replay_result.get("changed_count", 0)
            # 简单评分：变化越少越稳定（保守策略），可根据业务调整
            score = 1.0 - (changed / max(total, 1))
            results.append(TuningResult(
                params=params,
                total_count=total,
                changed_count=changed,
                improvement_score=round(score, 4),
            ))
        except Exception as e:
            logger.warning("参数组合 {} 回放失败: {}", params, e)
            results.append(TuningResult(params=params, improvement_score=-1))

    # 按评分排序
    results.sort(key=lambda r: r.improvement_score, reverse=True)
    best = results[0] if results else None

    return {
        "skill_id": skill_id,
        "period": {"from": date_from, "to": date_to},
        "combinations_tested": len(results),
        "best": {
            "params": best.params,
            "score": best.improvement_score,
            "total_count": best.total_count,
            "changed_count": best.changed_count,
        } if best else None,
        "results": [
            {
                "params": r.params,
                "score": r.improvement_score,
                "total_count": r.total_count,
                "changed_count": r.changed_count,
            }
            for r in results
        ],
    }
