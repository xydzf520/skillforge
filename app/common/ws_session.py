"""WebSocket 会话限流与保活辅助。"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import WebSocket
from loguru import logger

from app.config import settings

_LUA_ACQUIRE = """
local key = KEYS[1]
local member = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

redis.call('SADD', key, member)
local count = redis.call('SCARD', key)
if count > limit then
    redis.call('SREM', key, member)
    return {0, count - 1}
end
redis.call('EXPIRE', key, ttl)
return {1, count}
"""

_LUA_RELEASE = """
local key = KEYS[1]
local member = ARGV[1]
local ttl = tonumber(ARGV[2])

redis.call('SREM', key, member)
local count = redis.call('SCARD', key)
if count > 0 then
    redis.call('EXPIRE', key, ttl)
else
    redis.call('DEL', key)
end
return count
"""

_LUA_TOUCH = """
local key = KEYS[1]
local member = ARGV[1]
local ttl = tonumber(ARGV[2])

if redis.call('SISMEMBER', key, member) == 1 then
    redis.call('EXPIRE', key, ttl)
    return 1
end
return 0
"""

_local_lock = asyncio.Lock()
_local_sessions: dict[tuple[str, str], set[str]] = {}


@dataclass(slots=True)
class WebSocketSessionLease:
    limiter: "WebSocketSessionLimiter"
    scope: str
    identity: str
    member: str
    count: int
    acquired: bool
    released: bool = False

    async def release(self) -> None:
        if not self.acquired or self.released:
            return
        self.released = True
        await self.limiter.release(self)

    async def touch(self) -> None:
        if not self.acquired or self.released:
            return
        await self.limiter.touch(self)


class WebSocketSessionLimiter:
    """按 scope+identity 维度限制活跃 WS 连接数。

    Redis 已初始化时走分布式 set；否则回退到当前进程内存计数。
    """

    def __init__(self, *, max_connections: int, ttl_seconds: int):
        self.max_connections = max(1, int(max_connections))
        self.ttl_seconds = max(30, int(ttl_seconds))

    def _redis_pool(self):
        from app.common.cache import _pool

        return _pool

    def _redis_key(self, scope: str, identity: str) -> str:
        return f"sf:ws-session:{scope}:{identity}"

    async def acquire(self, scope: str, identity: str) -> WebSocketSessionLease:
        member = uuid.uuid4().hex
        pool = self._redis_pool()
        if pool is not None:
            try:
                result = await pool.eval(
                    _LUA_ACQUIRE,
                    1,
                    self._redis_key(scope, identity),
                    member,
                    str(self.max_connections),
                    str(self.ttl_seconds),
                )
                acquired = int(result[0]) == 1
                count = int(result[1])
                return WebSocketSessionLease(
                    limiter=self,
                    scope=scope,
                    identity=identity,
                    member=member,
                    count=count,
                    acquired=acquired,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws session limiter Redis acquire 失败，降级本地计数 scope={} identity={} err={}", scope, identity, exc)

        async with _local_lock:
            bucket = _local_sessions.setdefault((scope, identity), set())
            if len(bucket) >= self.max_connections:
                return WebSocketSessionLease(
                    limiter=self,
                    scope=scope,
                    identity=identity,
                    member=member,
                    count=len(bucket),
                    acquired=False,
                )
            bucket.add(member)
            return WebSocketSessionLease(
                limiter=self,
                scope=scope,
                identity=identity,
                member=member,
                count=len(bucket),
                acquired=True,
            )

    async def release(self, lease: WebSocketSessionLease) -> None:
        pool = self._redis_pool()
        if pool is not None:
            try:
                await pool.eval(
                    _LUA_RELEASE,
                    1,
                    self._redis_key(lease.scope, lease.identity),
                    lease.member,
                    str(self.ttl_seconds),
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws session limiter Redis release 失败，降级本地计数 scope={} identity={} err={}", lease.scope, lease.identity, exc)

        async with _local_lock:
            bucket = _local_sessions.get((lease.scope, lease.identity))
            if not bucket:
                return
            bucket.discard(lease.member)
            if not bucket:
                _local_sessions.pop((lease.scope, lease.identity), None)

    async def touch(self, lease: WebSocketSessionLease) -> None:
        pool = self._redis_pool()
        if pool is not None:
            try:
                await pool.eval(
                    _LUA_TOUCH,
                    1,
                    self._redis_key(lease.scope, lease.identity),
                    lease.member,
                    str(self.ttl_seconds),
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws session limiter Redis touch 失败，降级本地计数 scope={} identity={} err={}", lease.scope, lease.identity, exc)


def spawn_json_heartbeat(
    websocket: WebSocket,
    *,
    lease: WebSocketSessionLease | None = None,
    interval_seconds: float | None = None,
    payload: dict[str, Any] | None = None,
) -> asyncio.Task[Any]:
    interval = float(interval_seconds or settings.WS_HEARTBEAT_INTERVAL_SECONDS or 25)
    interval = max(0.05, interval)
    heartbeat_payload = payload or {"type": "heartbeat"}

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(interval)
            if lease is not None:
                await lease.touch()
            await websocket.send_json(heartbeat_payload)

    return asyncio.create_task(_heartbeat())


def spawn_ping_pong_listener(
    websocket: WebSocket,
    *,
    on_message: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
) -> asyncio.Task[Any]:
    async def _listen() -> None:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            msg_type = str(msg.get("type") or "").lower()
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if msg_type in {"pong", "heartbeat"}:
                continue
            if on_message is None:
                continue
            result = on_message(msg)
            if inspect.isawaitable(result):
                await result

    return asyncio.create_task(_listen())


user_ws_session_limiter = WebSocketSessionLimiter(
    max_connections=settings.WS_MAX_CONNECTIONS_PER_USER,
    ttl_seconds=max(60, settings.WS_HEARTBEAT_INTERVAL_SECONDS * 4),
)
