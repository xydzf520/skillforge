"""工作台 patch 预览生成 — 模块级 diff 和 hunk 计算。"""

from __future__ import annotations
import json


def build_diff_preview(target_module: str, patch: dict, before: dict | None = None) -> dict:
    """
    对比 before 和 patch 生成 diff 预览。

    返回:
    {
        "target_module": "params",
        "before": "roi_threshold: 1.2",
        "after": "roi_threshold: 1.5",
        "hunks": [
            {"index": 0, "type": "modify", "path": "params[0].value", "old": "1.2", "new": "1.5"}
        ],
        "summary": "修改了 1 个参数"
    }
    """
    if not before:
        return {
            "target_module": target_module,
            "before": "",
            "after": _format_value(patch),
            "hunks": [],
            "changed_keys": sorted(list(patch.keys())),
            "summary": f"本次将修改模块 {target_module}",
        }

    before_data = before.get(target_module, before)
    after_data = patch.get(target_module, patch)

    hunks = _compute_hunks(target_module, before_data, after_data)

    return {
        "target_module": target_module,
        "before": _format_value(before_data),
        "after": _format_value(after_data),
        "hunks": hunks,
        "changed_keys": sorted(list(patch.keys())),
        "summary": _summarize_hunks(target_module, hunks),
    }


def _compute_hunks(module: str, before, after) -> list[dict]:
    """计算两个模块值之间的 hunk 列表。"""
    hunks = []

    # 字符串类型（goal 等）
    if isinstance(before, str) and isinstance(after, str):
        if before != after:
            hunks.append({
                "index": 0, "type": "modify",
                "path": module, "old": before, "new": after,
            })
        return hunks

    # 列表类型（params, rules, output_table, test_cases）
    if isinstance(before, list) and isinstance(after, list):
        return _diff_lists(module, before, after)

    # 字典类型（workflow, meta）
    if isinstance(before, dict) and isinstance(after, dict):
        return _diff_dicts(module, before, after)

    # 类型不同
    if before != after:
        hunks.append({
            "index": 0, "type": "modify",
            "path": module, "old": _format_value(before), "new": _format_value(after),
        })
    return hunks


def _diff_lists(module: str, before: list, after: list) -> list[dict]:
    """对比两个列表，按索引生成 hunks。"""
    hunks = []
    idx = 0
    max_len = max(len(before), len(after))

    for i in range(max_len):
        b = before[i] if i < len(before) else None
        a = after[i] if i < len(after) else None

        if b is None and a is not None:
            # 新增
            hunks.append({
                "index": idx, "type": "add",
                "path": f"{module}[{i}]",
                "old": None, "new": _format_value(a),
            })
            idx += 1
        elif a is None and b is not None:
            # 删除
            hunks.append({
                "index": idx, "type": "remove",
                "path": f"{module}[{i}]",
                "old": _format_value(b), "new": None,
            })
            idx += 1
        elif b != a:
            # 修改 — 查找具体哪些字段变了
            if isinstance(b, dict) and isinstance(a, dict):
                for key in set(list(b.keys()) + list(a.keys())):
                    bv = b.get(key)
                    av = a.get(key)
                    if bv != av:
                        hunks.append({
                            "index": idx, "type": "modify",
                            "path": f"{module}[{i}].{key}",
                            "old": _format_value(bv), "new": _format_value(av),
                        })
                        idx += 1
            else:
                hunks.append({
                    "index": idx, "type": "modify",
                    "path": f"{module}[{i}]",
                    "old": _format_value(b), "new": _format_value(a),
                })
                idx += 1

    return hunks


def _diff_dicts(module: str, before: dict, after: dict) -> list[dict]:
    """对比两个字典，按 key 生成 hunks。"""
    hunks = []
    idx = 0
    all_keys = sorted(set(list(before.keys()) + list(after.keys())))

    for key in all_keys:
        bv = before.get(key)
        av = after.get(key)
        if bv is None and av is not None:
            hunks.append({"index": idx, "type": "add", "path": f"{module}.{key}", "old": None, "new": _format_value(av)})
            idx += 1
        elif av is None and bv is not None:
            hunks.append({"index": idx, "type": "remove", "path": f"{module}.{key}", "old": _format_value(bv), "new": None})
            idx += 1
        elif bv != av:
            hunks.append({"index": idx, "type": "modify", "path": f"{module}.{key}", "old": _format_value(bv), "new": _format_value(av)})
            idx += 1

    return hunks


def _format_value(val) -> str:
    """将值格式化为可读字符串。"""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False, indent=None)
    except (TypeError, ValueError):
        return str(val)


def _summarize_hunks(module: str, hunks: list[dict]) -> str:
    """生成 hunk 摘要。"""
    if not hunks:
        return f"模块 {module} 无变更"
    adds = sum(1 for h in hunks if h["type"] == "add")
    mods = sum(1 for h in hunks if h["type"] == "modify")
    dels = sum(1 for h in hunks if h["type"] == "remove")
    parts = []
    if adds: parts.append(f"新增 {adds} 项")
    if mods: parts.append(f"修改 {mods} 项")
    if dels: parts.append(f"删除 {dels} 项")
    return f"模块 {module}：{'，'.join(parts)}"
