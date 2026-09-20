"""静态检查规则集合。每个规则都是纯函数，输入 SkillStructured，输出 LintIssue[]。"""

from __future__ import annotations
import re

from app.skills.core.parser import SkillStructured
from app.skills.lint.linter import LintIssue


# ═══════════════════════════════════════════════════════
# 规则 1: 基础元信息完整性
# ═══════════════════════════════════════════════════════

def check_metadata(s: SkillStructured) -> list[LintIssue]:
    """检查 frontmatter 必填字段。"""
    issues = []
    fm = s.frontmatter or {}

    if not fm.get("name"):
        issues.append(LintIssue(
            rule="metadata.name",
            severity="error",
            message="缺少 Skill 名称（frontmatter.name）",
            suggestion="在 SKILL.md 头部的 YAML 中添加 name 字段",
        ))

    if not fm.get("description") and not s.purpose:
        issues.append(LintIssue(
            rule="metadata.description",
            severity="error",
            message="缺少目标描述（frontmatter.description 或「目的」章节）",
            suggestion="用一句话说明这个 Skill 的业务价值",
        ))

    if not fm.get("department"):
        issues.append(LintIssue(
            rule="metadata.department",
            severity="warning",
            message="未指定所属部门",
            suggestion="添加 department 字段方便权限管理",
        ))

    if not fm.get("risk_level"):
        issues.append(LintIssue(
            rule="metadata.risk_level",
            severity="warning",
            message="未指定风险等级（R1/R2/R3/R4）",
            suggestion="根据动作影响评估风险等级：R1 可逆/R2 默认/R3 需审批/R4 高风险",
        ))

    return issues


# ═══════════════════════════════════════════════════════
# 规则 2: 分支完备性（最关键 — 基线 53% 缺兜底）
# ═══════════════════════════════════════════════════════

ELSE_KEYWORDS = ["其他", "else", "默认", "default", "不满足", "否则", "兜底", "剩余"]


def has_else_branch(branches: list) -> bool:
    """判断分支列表是否包含兜底分支。公开 API（coach/retrieval/motif 复用）。"""
    if not branches:
        return False
    last = (branches[-1].condition or "").lower()
    return any(kw in last for kw in ELSE_KEYWORDS)


# 保留私有别名以兼容模块内旧调用
_has_else_branch = has_else_branch


def check_branch_completeness(s: SkillStructured) -> list[LintIssue]:
    """检查每个决策步骤是否有兜底分支。"""
    issues = []
    if not s.steps:
        return issues

    for step in s.steps:
        if not step.branches:
            issues.append(LintIssue(
                rule="branch_completeness",
                severity="error",
                message=f"步骤「{step.name or step.id}」没有任何分支",
                location=step.id,
                suggestion="添加至少一个判断分支 + 兜底分支",
                auto_fixable=True,
            ))
            continue

        # 只有 1 个分支时肯定缺 else
        if len(step.branches) == 1 and not _has_else_branch(step.branches):
            issues.append(LintIssue(
                rule="branch_completeness",
                severity="error",
                message=f"步骤「{step.name or step.id}」只有 1 个分支，缺少兜底（else）",
                location=step.id,
                suggestion=f"添加一个「其他情况」分支处理未命中的输入",
                auto_fixable=True,
            ))
        elif len(step.branches) >= 2 and not _has_else_branch(step.branches):
            issues.append(LintIssue(
                rule="branch_completeness",
                severity="warning",
                message=f"步骤「{step.name or step.id}」缺少兜底分支，未命中所有条件时行为未定义",
                location=step.id,
                suggestion="添加「其他情况」分支，明确未预期输入的处理方式",
                auto_fixable=True,
            ))

    return issues


# ═══════════════════════════════════════════════════════
# 规则 3: 不可达分支
# ═══════════════════════════════════════════════════════

