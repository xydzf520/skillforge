"""Parameter tuning features extracted from ai_service.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import call_llm_cached
from app.execution.models import DecisionLog
from app.skills.intelligence.ai_service_helpers import _truncate_dict
from app.skills.core.git_service import git_service
from app.skills.core.service_shared import validate_skill_id
from app.common.time_utils import now_bjt


async def suggest_param_tuning(
    db: AsyncSession,
    skill_id: str,
    days: int = 14,
    *,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    validate_skill_id(skill_id)

    since = now_bjt() - timedelta(days=days)
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .where(DecisionLog.created_at >= since)
        .order_by(DecisionLog.created_at.desc())
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    if len(logs) < 20:
        return {
            "suggestions": [],
            "data_quality": {
                "total_decisions": len(logs),
                "minimum_required": 20,
                "message": f"数据不足：最近{days}天仅有{len(logs)}条决策，至少需要20条",
            },
        }

    policy_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
    try:
        current_params = yaml.safe_load(policy_raw) or {}
    except yaml.YAMLError:
        current_params = {}

    if not current_params:
        return {"suggestions": [], "data_quality": {"message": "无 policy_pack.yaml 参数"}}

    total = len(logs)
    adopted = sum(1 for log in logs if log.user_action == "completed")
    rejected = sum(1 for log in logs if log.user_action == "rejected")
    with_feedback = sum(1 for log in logs if log.business_impact)

    stats_lines = [
        f"总决策数: {total}",
        f"采纳数: {adopted} ({round(adopted/total*100)}%)",
        f"否决数: {rejected} ({round(rejected/total*100)}%)",
        f"有业务反馈: {with_feedback}",
    ]

    samples = []
    for log in logs[:30]:
        samples.append({
            "date": log.created_at.strftime("%Y-%m-%d") if log.created_at else "",
            "input_snapshot": _truncate_dict(log.input_snapshot, 200),
            "suggested_action": log.suggested_action,
            "user_action": log.user_action,
            "feedback": _truncate_dict(log.business_impact, 100),
        })

    system = (
        "你是SkillForge参数调优分析师。分析历史执行数据，给出参数调优建议。"
        "输出纯JSON: {\"suggestions\": [{\"param\": \"参数名\", \"current_value\": 当前值, "
        "\"suggested_value\": 建议值, \"confidence\": 0-1, \"evidence\": \"数据依据\"}]}"
    )
    user = (
        f"## 当前参数\n```yaml\n{yaml.dump(current_params, allow_unicode=True)}```\n\n"
        f"## 统计概览（最近{days}天）\n" + "\n".join(stats_lines) + "\n\n"
        f"## 决策采样（{len(samples)}条）\n```json\n{json.dumps(samples, ensure_ascii=False, indent=1)}\n```\n\n"
        "## 分析要求\n"
        "1. 找出当前参数值与最优区间的偏差\n"
        "2. 给出具体调整建议和置信度(0-1)\n"
        "3. 用数据支撑每条建议\n"
    )

    cache_key = f"param_tuning:{skill_id}:{days}"
    ai_result = await call_llm_cached(
        cache_key, system, user,
        cache_ttl=600, max_tokens=2000, timeout=30,
        call_source="skill.param_tuning",
        cost_context={
            "skill_id": skill_id,
            "user_id": user_id,
            "department": department,
        },
    )

    suggestions = ai_result.get("suggestions", []) if ai_result else []
    return {
        "suggestions": suggestions,
        "data_quality": {
            "total_decisions": total,
            "decisions_with_feedback": with_feedback,
            "analysis_period": f"{since.strftime('%Y-%m-%d')} ~ {now_bjt().strftime('%Y-%m-%d')}",
        },
    }
