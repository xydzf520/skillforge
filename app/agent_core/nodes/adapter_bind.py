"""Node 4: bind the target adapter（含 jsonschema 校验，v7 G2）。"""

from __future__ import annotations

import json

from loguru import logger

from app.adapters.dingtalk_card.render import render_card
from app.agent_core.adapter_registry import (
    list_adapters,
    load_adapter_schema,
    validate_output,
)
from app.agent_core.prompt_loader import load_prompt
from app.common.ai import call_llm


async def _llm_refine_adapter(contract_output: dict, available_adapters: list[str]) -> dict | None:
    """让 LLM 在 contract.output 基础上补全 schema 字段值。"""
    try:
        prompt = load_prompt("adapter_bind")
    except FileNotFoundError:
        return None
    user = json.dumps(
        {"contract_output": contract_output, "available_adapters": available_adapters},
        ensure_ascii=False,
    )
    result = await call_llm(
        system=prompt,
        user=user,
        json_mode=True,
        temperature=0.2,
        call_source="agent_core.adapter_bind",
    )
    if not isinstance(result, dict) or result.get("_error"):
        return None
    if result.get("adapter") not in set(available_adapters):
        return None
    return result


async def adapter_bind_node(state: dict) -> dict:
    next_state = dict(state)
    contract = next_state.get("contract") or {}
    output = contract.get("output") or {}
    available = list_adapters()

    adapter = {
        "name": output.get("adapter", "dingtalk_card"),
        "schema": output.get("schema", {}),
        "recipient": output.get("recipient", ""),
        "validation": "由模板版规则填充",
    }

    refined = await _llm_refine_adapter(output, available)
    if refined:
        adapter["name"] = refined["adapter"]
        adapter["schema"] = refined.get("schema") or adapter["schema"]
        adapter["recipient"] = refined.get("recipient") or adapter["recipient"]
        adapter["validation"] = refined.get("validation") or adapter["validation"]
        logger.info("[adapter_bind] LLM 补全成功 adapter={}", adapter["name"])

    # G2: jsonschema 校验
    validation = validate_output(adapter["name"], adapter.get("schema") or {})
    adapter["schema_valid"] = validation["valid"]
    adapter["schema_errors"] = validation["errors"]
    adapter["schema_missing"] = validation["missing"]
    if not validation["valid"]:
        logger.warning(
            "[adapter_bind] schema 校验失败 adapter={} errors={}",
            adapter["name"],
            validation["errors"][:3],
        )

    if adapter["name"] == "dingtalk_card":
        adapter["preview_schema"] = render_card({
            "title": "预演卡片",
            "markdown": "卡片渲染准备完成",
            "actions": ["查看详情"],
        })
    next_state["adapter"] = adapter

    # 把 schema 校验结果反馈到 review_state.preview.detail，方便前端展示
    if not validation["valid"]:
        review_state = next_state.get("review_state") or {}
        preview_state = review_state.get("preview") or {}
        detail = dict(preview_state.get("detail") or {})
        detail["schema_errors"] = validation["errors"]
        preview_state["detail"] = detail
        review_state["preview"] = preview_state
        next_state["review_state"] = review_state

    return next_state
