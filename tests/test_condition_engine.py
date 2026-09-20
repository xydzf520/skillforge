"""
条件评估引擎测试：覆盖所有操作符、逻辑组合、边界情况。
"""

import pytest

from app.approval.condition_engine import evaluate_condition
from app.approval.service import _rule_matches


# ===== 向后兼容：精确匹配 =====

class TestExactMatch:
    """直接值等价于 eq 操作符，向后兼容旧格式。"""

    def test_string_match(self):
        assert evaluate_condition({"risk_level": "R3"}, {"risk_level": "R3"}) is True

    def test_string_mismatch(self):
        assert evaluate_condition({"risk_level": "R3"}, {"risk_level": "R1"}) is False

    def test_int_match(self):
        assert evaluate_condition({"count": 100}, {"count": 100}) is True

    def test_int_mismatch(self):
        assert evaluate_condition({"count": 100}, {"count": 50}) is False

    def test_bool_match(self):
        assert evaluate_condition({"enabled": True}, {"enabled": True}) is True

    def test_bool_mismatch(self):
        assert evaluate_condition({"enabled": True}, {"enabled": False}) is False

    def test_multi_field_all_match(self):
        """多字段条件取 AND 语义"""
        cond = {"department": "EC", "risk_level": "R3"}
        payload = {"department": "EC", "risk_level": "R3", "name": "test"}
        assert evaluate_condition(cond, payload) is True

    def test_multi_field_partial_mismatch(self):
        cond = {"department": "EC", "risk_level": "R3"}
        payload = {"department": "EC", "risk_level": "R1"}
        assert evaluate_condition(cond, payload) is False

    def test_missing_field_in_payload(self):
        """payload 缺少条件字段时，值为 None，不等于非 None 期望值"""
        assert evaluate_condition({"risk_level": "R3"}, {"name": "test"}) is False

    def test_none_match_none(self):
        """条件值为 None，payload 值也为 None"""
        assert evaluate_condition({"field": None}, {"field": None}) is True
        assert evaluate_condition({"field": None}, {}) is True

    def test_none_mismatch(self):
        assert evaluate_condition({"field": None}, {"field": "value"}) is False


# ===== eq 操作符 =====

class TestEqOperator:
    def test_eq_string(self):
        assert evaluate_condition({"status": {"eq": "active"}}, {"status": "active"}) is True

    def test_eq_number(self):
        assert evaluate_condition({"count": {"eq": 42}}, {"count": 42}) is True

    def test_eq_cross_numeric_types(self):
        """int vs float 宽松比较"""
        assert evaluate_condition({"count": {"eq": 42}}, {"count": 42.0}) is True
        assert evaluate_condition({"count": {"eq": 42.0}}, {"count": 42}) is True

    def test_eq_mismatch(self):
        assert evaluate_condition({"status": {"eq": "active"}}, {"status": "draft"}) is False


# ===== ne 操作符 =====

class TestNeOperator:
    def test_ne_mismatch_returns_true(self):
        assert evaluate_condition({"status": {"ne": "draft"}}, {"status": "active"}) is True

    def test_ne_match_returns_false(self):
        assert evaluate_condition({"status": {"ne": "draft"}}, {"status": "draft"}) is False

    def test_ne_missing_field(self):
        """字段不存在时 actual=None, ne 非 None 值 => True"""
        assert evaluate_condition({"status": {"ne": "draft"}}, {}) is True

    def test_ne_none(self):
        """ne None：当 actual 有值时为 True"""
        assert evaluate_condition({"field": {"ne": None}}, {"field": "val"}) is True
        assert evaluate_condition({"field": {"ne": None}}, {}) is False


# ===== gt / gte / lt / lte 操作符 =====

