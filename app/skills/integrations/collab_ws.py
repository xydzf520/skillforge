"""编辑感知协同 WebSocket 服务。

简化版实时协同：不做 CRDT/OT，只做编辑状态通知 + 冲突检测。
- 用户开始编辑时广播 "editing" 事件
- 用户保存时广播 "saved" 事件
- 保存前比较 base_commit vs HEAD，检测冲突
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket
from loguru import logger

from app.skills.core.git_service import git_service


# 每个 skill_id 维护一组活跃的 WebSocket 连接
_active_connections: dict[str, dict[str, WebSocket]] = defaultdict(dict)
_lock = asyncio.Lock()


async def connect(skill_id: str, user_id: str, websocket: WebSocket) -> None:
    """注册编辑者连接并通知其他人"""
    async with _lock:
        _active_connections[skill_id][user_id] = websocket
    await _broadcast(skill_id, {
        "type": "user_joined",
        "user_id": user_id,
        "active_users": list(_active_connections[skill_id].keys()),
    }, exclude=user_id)


async def disconnect(skill_id: str, user_id: str) -> None:
    """注销编辑者连接并通知其他人。同时自动释放编辑锁（防止浏览器崩溃后锁不释放）。"""
    async with _lock:
        _active_connections[skill_id].pop(user_id, None)
        if not _active_connections[skill_id]:
            del _active_connections[skill_id]

    # 自动释放编辑锁
    try:
        from app.database import async_session_factory
        from app.skills import service as skill_service
        async with async_session_factory() as db:
            await skill_service.release_lock(db, skill_id, user_id)
            await db.commit()
    except Exception:
        pass  # 锁释放失败不影响断连流程

    await _broadcast(skill_id, {
        "type": "user_left",
        "user_id": user_id,
        "active_users": list(_active_connections.get(skill_id, {}).keys()),
    })


async def notify_editing(skill_id: str, user_id: str, module: str = "") -> None:
    """通知其他用户：某人正在编辑"""
    await _broadcast(skill_id, {
        "type": "editing",
        "user_id": user_id,
        "module": module,
    }, exclude=user_id)


async def notify_saved(skill_id: str, user_id: str, commit_hash: str = "") -> None:
    """通知其他用户：有人保存了变更"""
    await _broadcast(skill_id, {
        "type": "saved",
        "user_id": user_id,
        "commit_hash": commit_hash,
    }, exclude=user_id)


def check_conflict(skill_id: str, base_commit: str) -> dict:
    """保存前冲突检测：比较 base_commit 是否仍是 HEAD。

    Args:
        skill_id: Skill ID
        base_commit: 用户打开编辑器时的 commit hash

    Returns:
        {"conflict": bool, "head_commit": str, "base_commit": str}
    """
    if not base_commit:
        return {"conflict": False, "head_commit": "", "base_commit": ""}

    logs = git_service.log(skill_id=skill_id, max_count=1)
    head_commit = logs[0]["hash_full"] if logs else ""

    return {
        "conflict": head_commit != base_commit and bool(head_commit),
        "head_commit": head_commit,
        "base_commit": base_commit,
    }


def get_active_editors(skill_id: str) -> list[str]:
    """获取当前正在编辑某 Skill 的用户列表"""
    return list(_active_connections.get(skill_id, {}).keys())


async def _broadcast(skill_id: str, message: dict, exclude: str = "") -> None:
    """向指定 skill 的所有连接广播消息（排除发送者）"""
    conns = _active_connections.get(skill_id, {})
    dead = []
    for uid, ws in conns.items():
        if uid == exclude:
            continue
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(uid)
    # 清理断开的连接
    for uid in dead:
        async with _lock:
            _active_connections.get(skill_id, {}).pop(uid, None)
