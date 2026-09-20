"""
语义化 Diff：将 SKILL.md 的原始文本 diff 转换为结构化变更描述。
审核人可以快速看到"改了什么"而非逐行阅读 Markdown diff。
"""

from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser, SkillStructured


async def compute_semantic_diff(review) -> dict:
    """对比审核的 before/after 两个版本，返回语义化变更列表。"""
    skill_id = review.skill_id

    # 获取新旧版本 SKILL.md
    old_content = ""
    if review.git_commit_before:
        old_content = git_service.get_file_at_commit(
            skill_id, "SKILL.md", review.git_commit_before
        ) or ""

    new_content = ""
    if review.git_commit_after:
        new_content = git_service.get_file_at_commit(
            skill_id, "SKILL.md", review.git_commit_after
        ) or ""

    # 解析结构
    old_parsed = skill_parser.parse(old_content) if old_content else SkillStructured()
    new_parsed = skill_parser.parse(new_content) if new_content else SkillStructured()

    changes = []

    # 1. Frontmatter 对比
    changes.extend(_diff_frontmatter(old_parsed.frontmatter, new_parsed.frontmatter))

    # 2. 目的对比
    if old_parsed.purpose.strip() != new_parsed.purpose.strip():
        changes.append({
            "category": "purpose",
            "type": "modified",
            "description": "修改了「目的」章节",
            "old_value": old_parsed.purpose[:200],
            "new_value": new_parsed.purpose[:200],
        })

    # 3. 决策步骤对比（核心）
    changes.extend(_diff_steps(old_parsed.steps, new_parsed.steps))

    # 4. 反例对比
    changes.extend(_diff_list(
        "antipattern",
        [(a.scenario, a) for a in old_parsed.antipatterns],
        [(a.scenario, a) for a in new_parsed.antipatterns],
        lambda a: f"{a.scenario} → {a.correct_action}",
    ))

    # 5. 输出定义对比
    changes.extend(_diff_list(
        "output",
        [(o.name, o) for o in old_parsed.output_definition],
        [(o.name, o) for o in new_parsed.output_definition],
        lambda o: f"{o.name}({o.format})",
    ))

    # 6. 数据输入对比
    changes.extend(_diff_list(
        "data_input",
        [(d.name, d) for d in old_parsed.data_inputs],
        [(d.name, d) for d in new_parsed.data_inputs],
        lambda d: f"{d.name} ← {d.source}",
    ))

    # 7. 测试用例对比
    changes.extend(_diff_list(
        "test_case",
        [(t.name, t) for t in old_parsed.test_cases],
        [(t.name, t) for t in new_parsed.test_cases],
        lambda t: f"{t.name}（{len(t.assert_rules)}条断言）",
    ))

    # 8. 自定义章节对比
    changes.extend(_diff_custom_sections(old_parsed.custom_sections, new_parsed.custom_sections))

    severity = _classify_severity(changes)

    return {
        "review_id": review.id,
        "total_changes": len(changes),
        "severity": severity,
        "changes": changes,
        "summary": _generate_summary(changes),
    }


def _diff_frontmatter(old_fm: dict, new_fm: dict) -> list[dict]:
    """对比 frontmatter 字段"""
    changes = []
    all_keys = set(old_fm.keys()) | set(new_fm.keys())
    for key in sorted(all_keys):
        old_val = old_fm.get(key)
        new_val = new_fm.get(key)
        if old_val != new_val:
            if old_val is None:
                changes.append({
                    "category": "frontmatter", "type": "added", "field": key,
                    "description": f"新增字段 {key} = {new_val}",
                    "new_value": str(new_val),
                })
            elif new_val is None:
                changes.append({
                    "category": "frontmatter", "type": "removed", "field": key,
                    "description": f"删除字段 {key}（原值 {old_val}）",
                    "old_value": str(old_val),
                })
            else:
                changes.append({
                    "category": "frontmatter", "type": "modified", "field": key,
                    "description": f"修改 {key}: {old_val} → {new_val}",
                    "old_value": str(old_val), "new_value": str(new_val),
                })
    return changes


def _diff_steps(old_steps: list, new_steps: list) -> list[dict]:
    """对比决策步骤——重点关注分支条件和阈值变更"""
    changes = []
    old_map = {s.id: s for s in old_steps if s.id}
    new_map = {s.id: s for s in new_steps if s.id}

    for sid in new_map:
        if sid not in old_map:
            changes.append({
                "category": "step", "type": "added", "step_id": sid,
                "description": f"新增步骤 {sid}: {new_map[sid].name}",
            })

    for sid in old_map:
        if sid not in new_map:
            changes.append({
                "category": "step", "type": "removed", "step_id": sid,
                "description": f"删除步骤 {sid}: {old_map[sid].name}",
            })

    for sid in old_map:
        if sid not in new_map:
            continue
        old_step, new_step = old_map[sid], new_map[sid]

        if old_step.name != new_step.name:
            changes.append({
                "category": "step", "type": "modified", "step_id": sid,
                "description": f"步骤 {sid} 名称变更: {old_step.name} → {new_step.name}",
                "old_value": old_step.name, "new_value": new_step.name,
            })

        changes.extend(_diff_branches(sid, old_step.branches, new_step.branches))

    return changes


