"""
Playbook实时执行状态推送。

EventBus：executor 每完成一个step就广播状态变更。
WebSocket：前端连接后按 run_id 订阅，实时接收节点状态。
"""

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.websockets import WebSocketState

from app.auth.dependencies import SESSION_COOKIE, verify_session_token
from app.database import async_session_factory
from app.execution.models import ExecutionRun

logger = logging.getLogger(__name__)

router = APIRouter()

# ===== 事件总线 =====

class PlaybookEventBus:
    """轻量内存事件总线：按 run_id 广播步骤状态"""

    def __init__(self):
        # run_id → set of asyncio.Queue
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """创建一个订阅队列"""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue):
        """移除订阅"""
        self._subscribers[run_id].discard(q)
        if not self._subscribers[run_id]:
            del self._subscribers[run_id]

    async def publish(self, run_id: str, event: dict):
        """广播事件到所有订阅者"""
        for q in list(self._subscribers.get(run_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 慢消费者丢弃


# 全局实例
event_bus = PlaybookEventBus()


# ===== WebSocket 端点 =====

@router.websocket("/ws/playbook-live/{run_id}")
async def playbook_live_ws(websocket: WebSocket, run_id: str):
    """
    WebSocket 实时推送 Playbook 执行状态。

    连接时通过 query 参数传递 session token：
      ws://host/api/playbooks/ws/playbook-live/{run_id}?token=xxx

    推送消息格式：
      { "type": "step_status", "step_id": "step_1", "status": "running" }
      { "type": "step_status", "step_id": "step_1", "status": "completed", "output": {...} }
      { "type": "run_complete", "status": "completed", "completed_steps": 3, "total_steps": 3 }
    """
    # 同源 WebSocket 握手会自动携带 HttpOnly session cookie。
    # 仅在显式传 token 时允许 query 覆盖，避免匿名连接。
    token = websocket.cookies.get(SESSION_COOKIE, "") or websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4001, reason="未登录")
        return

    # v2: verify_session_token 返回 (uid, rev) 元组
    verify_result = verify_session_token(token)
    if not verify_result:
        await websocket.close(code=4001, reason="认证失败")
        return
    user_id, token_rev = verify_result

    # run_id 鉴权：检查 ExecutionRun 存在且用户有部门级访问权限
    from app.auth.models import User
    from app.execution.models import DecisionLog
    from app.skills.core.models import Skill
    async with async_session_factory() as session:
        result = await session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            await websocket.close(code=4003, reason="执行记录不存在")
            return
        # 加载用户对象以检查部门权限
        user_result = await session.execute(select(User).where(User.id == user_id))
        user_obj = user_result.scalar_one_or_none()
        if not user_obj:
            await websocket.close(code=4001, reason="认证失败")
            return
        # v2: 账号禁用 / permissions_rev 不匹配 → 拒绝连接
        if getattr(user_obj, "state", None) == "disabled" or not user_obj.is_active:
            await websocket.close(code=4403, reason="AUTH_ACCOUNT_DISABLED")
            return
        if token_rev != int(getattr(user_obj, "permissions_rev", 0) or 0):
            await websocket.close(code=4401, reason="AUTH_SESSION_EXPIRED")
            return
        if user_obj.role not in ("admin", "ai_engineer") and not user_obj.can_view_all:
            # 非特权用户：检查该执行记录是否关联了本部门的Skill
            dept_result = await session.execute(
                select(Skill.department)
                .join(DecisionLog, DecisionLog.skill_id == Skill.id)
                .where(DecisionLog.run_id == run_id)
                .limit(1)
            )
            run_dept = dept_result.scalar_one_or_none()
            if run_dept and run_dept != user_obj.department:
                await websocket.close(code=4003, reason="无权查看该执行记录")
                return

    await websocket.accept()

    q = event_bus.subscribe(run_id)
    try:
        while True:
            event = await q.get()
            if websocket.client_state == WebSocketState.DISCONNECTED:
                break
            await websocket.send_json(event)

            # 如果是运行结束事件，发完后关闭
            if event.get("type") == "run_complete":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WebSocket异常: %s", e)
    finally:
        event_bus.unsubscribe(run_id, q)
        if websocket.client_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except Exception as e:
                logger.debug("playbook live WS close 失败(可能已断): %s", e)
