"""
stream-json 协议编解码。

aiclawcode 的 stdin/stdout 都是 line-delimited JSON：
- 每行一个 JSON 对象
- UTF-8 编码
- 单条消息内不能换行（JSON 序列化时强制 ensure_ascii=False, indent=None）

本模块只负责 bytes ↔ dict 转换，不做语义解释。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4


def encode_message(msg: dict[str, Any]) -> bytes:
    """把一个消息 dict 编码成可写入子进程 stdin 的 bytes（含末尾换行）。"""
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_line(line: bytes | str) -> dict[str, Any] | None:
    """
    解码一行子进程 stdout。
    返回 None 表示这一行不是合法 JSON（应跳过，可能是日志/banner）。
    """
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8", errors="replace")
        except Exception:
            return None
    line = line.strip()
    if not line or not line.startswith(("{", "[")):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


# ─────────────────────────────────────────────────────────
# 消息构造（client → aiclawcode 进程）
# ─────────────────────────────────────────────────────────

def build_user_message(content: str, *, parent_tool_use_id: str | None = None) -> dict[str, Any]:
    """构造用户消息（stream-json input 格式）。"""
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": parent_tool_use_id,
        "session_id": "",
    }


def build_permission_response(
    request_id: str,
    behavior: str,  # "allow" | "deny"
    *,
    updated_input: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """
    构造 control_response，回复 aiclawcode 的权限请求。

    behavior == "allow":
        必须带 updated_input（即使没改也是工具原始 input）
    behavior == "deny":
        建议带 message 说明拒绝原因（aiclawcode 会把它作为 tool_result 喂回模型）
    """
    if behavior not in ("allow", "deny"):
        raise ValueError(f"invalid behavior: {behavior}")

    response_body: dict[str, Any] = {"behavior": behavior}
    if behavior == "allow":
        response_body["updatedInput"] = updated_input or {}
    else:
        response_body["message"] = message or "User denied"

    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": response_body,
        },
    }


def build_interrupt_request() -> dict[str, Any]:
    """构造中断请求。"""
    return {
        "type": "control_request",
        "request_id": str(uuid4()),
        "request": {"subtype": "interrupt"},
    }
