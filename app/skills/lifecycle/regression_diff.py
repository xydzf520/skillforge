"""Skill 改动后的回归 diff。

不在 OpenClaw 上跑两次（成本太高且 reload 是全量 swap），而是用静态 diff：
  - 解析 base 和 head 两个版本的 SKILL.md
  - 对比 frontmatter / steps / branches / test_cases / output_definition
  - 对每条 changed 的 branch，标注哪些 test_case 会被影响
    （影响判定：test_case 的 expected 中提到了改动 branch 的 conclusion，
     或 test_case input 引用了 branch.condition 中的字段）

为什么静态足够：
  Skill 的执行结果取决于"决策树规则 + 输入 + 参数"。规则改动 = 决策树结构改动；
  动作的副作用（钉钉推送、写库）由 OpenClaw 完成，对回归 diff 而言关心的是
  "测试用例命中的 conclusion 会不会变"，这个用静态 diff 就能给出确定答案。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser, SkillStructured, DecisionStep, Branch


# ═══════════════════════════════════════════════════════
# diff 数据结构
# ═══════════════════════════════════════════════════════

@dataclass
class BranchDiff:
    step_id: str
    step_name: str
    branch_index: int
    change_type: str        # added / removed / modified
    old: dict | None = None
    new: dict | None = None
    affected_test_cases: list[str] = field(default_factory=list)
    fields_changed: list[str] = field(default_factory=list)  # condition / conclusion / action / next_step

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StepDiff:
    step_id: str
    change_type: str        # added / removed / modified / unchanged
    old_name: str | None = None
    new_name: str | None = None
    name_changed: bool = False
    branches_added: list[BranchDiff] = field(default_factory=list)
    branches_removed: list[BranchDiff] = field(default_factory=list)
    branches_modified: list[BranchDiff] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RegressionDiff:
    skill_id: str
    base_ref: str           # git ref / "working_tree" — 用户请求的 ref
    head_ref: str           # git ref / "working_tree"
    has_changes: bool
    summary: str
    steps: list[StepDiff] = field(default_factory=list)
    frontmatter_changes: dict = field(default_factory=dict)  # {field: {old, new}}
    test_cases_added: list[str] = field(default_factory=list)
    test_cases_removed: list[str] = field(default_factory=list)
    test_cases_affected: list[str] = field(default_factory=list)  # 受规则改动影响的现有用例
    output_definition_changed: bool = False
    # [H5] 透明告知用户的注意事项 — 例如降级 ref / 过度估计的影响判定
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

_FIELD_RE = re.compile(r"([\u4e00-\u9fffA-Za-z_][\u4e00-\u9fff\w]*)\s*[<>=]")


def _branch_to_dict(b: Branch) -> dict:
    return {
        "condition": b.condition or "",
        "conclusion": b.conclusion or "",
        "action": b.action or "",
        "next_step": b.next_step,
    }


def _branch_fields_diff(old: Branch, new: Branch) -> list[str]:
    """返回两 branch 不同的字段名列表。"""
    changed = []
    if (old.condition or "") != (new.condition or ""):
        changed.append("condition")
    if (old.conclusion or "") != (new.conclusion or ""):
        changed.append("conclusion")
    if (old.action or "") != (new.action or ""):
        changed.append("action")
    if (old.next_step or None) != (new.next_step or None):
        changed.append("next_step")
    return changed


def _extract_metric_names(condition: str) -> set[str]:
    """从 condition 中抽出可能的字段名（用于关联测试用例）。"""
    if not condition:
        return set()
    return set(_FIELD_RE.findall(condition))


def _affected_tests(
    branch: Branch,
    test_cases: list,
) -> list[str]:
    """找出哪些测试用例会被该 branch 的改动影响。

    影响判定（按 OR 关系）：
      1. test_case.expected_output.conclusion 包含 branch.conclusion
      2. test_case.input_data 的 key 出现在 branch.condition 的字段名集合中

    [H5] 注意: 这是 *过度估计*。条件 2 仅按字段名匹配, 不做值域分析:
       例如 condition `ROI > 2.0` 改成 `ROI > 1.0`, 输入 `ROI=1.5` 的用例
       会被标记影响, 但实际从不走该分支。前端在展示 affected 列表时应附加
       "estimated, may include false positives" 的提示。
    """
    if not test_cases:
        return []
    referenced_metrics = _extract_metric_names(branch.condition or "")
    target_conclusion = (branch.conclusion or "").strip()
    out: list[str] = []
    for tc in test_cases:
        name = getattr(tc, "name", None) or "(unnamed)"
        # 命中条件 1：expected.conclusion 一致
        expected = getattr(tc, "expected_output", None) or {}
        if isinstance(expected, dict) and target_conclusion:
            if str(expected.get("conclusion") or "").strip() == target_conclusion:
                out.append(name)
                continue
        # 命中条件 2：input 字段重叠
        inp = getattr(tc, "input_data", None) or {}
        if isinstance(inp, dict) and referenced_metrics:
            if set(inp.keys()) & referenced_metrics:
                out.append(name)
                continue
    return out


# ═══════════════════════════════════════════════════════
# 主对比函数
# ═══════════════════════════════════════════════════════

def diff_structured(
    skill_id: str,
    base_md: str,
    head_md: str,
    *,
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
) -> RegressionDiff:
    """对两份 SKILL.md 文本做结构化 diff。"""
    base = skill_parser.parse(base_md) if base_md else SkillStructured()
    head = skill_parser.parse(head_md) if head_md else SkillStructured()

    diff = RegressionDiff(
        skill_id=skill_id,
        base_ref=base_ref,
        head_ref=head_ref,
        has_changes=False,
        summary="",
    )

    # ── 1. frontmatter ──
    base_fm = base.frontmatter or {}
    head_fm = head.frontmatter or {}
    fm_keys = set(base_fm.keys()) | set(head_fm.keys())
    for key in fm_keys:
        ov = base_fm.get(key)
        nv = head_fm.get(key)
        if ov != nv:
            diff.frontmatter_changes[key] = {"old": ov, "new": nv}

    # ── 2. steps ──
    base_steps_by_id: dict[str, DecisionStep] = {s.id: s for s in (base.steps or []) if s.id}
    head_steps_by_id: dict[str, DecisionStep] = {s.id: s for s in (head.steps or []) if s.id}
    all_step_ids = set(base_steps_by_id.keys()) | set(head_steps_by_id.keys())

    affected_tests_set: set[str] = set()

    for sid in sorted(all_step_ids):
        old_step = base_steps_by_id.get(sid)
        new_step = head_steps_by_id.get(sid)

        if old_step and not new_step:
            # 整个 step 被删
            sd = StepDiff(
                step_id=sid,
                change_type="removed",
                old_name=old_step.name,
            )
            for idx, br in enumerate(old_step.branches or []):
                bd = BranchDiff(
                    step_id=sid,
                    step_name=old_step.name or sid,
                    branch_index=idx,
                    change_type="removed",
                    old=_branch_to_dict(br),
                    affected_test_cases=_affected_tests(br, head.test_cases or []),
                )
                affected_tests_set.update(bd.affected_test_cases)
                sd.branches_removed.append(bd)
            diff.steps.append(sd)
            continue

        if new_step and not old_step:
            # 新增的 step
            sd = StepDiff(
                step_id=sid,
                change_type="added",
                new_name=new_step.name,
            )
            for idx, br in enumerate(new_step.branches or []):
                bd = BranchDiff(
                    step_id=sid,
                    step_name=new_step.name or sid,
                    branch_index=idx,
                    change_type="added",
                    new=_branch_to_dict(br),
                )
                sd.branches_added.append(bd)
            diff.steps.append(sd)
            continue

        # step 两边都有：对比 branches
        sd = StepDiff(
            step_id=sid,
            change_type="unchanged",
            old_name=old_step.name,
            new_name=new_step.name,
            name_changed=(old_step.name != new_step.name),
        )

        old_branches = old_step.branches or []
        new_branches = new_step.branches or []
        max_len = max(len(old_branches), len(new_branches))
        any_branch_change = False

        for i in range(max_len):
            ob = old_branches[i] if i < len(old_branches) else None
            nb = new_branches[i] if i < len(new_branches) else None

            if ob and not nb:
                bd = BranchDiff(
                    step_id=sid,
                    step_name=old_step.name or sid,
                    branch_index=i,
                    change_type="removed",
                    old=_branch_to_dict(ob),
                    affected_test_cases=_affected_tests(ob, head.test_cases or []),
                )
                affected_tests_set.update(bd.affected_test_cases)
                sd.branches_removed.append(bd)
                any_branch_change = True
            elif nb and not ob:
                bd = BranchDiff(
                    step_id=sid,
                    step_name=new_step.name or sid,
                    branch_index=i,
                    change_type="added",
                    new=_branch_to_dict(nb),
                )
                sd.branches_added.append(bd)
                any_branch_change = True
            else:
                changed_fields = _branch_fields_diff(ob, nb)
                if changed_fields:
                    bd = BranchDiff(
                        step_id=sid,
                        step_name=new_step.name or sid,
                        branch_index=i,
                        change_type="modified",
                        old=_branch_to_dict(ob),
                        new=_branch_to_dict(nb),
                        fields_changed=changed_fields,
                        affected_test_cases=_affected_tests(nb, head.test_cases or []),
                    )
                    affected_tests_set.update(bd.affected_test_cases)
                    sd.branches_modified.append(bd)
                    any_branch_change = True

        if any_branch_change or sd.name_changed:
            sd.change_type = "modified"
        if sd.change_type != "unchanged" or sd.name_changed:
            diff.steps.append(sd)

    # ── 3. test_cases ──
    base_tc_names = {tc.name for tc in (base.test_cases or []) if tc.name}
    head_tc_names = {tc.name for tc in (head.test_cases or []) if tc.name}
    diff.test_cases_added = sorted(head_tc_names - base_tc_names)
    diff.test_cases_removed = sorted(base_tc_names - head_tc_names)
    diff.test_cases_affected = sorted(affected_tests_set)

    # ── 4. output_definition ──
    base_out = [(o.name, o.format, o.recipient, o.approval_level) for o in (base.output_definition or [])]
    head_out = [(o.name, o.format, o.recipient, o.approval_level) for o in (head.output_definition or [])]
    diff.output_definition_changed = (base_out != head_out)

    # ── 5. summary ──
    branch_changes = sum(
        len(s.branches_added) + len(s.branches_removed) + len(s.branches_modified)
        for s in diff.steps
    )
    diff.has_changes = bool(
        diff.frontmatter_changes
        or diff.steps
        or diff.test_cases_added
        or diff.test_cases_removed
        or diff.output_definition_changed
    )

    if not diff.has_changes:
        diff.summary = "无规则层面变化"
    else:
        parts = []
        if branch_changes:
            parts.append(f"{branch_changes} 个分支变动")
        if diff.test_cases_affected:
            parts.append(f"影响 {len(diff.test_cases_affected)} 个测试用例")
        if diff.test_cases_added:
            parts.append(f"新增 {len(diff.test_cases_added)} 个测试用例")
        if diff.test_cases_removed:
            parts.append(f"删除 {len(diff.test_cases_removed)} 个测试用例")
        if diff.frontmatter_changes:
            parts.append(f"{len(diff.frontmatter_changes)} 项元信息修改")
        if diff.output_definition_changed:
            parts.append("输出定义改动")
        diff.summary = "；".join(parts) if parts else "细微改动"

    # [H5] 影响判定的过度估计提示 — 仅在确实输出 affected 列表时附上
    if diff.test_cases_affected:
        diff.notes.append(
            "test_cases_affected 是按字段名 + conclusion 字面匹配的过度估计, "
            "可能包含假阳性 — 实际不走该分支的输入会被误标"
        )

    return diff


# ═══════════════════════════════════════════════════════
# 入口：从 git 取两份 SKILL.md
# ═══════════════════════════════════════════════════════

def compute_regression_diff(
    skill_id: str,
    *,
    base_ref: str = "HEAD~1",
) -> RegressionDiff:
    """对比工作区当前 SKILL.md 与某个历史 commit 的版本。

    Args:
        skill_id: Skill ID
        base_ref: 对比的 base 版本（默认 HEAD~1，即上一次 commit）。
                  传 "HEAD" 表示与当前 HEAD 比（看工作区未提交的变更）

    [H5] base_ref 不存在时降级:
        - HEAD~1 不存在 (例如这是首个 commit) → 退到 HEAD, notes 提示
        - HEAD 也读不到 → 空字符串, 把所有 head 内容当成新增
    """
    head_md = git_service.read_file(skill_id, "SKILL.md") or ""
    actual_base_ref = base_ref
    base_md = git_service.get_file_at_commit(skill_id, "SKILL.md", base_ref) or ""
    fallback_note: str | None = None

    # 如果 base_ref 拿不到, 且不是已经请求 HEAD, 自动降级到 HEAD
    if not base_md and base_ref != "HEAD":
        head_attempt = git_service.get_file_at_commit(skill_id, "SKILL.md", "HEAD") or ""
        if head_attempt:
            base_md = head_attempt
            actual_base_ref = "HEAD"
            fallback_note = (
                f"base_ref={base_ref} 不存在或无内容, 已降级到 HEAD 比对 (你看到的"
                f"是工作区与最后一次 commit 的差异)"
            )

    diff = diff_structured(
        skill_id,
        base_md,
        head_md,
        base_ref=actual_base_ref,
        head_ref="working_tree",
    )
    if fallback_note:
        diff.notes.insert(0, fallback_note)
    return diff
