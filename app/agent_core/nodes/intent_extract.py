"""Node 1: user input -> TaskContract（LLM-first，正则 fallback）。"""

from __future__ import annotations

from app.agent_core.llm import generate_contract_bundle


async def intent_extract_node(state: dict) -> dict:
    """LangGraph 节点：异步生成 contract bundle 并填充 state。"""
    bundle = await generate_contract_bundle(state.get("user_input", ""))
    next_state = dict(state)
    next_state["intent_md"] = bundle["intent_md"]
    next_state["contract"] = bundle["contract"]
    next_state["policy_yaml"] = bundle["policy_yaml"]
    next_state["review_state"] = bundle["review_state"]
    next_state["preview_result"] = bundle["preview"]
    next_state["skill"] = bundle["skill"]
    next_state["skill_md"] = ""  # skill_generate 节点会填充
    next_state["preview_cache_key"] = bundle["cache_key"]
    return next_state