class TestComparisonOperators:
    def test_gt_numbers(self):
        assert evaluate_condition({"count": {"gt": 100}}, {"count": 150}) is True
        assert evaluate_condition({"count": {"gt": 100}}, {"count": 100}) is False
        assert evaluate_condition({"count": {"gt": 100}}, {"count": 50}) is False

    def test_gte_numbers(self):
        assert evaluate_condition({"count": {"gte": 100}}, {"count": 100}) is True
        assert evaluate_condition({"count": {"gte": 100}}, {"count": 150}) is True
        assert evaluate_condition({"count": {"gte": 100}}, {"count": 50}) is False

    def test_lt_numbers(self):
        assert evaluate_condition({"count": {"lt": 100}}, {"count": 50}) is True
        assert evaluate_condition({"count": {"lt": 100}}, {"count": 100}) is False

    def test_lte_numbers(self):
        assert evaluate_condition({"count": {"lte": 100}}, {"count": 100}) is True
        assert evaluate_condition({"count": {"lte": 100}}, {"count": 50}) is True
        assert evaluate_condition({"count": {"lte": 100}}, {"count": 150}) is False

    def test_gt_float(self):
        assert evaluate_condition({"score": {"gt": 0.5}}, {"score": 0.8}) is True
        assert evaluate_condition({"score": {"gt": 0.5}}, {"score": 0.3}) is False

    def test_string_comparison(self):
        """字符串按字典序比较"""
        assert evaluate_condition(
            {"data_sensitivity": {"gte": "L3"}},
            {"data_sensitivity": "L3"},
        ) is True
        assert evaluate_condition(
            {"data_sensitivity": {"gte": "L3"}},
            {"data_sensitivity": "L5"},
        ) is True
        assert evaluate_condition(
            {"data_sensitivity": {"gte": "L3"}},
            {"data_sensitivity": "L1"},
        ) is False

    def test_numeric_string_coercion(self):
        """字符串形式的数值可以跨类型比较"""
        assert evaluate_condition({"count": {"gt": 100}}, {"count": "150"}) is True
        assert evaluate_condition({"count": {"gt": "100"}}, {"count": 150}) is True

    def test_comparison_with_none_actual(self):
        """actual 为 None 时，有序比较返回 False"""
        assert evaluate_condition({"count": {"gt": 0}}, {}) is False


# ===== in 操作符 =====

class TestInOperator:
    def test_in_string_list(self):
        assert evaluate_condition(
            {"risk_level": {"in": ["R2", "R3"]}},
            {"risk_level": "R3"},
        ) is True

    def test_not_in_list(self):
        assert evaluate_condition(
            {"risk_level": {"in": ["R2", "R3"]}},
            {"risk_level": "R1"},
        ) is False

    def test_in_number_list(self):
        assert evaluate_condition({"level": {"in": [1, 2, 3]}}, {"level": 2}) is True

    def test_in_cross_type(self):
        """int vs float 跨类型 in"""
        assert evaluate_condition({"level": {"in": [1, 2, 3]}}, {"level": 2.0}) is True

    def test_in_empty_list(self):
        assert evaluate_condition({"risk_level": {"in": []}}, {"risk_level": "R3"}) is False

    def test_in_none_actual(self):
        """actual 为 None 且列表中有 None"""
        assert evaluate_condition({"field": {"in": [None, "a"]}}, {}) is True
        assert evaluate_condition({"field": {"in": ["a", "b"]}}, {}) is False


# ===== contains 操作符 =====

class TestContainsOperator:
    def test_contains_substring(self):
        assert evaluate_condition(
            {"name": {"contains": "投放"}},
            {"name": "EC-投放-01"},
        ) is True

    def test_contains_no_match(self):
        assert evaluate_condition(
            {"name": {"contains": "投放"}},
            {"name": "EC-审核-01"},
        ) is False

    def test_contains_number_coercion(self):
        """数值转字符串后做 contains"""
        assert evaluate_condition({"code": {"contains": "42"}}, {"code": "item-42-x"}) is True

    def test_contains_none_actual(self):
        assert evaluate_condition({"name": {"contains": "x"}}, {}) is False


# ===== not 操作符（顶层逻辑） =====

