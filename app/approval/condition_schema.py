"""condition_json 的 JSON Schema 定义。

审批规则和 ABAC 策略的条件表达式格式统一校验。
"""

from __future__ import annotations

from typing import Any

# 操作符 Schema 定义
_OPERATOR_SCHEMA = {
    "type": "object",
    "description": "操作符条件（至少包含一个操作符）",
    "properties": {
        "in": {
            "type": "array",
            "description": "值在列表内",
            "items": {},
        },
        "not_in": {
            "type": "array",
            "description": "值不在列表内",
            "items": {},
        },
        "eq": {
            "description": "等于（与字面量等价）",
        },
        "ne": {
            "description": "不等于",
        },
        "gt": {
            "description": "大于",
            "oneOf": [{"type": "number"}, {"type": "string"}],
        },
        "gte": {
            "description": "大于等于",
            "oneOf": [{"type": "number"}, {"type": "string"}],
        },
        "lt": {
            "description": "小于",
            "oneOf": [{"type": "number"}, {"type": "string"}],
        },
        "lte": {
            "description": "小于等于",
            "oneOf": [{"type": "number"}, {"type": "string"}],
        },
        "contains": {
            "description": "列表包含指定元素",
        },
        "regex": {
            "type": "string",
            "description": "正则表达式匹配",
        },
    },
    "additionalProperties": False,
}

# 字段值可以是字面量或操作符对象
_FIELD_VALUE_SCHEMA = {
    "oneOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "array", "items": {}},
        _OPERATOR_SCHEMA,
    ]
}

CONDITION_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "SkillForge Condition Expression",
    "description": "审批规则和 ABAC 策略的条件表达式格式。"
                   "顶层 dict 的每个 key 对应目标对象的字段名，value 是匹配规则。"
                   "多个字段之间是 AND 语义。",
    "oneOf": [
        {
            "type": "object",
            "description": "字段条件集合（AND 语义）",
            "additionalProperties": _FIELD_VALUE_SCHEMA,
        },
        {
            "type": "null",
            "description": "空条件（始终匹配）",
        },
    ],
    "examples": [
        None,
        {"role": "admin"},
        {"role": {"in": ["admin", "ai_engineer"]}},
        {"risk_level": "R3", "data_sensitivity": {"gte": "L3"}},
        {"is_production": True, "hour": {"gte": 9, "lte": 18}},
        {"department": {"not_in": ["外部"]}, "name": {"regex": "^EC-.*"}},
    ],
}


def validate_condition_json(condition: dict | None) -> list[str]:
    """校验 condition_json 格式，返回错误列表。

    不依赖 jsonschema 库，用纯 Python 做轻量校验。
    返回空列表表示合法。
    """
    errors: list[str] = []

    if condition is None:
        return errors

    if not isinstance(condition, dict):
        errors.append(f"条件必须是 dict 或 null，实际类型: {type(condition).__name__}")
        return errors

    _ALLOWED_OPERATORS = {"in", "not_in", "eq", "ne", "gt", "gte", "lt", "lte", "contains", "regex"}

    for field, rule in condition.items():
        if not isinstance(field, str):
            errors.append(f"字段名必须是字符串，实际值: {field!r}")
            continue

        # 字面量直接跳过
        if not isinstance(rule, dict):
            continue

        # 操作符校验
        for op in rule:
            if op not in _ALLOWED_OPERATORS:
                errors.append(f"字段 '{field}' 的操作符 '{op}' 不在允许列表内")

            # in / not_in 必须是列表
            if op in ("in", "not_in") and not isinstance(rule[op], list):
                errors.append(f"字段 '{field}' 的操作符 '{op}' 值必须是数组")

            # regex 必须是合法正则
            if op == "regex":
                import re
                try:
                    re.compile(rule[op])
                except (re.error, TypeError) as e:
                    errors.append(f"字段 '{field}' 的 regex 不合法: {e}")

    return errors
