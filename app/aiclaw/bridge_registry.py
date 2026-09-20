"""AIClaw Bridge 连接注册中心。"""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from datetime import datetime
import gzip
import json
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from loguru import logger

from app.common.exceptions import AppError
from app.config import settings

from app.common.time_utils import now_bjt
from .metrics import (
    bridge_active_runs,
    bridge_chat_queue_depth,
    bridge_epoch_swap_total,
    bridge_online,
    bridge_reject_total,
    chat_queue_overflow_total,
)

_BRIDGE_WS_CHUNK_THRESHOLD_BYTES = 256 * 1024
_BRIDGE_WS_CHUNK_TARGET_BYTES = 192 * 1024


def _bridge_transport_blob(value: dict[str, Any]) -> tuple[str, str]:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) >= _BRIDGE_WS_CHUNK_THRESHOLD_BYTES and "content_base64" not in value:
        compressed = gzip.compress(raw, compresslevel=6)
        if len(compressed) < len(raw):
            return "gzip+base64", base64.b64encode(compressed).decode("ascii")
    return "json", raw.decode("utf-8")


def _bridge_transport_chunks(value: dict[str, Any]) -> tuple[str, list[str]]:
    encoding, blob = _bridge_transport_blob(value)
    if len(blob.encode("utf-8")) <= _BRIDGE_WS_CHUNK_THRESHOLD_BYTES:
        return encoding, [blob]
    return encoding, [
        blob[index:index + _BRIDGE_WS_CHUNK_TARGET_BYTES]
        for index in range(0, len(blob), _BRIDGE_WS_CHUNK_TARGET_BYTES)
    ]


