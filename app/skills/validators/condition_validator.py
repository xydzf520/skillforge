"""条件表达式 DSL 验证器。

对 SKILL.md 中决策树分支的条件表达式做语法校验。
支持的条件格式：
  - 比较运算: ROI > 1.5, 预算 <= 10000
  - 模板变量: ROI > {threshold}, {metric} >= {baseline}
  - 自然语言: "属于高风险类别", "是否需要审批"
  - 范围: "ROI between 1.0 and 2.0", "介于 A 和 B"
  - 否定: "不属于白名单"
  - 复合: 多个条件用 且/或/AND/OR 连接

验证规则（warning 级别，不阻断保存）：
  1. 条件不能为空
  2. 模板变量 {xxx} 必须在 frontmatter 或 policy_pack 中有定义
  3. 比较运算符两侧不能都是常量（无参数化意义）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 模板变量提取正则
_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)\}")

# 比较运算符
_COMPARISON_OP_RE = re.compile(r"[><=≥≤≠]+")

# 纯常量检测（数字/百分比/布尔）
_CONSTANT_RE = re.compile(r"^[\d.,]+%?$|^true$|^false$|^是$|^否$", re.IGNORECASE)


@dataclass
class ConditionValidationResult:
    """条件验证结果"""
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    template_vars: set[str] = field(default_factory=set)


def validate_conditions(steps: list, known_params: set[str] | None = None) -> ConditionValidationResult:
    """验证决策树中所有分支条件的合法性。

    Args:
        steps: parser 解析后的 DecisionStep 列表
        known_params: frontmatter / policy_pack 中定义的参数名集合
    """
    result = ConditionValidationResult()
    params = known_params or set()

    for step in steps:
        step_id = getattr(step, "id", "?")
        branches = getattr(step, "branches", [])

        for i, branch in enumerate(branches):
            condition = getattr(branch, "condition", "").strip()

            # 1. 空条件检测
            if not condition:
                result.warnings.append(
                    f"Step {step_id} 分支 {i + 1}: 条件为空"
                )
                continue

            # 2. 模板变量引用检查
            template_vars = set(_TEMPLATE_VAR_RE.findall(condition))
            result.template_vars.update(template_vars)
            if params:
                undefined = template_vars - params
                for var in sorted(undefined):
                    result.warnings.append(
                        f"Step {step_id} 分支 {i + 1}: 条件引用了未定义的参数 '{{{var}}}'"
                    )

            # 3. 纯常量比较检测
            if _COMPARISON_OP_RE.search(condition) and not template_vars:
                parts = _COMPARISON_OP_RE.split(condition)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) == 2 and all(_CONSTANT_RE.match(p) for p in parts):
                    result.warnings.append(
                        f"Step {step_id} 分支 {i + 1}: 条件 '{condition}' 两侧都是常量，建议参数化"
                    )

    result.ok = not result.errors
    return result
