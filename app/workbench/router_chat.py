"""Workbench 聊天相关路由：REST 对话、流式 WS 对话、Coding Agent WS、Slash Command。"""

import asyncio
from contextlib import suppress

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.exceptions import AppError
from app.common.ws_auth import (
    WS_CODE_AUTH_REQUIRED,
    WS_CODE_OVERFLOW,
    WS_CODE_INTERNAL_ERROR,
    WS_CODE_PERMISSION_DENIED,
    get_current_user_ws,
)
from app.common.ws_session import spawn_json_heartbeat, user_ws_session_limiter
from app.database import get_db
from app.workbench.schemas import (
    ChatRequest,
    ChatResponse,
    CommandRequest,
)
from app.workbench.service import workbench_service
from app.workbench.router_support import check_skill_read_access, db_session_for_check

router = APIRouter()


async def _check_skill_department(skill_id: str, current_user: User, db: AsyncSession):
    """加载 Skill 并校验部门权限，供需要 skill_id 的端点复用。"""
    await check_skill_read_access(skill_id, current_user, db)


def _db_session_for_check():
    """
    轻量包装：返回一个 async session 上下文管理器，供 WS 端点的部门检查使用。

    AsyncSession 自身就是 async context manager，__aexit__ 会自动 close()，
    这里只是把 _db_mod 的引用收敛到工具函数里。读操作无需显式 commit/rollback。
    """
    return db_session_for_check()


@router.post("/{skill_id}/workbench/chat", response_model=ChatResponse)
async def workbench_chat(
    skill_id: str,
    body: ChatRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """上下文感知对话 — 统一 AI 交互入口。"""
    await _check_skill_department(skill_id, current_user, db)
    result = await workbench_service.handle_chat(
        skill_id=skill_id,
        session_id=body.session_id,
        message=body.message,
        context=body.context.model_dump(),
        intent=body.intent,
        reference_ids=body.reference_ids,
        user_id=current_user.id,
        department=current_user.department,
    )
    return ChatResponse(**result)


@router.websocket("/{skill_id}/workbench/chat/stream")
async def workbench_chat_stream(websocket: WebSocket, skill_id: str):
    """
    Workbench 统一对话流式 WebSocket。

    协议：
        客户端 → {
            "type": "user_message",
            "content": "...",
            "context": {active_module, selection, draft_snapshot, recent_failures},
            "session_id": "...",       (可选)
            "intent": "...",           (可选)
            "reference_ids": []        (可选)
        }
        服务端 → {"type": "delta", "content": "片段"}              (问答类多次推送)
        服务端 → {"type": "patch", "patch": {...}, "message": "..."}  (patch 类一次性)
        服务端 → {"type": "done", "finish_reason": "stop"}
        服务端 → {"type": "usage_summary", "usage": {...}}
        服务端 → {"type": "error", "code": "...", "error": "..."}

        客户端 → {"type": "stop"}     (中断生成)
        客户端 → {"type": "ping"}     → 服务端 {"type": "pong"}
    """
    await websocket.accept()

    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return

    if not role_matches_any(user, ("admin", "ai_engineer", "aibp")):
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="permission denied")
        return

    # 部门权限检查（懒加载，不在 accept 时阻塞）
    try:
        async with _db_session_for_check() as db:
            await _check_skill_department(skill_id, user, db)
    except AppError as e:
        await websocket.send_json({"type": "error", "code": e.code, "error": e.message})
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason=e.code)
        return
    except Exception as e:
        logger.exception(f"workbench stream 部门检查失败: {e}")
        await websocket.close(code=WS_CODE_INTERNAL_ERROR, reason=str(e)[:100])
        return

    # 并发上限：与 coding stream 一致，单用户同时只允许 N 个 workbench-chat WS
    lease = await user_ws_session_limiter.acquire("workbench-chat", user.id)
    if not lease.acquired:
        await websocket.send_json({
            "type": "error",
            "code": "WS_TOO_MANY_CONNECTIONS",
            "error": f"该账号已有 {lease.count} 个对话流在运行，请先关闭旧连接",
        })
        await websocket.close(code=WS_CODE_OVERFLOW, reason="too many chat streams")
        return

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if msg_type == "stop":
                continue
            if msg_type != "user_message":
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE_TYPE",
                    "error": f"未知消息类型: {msg_type}",
                })
                continue

            content = (msg.get("content") or "").strip()
            if not content:
                await websocket.send_json({
                    "type": "error", "code": "EMPTY_MESSAGE",
                    "error": "消息内容不能为空",
                })
                continue

            ctx = msg.get("context") or {}
            try:
                async for event in workbench_service.handle_chat_stream(
                    skill_id=skill_id,
                    session_id=msg.get("session_id") or "",
                    message=content,
                    context=ctx,
                    intent=msg.get("intent"),
                    reference_ids=msg.get("reference_ids") or [],
                    user_id=user.id,
                    department=user.department,
                ):
                    await websocket.send_json(event)
                    if event.get("type") == "error":
                        break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.exception(f"workbench 流式对话异常 skill={skill_id}: {e}")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INTERNAL_ERROR",
                        "error": str(e)[:300],
                    })
                except Exception as send_err:
                    logger.debug(f"workbench WS 错误回发失败(连接可能已断) skill={skill_id}: {send_err}")
    except WebSocketDisconnect:
        logger.info(f"workbench 流式对话 WS 断开 skill={skill_id}")
    except Exception as e:
        logger.exception(f"workbench 流式对话 WS 致命异常 skill={skill_id}: {e}")
        try:
            await websocket.close(code=WS_CODE_INTERNAL_ERROR, reason=str(e)[:100])
        except Exception as close_err:
            logger.debug(f"workbench WS close 失败(连接可能已断) skill={skill_id}: {close_err}")
    finally:
        with suppress(Exception):
            await lease.release()