def _bridge_transport_decode(parts: dict[int, str], chunk_count: int, encoding: str) -> dict[str, Any]:
    if chunk_count <= 0:
        raise ValueError("chunk_count must be positive")
    missing = [idx for idx in range(chunk_count) if idx not in parts]
    if missing:
        raise ValueError(f"missing chunk indexes: {missing[:5]}")
    blob = "".join(parts[idx] for idx in range(chunk_count))
    if encoding == "gzip+base64":
        raw = gzip.decompress(base64.b64decode(blob))
    elif encoding == "json":
        raw = blob.encode("utf-8")
    else:
        raise ValueError(f"unsupported encoding: {encoding}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("decoded payload must be object")
    return value


class BridgeConnection:
    """单个 AIClaw bridge 连接。"""

    def __init__(self, instance_id: str, ws: WebSocket, epoch: int):
        self.instance_id = instance_id
        self.ws = ws
        self.epoch = epoch
        self.connected_at = now_bjt()
        self.last_ping = self.connected_at
        self.superseded = False
        self._pending: dict[str, tuple[int, asyncio.Future, float]] = {}
        self._bridge_op_response_chunks: dict[str, dict[str, Any]] = {}
        self._chat_queues: dict[str, asyncio.Queue] = {}
        self._active_runs: set[str] = set()
        # AIClaw 实际 runId → SkillForge sf_run_id 的映射，
        # 因为 AIClaw 可能返回与 idempotencyKey 不同的 runId
        self._run_id_aliases: dict[str, str] = {}
        self._cleanup_lock = asyncio.Lock()
        self._closed = False

    async def call(self, method: str, params: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        if len(self._pending) >= settings.BRIDGE_PENDING_MAX_PER_CONN:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="pending_full").inc()
            raise AppError("BRIDGE_BUSY", 503)
        request_id = f"req-{uuid4().hex[:12]}"
        fut = asyncio.get_running_loop().create_future()
        ttl = max(settings.BRIDGE_PENDING_TTL_SECONDS, int(timeout or 0) + 5)
        expires_at = asyncio.get_running_loop().time() + ttl
        self._pending[request_id] = (self.epoch, fut, expires_at)
        await self.ws.send_json(
            {
                "type": "forward_request",
                "request_id": request_id,
                "epoch": self.epoch,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(fut, timeout=timeout or settings.BRIDGE_REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise AppError("REQUEST_TIMEOUT", 504) from exc
        finally:
            self._pending.pop(request_id, None)

    async def bridge_op(self, op: str, payload: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        """让 bridge 在本地执行一个操作（不转发到 AIClaw）。

        bridge 端会按 op 名分支处理（install_skill / remove_skill / list_local_skills 等）。
        与 call() 共用同一个 _pending future 表，因为 bridge_op_response 的 request_id 唯一。
        """
        if len(self._pending) >= settings.BRIDGE_PENDING_MAX_PER_CONN:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="pending_full").inc()
            raise AppError("BRIDGE_BUSY", 503)
        request_id = f"op-{uuid4().hex[:12]}"
        fut = asyncio.get_running_loop().create_future()
        ttl = max(settings.BRIDGE_PENDING_TTL_SECONDS, int(timeout or 0) + 5)
        expires_at = asyncio.get_running_loop().time() + ttl
        self._pending[request_id] = (self.epoch, fut, expires_at)
        encoding, chunks = _bridge_transport_chunks(payload)
        if len(chunks) == 1 and encoding == "json":
            await self.ws.send_json(
                {
                    "type": "bridge_op",
                    "request_id": request_id,
                    "epoch": self.epoch,
                    "op": op,
                    "payload": payload,
                }
            )
        else:
            chunk_count = len(chunks)
            for chunk_index, chunk in enumerate(chunks):
                await self.ws.send_json(
                    {
                        "type": "bridge_op_chunk",
                        "request_id": request_id,
                        "epoch": self.epoch,
                        "op": op,
                        "encoding": encoding,
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "data": chunk,
                    }
                )
        try:
            return await asyncio.wait_for(fut, timeout=timeout or settings.BRIDGE_REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise AppError("REQUEST_TIMEOUT", 504) from exc
        finally:
            self._pending.pop(request_id, None)
            self._bridge_op_response_chunks.pop(request_id, None)

    async def handle_bridge_op_response(self, msg: dict[str, Any]) -> None:
        request_id = msg.get("request_id")
        entry = self._pending.get(request_id)
        if not entry:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="unknown_request").inc()
            return
        pending_epoch, fut, _expires_at = entry
        response_epoch = msg.get("epoch")
        if response_epoch != pending_epoch or response_epoch != self.epoch:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="epoch_mismatch").inc()
            if not fut.done():
                fut.set_exception(AppError("EPOCH_MISMATCH", 409))
            return
        if fut.done():
            return
        if msg.get("ok", True):
            fut.set_result(msg.get("result", {}))
        else:
            detail = msg.get("error", {})
            if isinstance(detail, dict):
                result = msg.get("result")
                if isinstance(result, dict) and result:
                    detail = {**detail, "result": result}
            fut.set_exception(AppError("BRIDGE_OP_ERROR", 502, {"detail": detail}))

    async def handle_bridge_op_response_chunk(self, msg: dict[str, Any]) -> None:
        request_id = msg.get("request_id")
        entry = self._pending.get(request_id)
        if not entry:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="unknown_request").inc()
            return
        pending_epoch, fut, expires_at = entry
        response_epoch = msg.get("epoch")
        if response_epoch != pending_epoch or response_epoch != self.epoch:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="epoch_mismatch").inc()
            if not fut.done():
                fut.set_exception(AppError("EPOCH_MISMATCH", 409))
            return
        if fut.done():
            return

        chunk_count = int(msg.get("chunk_count") or 0)
        chunk_index = int(msg.get("chunk_index") or 0)
        state = self._bridge_op_response_chunks.get(request_id)
        if state is None:
            state = {
                "encoding": msg.get("encoding") or "json",
                "chunk_count": chunk_count,
                "parts": {},
                "expires_at": expires_at,
            }
            self._bridge_op_response_chunks[request_id] = state
        if state["encoding"] != (msg.get("encoding") or "json") or state["chunk_count"] != chunk_count:
            self._bridge_op_response_chunks.pop(request_id, None)
            fut.set_exception(AppError("BRIDGE_OP_ERROR", 502, {"detail": {"message": "bridge response chunk metadata mismatch"}}))
            return

        state["parts"][chunk_index] = msg.get("data") or ""
        if len(state["parts"]) < chunk_count:
            return

        try:
            result = _bridge_transport_decode(
                state["parts"],
                chunk_count=state["chunk_count"],
                encoding=state["encoding"],
            )
        except Exception as exc:  # noqa: BLE001
            fut.set_exception(AppError("BRIDGE_OP_ERROR", 502, {"detail": {"message": f"bridge response chunk decode failed: {exc}"}}))
        else:
            fut.set_result(result)
        finally:
            self._bridge_op_response_chunks.pop(request_id, None)

    def register_run_alias(self, sf_run_id: str, aiclaw_run_id: str | None) -> None:
        """注册 AIClaw 真实 runId → SkillForge run_id 的映射。

        AIClaw 的 chat.send 响应中可能返回与 idempotencyKey 不同的 runId；
        即使相同也安全注册（同 key 同 value）。
        """
        if not aiclaw_run_id or aiclaw_run_id == sf_run_id:
            return
        if sf_run_id not in self._active_runs:
            return
        self._run_id_aliases[aiclaw_run_id] = sf_run_id

    def open_chat_stream(self, run_id: str) -> asyncio.Queue:
        if len(self._active_runs) >= settings.BRIDGE_ACTIVE_RUNS_MAX_PER_INSTANCE:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="active_runs_full").inc()
            raise AppError("AICLAW_RATE_LIMITED", 429)
        queue: asyncio.Queue = asyncio.Queue(maxsize=settings.BRIDGE_CHAT_QUEUE_MAX)
        self._chat_queues[run_id] = queue
        self._active_runs.add(run_id)
        bridge_active_runs.labels(instance_id=self.instance_id).set(len(self._active_runs))
        bridge_chat_queue_depth.labels(instance_id=self.instance_id, run_id=run_id).set(0)
        return queue

    def close_chat_stream(self, run_id: str) -> None:
        self._chat_queues.pop(run_id, None)
        self._active_runs.discard(run_id)
        # 清掉指向该 run_id 的所有 alias
        stale_aliases = [k for k, v in self._run_id_aliases.items() if v == run_id]
        for alias in stale_aliases:
            self._run_id_aliases.pop(alias, None)
        bridge_active_runs.labels(instance_id=self.instance_id).set(len(self._active_runs))

    async def handle_forward_response(self, msg: dict[str, Any]) -> None:
        request_id = msg.get("request_id")
        entry = self._pending.get(request_id)
        if not entry:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="unknown_request").inc()
            return

        pending_epoch, fut, _expires_at = entry
        response_epoch = msg.get("epoch")
        if response_epoch != pending_epoch or response_epoch != self.epoch:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="epoch_mismatch").inc()
            if not fut.done():
                fut.set_exception(AppError("EPOCH_MISMATCH", 409))
            return

        if fut.done():
            return
        if msg.get("ok", True):
            fut.set_result(msg.get("payload", {}))
        else:
            fut.set_exception(AppError("AICLAW_ERROR", 502, {"detail": msg.get("error", {})}))

    async def handle_forward_event(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload", {}) or {}
        event_epoch = msg.get("epoch")
        if event_epoch != self.epoch:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="epoch_mismatch").inc()
            # P1-7 epoch mismatch 不再完全静默：
            #   - log warning 便于排查 race
            #   - 尝试通知潜在受影响的 run（如能解析 run_id 且仍在新 epoch 的 active 集合中）
            #     发送 abort sentinel，让 chat 流明确终止而不是无声卡住
            from loguru import logger as _log
            raw_run_id = payload.get("runId") or payload.get("run_id")
            _log.warning(
                "bridge forward_event epoch mismatch instance={} expected={} got={} run_id={}",
                self.instance_id, self.epoch, event_epoch, raw_run_id,
            )
            if raw_run_id:
                run_id_alias = self._run_id_aliases.get(raw_run_id, raw_run_id)
                queue = self._chat_queues.get(run_id_alias)
                if queue is not None:
                    sentinel = {
                        "event": "chat",
                        "payload": {
                            "state": "aborted",
                            "reason": "epoch_mismatch",
                            "runId": run_id_alias,
                        },
                    }
                    try:
                        queue.put_nowait(sentinel)
                    except asyncio.QueueFull:
                        # 队列满 → drop oldest 让位给 abort sentinel
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            queue.put_nowait(sentinel)
                        except asyncio.QueueFull:
                            pass
            return

        raw_run_id = payload.get("runId") or payload.get("run_id")
        # 如果是 AIClaw 真实 runId，通过 alias 翻译成 SkillForge 端的 sf_run_id
        run_id = self._run_id_aliases.get(raw_run_id, raw_run_id) if raw_run_id else None
        if not run_id or run_id not in self._active_runs:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="inactive_run").inc()
            return

        queue = self._chat_queues.get(run_id)
        if queue is None:
            bridge_reject_total.labels(instance_id=self.instance_id, reason="inactive_run").inc()
            return

        item = {"event": msg.get("event"), "payload": payload}
        state = (payload.get("state") or "").lower()
        is_control = state in {"final", "aborted", "error"}

        if is_control:
            # control event 永远不丢，必要时 drop oldest chunk 让位
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    # 极端：control 仍塞不进 → 紧急 sentinel + 强制终止该 run
                    chat_queue_overflow_total.labels(instance_id=self.instance_id).inc()
                    await self._force_terminate_run(run_id, "queue_overflow_lost")
                    return
        else:
            # chunk event 可丢可覆盖
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    pass
                chat_queue_overflow_total.labels(instance_id=self.instance_id).inc()
                bridge_reject_total.labels(instance_id=self.instance_id, reason="queue_full").inc()

        bridge_chat_queue_depth.labels(instance_id=self.instance_id, run_id=run_id).set(queue.qsize())

    async def _force_terminate_run(self, run_id: str, reason: str) -> None:
        """极端 case：control event 都塞不进 → 强制终止该 run"""
        queue = self._chat_queues.get(run_id)
        if not queue:
            return
        # 清空 chunk 后塞入紧急 sentinel
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(
                {
                    "event": "chat",
                    "payload": {
                        "state": "queue_overflow_lost",
                        "reason": reason,
                        "runId": run_id,
                    },
                }
            )
        except asyncio.QueueFull:
            pass

    async def cleanup_on_disconnect(self, reason: str = "disconnected") -> None:
        async with self._cleanup_lock:
            if self._closed:
                return
            self._closed = True

            for entry in list(self._pending.values()):
                fut = entry[1]
                if not fut.done():
                    fut.set_exception(AppError("BRIDGE_DISCONNECTED", 503, {"detail": reason}))
            self._pending.clear()
            self._bridge_op_response_chunks.clear()

            for run_id, queue in list(self._chat_queues.items()):
                sentinel = {
                    "event": "chat",
                    "payload": {"state": "aborted", "reason": reason, "runId": run_id},
                }
                # put_nowait + 满则 drop oldest 让位，避免 cleanup 阻塞
                try:
                    queue.put_nowait(sentinel)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(sentinel)
                    except asyncio.QueueFull:
                        pass

            self._chat_queues.clear()
            self._active_runs.clear()
            self._run_id_aliases.clear()
            bridge_active_runs.labels(instance_id=self.instance_id).set(0)


