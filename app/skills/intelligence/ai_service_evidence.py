from __future__ import annotations

import json
from datetime import datetime, timedelta

import yaml
from sqlalchemy import select

from app.execution.models import DecisionLog
from app.common.time_utils import now_bjt


async def get_param_evidence_impl(
    *,
    db,
    skill_id: str,
    param_name: str,
    validate_skill_id,
    git_service,
    call_llm_cached,
    truncate_dict,
    days: int = 30,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    validate_skill_id(skill_id)

    policy_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
    try:
        all_params = yaml.safe_load(policy_raw) or {}
    except yaml.YAMLError:
        all_params = {}

    current_value = all_params.get(param_name)

    since = now_bjt() - timedelta(days=days)
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .where(DecisionLog.created_at >= since)
        .order_by(DecisionLog.created_at.desc())
        .limit(500)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    total = len(logs)
    completed = sum(1 for log in logs if log.user_action == "completed")
    rejected = sum(1 for log in logs if log.user_action == "rejected")
    pending = total - completed - rejected

    referenced = 0
    for log in logs:
        snapshot = json.dumps(log.input_snapshot or {}, ensure_ascii=False)
        result_text = json.dumps(log.suggested_action or {}, ensure_ascii=False)
        if param_name in snapshot or param_name in result_text:
            referenced += 1

    enough_data = total >= 20

    evidence = {
        "param_name": param_name,
        "current_value": current_value,
        "sample_size": total,
        "window_days": days,
        "referenced_count": referenced,
        "distribution": {
            "completed": completed,
            "rejected": rejected,
            "pending": pending,
        },
        "adoption_rate": round(completed / total, 3) if total else 0.0,
        "rejection_rate": round(rejected / total, 3) if total else 0.0,
        "enough_data": enough_data,
        "recommended": None,
        "confidence": 0.0,
        "evidence_text": "",
    }

    if not enough_data:
        evidence["evidence_text"] = f"数据不足：最近 {days} 天仅 {total} 条决策，至少需要 20 条"
        return evidence

    samples = []
    for log in logs[:20]:
        samples.append({
            "date": log.created_at.strftime("%Y-%m-%d") if log.created_at else "",
            "input": truncate_dict(log.input_snapshot, 120),
            "suggested": log.suggested_action,
            "user_action": log.user_action,
        })

    system = (
        "你是 SkillForge 参数调优分析师。根据历史决策为单个参数给出结构化建议。"
        "只输出 JSON: {\"recommended\": 数值, \"confidence\": 0-1, \"evidence\": \"说明\"}"
    )
    user = (
        f"## 目标参数\n{param_name} (当前值: {current_value})\n\n"
        f"## 决策概况（最近{days}天）\n"
        f"总数: {total}, 采纳: {completed}, 驳回: {rejected}, 引用本参数: {referenced}\n\n"
        f"## 决策样本（{len(samples)}条）\n```json\n{json.dumps(samples, ensure_ascii=False, indent=1)}\n```\n\n"
        "## 任务\n给出此参数的推荐值及置信度和简短理由（中文）。"
    )

    cache_key = f"param_evidence:{skill_id}:{param_name}:{days}"
    try:
        ai_result = await call_llm_cached(
            cache_key, system, user,
            cache_ttl=600, max_tokens=600, timeout=20,
            call_source="skill.param_evidence",
            cost_context={
                "skill_id": skill_id,
                "user_id": user_id,
                "department": department,
            },
        )
        if isinstance(ai_result, dict):
            evidence["recommended"] = ai_result.get("recommended")
            evidence["confidence"] = float(ai_result.get("confidence", 0) or 0)
            evidence["evidence_text"] = str(ai_result.get("evidence", ""))
    except Exception:
        evidence["evidence_text"] = "AI 分析暂不可用，仅显示历史数据"

    return evidence
