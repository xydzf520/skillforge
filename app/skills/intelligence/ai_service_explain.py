from __future__ import annotations

import json


async def build_explain_pack_impl(
    *,
    db,
    skill_id: str,
    cache_get,
    cache_set,
    call_llm_cached,
    user_id: str | None = None,
    department: str | None = None,
) -> dict:
    from app.common.prompt_registry import init_registry, prompt_registry
    from app.skills import service as skill_service

    data = await skill_service.get_skill(db, skill_id)
    version_key = data.get("current_version") or data.get("updated_at") or "latest"
    cached = await cache_get(skill_id, version_key)
    if cached is not None:
        return cached

    parsed = data.get("parsed") or {}
    goal = parsed.get("purpose") or data.get("description") or ""
    steps = parsed.get("steps") or []
    outputs = parsed.get("output_definition") or []
    tests = parsed.get("test_cases") or []
    params = data.get("policy_pack") or {}

    covered_rules = min(len(steps), len(tests))
    uncovered_rules = max(0, len(steps) - covered_rules)

    base_pack = {
        "executive_summary": (
            f"这个 Skill 主要用于「{goal or data.get('name') or skill_id}」。"
            if goal or data.get("name")
            else "这个 Skill 当前缺少足够的目标描述。"
        ),
        "trigger_summary": f"当前通过「{data.get('trigger_type') or 'manual'}」触发。",
        "decision_ladder": [
            {
                "id": step.get("id") or step.get("name") or f"step_{idx + 1}",
                "name": step.get("name") or step.get("id") or f"步骤 {idx + 1}",
                "summary": "；".join(
                    f"{branch.get('condition') or '条件'}时，{branch.get('conclusion') or branch.get('action') or '给出处理结果'}"
                    for branch in (step.get("branches") or [])[:3]
                ) or "当前还没有配置分支",
            }
            for idx, step in enumerate(steps)
        ],
        "parameter_impacts": [
            {
                "name": key,
                "value": value,
                "impact": f"{key} 会影响相关规则中的阈值判断与分支命中。",
            }
            for key, value in list(params.items())[:20]
        ],
        "output_contract": [
            {
                "name": item.get("name") or item.get("field") or "未命名输出",
                "recipient": item.get("recipient") or "待确认接收方",
                "format": item.get("format") or "text",
            }
            for item in outputs[:20]
        ],
        "test_confidence": {
            "total_cases": len(tests),
            "covered_rules": covered_rules,
            "uncovered_rules": uncovered_rules,
            "summary": (
                f"当前已有 {len(tests)} 个测试样例，其中 {covered_rules} 条规则具备覆盖。"
                if tests
                else "当前还没有测试样例，讲解时需要明确说明这一点。"
            ),
        },
        "source": "fallback",
        "prompt_hash": None,
    }

    try:
        if not prompt_registry._sections:
            init_registry()
        system_prompt, prompt_hash = prompt_registry.build("skill_explainer")
        user_prompt = (
            f"## Skill 名称\n{data.get('name') or skill_id}\n\n"
            f"## 当前基础 Explain Pack\n```json\n{json.dumps(base_pack, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 目标\n{goal}\n\n"
            f"## 决策步骤\n```json\n{json.dumps(steps[:10], ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 参数\n```json\n{json.dumps(params, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 输出\n```json\n{json.dumps(outputs[:10], ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 测试\n```json\n{json.dumps(tests[:10], ensure_ascii=False, indent=2)}\n```"
        )
        ai_result = await call_llm_cached(
            f"skills:explain_pack:ai:{skill_id}:{version_key}",
            system_prompt,
            user_prompt,
            cache_ttl=600,
            max_tokens=1200,
            timeout=30,
            call_source="skill.explain_pack",
            cost_context={
                "skill_id": skill_id,
                "user_id": user_id,
                "department": department,
                "prompt_hash": prompt_hash,
            },
        )
        if isinstance(ai_result, dict):
            base_pack.update({
                "executive_summary": ai_result.get("executive_summary") or base_pack["executive_summary"],
                "trigger_summary": ai_result.get("trigger_summary") or base_pack["trigger_summary"],
                "decision_ladder": ai_result.get("decision_ladder") or base_pack["decision_ladder"],
                "parameter_impacts": ai_result.get("parameter_impacts") or base_pack["parameter_impacts"],
                "output_contract": ai_result.get("output_contract") or base_pack["output_contract"],
                "test_confidence": ai_result.get("test_confidence") or base_pack["test_confidence"],
                "source": "ai",
                "prompt_hash": prompt_hash,
            })
    except Exception as e:
        from loguru import logger
        logger.warning("ai_service_explain AI 增强失败，使用 base_pack: {}", e)

    await cache_set(skill_id, version_key, base_pack)
    return base_pack