def check_unreachable_branch(s: SkillStructured) -> list[LintIssue]:
    """检查是否有永远不会被触发的分支。

    简化版：只检查 else 分支后面还有其他分支的情况。
    """
    issues = []
    for step in (s.steps or []):
        if not step.branches or len(step.branches) < 2:
            continue

        # 找到第一个 else 分支
        for i, br in enumerate(step.branches):
            cond = (br.condition or "").lower()
            if any(kw in cond for kw in ELSE_KEYWORDS) and i < len(step.branches) - 1:
                # else 后面还有分支 → 不可达
                for j in range(i + 1, len(step.branches)):
                    later = step.branches[j]
                    issues.append(LintIssue(
                        rule="unreachable_branch",
                        severity="error",
                        message=f"步骤「{step.name}」的分支 #{j+1}「{later.condition[:30]}」永远不会执行（前面已有兜底分支）",
                        location=f"{step.id}#branch_{j}",
                        suggestion="把兜底分支移到最后，或删除不可达分支",
                    ))
                break

    return issues


# ═══════════════════════════════════════════════════════
# 规则 4: 阈值冲突（两个条件区间重叠）
# ═══════════════════════════════════════════════════════

_NUM_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)")
_OP_PATTERN = re.compile(r"([<>]=?|==|!=)")


def _extract_threshold(condition: str) -> tuple[str, float] | None:
    """从条件字符串中提取比较符和数值。"""
    if not condition:
        return None
    op_match = _OP_PATTERN.search(condition)
    num_match = _NUM_PATTERN.search(condition)
    if not op_match or not num_match:
        return None
    try:
        return op_match.group(1), float(num_match.group(1))
    except ValueError:
        return None


def check_threshold_conflict(s: SkillStructured) -> list[LintIssue]:
    """检查同一步骤内多个阈值是否有区间重叠。

    例如:
      ROI > 1.5 → 绿灯
      ROI > 1.2 → 黄灯  ← 与上面重叠（当 ROI=1.6 时都命中）
    """
    issues = []
    for step in (s.steps or []):
        if not step.branches or len(step.branches) < 2:
            continue

        thresholds = []
        for br in step.branches:
            t = _extract_threshold(br.condition or "")
            if t:
                thresholds.append((br.condition, t))

        # 检查是否有重叠的 > 条件
        gt_conditions = [(c, v) for c, (op, v) in thresholds if op in ('>', '>=')]
        if len(gt_conditions) >= 2:
            # 按阈值降序排序，正常应该是 "> 1.5" 在 "> 1.2" 之前
            sorted_gts = sorted(gt_conditions, key=lambda x: -x[1])
            if [x[0] for x in sorted_gts] != [x[0] for x in gt_conditions]:
                issues.append(LintIssue(
                    rule="threshold_conflict",
                    severity="warning",
                    message=f"步骤「{step.name}」的阈值顺序可能导致重叠：大阈值应该在小阈值之前",
                    location=step.id,
                    suggestion="按阈值降序排列分支（例如 > 1.5 应在 > 1.2 之前）",
                ))

    return issues


# ═══════════════════════════════════════════════════════
# 规则 5: 阈值空洞（未覆盖的区间）
# ═══════════════════════════════════════════════════════

def check_threshold_gap(s: SkillStructured) -> list[LintIssue]:
    """检查阈值之间是否有未覆盖的区间。

    例如:
      ROI > 1.5 → 绿灯
      ROI < 0.8 → 红灯
      ← 缺失 [0.8, 1.5] 区间的处理
    """
    issues = []
    for step in (s.steps or []):
        if not step.branches or len(step.branches) < 2:
            continue

        # 如果已经有 else 分支，就不算空洞
        if _has_else_branch(step.branches):
            continue

        gts = []  # > 阈值
        lts = []  # < 阈值
        for br in step.branches:
            t = _extract_threshold(br.condition or "")
            if not t:
                continue
            op, val = t
            if op in ('>', '>='):
                gts.append(val)
            elif op in ('<', '<='):
                lts.append(val)

        if gts and lts:
            min_gt = min(gts)
            max_lt = max(lts)
            if max_lt < min_gt:
                issues.append(LintIssue(
                    rule="threshold_gap",
                    severity="warning",
                    message=f"步骤「{step.name}」的阈值区间 [{max_lt}, {min_gt}] 未被覆盖",
                    location=step.id,
                    suggestion=f"添加中间区间的分支或兜底分支",
                ))

    return issues


