"""AI Change Coach — 事件驱动的主动编辑建议。

不等用户问，根据编辑事件给出"下一步最值得改的 3 项"：

触发事件:
  - branch_changed: 分支新增/删除 → 检查完备性
  - param_changed: 参数改动 → 预估影响
  - coverage_gap: 覆盖盲区 → 生成测试建议
  - save_pending: 保存前 → 质检汇总
  - metric_degraded: 指标恶化 → 定位问题规则

输出: 按优先级排序的建议卡列表。
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal

from app.skills.core.parser import SkillStructured
from app.skills.lint import lint_skill, has_else_branch


EventType = Literal[
    "branch_changed", "param_changed", "coverage_gap",
    "save_pending", "metric_degraded", "open_editor",
    "reverted_repeatedly",
]

SuggestionKind = Literal["inline", "diff", "evidence_card"]


@dataclass
class Suggestion:
    """一条编辑建议。"""
    id: str
    title: str                       # 简短标题
    description: str                 # 详细说明
    kind: SuggestionKind             # 输出形态
    severity: str = "info"           # critical/high/medium/low/info
    module: str = ""                 # 建议关联的模块
    action: str = ""                 # 建议的动作 ID
    evidence: dict = field(default_factory=dict)  # 证据数据（样本/指标/引用）
    priority: int = 0                # 0-100，越高越优先

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoachResponse:
    """Coach 对一次事件的响应。"""
    event: EventType
    suggestions: list[Suggestion] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "summary": self.summary,
        }


async def handle_event(
    event: EventType,
    structured: SkillStructured,
    *,
    context: dict | None = None,
) -> CoachResponse:
    """处理编辑事件，返回建议列表。"""
    context = context or {}
    response = CoachResponse(event=event)

    # 每个事件触发不同的检查器
    if event == "branch_changed":
        _check_branch_completeness(structured, response)
        _check_param_extraction(structured, response)
    elif event == "param_changed":
        _check_param_impact(structured, response, context)
    elif event == "coverage_gap":
        _check_missing_tests(structured, response)
    elif event == "save_pending":
        _run_full_checklist(structured, response)
    elif event == "metric_degraded":
        _check_degradation(structured, response, context)
    elif event == "reverted_repeatedly":
        _check_repeated_revert(structured, response, context)
    elif event == "open_editor":
        _give_onboarding_suggestions(structured, response)

    # 按优先级排序
    response.suggestions.sort(key=lambda s: -s.priority)
    response.suggestions = response.suggestions[:3]  # 最多 3 条

    if response.suggestions:
        response.summary = f"发现 {len(response.suggestions)} 项可改进"
    else:
        response.summary = "当前状态良好"

    return response


# ═══════════════════════════════════════════════════════
# 检查器实现
# ═══════════════════════════════════════════════════════

def _check_branch_completeness(structured: SkillStructured, response: CoachResponse):
    """分支完备性检查。"""
    for step in (structured.steps or []):
        if not step.branches:
            continue
        if not has_else_branch(step.branches):
            response.suggestions.append(Suggestion(
                id=f"complete_{step.id}",
                title=f"步骤「{step.name or step.id}」缺少兜底分支",
                description="未命中所有条件时行为未定义，建议添加「其他情况」分支。",
                kind="diff",
                severity="high",
                module="rules",
                action="complete_branches",
                priority=90,
            ))


def _check_param_extraction(structured: SkillStructured, response: CoachResponse):
    """检查 magic number 是否应提取为参数。"""
    import re
    num_re = re.compile(r"-?\d+(?:\.\d+)?")

    magic_nums = set()
    for step in (structured.steps or []):
        for br in step.branches:
            for n in num_re.findall(br.condition or ""):
                try:
                    val = float(n)
                    if abs(val) > 2 and val not in (0, 1, 2):
                        magic_nums.add(n)
                except ValueError:
                    pass

    if len(magic_nums) >= 3:
        response.suggestions.append(Suggestion(
            id="extract_params",
            title=f"发现 {len(magic_nums)} 个硬编码数值",
            description=f"建议抽取为参数便于调整和实验：{', '.join(list(magic_nums)[:5])}",
            kind="diff",
            severity="medium",
            module="params",
            action="extract_param",
            priority=60,
            evidence={"magic_numbers": list(magic_nums)},
        ))


def _relative_change(old, new) -> float | None:
    """计算相对变化（返回绝对比例，如 0.25 表示 ±25%）。非数值返回 None。"""
    try:
        o = float(old)
        n = float(new)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None if n == 0 else float("inf")
    return abs(n - o) / abs(o)


def _check_param_impact(structured, response, context):
    """参数改动时预估影响（需要执行数据）。

    §4.2 规则：阈值改动超过 ±20% 时应升级为 high 级 evidence_card。
    """
    param_name = context.get("param_name", "")
    old_value = context.get("old_value")
    new_value = context.get("new_value")

    if not param_name:
        return

    # 找到所有引用该参数的分支
    referenced_branches = []
    for step in (structured.steps or []):
        for i, br in enumerate(step.branches):
            if param_name in (br.condition or ""):
                referenced_branches.append({"step": step.id, "branch": i, "condition": br.condition})

    # 跨 Skill 下游影响（context 里若带有 cross_skill_usages 直接用，否则不强求）
    # router 端在调用 coachEvent 时可以预先注入；coach 自身不直接查 DB 避免阻塞
    cross_skill_usages = context.get("cross_skill_usages") or []

    if not referenced_branches and not cross_skill_usages:
        return

    # §4.2 ±20% 阈值：根据变化幅度升级严重度
    rel_change = _relative_change(old_value, new_value)
    is_major = rel_change is not None and rel_change >= 0.20

    cross_count = len({u.get("skill_id") for u in cross_skill_usages})
    cross_text = f"，下游 {cross_count} 个 Skill 也引用了该字段" if cross_count else ""

    if is_major:
        pct = int(rel_change * 100) if rel_change != float("inf") else None
        direction = "上调" if (new_value or 0) > (old_value or 0) else "下调"
        pct_text = f"{direction} {pct}%" if pct is not None else "从 0 调起"
        title = f"参数 {param_name} {pct_text}（超过 ±20%）"
        desc = (
            f"从 {old_value} 改为 {new_value}，影响 {len(referenced_branches)} 个分支{cross_text}。"
            f"建议打开影响预览面板查看样本命中变化。"
        )
        severity = "high"
        priority = 92
    elif cross_count:
        title = f"参数 {param_name} 影响 {len(referenced_branches)} 个本地分支 + {cross_count} 个下游 Skill"
        desc = f"从 {old_value} 改为 {new_value}，下游 Skill 也引用了同名字段，请确认其他 Skill 的判定不受波及"
        severity = "high"
        priority = 88
    else:
        title = f"参数 {param_name} 影响 {len(referenced_branches)} 个分支"
        desc = f"从 {old_value} 改为 {new_value}，建议回放最近样本查看影响"
        severity = "medium"
        priority = 75

    response.suggestions.append(Suggestion(
        id=f"param_impact_{param_name}",
        title=title,
        description=desc,
        kind="evidence_card",
        severity=severity,
        module="params",
        action="simulate_param",
        priority=priority,
        evidence={
            "param": param_name,
            "old": old_value,
            "new": new_value,
            "relative_change": rel_change if rel_change != float("inf") else None,
            "is_major_change": is_major,
            "affected_branches": referenced_branches,
            "cross_skill_usages": cross_skill_usages,
            "cross_skill_count": cross_count,
        },
    ))


def _check_missing_tests(structured, response):
    """检查测试覆盖盲区。"""
    total_branches = sum(len(s.branches) for s in (structured.steps or []))
    test_count = len(structured.test_cases or [])

    if total_branches > 0 and test_count < total_branches:
        gap = total_branches - test_count
        response.suggestions.append(Suggestion(
            id="generate_tests",
            title=f"缺少 {gap} 个测试用例",
            description=f"当前 {test_count}/{total_branches} 分支有测试，AI 可自动生成缺失用例",
            kind="diff",
            severity="high" if test_count == 0 else "medium",
            module="test_cases",
            action="generate_tests",
            priority=85 if test_count == 0 else 65,
        ))

    if not structured.antipatterns:
        response.suggestions.append(Suggestion(
            id="generate_antipatterns",
            title="缺少反例",
            description="添加 2-3 个易误判的场景可显著降低误判率",
            kind="diff",
            severity="medium",
            module="antipatterns",
            action="generate_counter_examples",
            priority=55,
        ))


def _run_full_checklist(structured, response):
    """保存前完整质检。"""
    report = lint_skill("(preview)", structured)
    for err in report.errors[:2]:
        response.suggestions.append(Suggestion(
            id=f"lint_{err.rule}",
            title=err.message,
            description=err.suggestion or "修复后才能通过质检门禁",
            kind="evidence_card",
            severity="high",
            module="",
            action="fix_lint",
            priority=95,
            evidence={"rule": err.rule, "location": err.location},
        ))
    for warn in report.warnings[:1]:
        response.suggestions.append(Suggestion(
            id=f"warn_{warn.rule}",
            title=warn.message,
            description=warn.suggestion or "建议修复",
            kind="inline",
            severity="medium",
            module="",
            action="fix_lint",
            priority=40,
        ))


def _check_repeated_revert(structured, response, context):
    """§4.2 同一处被反复撤销修改 → 提供替代方案。

    context 期望字段：
      - location: 位置标识（step_id / param_name）
      - revert_count: 撤销次数
      - last_values: [值1, 值2, ...] 历次修改的值列表
    """
    location = context.get("location", "")
    revert_count = context.get("revert_count", 0)
    last_values = context.get("last_values", [])

    if not location or revert_count < 2:
        return

    response.suggestions.append(Suggestion(
        id=f"repeated_revert_{location}",
        title=f"「{location}」已反复修改 {revert_count} 次",
        description=(
            "你似乎在这个位置反复犹豫。建议：1) 查看相关的历史数据；"
            "2) 试试 AI 提取替代方案；3) 切到画布视图看全局影响"
        ),
        kind="evidence_card",
        severity="medium",
        module="",
        action="suggest_alternatives",
        priority=80,
        evidence={
            "location": location,
            "revert_count": revert_count,
            "last_values": last_values[-5:],  # 最多保留 5 个历史值
        },
    ))


def _check_degradation(structured, response, context):
    """运行指标恶化时的建议。"""
    metric = context.get("metric", "误判率")
    delta = context.get("delta", 0)
    response.suggestions.append(Suggestion(
        id="investigate_degradation",
        title=f"{metric} 恶化 {delta}%",
        description="建议查看最近样本回放，定位受影响的规则分支",
        kind="evidence_card",
        severity="high",
        action="analyze_failure",
        priority=100,
        evidence=context,
    ))


def _give_onboarding_suggestions(structured, response):
    """打开编辑器时给的引导建议。"""
    steps_count = len(structured.steps or [])
    tests_count = len(structured.test_cases or [])

    # 没规则 → 建议先定义规则
    if steps_count == 0:
        response.suggestions.append(Suggestion(
            id="add_first_rule",
            title="开始定义决策规则",
            description="Skill 核心是决策规则，点击「规则」模块添加第一个判定步骤",
            kind="inline",
            severity="info",
            module="rules",
            action="go_rules",
            priority=90,
        ))
        return

    # 有规则但没测试 → 建议补测试
    if tests_count == 0:
        response.suggestions.append(Suggestion(
            id="add_first_test",
            title="添加测试用例",
            description=f"{steps_count} 条规则但 0 个测试，建议至少添加 2-3 个用例",
            kind="inline",
            severity="medium",
            module="test_cases",
            action="generate_tests",
            priority=80,
        ))

    # 检查完备性
    missing_else = sum(1 for s in structured.steps if not has_else_branch(s.branches))
    if missing_else:
        response.suggestions.append(Suggestion(
            id="complete_all_else",
            title=f"{missing_else} 个步骤缺少兜底分支",
            description="一键为所有步骤补全「其他情况」分支",
            kind="diff",
            severity="high",
            module="rules",
            action="complete_all_branches",
            priority=85,
        ))
