"""Shared helpers for split skill AI services."""

from __future__ import annotations

import json


def _truncate_dict(d: dict | None, max_len: int = 200) -> dict | None:
    """截断 dict 的 JSON 表示，防止 Prompt 过长。保留前 N 个 key 而非硬截断字符串。"""
    if not d:
        return d
    s = json.dumps(d, ensure_ascii=False)
    if len(s) <= max_len:
        return d
    if isinstance(d, dict):
        result = {}
        for k, v in d.items():
            result[k] = v
            if len(json.dumps(result, ensure_ascii=False)) > max_len:
                result.pop(k)
                result["_truncated"] = True
                break
        return result
    return {"_truncated": s[:max_len]}
