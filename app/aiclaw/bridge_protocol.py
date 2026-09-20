"""AIClaw bridge 帧 schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AuthInitFrame(BaseModel):
    type: Literal["auth_init"]
    instance_id: str
    pubkey: str
    enrollment_token: str
    fingerprint: str | None = None
    platform: str | None = None


class AuthResponseFrame(BaseModel):
    type: Literal["auth_response"]
    instance_id: str
    signature: str


class PongFrame(BaseModel):
    type: Literal["pong"]


class ForwardResponseFrame(BaseModel):
    type: Literal["forward_response"]
    request_id: str
    epoch: int
    ok: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    # Older/local gateways can return a plain-text transport error (for example
    # ``local gateway offline``).  Treat it as a valid error payload so one
    # failed forwarded call does not tear down the whole Bridge WebSocket.
    error: dict[str, Any] | str | None = None


class ForwardEventFrame(BaseModel):
    type: Literal["forward_event"]
    epoch: int
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class BridgeOpResponseFrame(BaseModel):
    """bridge 在本地执行某个 op 后的响应（不是转发到 AIClaw）。"""
    type: Literal["bridge_op_response"]
    request_id: str
    epoch: int
    ok: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | str | None = None


class BridgeOpResponseChunkFrame(BaseModel):
    """bridge 分片返回大结果，服务端聚合后再交给 pending future。"""
    type: Literal["bridge_op_response_chunk"]
    request_id: str
    epoch: int
    encoding: Literal["json", "gzip+base64"] = "json"
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    data: str = ""


class BridgeCapabilitiesFrame(BaseModel):
    """bridge auth 成功后第一时间上报本机环境与可写目录。

    SkillForge 用这个决定 sync skill 时往哪写、是否能写。
    """
    type: Literal["bridge_capabilities"]
    gateway_kind: str = "unknown"      # aiclaw / openclaw / unknown
    gateway_version: str | None = None
    platform: str | None = None         # linux / darwin / win32
    skills_dirs: list[str] = Field(default_factory=list)  # 可写的 skills 根目录候选
    skills_dir_default: str | None = None                  # 首选目录
    home: str | None = None
    bridge_version: str | None = None
    runtimes: list[str] = Field(default_factory=list)
    ops: list[str] = Field(default_factory=list)
    # 硬件资源信息（每次重连刷新）
    disk: dict[str, Any] = Field(default_factory=dict)    # {mount_point: {total_gb, used_gb, avail_gb, use_pct}}
    memory: dict[str, Any] = Field(default_factory=dict)   # {total_gb, avail_gb, used_pct}
    gpu: list[dict[str, Any]] = Field(default_factory=list)  # [{name, vram_total_mb, vram_used_mb, vram_free_mb, gpu_util_pct}]
    training: dict[str, Any] = Field(default_factory=dict)  # {gateway, supported_tasks, worker_count, gpu_count}
    workload_roles: list[str] = Field(default_factory=list)  # video_generation 等平台调度角色
    media: dict[str, Any] = Field(default_factory=dict)  # 受控媒体能力、模型和队列摘要
    resident_models: list[dict[str, Any]] = Field(default_factory=list)  # [{model, deployment_id, status, loaded}]


class ShutdownFrame(BaseModel):
    type: Literal["shutdown"]