class TestNotOperator:
    def test_not_negates_match(self):
        cond = {"not": {"status": "draft"}}
        assert evaluate_condition(cond, {"status": "active"}) is True
        assert evaluate_condition(cond, {"status": "draft"}) is False

    def test_not_with_operator(self):
        cond = {"not": {"count": {"gt": 100}}}
        assert evaluate_condition(cond, {"count": 50}) is True
        assert evaluate_condition(cond, {"count": 200}) is False

    def test_field_level_not(self):
        """字段级 not: {"field": {"not": value}}"""
        cond = {"status": {"not": "draft"}}
        assert evaluate_condition(cond, {"status": "active"}) is True
        assert evaluate_condition(cond, {"status": "draft"}) is False

    def test_field_level_not_with_operator(self):
        """字段级 not 嵌套操作符: {"field": {"not": {"in": [...]}}}"""
        cond = {"risk_level": {"not": {"in": ["R1", "R2"]}}}
        assert evaluate_condition(cond, {"risk_level": "R3"}) is True
        assert evaluate_condition(cond, {"risk_level": "R1"}) is False


# ===== and 操作符 =====

class TestAndOperator:
    def test_and_all_match(self):
        cond = {"and": [
            {"department": "EC"},
            {"risk_level": {"in": ["R2", "R3"]}},
        ]}
        payload = {"department": "EC", "risk_level": "R3"}
        assert evaluate_condition(cond, payload) is True

    def test_and_partial_mismatch(self):
        cond = {"and": [
            {"department": "EC"},
            {"risk_level": {"in": ["R2", "R3"]}},
        ]}
        payload = {"department": "EC", "risk_level": "R1"}
        assert evaluate_condition(cond, payload) is False

    def test_and_empty_list(self):
        """空 and 列表 => all([]) => True"""
        assert evaluate_condition({"and": []}, {"x": 1}) is True

    def test_and_single_item(self):
        cond = {"and": [{"status": "active"}]}
        assert evaluate_condition(cond, {"status": "active"}) is True


# ===== or 操作符 =====

class TestOrOperator:
    def test_or_first_matches(self):
        cond = {"or": [
            {"risk_level": "R3"},
            {"data_sensitivity": {"gte": "L3"}},
        ]}
        assert evaluate_condition(cond, {"risk_level": "R3", "data_sensitivity": "L1"}) is True

    def test_or_second_matches(self):
        cond = {"or": [
            {"risk_level": "R3"},
            {"data_sensitivity": {"gte": "L3"}},
        ]}
        assert evaluate_condition(cond, {"risk_level": "R1", "data_sensitivity": "L5"}) is True

    def test_or_none_match(self):
        cond = {"or": [
            {"risk_level": "R3"},
            {"data_sensitivity": {"gte": "L3"}},
        ]}
        assert evaluate_condition(cond, {"risk_level": "R1", "data_sensitivity": "L1"}) is False

    def test_or_empty_list(self):
        """空 or 列表 => any([]) => False"""
        assert evaluate_condition({"or": []}, {"x": 1}) is False


# ===== 嵌套条件 =====