# ═══════════════════════════════════════════════════════
# 规则 6: 术语一致性
# ═══════════════════════════════════════════════════════

def check_term_consistency(s: SkillStructured) -> list[LintIssue]:
    """检查条件中引用的术语是否在数据输入或参数中定义。"""
    issues = []
    if not s.steps:
        return issues

    # 收集已定义的术语
    defined_terms = set()
    for di in (s.data_inputs or []):
        if di.name:
            defined_terms.add(di.name)
    for key in (s.frontmatter or {}).keys():
        defined_terms.add(key)
    # 参数名从 policy_pack 等来源

    # 从条件中提取被引用的术语（中文词/英文标识符）
    term_pattern = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}')
    referenced = set()
    for step in s.steps:
        for br in step.branches:
            if br.condition:
                for term in term_pattern.findall(br.condition):
                    # 过滤掉常见操作词
                    if term not in {"绿灯", "红灯", "黄灯", "通过", "失败", "true", "false", "null"}:
                        referenced.add(term)

    # 暂不报未定义术语（误报率太高），仅记录用于后续 AI 分析
    return issues


# ═══════════════════════════════════════════════════════
# 规则 7: 测试覆盖
# ═══════════════════════════════════════════════════════

def check_test_coverage(s: SkillStructured) -> list[LintIssue]:
    """检查是否每个分支都有测试覆盖。"""
    issues = []

    total_branches = sum(len(step.branches) for step in (s.steps or []))
    test_count = len(s.test_cases or [])

    if total_branches == 0:
        # 没有规则，只检查反例
        if not (s.antipatterns or []):
            issues.append(LintIssue(
                rule="test_coverage.antipatterns",
                severity="warning",
                message="没有定义反例（容易误判的场景）",
                suggestion="添加 2-3 个反例防止 Skill 误判边界情况",
                auto_fixable=True,
            ))
        return issues

    if test_count == 0:
        issues.append(LintIssue(
            rule="test_coverage",
            severity="error",
            message=f"该 Skill 有 {total_branches} 个分支但没有任何测试用例",
            suggestion=f"至少添加 {min(total_branches, 3)} 个测试用例覆盖主要分支",
            auto_fixable=True,
        ))
    elif test_count < total_branches:
        issues.append(LintIssue(
            rule="test_coverage",
            severity="warning",
            message=f"测试用例数 ({test_count}) 少于分支数 ({total_branches})，可能存在未覆盖分支",
            suggestion=f"建议至少 {total_branches} 个测试用例覆盖全部分支",
            auto_fixable=True,
        ))

    # 反例检查
    if not (s.antipatterns or []):
        issues.append(LintIssue(
            rule="test_coverage.antipatterns",
            severity="warning",
            message="没有定义反例（容易误判的场景）",
            suggestion="添加 2-3 个反例防止 Skill 误判边界情况",
            auto_fixable=True,
        ))

    return issues


# ═══════════════════════════════════════════════════════
# 规则 8: 参数使用
# ═══════════════════════════════════════════════════════

def check_param_usage(s: SkillStructured) -> list[LintIssue]:
    """检查规则中的 magic number 是否应该抽成参数。"""
    issues = []
    if not s.steps:
        return issues

    magic_number_count = 0
    for step in s.steps:
        for br in step.branches:
            if not br.condition:
                continue
            # 查找条件中的数字
            numbers = _NUM_PATTERN.findall(br.condition)
            for n in numbers:
                # 0/1/2 这种小数字不算 magic number（可能是枚举）
                try:
                    val = float(n)
                    if abs(val) > 2 or (val != 0 and val != 1 and val != 2 and '.' in n):
                        magic_number_count += 1
                except ValueError:
                    pass

    if magic_number_count >= 3:
        issues.append(LintIssue(
            rule="param_usage",
            severity="info",
            message=f"检测到 {magic_number_count} 个 magic number（硬编码数值），建议抽成参数方便调整",
            suggestion="把阈值抽取到 policy_pack.yaml 作为可配参数",
            auto_fixable=True,
        ))

    return issues