class BridgeRegistry:
    """Bridge 连接的内存注册中心。"""

    def __init__(self):
        self._connections: dict[str, BridgeConnection] = {}
        self._epochs: dict[str, int] = defaultdict(int)
        self._instance_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._gc_task: asyncio.Task | None = None

    def start_gc_loop(self) -> asyncio.Task:
        """启动后台 _pending TTL 清理协程，由 main.py lifespan 调用一次。"""
        if self._gc_task and not self._gc_task.done():
            return self._gc_task
        self._gc_task = asyncio.create_task(self._pending_gc_loop())
        return self._gc_task

    async def _pending_gc_loop(self) -> None:
        """周期清理过期 _pending entry，让被遗弃的 future 抛 REQUEST_TIMEOUT。
        同时清理离线 instance 留存的 _instance_locks / _epochs（避免长期内存泄漏）。
        """
        from loguru import logger as _logger

        while True:
            try:
                await asyncio.sleep(10)
                now = asyncio.get_running_loop().time()
                for instance_id, conn in list(self._connections.items()):
                    expired = [
                        rid
                        for rid, (_, _fut, expires_at) in list(conn._pending.items())
                        if expires_at <= now
                    ]
                    for rid in expired:
                        entry = conn._pending.pop(rid, None)
                        if not entry:
                            continue
                        conn._bridge_op_response_chunks.pop(rid, None)
                        _, fut, _exp = entry
                        if not fut.done():
                            fut.set_exception(AppError("REQUEST_TIMEOUT", 504))
                            bridge_reject_total.labels(instance_id=instance_id, reason="pending_timeout").inc()
                    stale_chunk_ids = [
                        rid
                        for rid, state in list(conn._bridge_op_response_chunks.items())
                        if state.get("expires_at", now) <= now or rid not in conn._pending
                    ]
                    for rid in stale_chunk_ids:
                        conn._bridge_op_response_chunks.pop(rid, None)
                # 清理离线 instance 的锁/epoch 残留
                offline_ids = [
                    iid for iid in list(self._instance_locks.keys())
                    if iid not in self._connections
                ]
                for iid in offline_ids:
                    lk = self._instance_locks.get(iid)
                    if lk is not None and not lk.locked():
                        self._instance_locks.pop(iid, None)
                    # epoch 是 int, 无"锁定"概念, 但只在断线后清
                    self._epochs.pop(iid, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _logger.warning("bridge pending gc loop error: {}", exc)

    async def register(self, instance_id: str, ws: WebSocket) -> BridgeConnection:
        if instance_id not in self._connections and len(self._connections) >= settings.BRIDGE_REGISTRY_MAX_CONNECTIONS:
            bridge_reject_total.labels(instance_id=instance_id, reason="registry_full").inc()
            raise AppError("BRIDGE_REGISTRY_FULL", 503)

        lock = self._instance_locks[instance_id]
        old: BridgeConnection | None = None
        async with lock:
            old = self._connections.get(instance_id)
            if old and old.ws is not ws:
                old.superseded = True
            self._epochs[instance_id] += 1
            epoch = self._epochs[instance_id]
            conn = BridgeConnection(instance_id, ws, epoch)
            self._connections[instance_id] = conn
            if old:
                bridge_epoch_swap_total.labels(instance_id=instance_id).inc()

        if old and old.ws is not ws:
            asyncio.create_task(self._cleanup_old_connection(old))
        return conn

    async def _cleanup_old_connection(self, old: BridgeConnection) -> None:
        try:
            await old.cleanup_on_disconnect(reason="replaced")
        finally:
            try:
                await old.ws.close(code=1000, reason="replaced")
            except Exception as e:
                logger.debug("旧 bridge WS close 失败(可能已断): {}", e)

    async def unregister(self, instance_id: str, ws: WebSocket | None = None) -> bool:
        lock = self._instance_locks[instance_id]
        conn = None
        async with lock:
            current = self._connections.get(instance_id)
            if not current:
                return False
            if ws is not None and current.ws is not ws:
                return False
            conn = current
            self._connections.pop(instance_id, None)

        if conn and not conn.superseded:
            await conn.cleanup_on_disconnect()
        return True

    def get(self, instance_id: str) -> BridgeConnection | None:
        conn = self._connections.get(instance_id)
        if conn and conn.superseded:
            return None
        return conn

    def is_online(self, instance_id: str) -> bool:
        return self.get(instance_id) is not None

    def list_online(self) -> list[str]:
        return list(self._connections.keys())

    async def kick(self, instance_id: str, reason: str = "kicked") -> bool:
        """主动踢掉某个 instance 的 bridge 连接（admin 撤销公钥后使用）。"""
        lock = self._instance_locks[instance_id]
        async with lock:
            conn = self._connections.pop(instance_id, None)
            if not conn:
                return False
            conn.superseded = True
        # 锁外做长时间清理
        try:
            await conn.cleanup_on_disconnect(reason=reason)
        except Exception as e:
            logger.warning("bridge cleanup_on_disconnect 失败 inst={}: {}", instance_id, e)
        try:
            await conn.ws.close(code=4403, reason=reason[:120])
        except Exception as e:
            logger.debug("bridge WS force close 失败 inst={}: {}", instance_id, e)
        bridge_online.labels(instance_id=instance_id).set(0)
        return True


bridge_registry = BridgeRegistry()
