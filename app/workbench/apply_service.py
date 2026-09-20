"""Workbench patch generation and apply helpers."""

from __future__ import annotations

import json
import re
from copy import deepcopy

import yaml

from app.skills.core.parser import Branch, DecisionStep, OutputItem, SkillStructured, TestCase, skill_parser


def _extract_number(message: str) -> float | None:
    match = re.search(r"(-?\d+(?:\.\d+)?)", message or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def build_patch_from_message(target_module: str, message: str, references: list[dict] | None = None, skill_id: str = "") -> dict:
    text = (message or "").strip()
    references = references or []
    if target_module == "goal":
        return {"goal": text}
    if target_module == "params":
        threshold = _extract_number(text)
        return {
            "params": [
                {
                    "name": "roi_threshold",
                    "default_value": threshold if threshold is not None else 1.5,
                    "description": text or "根据对话更新参数",
                }
            ]
        }
    if target_module == "rules":
        return {
            "rules": [
                {
                    "id": "rule_dialog_1",
                    "name": "根据对话生成规则",
                    "description": text or "根据用户描述补全规则",
                    "branches": [
                        {
                            "condition": text or "满足业务目标",
                            "conclusion": "建议执行",
                            "action": "按新规则处理",
                            "next_step": None,
                        }
                    ],
                }
            ]
        }
    if target_module == "output_table":
        return {
            "output_table": [
                {"name": "结论", "format": "text", "recipient": "运营", "approval_level": "L1"},
                {"name": "建议动作", "format": "text", "recipient": "运营", "approval_level": "L1"},
                {"name": "说明", "format": "markdown", "recipient": "运营", "approval_level": "L1"},
            ],
            "notes": text,
        }
    if target_module == "test_cases":
        return {
            "test_cases": [
                {
                    "name": "对话生成样例",
                    "input_data": {"prompt": text},
                    "expected_output": {"summary": "应能给出结构化结果"},
                    "assert_rules": ["summary exists"],
                }
            ]
        }
    if target_module == "workflow":
        nodes = [{
            "id": f"{skill_id or 'current'}_node",
            "label": skill_id or "当前 Skill",
            "detail": "当前 Skill 节点",
            "skill_id": skill_id or "current-skill",
        }]
        edges = []
        bindings = []

        for idx, ref in enumerate(references, start=1):
            if not isinstance(ref, dict):
                continue
            ref_id = ref.get("source_id") or ref.get("id") or f"ref_{idx}"
            ref_module = ref.get("source_module") or ""
            ref_type = ref.get("source_type") or "skill_module"
            node_id = f"ref_{idx}_{ref_id}"
            nodes.append({
                "id": node_id,
                "label": ref.get("title") or ref_id,
                "detail": f"{ref_type} / {ref_module}".strip(" /"),
                "skill_id": ref_id,
            })
            edges.append({
                "id": f"edge_{idx}",
                "source": node_id,
                "target": f"{skill_id or 'current'}_node",
                "label": ref.get("reference_mode") or "copy_structure",
            })
            if ref_module == "output_table":
                bindings.append({
                    "source": node_id,
                    "target": f"{skill_id or 'current'}_node",
                    "mapping": "output_table -> input",
                })

        if "审批" in text or "approval" in text.lower():
            approval_id = "approval_node"
            nodes.append({
                "id": approval_id,
                "label": "人工审批",
                "detail": "人工确认节点",
                "skill_id": "",
            })
            edges.append({
                "id": f"edge_approval_{len(edges) + 1}",
                "source": f"{skill_id or 'current'}_node",
                "target": approval_id,
                "label": "need_approval",
            })

        return {
            "workflow": {
                "nodes": nodes,
                "edges": edges,
                "bindings": bindings,
                "summary": text or "工作流草稿",
                "status": "draft",
            }
        }
    return {target_module: {"message": text, "status": "draft"}}


def _structure_to_parser_doc(skill_id: str, document: dict) -> SkillStructured:
    meta = deepcopy(document.get("meta", {}))
    frontmatter = {
        "name": meta.get("name") or skill_id,
        "description": document.get("goal") or meta.get("description") or "",
        "department": meta.get("department") or "",
        "trigger_type": meta.get("trigger_type") or "manual",
        "risk_level": meta.get("risk_level") or "R2",
    }
    return SkillStructured(
        frontmatter=frontmatter,
        purpose=document.get("goal", ""),
        steps=[
            DecisionStep(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                description=item.get("description", ""),
                branches=[
                    Branch(
                        condition=branch.get("condition", ""),
                        conclusion=branch.get("conclusion", ""),
                        action=branch.get("action", ""),
                        next_step=branch.get("next_step"),
                    )
                    for branch in item.get("branches", [])
                ],
            )
            for item in document.get("rules", [])
        ],
        output_definition=[
            OutputItem(
                name=item.get("name", ""),
                format=item.get("format", ""),
                recipient=item.get("recipient", ""),
                approval_level=item.get("approval_level", ""),
            )
            for item in document.get("output_table", [])
        ],
        test_cases=[
            TestCase(
                name=item.get("name", ""),
                input_data=item.get("input_data", {}) or {},
                expected_output=item.get("expected_output", {}) or {},
                assert_rules=item.get("assert_rules", []) or [],
            )
            for item in document.get("test_cases", [])
        ],
        custom_sections=deepcopy(document.get("custom_sections", {})),
    )


def _params_to_policy_yaml(document: dict) -> str:
    payload = {}
    for item in document.get("params", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("key")
        if not name:
            continue
        payload[name] = item.get("default_value", item.get("value"))
    return yaml.dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False)


def apply_patch_to_skill_document(skill_id: str, document: dict, target_module: str, patch: dict) -> dict:
    updated = deepcopy(document)
    if "meta" not in updated:
        updated["meta"] = {}
    updated["meta"]["id"] = updated["meta"].get("id") or skill_id

    if target_module == "goal":
        updated["goal"] = patch.get("goal", updated.get("goal", ""))
    elif target_module == "params":
        updated["params"] = deepcopy(patch.get("params", updated.get("params", [])))
    elif target_module == "rules":
        updated["rules"] = deepcopy(patch.get("rules", updated.get("rules", [])))
    elif target_module == "output_table":
        updated["output_table"] = deepcopy(patch.get("output_table", updated.get("output_table", [])))
    elif target_module == "test_cases":
        updated["test_cases"] = deepcopy(patch.get("test_cases", updated.get("test_cases", [])))
    elif target_module == "workflow":
        updated["workflow"] = deepcopy(patch.get("workflow", updated.get("workflow", {})))

    parser_doc = _structure_to_parser_doc(skill_id, updated)
    updated["skill_md"] = skill_parser.render(parser_doc)
    updated["policy_pack_yaml"] = _params_to_policy_yaml(updated)
    return updated


def apply_partial_patch(skill_id: str, document: dict, target_module: str, hunks: list[dict], accepted_indices: list[int]) -> dict:
    """
    Hunk 级部分 Patch 应用 — 只接受指定索引的 hunks。

    每个 hunk 格式: {"index": 0, "type": "modify|add|remove", "path": "params[0].value", "old": "1.2", "new": "1.5"}
    path 格式: "module[i].field" 或 "module.field" 或 "module"

    对于 accepted_indices 中的 hunk，应用其变更；其余保持原值。
    """
    updated = deepcopy(document)
    if "meta" not in updated:
        updated["meta"] = {}
    updated["meta"]["id"] = updated["meta"].get("id") or skill_id

    accepted_set = set(accepted_indices)
    accepted_hunks = [h for h in hunks if h.get("index") in accepted_set]

    for hunk in accepted_hunks:
        _apply_single_hunk(updated, target_module, hunk)

    parser_doc = _structure_to_parser_doc(skill_id, updated)
    updated["skill_md"] = skill_parser.render(parser_doc)
    updated["policy_pack_yaml"] = _params_to_policy_yaml(updated)
    return updated


def _apply_single_hunk(document: dict, target_module: str, hunk: dict):
    """将单个 hunk 应用到 document 上。"""
    path = hunk.get("path", "")
    hunk_type = hunk.get("type", "modify")
    new_val = hunk.get("new")

    # 解析 path: "module[i].field" 或 "module.field"
    parts = re.findall(r'(\w+)|\[(\d+)\]', path)

    target = document
    for i, (name, idx) in enumerate(parts[:-1]):
        if name:
            target = target.get(name, target) if isinstance(target, dict) else target
        elif idx:
            index = int(idx)
            if isinstance(target, list) and index < len(target):
                target = target[index]

    # 应用到最后一级
    last_name, last_idx = parts[-1] if parts else ('', '')
    if last_name:
        if isinstance(target, dict):
            if hunk_type == "remove":
                target.pop(last_name, None)
            else:
                try:
                    target[last_name] = json.loads(new_val) if isinstance(new_val, str) else new_val
                except (json.JSONDecodeError, TypeError):
                    target[last_name] = new_val
    elif last_idx:
        index = int(last_idx)
        if isinstance(target, list):
            if hunk_type == "add":
                target.insert(index, new_val)
            elif hunk_type == "remove" and index < len(target):
                target.pop(index)
            elif hunk_type == "modify" and index < len(target):
                target[index] = new_val
