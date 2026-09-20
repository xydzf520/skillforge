"""条件表达式评估引擎。

支持 ABAC 策略和审批规则的 JSONB 条件求值。

顶层结构：
- dict 中可以出现特殊 key `and` / `or` / `not`，用于逻辑组合；其余 key 视作字段名。
- 多个字段 key 之间是 AND。

字段匹配规则：
- 字面量 `{"field": value}` → 等值匹配
- 操作符 `{"field": {"op": value, ...}}` → 每个操作符单独 AND

支持的操作符：
- eq / ne / in / not_in / gt / gte / lt / lte / contains / regex / not

跨类型兼容：
- 数值与数值字符串可互相比较（gt/lt/gte/lte）
- int 与 float 在 eq/in 中等价
- contains 对非列表值退化为 str 子串匹配
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


MAX_CONDITION_DEPTH = 32


def evaluate_condition(
    condition: dict | None,
    target: dict | None,
    *,
    _depth: int = 0,
) -> bool:
    """评估一个条件表达式。

    Args:
        condition: 条件 dict，None 或空 dict 视为始终匹配。
        target:    被评估的目标属性 dict，None 视为空 dict。
        _depth:    递归深度（内部参数，防止恶意深层嵌套撑爆 Python 栈）。

    Returns:
        True 表示条件匹配。非法结构不抛异常，记录 warning 后返回 False。
    """
    if _depth > MAX_CONDITION_DEPTH:
        logger.warning("condition_engine: 递归深度超过 {}，拒绝求值", MAX_CONDITION_DEPTH)
        return False
    if not condition:
        return True
    payload = target or {}

    for key, rule in condition.items():
        if key == "and":
            if not _evaluate_and(rule, payload, _depth + 1):
                return False
            continue
        if key == "or":
            if not _evaluate_or(rule, payload, _depth + 1):
                return False
            continue
        if key == "not":
            if not _evaluate_not_top(rule, payload, _depth + 1):
                return False
            continue

        value = payload.get(key)
        if not _match_field(value, rule):
            return False

    return True


def _evaluate_and(rule: Any, payload: dict, depth: int) -> bool:
    if not isinstance(rule, list):
        logger.warning("condition_engine: 'and' 值应为 list，收到 {}", type(rule).__name__)
        return False
    return all(evaluate_condition(item, payload, _depth=depth) for item in rule)


def _evaluate_or(rule: Any, payload: dict, depth: int) -> bool:
    if not isinstance(rule, list):
        logger.warning("condition_engine: 'or' 值应为 list，收到 {}", type(rule).__name__)
        return False
    return any(evaluate_condition(item, payload, _depth=depth) for item in rule)


def _evaluate_not_top(rule: Any, payload: dict, depth: int) -> bool:
    """顶层 not：整个子条件取反。"""
    if not isinstance(rule, dict):
        logger.warning("condition_engine: 'not' 值应为 dict，收到 {}", type(rule).__name__)
        return False
    return not evaluate_condition(rule, payload, _depth=depth)


def _match_field(value: Any, rule: Any) -> bool:
    """字段级匹配。rule 可能是字面量或操作符 dict。"""
    if not isinstance(rule, dict):
        return _eq(value, rule)

    for op, expected in rule.items():
        if not _apply_operator(op, value, expected):
            return False
    return True


def _apply_operator(op: str, value: Any, expected: Any) -> bool:
    if op == "eq":
        return _eq(value, expected)
    if op == "ne":
        return not _eq(value, expected)
    if op == "in":
        if not isinstance(expected, (list, tuple, set)):
            logger.warning("condition_engine: 'in' 值应为 list，收到 {}", type(expected).__name__)
            return False
        return any(_eq(value, item) for item in expected)
    if op == "not_in":
        if not isinstance(expected, (list, tuple, set)):
            logger.warning("condition_engine: 'not_in' 值应为 list，收到 {}", type(expected).__name__)
            return False
        return not any(_eq(value, item) for item in expected)
    if op in ("gt", "gte", "lt", "lte"):
        return _compare(op, value, expected)
    if op == "contains":
        return _contains(value, expected)
    if op == "regex":
        return _regex(value, expected)
    if op == "not":
        # 字段级 not：对子规则取反。支持 {"field": {"not": value}} 和 {"field": {"not": {"in": [...]}}}
        return not _match_field(value, expected)

    # 未知操作符 → fail-closed：拒绝匹配
    # （注：向前兼容由"先部署新引擎再写新算子策略"保证；运行期遇到未知算子必须 False，
    #  否则策略里写错算子 = 条件自动成立，可导致 ABAC 错误 allow / approval 规则错误命中）
    logger.warning("condition_engine: 未知操作符 '{}'，视为不匹配", op)
    return False


def _eq(value: Any, expected: Any) -> bool:
    """宽松等值：int/float 跨类型视为相等；其余按 Python == 比较。"""
    if isinstance(value, bool) or isinstance(expected, bool):
        # bool 是 int 子类，但语义上不应与 int 等价
        return value is expected or value == expected and type(value) is type(expected)
    if isinstance(value, (int, float)) and isinstance(expected, (int, float)):
        return float(value) == float(expected)
    return value == expected


def _compare(op: str, value: Any, expected: Any) -> bool:
    if value is None:
        return False
    cmp_v, cmp_e = _coerce_numeric(value, expected)
    try:
        if op == "gt":
            return cmp_v > cmp_e
        if op == "gte":
            return cmp_v >= cmp_e
        if op == "lt":
            return cmp_v < cmp_e
        if op == "lte":
            return cmp_v <= cmp_e
    except TypeError as exc:
        logger.warning("condition_engine: 比较类型不兼容 {} vs {}: {}", type(value).__name__, type(expected).__name__, exc)
        return False
    return False


def _coerce_numeric(value: Any, expected: Any) -> tuple[Any, Any]:
    """一端是数值、另一端是数值字符串时，转为 float 比较。"""
    if isinstance(value, str) and isinstance(expected, (int, float)):
        try:
            return float(value), float(expected)
        except ValueError:
            return value, expected
    if isinstance(expected, str) and isinstance(value, (int, float)):
        try:
            return float(value), float(expected)
        except ValueError:
            return value, expected
    return value, expected


def _contains(value: Any, expected: Any) -> bool:
    """容器包含或字符串子串匹配；数值会 str 化后做子串匹配。"""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return expected in value
    # 对字符串与数值，统一 str 化后做子串匹配
    try:
        return str(expected) in str(value)
    except Exception:  # noqa: BLE001
        return False


def _regex(value: Any, expected: Any) -> bool:
    if value is None or not isinstance(expected, str):
        return False
    try:
        return bool(re.search(expected, str(value)))
    except re.error as exc:
        logger.warning("condition_engine: 非法正则 {}: {}", expected, exc)
        return False
