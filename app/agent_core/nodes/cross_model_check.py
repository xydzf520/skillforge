"""Node 7: 跨模型一致性 check（v7 D4）。

发布模式下用第二个模型重新跑 intent_extract，对比关键字段一致性：
- goal / trigger.type / output.adapter / risks.level
- permissions[].action 集合
- input[].source 集合

结果以 xmodel:{contract_hash} 缓存到 Redis（TTL 1h），命中直接返回。

副模型从 settings.AI_SECONDARY_MODEL 取，未配置时不跑副模型，
但仍然把第一次 contract 的"自一致 hash"作为基线写进 state（用于审计）。
"""

from __future__ import annotations

import hashlib
import json

from loguru import logger

from app.agent_core.intent_extractor import (
    _is_basic_contract_valid,
    _normalize_contract,
)
from app.agent_core.prompt_loader import load_prompt
from app.common.ai import call_llm
from app.common.cache import cache_get, cache_set
from app.config import settings


_KEY_FIELDS = ["goal", "trigger.type", "output.adapter", "risks.level"]


def _hash_contract(contract: dict) -> str:
    payload = {
        "goal": contract.get("goal"),
        "trigger": (contract.get("trigger") or {}).get("type"),
        "adapter": (contract.get("output") or {}).get("adapter"),
        "risks": (contract.get("risks") or {}).get("level"),
        "permissions": sorted([p.get("action") for p in (contract.get("permissions") or [])]),
        "inputs": sorted([(i.get("name"), i.get("source")) for i in (contract.get("input") or [])]),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def _extract_with_secondary_model(message: str, model_name: str) -> dict | None:
    try:
        system_prompt = load_prompt("intent_extract")
    except FileNotFoundError:
        return None
    result = await call_llm(
        system=system_prompt,
        user=message,
        json_mode=True,
        temperature=0.0,  # 一致性 check 用 0 温度更稳定
        max_tokens=4000,
        model_override=model_name,
        call_source="agent_core.cross_model_check",
    )
    if not isinstance(result, dict):
        return None
    if not _is_basic_contract_valid(result):
        return None
    return _normalize_contract(result, message)


def _compare_contracts(primary: dict, secondary: dict) -> dict:
    """对比两个 contract 的关键字段，返回匹配 / 不匹配清单。"""
    matched, mismatched = [], []
    p_trigger = (primary.get("trigger") or {}).get("type")
    s_trigger = (secondary.get("trigger") or {}).get("type")
    if p_trigger == s_trigger:
        matched.append("trigger.type")
    else:
        mismatched.append({"field": "trigger.type", "primary": p_trigger, "secondary": s_trigger})

    p_adapter = (primary.get("output") or {}).get("adapter")
    s_adapter = (secondary.get("output") or {}).get("adapter")
    if p_adapter == s_adapter:
        matched.append("output.adapter")
    else:
        mismatched.append({"field": "output.adapter", "primary": p_adapter, "secondary": s_adapter})

    p_risk = (primary.get("risks") or {}).get("level")
    s_risk = (secondary.get("risks") or {}).get("level")
    if p_risk == s_risk:
        matched.append("risks.level")
    else:
        mismatched.append({"field": "risks.level", "primary": p_risk, "secondary": s_risk})

    p_perms = sorted({p.get("action") for p in (primary.get("permissions") or [])})
    s_perms = sorted({p.get("action") for p in (secondary.get("permissions") or [])})
    if p_perms == s_perms:
        matched.append("permissions[].action")
    else:
        mismatched.append({"field": "permissions[].action", "primary": p_perms, "secondary": s_perms})

    return {
        "matched_fields": matched,
        "mismatched_fields": mismatched,
        "consistent": not mismatched,
    }


async def cross_model_check_node(state: dict) -> dict:
    next_state = dict(state)
    contract = next_state.get("contract") or {}
    message = next_state.get("user_input") or ""

    contract_hash = _hash_contract(contract)
    cache_key = f"xmodel:{contract_hash}"
    try:
        cached = await cache_get(cache_key)
    except Exception:  # noqa: BLE001 — Redis 未初始化时跳过缓存
        cached = None
    if cached:
        next_state["xmodel_check"] = {**cached, "from_cache": True}
        return next_state

    secondary_model = settings.AI_SECONDARY_MODEL
    if not secondary_model:
        next_state["xmodel_check"] = {
            "status": "skipped",
            "reason": "AI_SECONDARY_MODEL 未配置",
            "primary_hash": contract_hash,
            "matched_fields": [],
            "mismatched_fields": [],
            "consistent": True,  # 无副模型时不阻断发布
            "from_cache": False,
        }
        return next_state

    try:
        secondary = await _extract_with_secondary_model(message, secondary_model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cross_model_check] secondary 调用异常: {}", exc)
        secondary = None

    if secondary is None:
        result = {
            "status": "secondary_failed",
            "reason": "副模型调用失败或返回不合法",
            "primary_hash": contract_hash,
            "secondary_model": secondary_model,
            "matched_fields": [],
            "mismatched_fields": [],
            "consistent": True,  # 副模型故障不阻断
            "from_cache": False,
        }
        next_state["xmodel_check"] = result
        return next_state

    comparison = _compare_contracts(contract, secondary)
    result = {
        "status": "completed",
        "primary_hash": contract_hash,
        "secondary_hash": _hash_contract(secondary),
        "secondary_model": secondary_model,
        **comparison,
        "from_cache": False,
    }
    try:
        await cache_set(cache_key, result, ttl=3600)
    except Exception as exc:  # noqa: BLE001 — Redis 未初始化或写失败都不影响主流程
        logger.debug("[cross_model_check] 写缓存失败: {}", exc)

    next_state["xmodel_check"] = result
    return next_state