class TestNestedConditions:
    def test_nested_and_in_or(self):
        """or 内嵌 and"""
        cond = {"or": [
            {"and": [{"department": "EC"}, {"risk_level": "R3"}]},
            {"and": [{"department": "BD"}, {"risk_level": "R2"}]},
        ]}
        assert evaluate_condition(cond, {"department": "EC", "risk_level": "R3"}) is True
        assert evaluate_condition(cond, {"department": "BD", "risk_level": "R2"}) is True
        assert evaluate_condition(cond, {"department": "EC", "risk_level": "R1"}) is False

    def test_nested_or_in_and(self):
        """and 内嵌 or"""
        cond = {"and": [
            {"department": "EC"},
            {"or": [{"risk_level": "R2"}, {"risk_level": "R3"}]},
        ]}
        assert evaluate_condition(cond, {"department": "EC", "risk_level": "R2"}) is True
        assert evaluate_condition(cond, {"department": "EC", "risk_level": "R1"}) is False
        assert evaluate_condition(cond, {"department": "BD", "risk_level": "R3"}) is False

    def test_nested_not_in_and(self):
        cond = {"and": [
            {"department": "EC"},
            {"not": {"status": "draft"}},
        ]}
        assert evaluate_condition(cond, {"department": "EC", "status": "active"}) is True
        assert evaluate_condition(cond, {"department": "EC", "status": "draft"}) is False

    def test_deeply_nested(self):
        """三层嵌套"""
        cond = {"or": [
            {"and": [
                {"department": "EC"},
                {"or": [
                    {"risk_level": "R3"},
                    {"count": {"gt": 1000}},
                ]},
            ]},
            {"status": "urgent"},
        ]}
        # EC + R3 => True
        assert evaluate_condition(cond, {"department": "EC", "risk_level": "R3", "count": 5}) is True
        # EC + 高 count => True
        assert evaluate_condition(cond, {"department": "EC", "risk_level": "R1", "count": 2000}) is True
        # urgent => True
        assert evaluate_condition(cond, {"department": "BD", "risk_level": "R1", "status": "urgent"}) is True
        # 都不满足 => False
        assert evaluate_condition(cond, {"department": "BD", "risk_level": "R1", "count": 5}) is False

    def test_multiple_operators_on_same_field(self):
        """同一字段多个操作符：取 AND"""
        cond = {"count": {"gte": 10, "lte": 100}}
        assert evaluate_condition(cond, {"count": 50}) is True
        assert evaluate_condition(cond, {"count": 10}) is True
        assert evaluate_condition(cond, {"count": 100}) is True
        assert evaluate_condition(cond, {"count": 5}) is False
        assert evaluate_condition(cond, {"count": 150}) is False


# ===== 空条件 / None =====

class TestEmptyCondition:
    def test_none_condition(self):
        assert evaluate_condition(None, {"x": 1}) is True

    def test_empty_dict_condition(self):
        assert evaluate_condition({}, {"x": 1}) is True

    def test_none_payload(self):
        """空 payload + 有条件 => 字段缺失 => False"""
        assert evaluate_condition({"risk_level": "R3"}, None) is False

    def test_none_condition_none_payload(self):
        assert evaluate_condition(None, None) is True

    def test_empty_condition_none_payload(self):
        assert evaluate_condition({}, None) is True


# ===== 非法条件（不崩溃，返回 False + 日志） =====

class TestInvalidConditions:
    def test_in_with_non_list(self):
        """in 操作符值不是列表"""
        assert evaluate_condition({"field": {"in": "not_a_list"}}, {"field": "x"}) is False

    def test_and_with_non_list(self):
        """and 值不是列表"""
        assert evaluate_condition({"and": "bad"}, {"x": 1}) is False

    def test_or_with_non_list(self):
        """or 值不是列表"""
        assert evaluate_condition({"or": "bad"}, {"x": 1}) is False

    def test_not_with_non_dict(self):
        """not 值不是字典（顶层）"""
        assert evaluate_condition({"not": "bad"}, {"x": 1}) is False

    def test_unknown_operator(self):
        """未知操作符 fail-closed：视为不匹配（M1/C2 安全改动）。

        旧行为"向前兼容"（未知算子放行）会被恶意 admin 利用 —— 策略里写错算子
        等于条件自动成立，可导致 ABAC 错误 allow / approval 规则错误命中。
        引入新算子必须"先部署新引擎再写新算子策略"。
        """
        assert evaluate_condition({"field": {"xyz_unknown": "."}}, {"field": "test"}) is False


# ===== _rule_matches 兼容性 =====

class TestRuleMatchesCompat:
    """确认 service._rule_matches 正确委托给 evaluate_condition。"""

    def test_delegates_to_engine(self):
        assert _rule_matches({"risk_level": "R3"}, {"risk_level": "R3"}) is True
        assert _rule_matches({"risk_level": "R3"}, {"risk_level": "R1"}) is False

    def test_none_condition(self):
        assert _rule_matches(None, {"x": 1}) is True

    def test_operator_via_rule_matches(self):
        assert _rule_matches(
            {"count": {"gt": 100}},
            {"count": 200},
        ) is True
