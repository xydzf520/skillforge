"""
Coding Agent 事件类型定义。

两套类型：
1. 原生 stream-json 事件（aiclawcode 进程的输入输出）
2. 统一对外事件 Event（SkillForge 后端 → 前端 WS 协议）

转换逻辑在 session_service.py 中实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


# ─────────────────────────────────────────────────────────
# 1. 原生 stream-json 事件类型（仅做枚举，解析时按需校验）
# ─────────────────────────────────────────────────────────

class RawEventType(str, Enum):
    """aiclawcode 进程 stdout 上能出现的顶层事件类型。"""
    SYSTEM = "system"           # subtype=init / post_turn_summary 等
    ASSISTANT = "assistant"     # 助手消息（含 text / tool_use 内容块）
    USER = "user"               # 工具结果包装（content 中含 tool_result）
    RESULT = "result"           # 一轮对话结束（subtype=success/error_*）
    CONTROL_REQUEST = "control_request"   # 服务端请求权限 / 中断 ack
    CONTROL_RESPONSE = "control_response" # 服务端对客户端 control 的回应
    KEEP_ALIVE = "keep_alive"
    STREAM_EVENT = "stream_event"         # SSE delta（开启 --include-partial-messages）


# ─────────────────────────────────────────────────────────
# 2. 统一对外 Event（Phase 3 转给前端 WS 用）
# ─────────────────────────────────────────────────────────

class EventType(str, Enum):
    """统一事件类型，前后端协议。"""
    SESSION_READY = "session_ready"           # 子进程 system/init 完成
    TEXT_DELTA = "text_delta"                 # 助手文本片段
    TOOL_CALL = "tool_call"                   # 助手发起工具调用
    TOOL_RESULT = "tool_result"               # 工具调用结果
    FILE_CHANGE = "file_change"               # Edit/Write 工具的文件变更通知（编辑器跟踪用）
    PERMISSION_REQUEST = "permission_request" # 等待用户审批的工具调用
    USAGE = "usage"                           # token 用量
    DONE = "done"                             # 一轮对话结束
    ERROR = "error"                           # 错误（含子进程崩溃）


@dataclass
class Event:
    """统一对外事件。to_dict() 直接序列化为前端 WS 帧。"""
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, **self.payload}


# ─────────────────────────────────────────────────────────
# 3. Permission 决策类型（permission_policy 用）
# ─────────────────────────────────────────────────────────

PermissionDecision = Literal["auto_allow", "notify_allow", "ask_user", "deny"]


@dataclass
class PermissionEvaluation:
    decision: PermissionDecision
    reason: str
    """简短说明。auto_allow 时也写一行原因便于审计。"""


# ─────────────────────────────────────────────────────────
# 4. 子进程错误码
# ─────────────────────────────────────────────────────────

class CodingAgentErrorCode(str, Enum):
    PROCESS_SPAWN_FAILED = "CODING_AGENT_SPAWN_FAILED"
    PROCESS_INIT_TIMEOUT = "CODING_AGENT_INIT_TIMEOUT"
    PROCESS_DIED = "CODING_AGENT_PROCESS_DIED"
    PROTOCOL_DECODE_ERROR = "CODING_AGENT_PROTOCOL_DECODE"
    POOL_EXHAUSTED = "CODING_AGENT_POOL_EXHAUSTED"
    SKILL_DIR_NOT_FOUND = "CODING_AGENT_SKILL_DIR_NOT_FOUND"
    PERMISSION_VIOLATION = "CODING_AGENT_PERMISSION_VIOLATION"
    LOCAL_PATH_UNAVAILABLE = "CODING_AGENT_LOCAL_PATH_UNAVAILABLE"
    RESUME_NO_SESSION = "RESUME_NO_SESSION"  # resume 时 session 已被 idle gc / 不存在
    TURN_INACTIVITY_TIMEOUT = "CODING_AGENT_TURN_INACTIVITY_TIMEOUT"  # 单轮 12min 无新事件, 强制终止