# ═══════════════════════════════════════════════════════════════
# Phase 3: Coding Agent (vendor/aiclawcode) WS 流式对话端点
# ═══════════════════════════════════════════════════════════════

@router.websocket("/{skill_id}/workbench/coding/stream")
async def workbench_coding_stream(websocket: WebSocket, skill_id: str):
    """
    新版 AI 编程对话流（基于 vendor/aiclawcode 子进程）。

    协议：
        客户端 → {"type": "user_message", "content": "..."}
        客户端 → {"type": "permission_response", "request_id": "...",
                  "behavior": "allow"|"deny", "updated_input": {...}, "message": "..."}
        客户端 → {"type": "interrupt"}
        客户端 → {"type": "ping"}

        服务端事件类型见 app/coding_agent/schemas.py:EventType
        - session_ready / text_delta / tool_call / tool_result / file_change /
          permission_request / usage / done / error
    """
    await websocket.accept()

    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return

    if not role_matches_any(user, ("admin", "ai_engineer", "aibp")):
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="permission denied")
        return

    # 部门权限
    try:
        async with _db_session_for_check() as db:
            await _check_skill_department(skill_id, user, db)
    except AppError as e:
        await websocket.send_json({"type": "error", "code": e.code, "error": e.message})
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason=e.code)
        return
    except Exception as e:
        logger.exception(f"coding stream 部门检查失败: {e}")
        await websocket.close(code=WS_CODE_INTERNAL_ERROR, reason=str(e)[:100])
        return

    lease = await user_ws_session_limiter.acquire("workbench-coding", user.id)
    if not lease.acquired:
        await websocket.send_json({
            "type": "error",
            "code": "WS_TOO_MANY_CONNECTIONS",
            "error": "你已有进行中的 Coding Agent 连接，请先关闭旧连接",
        })
        await websocket.close(code=WS_CODE_OVERFLOW, reason="too many coding streams")
        return

    incoming_queue: asyncio.Queue[dict] = asyncio.Queue()
    heartbeat_task = spawn_json_heartbeat(websocket, lease=lease)
    active_stream_task: asyncio.Task[None] | None = None
    client_attached = True
    client_disconnected = False

    async def _reader_loop() -> None:
        try:
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
                await incoming_queue.put(msg)
        except WebSocketDisconnect:
            await incoming_queue.put({"type": "__disconnect__"})
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"coding WS reader crashed skill={skill_id}: {exc}")
            await incoming_queue.put({
                "type": "__reader_error__",
                "code": "CODING_STREAM_READER_FAILED",
                "error": str(exc)[:300],
            })

    async def _pipe_stream(stream) -> None:
        nonlocal client_attached
        terminal_seen = False
        try:
            async for event in stream:
                if client_attached:
                    try:
                        await websocket.send_json(event)
                    except Exception as send_exc:  # noqa: BLE001
                        client_attached = False
                        logger.info(
                            "coding stream client detached skill={} user={} reason={}",
                            skill_id,
                            user.id,
                            str(send_exc)[:120],
                        )
                if event.get("type") in {"done", "error"}:
                    terminal_seen = True
                    break
        except asyncio.CancelledError:
            terminal_seen = True
            raise
        except Exception as exc:  # noqa: BLE001
            terminal_seen = True
            logger.exception(f"coding stream pipe failed skill={skill_id}: {exc}")
            with suppress(Exception):
                await websocket.send_json({
                    "type": "error",
                    "code": "CODING_STREAM_PIPE_FAILED",
                    "error": str(exc)[:300],
                })
        finally:
            if not terminal_seen and client_attached:
                logger.warning(
                    "coding stream ended without terminal event skill={} user={}",
                    skill_id,
                    user.id,
                )
                with suppress(Exception):
                    await websocket.send_json({
                        "type": "error",
                        "code": "CODING_STREAM_ENDED_WITHOUT_DONE",
                        "error": "AI 会话已中断，未返回完成事件，请重新发送或刷新续流",
                    })

    def _track_stream(task: asyncio.Task[None]) -> None:
        nonlocal active_stream_task
        if active_stream_task is task:
            active_stream_task = None
        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.exception(f"coding stream task crashed skill={skill_id}: {exc}")

    async def _start_stream(msg: dict) -> None:
        nonlocal active_stream_task
        if active_stream_task and not active_stream_task.done():
            await websocket.send_json({
                "type": "error",
                "code": "STREAM_BUSY",
                "error": "当前已有进行中的 Coding Agent 会话，请先等待完成或发送 interrupt",
            })
            return

        mtype = msg.get("type")
        if mtype == "resume":
            try:
                last_seq = int(msg.get("last_seq") or 0)
            except Exception:
                last_seq = 0
            stream = workbench_service.resume_coding_chat_stream(
                skill_id=skill_id,
                user_id=user.id,
                last_seq=last_seq,
            )
        else:
            content = (msg.get("content") or "").strip()
            images = msg.get("images")
            mode = str(msg.get("mode") or "edit")
            if not content and not images:
                await websocket.send_json({
                    "type": "error",
                    "code": "EMPTY_MESSAGE",
                    "error": "消息内容不能为空",
                })
                return
            stream = workbench_service.handle_coding_chat_stream(
                skill_id=skill_id,
                user_id=user.id,
                message=content or "分析这张图片",
                session_id=msg.get("session_id"),
                images=images,
                mode=mode,
            )

        active_stream_task = asyncio.create_task(_pipe_stream(stream))
        active_stream_task.add_done_callback(_track_stream)

    reader_task = asyncio.create_task(_reader_loop())
    try:
        while True:
            msg = await incoming_queue.get()
            mtype = msg.get("type")

            if mtype == "__disconnect__":
                client_attached = False
                client_disconnected = True
                break
            if mtype == "__reader_error__":
                await websocket.send_json({
                    "type": "error",
                    "code": msg.get("code") or "CODING_STREAM_READER_FAILED",
                    "error": msg.get("error") or "连接读取失败",
                })
                break

            if mtype == "interrupt":
                try:
                    await workbench_service.coding_interrupt(
                        skill_id=skill_id, user_id=user.id,
                    )
                except Exception as e:
                    logger.warning(f"coding interrupt failed: {e}")
                continue

            if mtype == "permission_response":
                try:
                    await workbench_service.coding_respond_permission(
                        skill_id=skill_id,
                        user_id=user.id,
                        request_id=msg.get("request_id") or "",
                        behavior=msg.get("behavior") or "deny",
                        updated_input=msg.get("updated_input"),
                        message=msg.get("message"),
                    )
                except Exception as e:
                    logger.warning(f"coding respond_permission failed: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "code": "PERMISSION_RESPONSE_FAILED",
                        "error": str(e)[:300],
                    })
                continue

            if mtype == "resume":
                try:
                    await _start_stream(msg)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"coding resume stream crashed skill={skill_id}: {e}")
                    with suppress(Exception):
                        await websocket.send_json({
                            "type": "error", "code": "INTERNAL_ERROR", "error": str(e)[:300],
                        })
                continue

            if mtype != "user_message":
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE_TYPE",
                    "error": f"未知消息类型: {mtype}",
                })
                continue

            try:
                await _start_stream(msg)
            except Exception as e:
                logger.exception(f"coding 流式对话异常 skill={skill_id}: {e}")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INTERNAL_ERROR",
                        "error": str(e)[:300],
                    })
                except Exception as send_err:
                    logger.debug(f"coding WS 错误回发失败(连接可能已断) skill={skill_id}: {send_err}")
    except WebSocketDisconnect:
        client_attached = False
        client_disconnected = True
        logger.info(f"coding 流式对话 WS 断开 skill={skill_id} user={user.id}")
        # [resume-v2] 不再主动 close session。之前这里会调 coding_close_session 直接
        # 杀子进程, 结果是: 用户刷新页 / 切页后重连, pool.get() 永远返回 None, resume
        # 协议形同虚设 (永远走 RESUME_NO_SESSION 分支)。
        #
        # 改为依赖 SessionPool 的 idle gc (见 session_pool.gc_loop) — 空闲超过
        # CODING_AGENT_IDLE_TTL_SEC 的 session 会被回收, 有 pending_tool_calls 的
        # 会跳过不回收。正常 resume 流程在 idle ttl 窗口内都能续接成功。
        #
        # lifespan shutdown 仍走 pool.close_all — 所以旧会话不会真的泄漏到进程退出。
    except Exception as e:
        logger.exception(f"coding 流式对话 WS 致命异常 skill={skill_id}: {e}")
        try:
            await websocket.close(code=WS_CODE_INTERNAL_ERROR, reason=str(e)[:100])
        except Exception as close_err:
            logger.debug(f"coding WS close 失败(连接可能已断) skill={skill_id}: {close_err}")
    finally:
        reader_task.cancel()
        heartbeat_task.cancel()
        # 客户端断开时不取消正在跑的 agent turn。否则 aiclawcode 可能停在
        # tool_use/permission_request 之间，后端不再消费 stdout，用户看到“说到一半没下文”。
        # 让 _pipe_stream 进入 detached drain：继续消费、自动处理权限、写 fe_history，
        # 前端重连后用 resume 回放。
        should_detach_stream = client_disconnected and active_stream_task and not active_stream_task.done()
        if active_stream_task and not active_stream_task.done() and not should_detach_stream:
            active_stream_task.cancel()
        with suppress(BaseException):
            await reader_task
        with suppress(BaseException):
            await heartbeat_task
        if active_stream_task and not should_detach_stream:
            with suppress(BaseException):
                await active_stream_task
        await lease.release()


@router.post("/{skill_id}/workbench/command", response_model=ChatResponse)
async def workbench_command(
    skill_id: str,
    body: CommandRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """Slash command 执行。"""
    await _check_skill_department(skill_id, current_user, db)
    result = await workbench_service.handle_command(
        skill_id=skill_id,
        session_id=body.session_id,
        command=body.command,
        context=body.context.model_dump(),
        user_id=current_user.id,
    )
    return ChatResponse(**result)
