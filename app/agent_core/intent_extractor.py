"""LLM 驱动的意图理解（v7 D 维度核心）。

策略：
1. 优先调 app.common.ai.call_llm 用 DeepSeek 抽取严格 TaskContract JSON。
2. LLM 失败 / JSON 不合法 / 缺关键字段 → fallback 到 task_contract.build_task_contract 的正则版本。
3. 两条路径都通过 build_bundle_from_contract 收口生成完整 bundle。

prompt 来源：app/agent_core/prompts/intent_extract.txt（带 JSON Schema 与 few-shot）。
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.agent_core.prompt_loader import load_prompt
from app.common.ai import call_llm
from app.workbench.task_contract import (
    TASK_CONTRACT_PROMPT_VERSION,
    build_task_contract,
    infer_fixtures,
    infer_test_cases,
)


_REQUIRED_TOP_KEYS = {"goal", "trigger", "input", "output", "permissions", "risks"}
_VALID_TRIGGER_TYPES = {"cron", "webhook", "event", "manual"}
_VALID_ADAPTERS = {"dingtalk_card", "email", "slack", "json_webhook", "csv_file"}
_VALID_RISK_LEVELS = {"R1", "R2", "R3"}
_VALID_DATA_CLASS = {"public", "internal", "confidential"}
_VALID_PERMISSION_ACTIONS = {"read_data", "write_data", "send_message", "external_api"}
_VALID_INPUT_TYPES = {"string", "number", "date", "json"}
_VALID_INPUT_SOURCES = {"user", "datasource", "previous_skill"}


def _is_basic_contract_valid(payload: Any) -> bool:
    """快速校验：必填顶层 key 全在、enum 字段在白名单内、test_cases 至少 3 条。"""
    if not isinstance(payload, dict):
        return False
    if not _REQUIRED_TOP_KEYS.issubset(payload.keys()):
        return False

    trigger = payload.get("trigger") or {}
    if not isinstance(trigger, dict) or trigger.get("type") not in _VALID_TRIGGER_TYPES:
        return False

    output = payload.get("output") or {}
    if not isinstance(output, dict) or output.get("adapter") not in _VALID_ADAPTERS:
        return False

    risks = payload.get("risks") or {}
    if not isinstance(risks, dict):
        return False
    if risks.get("level") not in _VALID_RISK_LEVELS:
        return False
    if risks.get("data_classification") not in _VALID_DATA_CLASS:
        return False

    inputs = payload.get("input") or []
    if not isinstance(inputs, list):
        return False
    for item in inputs:
        if not isinstance(item, dict):
            return False
        if item.get("type") not in _VALID_INPUT_TYPES:
            return False
        if item.get("source") not in _VALID_INPUT_SOURCES:
            return False

    permissions = payload.get("permissions") or []
    if not isinstance(permissions, list):
        return False
    for item in permissions:
        if not isinstance(item, dict):
            return False
        if item.get("action") not in _VALID_PERMISSION_ACTIONS:
            return False
        if not isinstance(item.get("reversible"), bool):
            return False

    return True


def _normalize_contract(payload: dict, message: str) -> dict:
    """补齐 fixtures / test_cases 等可选字段，避免后续渲染 KeyError。"""
    contract = dict(payload)
    contract.pop("_missing", None)
    contract.pop("_clarification_needed", None)

    if not contract.get("fixtures"):
        contract["fixtures"] = infer_fixtures(message)

    test_cases = contract.get("test_cases") or []
    if len(test_cases) < 3:
        contract["test_cases"] = infer_test_cases(message, contract.get("output") or {})
    else:
        # 规范化字段名
        normalized = []
        for case in test_cases:
            if not isinstance(case, dict):
                continue
            normalized.append(
                {
                    "name": case.get("name") or "测试场景",
                    "input": case.get("input") or {"scenario": "normal"},
                    "expected_keywords": case.get("expected_keywords") or [],
                }
            )
        contract["test_cases"] = normalized

    # 兜底 risks.department
    risks = contract.get("risks") or {}
    if not risks.get("department"):
        from app.workbench.task_contract import _guess_department  # 局部 import 避免循环
        risks["department"] = _guess_department(message)
        contract["risks"] = risks

    return contract


async def extract_contract(message: str) -> dict:
    """
    意图抽取主入口：先 LLM，失败 fallback 正则。

    返回值始终是合法的 contract dict（最差情况退化到正则结果）。
    """
    llm_contract = await _extract_via_llm(message)
    if llm_contract is not None:
        logger.info("[intent_extract] LLM 抽取成功 prompt_version={}", TASK_CONTRACT_PROMPT_VERSION)
        return llm_contract

    logger.warning("[intent_extract] LLM 抽取失败，回退到正则版本")
    return build_task_contract(message)


async def _extract_via_llm(message: str) -> dict | None:
    try:
        system_prompt = load_prompt("intent_extract")
    except FileNotFoundError as exc:
        logger.warning("intent_extract.txt 不存在: {}", exc)
        return None

    try:
        result = await call_llm(
            system=system_prompt,
            user=message,
            json_mode=True,
            temperature=0.2,
            max_tokens=4000,  # contract + fixtures + 3 test_cases 会比较长
            call_source="agent_core.intent_extract",
            cost_context={"prompt_hash": TASK_CONTRACT_PROMPT_VERSION},
        )
    except Exception as exc:  # noqa: BLE001 — call_llm 内部已捕获，但保险起见
        logger.warning("call_llm 调用异常: {}", exc)
        return None

    if result is None:
        return None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None

    if not _is_basic_contract_valid(result):
        logger.warning("[intent_extract] LLM 返回未通过 schema 校验，部分字段缺失或越界")
        return None

    return _normalize_contract(result, message)
