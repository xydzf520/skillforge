"""
Skill测试执行器：批量跑测试用例 + 断言比对。
读取 tests/test_cases.yaml 中的用例，通过OpenClaw执行并比对结果。
"""

import json
import time

import yaml
from loguru import logger

from app.execution.openclaw_client import default_client
from app.skills.core.git_service import git_service


async def run_test_cases(
    skill_id: str,
    case_ids: list[int] | None = None,
) -> dict:
    """
    执行指定Skill的测试用例。
    case_ids=None 则执行全部用例。
    """
    # 读取测试用例定义
    test_yaml = git_service.read_file(skill_id, "tests/test_cases.yaml")
    if not test_yaml:
        return {"total": 0, "passed": 0, "failed": 0, "results": [],
                "error": "未找到 tests/test_cases.yaml"}

    try:
        data = yaml.safe_load(test_yaml)
        cases = data.get("test_cases", [])
    except yaml.YAMLError as e:
        return {"total": 0, "passed": 0, "failed": 0, "results": [],
                "error": f"YAML解析失败: {e}"}

    if not cases:
        return {"total": 0, "passed": 0, "failed": 0, "results": []}

    # 筛选指定用例
    if case_ids:
        cases = [c for i, c in enumerate(cases) if (i + 1) in case_ids]

    results = []
    passed = 0

    for i, case in enumerate(cases):
        case_name = case.get("name", f"用例{i + 1}")
        input_data = case.get("input", {})
        expected = case.get("expected_output", {})
        assert_rules = case.get("assert_rules", [])

        start_time = time.time()

        try:
            # 调用OpenClaw执行（沙箱模式）
            output = await default_client.run_skill(
                skill_id, params=input_data, sandbox=True,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            # 断言比对
            case_passed, failures = _check_assertions(output, expected, assert_rules)

            if case_passed:
                passed += 1

            results.append({
                "case_id": i + 1,
                "name": case_name,
                "passed": case_passed,
                "input": input_data,
                "expected": expected,
                "actual": output,
                "failures": failures,
                "duration_ms": duration_ms,
            })

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            results.append({
                "case_id": i + 1,
                "name": case_name,
                "passed": False,
                "input": input_data,
                "expected": expected,
                "actual": None,
                "failures": [f"执行异常: {e}"],
                "duration_ms": duration_ms,
            })

    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }


def _check_assertions(output: dict, expected: dict, assert_rules: list[str]) -> tuple[bool, list[str]]:
    """
    检查断言规则。
    支持简单的 "output.field == value" 格式。
    """
    failures = []

    # 先检查 assert_rules
    for rule in assert_rules:
        try:
            result = _eval_assertion(rule, output, expected)
            if not result:
                failures.append(f"断言失败: {rule}")
        except Exception as e:
            failures.append(f"断言异常: {rule} ({e})")

    # 如果没有assert_rules，按expected字段逐个比对
    if not assert_rules and expected:
        for key, exp_val in expected.items():
            actual_val = _get_nested(output, key)
            if actual_val != exp_val:
                failures.append(f"{key}: 期望={exp_val}, 实际={actual_val}")

    return len(failures) == 0, failures


def _eval_assertion(rule: str, output: dict, expected: dict) -> bool:
    """
    解析并执行断言规则。
    支持格式: "output.field == value" / "output.count > 0"
    """
    # 替换引用
    rule = rule.replace("expected.", "expected_")

    # 构建安全的eval上下文
    context = {
        "output": output,
        "expected": expected,
    }

    # 简单的字段访问解析
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if op in rule:
            left, right = rule.split(op, 1)
            left_val = _resolve_ref(left.strip(), context)
            right_val = _resolve_ref(right.strip(), context)

            if op == "==":
                return left_val == right_val
            elif op == "!=":
                return left_val != right_val
            elif op == ">":
                return float(left_val) > float(right_val)
            elif op == "<":
                return float(left_val) < float(right_val)
            elif op == ">=":
                return float(left_val) >= float(right_val)
            elif op == "<=":
                return float(left_val) <= float(right_val)

    return False


def _resolve_ref(ref: str, context: dict):
    """解析引用如 output.status 或字面值"""
    ref = ref.strip().strip("'\"")

    # 尝试作为路径解析
    if ref.startswith("output."):
        return _get_nested(context["output"], ref[7:])
    if ref.startswith("expected."):
        return _get_nested(context["expected"], ref[9:])

    # 尝试作为数字
    try:
        return int(ref)
    except ValueError:
        try:
            return float(ref)
        except ValueError:
            pass

    # 布尔值
    if ref.lower() == "true":
        return True
    if ref.lower() == "false":
        return False

    return ref


def _get_nested(data: dict, path: str):
    """从嵌套dict中获取值，支持 a.b.c 和 a[0].b 格式"""
    if not data or not path:
        return None

    parts = path.replace("[", ".").replace("]", "").split(".")
    current = data

    for part in parts:
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None

    return current
