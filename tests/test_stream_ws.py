"""F2 流式 WebSocket 端点 E2E 测试。

用 starlette TestClient 的 websocket_connect 验证 WS 协议层逻辑：
- 鉴权（未登录 close 4401）
- ping/pong 心跳
- 协议错误（INVALID_MESSAGE_TYPE / EMPTY_MESSAGE）
- 正常流式：service 层 yield 的事件能正确送达客户端
- LLM 错误事件能被透传

替代原方案中的 Playwright E2E（保持依赖最小化，不引入浏览器）。
DB 写入与 service 层 generator 的覆盖见 tests/test_agent_chat_stream.py。
本文件只关注 WS 协议层（service 层完全 mock）。
"""

import pytest
from unittest.mock import MagicMock

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _make_admin_user():
    user = MagicMock()
    user.id = "admin"
    user.username = "admin"
    user.role = "admin"
    user.department = "AI小组"
    user.is_active = True
    user.dingtalk_user_id = None
    return user


@pytest.fixture
def ws_client(monkeypatch):
    """构造 FastAPI app + 同步 TestClient（支持 websocket_connect）。

    完全 mock service 层，不依赖 DB。
    """
    mock_admin = _make_admin_user()

    async def fake_get_user_ws(websocket):
        return mock_admin

    monkeypatch.setattr(
        "app.testing.router.get_current_user_ws", fake_get_user_ws
    )
    monkeypatch.setattr(
        "app.workbench.router_chat.get_current_user_ws", fake_get_user_ws
    )
    monkeypatch.setattr(
        "app.workbench.router_contract.get_current_user_ws", fake_get_user_ws
    )

    # mock workbench 部门校验（分布在多个子路由中）
    async def noop_dept_check(*args, **kwargs):
        return None
    for sub_module in ("router", "router_chat", "router_patch", "router_session"):
        try:
            monkeypatch.setattr(
                f"app.workbench.{sub_module}._check_skill_department", noop_dept_check
            )
        except AttributeError:
            pass
    # _db_session_for_check 在 workbench_chat_stream 内被调用以执行部门校验，
    # mock 它返回一个空上下文管理器
    class _NoopSession:
        async def __aenter__(self):
            return None
        async def __aexit__(self, *a):
            return None
    monkeypatch.setattr(
        "app.workbench.router_chat._db_session_for_check", lambda: _NoopSession()
    )

    from app.common.exceptions import AppError, app_error_handler
    from app.testing.router import router as testing_router
    from app.workbench.router import router as workbench_router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(testing_router, prefix="/api/skills")
    app.include_router(workbench_router, prefix="/api/skills")

    client = TestClient(app)
    yield client
    client.close()


# ===== 1. agent 流式正常路径 =====

def test_agent_stream_full_flow(ws_client, monkeypatch):
    """正常对话：发送 user_message → 收齐 delta + done"""
    async def fake_send(*args, **kwargs):
        yield {"type": "delta", "content": "你好"}
        yield {"type": "delta", "content": "，"}
        yield {"type": "delta", "content": "我是 AI"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr(
        "app.testing.router.agent_chat_service.send_message_stream",
        fake_send,
    )

    with ws_client.websocket_connect(
        "/api/skills/chat/test-conv/stream"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "你好啊"})

        events = []
        for _ in range(20):
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") in ("done", "error"):
                break

        deltas = [e for e in events if e["type"] == "delta"]
        assert len(deltas) == 3
        assert "".join(d["content"] for d in deltas) == "你好，我是 AI"

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["finish_reason"] == "stop"


# ===== 2. ping / pong 心跳 =====

def test_agent_ping_pong(ws_client):
    with ws_client.websocket_connect(
        "/api/skills/chat/test-conv/stream"
    ) as ws:
        ws.send_json({"type": "ping"})
        msg = ws.receive_json()
        assert msg == {"type": "pong"}


# ===== 3. 协议错误 =====

def test_agent_invalid_message_type(ws_client):
    with ws_client.websocket_connect(
        "/api/skills/chat/test-conv/stream"
    ) as ws:
        ws.send_json({"type": "wrong_type"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "INVALID_MESSAGE_TYPE"


def test_agent_empty_message(ws_client):
    with ws_client.websocket_connect(
        "/api/skills/chat/test-conv/stream"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "   "})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "EMPTY_MESSAGE"


# ===== 4. LLM error 中途透传 =====

def test_agent_stream_llm_error(ws_client, monkeypatch):
    async def fake_send(*args, **kwargs):
        yield {"type": "delta", "content": "开头"}
        yield {"type": "error", "code": "STREAM_FAILED", "error": "模拟 LLM 错误"}

    monkeypatch.setattr(
        "app.testing.router.agent_chat_service.send_message_stream",
        fake_send,
    )

    with ws_client.websocket_connect(
        "/api/skills/chat/test-conv/stream"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "你好"})

        events = []
        for _ in range(10):
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") == "error":
                break

        # 应至少包含一条 delta 和一条 error
        assert any(e["type"] == "delta" for e in events)
        assert any(e["type"] == "error" for e in events)


# ===== 5. 未鉴权 =====

def test_agent_stream_unauthorized(ws_client, monkeypatch):
    """get_current_user_ws 返回 None 时 close(4401)"""
    async def fake_no_user(websocket):
        return None
    monkeypatch.setattr(
        "app.testing.router.get_current_user_ws", fake_no_user
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect(
            "/api/skills/chat/test-conv/stream"
        ) as ws:
            ws.receive_json()
    assert exc_info.value.code == 4401


# ===== 6. workbench 流式 =====

def test_workbench_stream_full_flow(ws_client, monkeypatch):
    """workbench 端点流式问答路径"""
    async def fake_handle_chat_stream(*args, **kwargs):
        yield {"type": "delta", "content": "wb"}
        yield {"type": "delta", "content": " 流式"}
        yield {"type": "done", "finish_reason": "stop"}

    monkeypatch.setattr(
        "app.workbench.router.workbench_service.handle_chat_stream",
        fake_handle_chat_stream,
    )

    with ws_client.websocket_connect(
        "/api/skills/SKILL-A/workbench/chat/stream"
    ) as ws:
        ws.send_json({
            "type": "user_message",
            "content": "测试问题",
            "context": {"active_module": "rules"},
        })

        events = []
        for _ in range(10):
            msg = ws.receive_json()
            events.append(msg)
            if msg.get("type") in ("done", "error"):
                break

        deltas = [e for e in events if e["type"] == "delta"]
        assert "".join(d["content"] for d in deltas) == "wb 流式"
        assert any(e["type"] == "done" for e in events)


def test_workbench_stream_role_check(ws_client, monkeypatch):
    """非 admin/ai_engineer/aibp 角色不可访问 workbench 流"""
    biz_user = _make_admin_user()
    biz_user.role = "biz_owner"

    async def fake_get_user(websocket):
        return biz_user
    monkeypatch.setattr(
        "app.workbench.router_chat.get_current_user_ws", fake_get_user
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect(
            "/api/skills/SKILL-A/workbench/chat/stream"
        ) as ws:
            ws.receive_json()
    assert exc_info.value.code == 4403