def _diff_branches(step_id: str, old_branches: list, new_branches: list) -> list[dict]:
    """对比同一步骤下的分支变更"""
    changes = []
    max_len = max(len(old_branches), len(new_branches))

    for i in range(max_len):
        old_b = old_branches[i] if i < len(old_branches) else None
        new_b = new_branches[i] if i < len(new_branches) else None

        if old_b is None:
            changes.append({
                "category": "branch", "type": "added", "step_id": step_id,
                "branch_index": i,
                "description": f"步骤 {step_id} 新增分支: {new_b.condition} → {new_b.conclusion}",
            })
        elif new_b is None:
            changes.append({
                "category": "branch", "type": "removed", "step_id": step_id,
                "branch_index": i,
                "description": f"步骤 {step_id} 删除分支: {old_b.condition} → {old_b.conclusion}",
            })
        else:
            if old_b.condition != new_b.condition:
                changes.append({
                    "category": "branch", "type": "condition_changed",
                    "step_id": step_id, "branch_index": i,
                    "description": f"步骤 {step_id} 分支{i+1} 条件变更: {old_b.condition} → {new_b.condition}",
                    "old_value": old_b.condition, "new_value": new_b.condition,
                })
            if old_b.conclusion != new_b.conclusion:
                changes.append({
                    "category": "branch", "type": "conclusion_changed",
                    "step_id": step_id, "branch_index": i,
                    "description": f"步骤 {step_id} 分支{i+1} 结论变更: {old_b.conclusion} → {new_b.conclusion}",
                    "old_value": old_b.conclusion, "new_value": new_b.conclusion,
                })
            if old_b.action != new_b.action:
                changes.append({
                    "category": "branch", "type": "action_changed",
                    "step_id": step_id, "branch_index": i,
                    "description": f"步骤 {step_id} 分支{i+1} 动作变更: {old_b.action} → {new_b.action}",
                    "old_value": old_b.action, "new_value": new_b.action,
                })

    return changes


def _diff_list(category: str, old_items: list[tuple], new_items: list[tuple],
               label_fn) -> list[dict]:
    """通用列表对比（反例/输出/数据输入）：检测新增、删除和修改"""
    changes = []
    old_map = {k: item for k, item in old_items}
    new_map = {k: item for k, item in new_items}

    for k, item in new_items:
        if k not in old_map:
            changes.append({
                "category": category, "type": "added",
                "description": f"新增{category}: {label_fn(item)}",
            })
        else:
            # 同 key 存在，检查内容是否变化
            old_label = label_fn(old_map[k])
            new_label = label_fn(item)
            if old_label != new_label:
                changes.append({
                    "category": category, "type": "modified",
                    "description": f"修改{category}: {old_label} → {new_label}",
                    "old_value": old_label, "new_value": new_label,
                })
    for k, item in old_items:
        if k not in new_map:
            changes.append({
                "category": category, "type": "removed",
                "description": f"删除{category}: {label_fn(item)}",
            })
    return changes


def _diff_custom_sections(old_sections: dict, new_sections: dict) -> list[dict]:
    """对比自定义章节变更"""
    changes = []
    all_keys = set(old_sections.keys()) | set(new_sections.keys())
    for key in sorted(all_keys):
        old_val = old_sections.get(key, "")
        new_val = new_sections.get(key, "")
        if key not in old_sections:
            changes.append({
                "category": "custom_section", "type": "added",
                "description": f"新增自定义章节「{key}」",
                "new_value": str(new_val)[:200],
            })
        elif key not in new_sections:
            changes.append({
                "category": "custom_section", "type": "removed",
                "description": f"删除自定义章节「{key}」",
                "old_value": str(old_val)[:200],
            })
        elif str(old_val).strip() != str(new_val).strip():
            changes.append({
                "category": "custom_section", "type": "modified",
                "description": f"修改自定义章节「{key}」",
                "old_value": str(old_val)[:200],
                "new_value": str(new_val)[:200],
            })
    return changes


def _classify_severity(changes: list) -> str:
    """根据变更内容判断严重程度"""
    if any(c["category"] == "branch" and "condition_changed" in c.get("type", "") for c in changes):
        return "high"
    if any(c["category"] == "step" for c in changes):
        return "medium"
    if changes:
        return "low"
    return "none"


def _generate_summary(changes: list) -> str:
    """生成变更摘要文本"""
    if not changes:
        return "无结构化变更"
    parts = []
    step_changes = [c for c in changes if c["category"] in ("step", "branch")]
    fm_changes = [c for c in changes if c["category"] == "frontmatter"]
    test_changes = [c for c in changes if c["category"] == "test_case"]
    custom_changes = [c for c in changes if c["category"] == "custom_section"]
    other_changes = [c for c in changes if c["category"] not in ("step", "branch", "frontmatter", "test_case", "custom_section")]
    if step_changes:
        parts.append(f"{len(step_changes)}处决策逻辑变更")
    if fm_changes:
        parts.append(f"{len(fm_changes)}处配置变更")
    if test_changes:
        parts.append(f"{len(test_changes)}处测试用例变更")
    if custom_changes:
        parts.append(f"{len(custom_changes)}处自定义章节变更")
    if other_changes:
        parts.append(f"{len(other_changes)}处其他变更")
    return "，".join(parts)
