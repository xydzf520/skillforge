"""Shared validation helpers for workbench patch payloads."""

from __future__ import annotations


VALID_TARGET_MODULES = {"goal", "rules", "params", "output_table", "test_cases", "workflow"}


def validate_module_patch(target_module: str, patch: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    if not patch:
        issues.append("patch 为空")

    if target_module not in VALID_TARGET_MODULES:
        issues.append("目标模块非法")
    elif target_module == "goal":
        goal = str(patch.get("goal", "") or "").strip()
        if not goal:
            issues.append("目标不能为空")
        elif len(goal) < 10:
            warnings.append("目标描述过短，建议至少 10 个字符")
    elif target_module == "params":
        params = patch.get("params") or []
        if not isinstance(params, list) or not params:
            issues.append("参数 patch 不能为空")
        for idx, item in enumerate(params):
            if not str(item.get("name") or item.get("key") or "").strip():
                issues.append(f"参数 #{idx + 1} 缺少名称")
    elif target_module == "rules":
        rules = patch.get("rules") or []
        if not isinstance(rules, list) or not rules:
            issues.append("规则 patch 不能为空")
        for idx, item in enumerate(rules):
            if not item.get("name"):
                warnings.append(f"规则 #{idx + 1} 缺少名称")
    elif target_module == "output_table":
        rows = patch.get("output_table") or []
        if not isinstance(rows, list) or not rows:
            issues.append("输出表格不能为空")
        names = [item.get("name") for item in rows if item.get("name")]
        if len(names) != len(set(names)):
            issues.append("输出字段名称重复")
    elif target_module == "test_cases":
        cases = patch.get("test_cases") or []
        if not isinstance(cases, list) or not cases:
            issues.append("测试样例不能为空")
    elif target_module == "workflow":
        workflow = patch.get("workflow") or {}
        if not isinstance(workflow, dict):
            issues.append("工作流 patch 格式非法")
        elif not workflow.get("nodes"):
            issues.append("工作流至少需要 1 个节点")

    structural_checks = []
    if issues:
        structural_checks.extend({"title": "结构错误", "status": "error", "message": issue} for issue in issues)
    if warnings:
        structural_checks.extend({"title": "结构警告", "status": "warning", "message": warning} for warning in warnings)
    if not structural_checks:
        structural_checks.append({"title": "结构校验", "status": "success", "message": "当前 patch 通过基础结构校验"})

    return {
        "valid": len(issues) == 0,
        "issues": [*issues, *warnings],
        "structural_checks": structural_checks,
        "sample_case_checks": [],
        "historical_replay_checks": [],
        "impact_summary": {"improved": 0, "regressed": 0, "unchanged": 0},
        "can_apply": len(issues) == 0,
        "warnings": warnings,
        "errors": issues,
    }
