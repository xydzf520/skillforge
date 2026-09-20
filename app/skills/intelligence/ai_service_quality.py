"""Quality/discovery AI features extracted from ai_service.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import call_llm, call_llm_cached
from app.execution.models import DecisionLog
from app.skills.intelligence.ai_service_helpers import _truncate_dict
from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser
from app.skills.core.service_shared import validate_skill_id
from app.common.time_utils import now_bjt


async def discover_antipatterns(
    db: AsyncSession,
    skill_id: str,
    days: int = 30,
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
        .where(DecisionLog.user_action == "rejected")
        .where(DecisionLog.created_at >= since)
        .order_by(DecisionLog.created_at.desc())
        .limit(50)
    )
    result = await db.execute(stmt)
    rejected_logs = result.scalars().all()

    if len(rejected_logs) < 5:
        return {
            "discovered": [],
            "stats": {
                "total_overridden": len(rejected_logs),
                "message": f"否决数据不足：最近{days}天仅有{len(rejected_logs)}条否决记录，至少需要5条",
            },
        }

    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    parsed = skill_parser.parse(skill_md) if skill_md else None
    existing_aps = []
    if parsed and parsed.antipatterns:
        existing_aps = [{"scenario": item.scenario, "action": item.correct_action} for item in parsed.antipatterns]

    overrides = []
    for log in rejected_logs:
        overrides.append({
            "date": log.created_at.strftime("%Y-%m-%d") if log.created_at else "",
            "input": _truncate_dict(log.input_snapshot, 200),
            "ai_suggestion": log.suggested_action,
            "user_action": log.user_action,
            "feedback": log.user_feedback or "",
            "reject_reason": log.reject_reason or "",
        })

    system = (
        "你是Skill反例分析师。从被否决的AI决策中发现潜在反例模式。"
        "输出纯JSON: {\"discovered\": [{\"title\": \"标题\", \"confidence\": \"high/medium/low\", "
        "\"trigger_condition\": \"触发条件\", \"occurrences\": 次数, "
        "\"suggested_rule\": \"建议规则\", \"feedback_summary\": \"运营反馈汇总\"}]}"
    )
    user = (
        f"## 被否决决策记录（最近{days}天，共{len(overrides)}条）\n"
        f"```json\n{json.dumps(overrides, ensure_ascii=False, indent=1)}\n```\n\n"
        f"## 现有反例列表\n```json\n{json.dumps(existing_aps, ensure_ascii=False)}\n```\n\n"
        "## 分析要求\n"
        "1. 按输入数据特征聚类被否决记录\n"
        "2. 找出反复出现的否决模式（出现≥2次）\n"
        "3. 排除已在现有反例列表中的模式\n"
        "4. 每个模式给出触发条件和建议规则\n"
    )

    cache_key = f"discover_ap:{skill_id}:{days}"
    ai_result = await call_llm_cached(
        cache_key, system, user,
        cache_ttl=1800, max_tokens=3000, timeout=45,
        call_source="skill.discover_antipatterns",
        cost_context={
            "skill_id": skill_id,
            "user_id": user_id,
            "department": department,
        },
    )

    discovered = ai_result.get("discovered", []) if ai_result else []
    return {
        "discovered": discovered,
        "stats": {
            "total_overridden": len(rejected_logs),
            "clustered": len(discovered),
            "already_covered": len(existing_aps),
        },
    }


async def generate_execution_summary(skill_name: str, result_json: dict) -> str | None:
    system = "你是SkillForge执行摘要生成器。将执行结果转为一句话中文摘要（200字以内），面向运营人员。"
    user = (
        f"Skill名称: {skill_name}\n"
        f"执行结果:\n```json\n{json.dumps(result_json, ensure_ascii=False, indent=1)[:3000]}\n```\n\n"
        "输出纯JSON: {\"summary\": \"摘要文本\"}"
    )
    result = await call_llm(
        system, user, max_tokens=500, timeout=15,
        call_source="skill.execution_summary",
    )
    return result.get("summary") if result else None


async def suggest_branches(
    db: AsyncSession,
    skill_id: str,
    step_id: str,
    existing_branches: list,
    *,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    validate_skill_id(skill_id)

    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    purpose = ""
    if skill_md:
      parsed = skill_parser.parse(skill_md)
      purpose = parsed.purpose[:300] if parsed else ""

    system = (
        "你是Skill决策树分支补全助手。基于已有分支条件，建议互补的分支。"
        "输出纯JSON: {\"suggestions\": [{\"condition\": \"条件\", \"conclusion\": \"结论\", \"action\": \"建议操作\"}]}"
    )
    user = (
        f"## Skill 目的\n{purpose}\n\n"
        f"## 当前步骤: {step_id}\n"
        f"## 已有分支\n```json\n{json.dumps(existing_branches, ensure_ascii=False, indent=1)}\n```\n\n"
        "## 补全要求\n"
        "1. 分析已有分支条件，找出未覆盖的场景\n"
        "2. 补充边界条件（如数据缺失、极端值）\n"
        "3. 确保所有分支互斥且穷尽\n"
    )

    cache_key = f"branches:{skill_id}:{step_id}"
    ai_result = await call_llm_cached(
        cache_key, system, user,
        cache_ttl=300, max_tokens=1500, timeout=30,
        call_source="skill.suggest_branches",
        cost_context={
            "skill_id": skill_id,
            "user_id": user_id,
            "department": department,
        },
    )

    return {"suggestions": ai_result.get("suggestions", []) if ai_result else []}
