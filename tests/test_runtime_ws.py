"""Architect / execute / coding WebSocket 协议与保活测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _make_user(role: str = "admin"):
    user = MagicMock()
    user.id = "u-admin"
    user.username = "admin"
    user.role = role
    user.department = "AI小组"
    user.is_active = True
    user.can_view_all = True
    user.dingtalk_user_id = None
    return user


@pytest.fixture
def runtime_ws_client(monkeypatch):
    user = _make_user()

    async def fake_get_user_ws(_websocket):
        return user

    async def fake_rate_check(_identity: str):
        return True, {"retry_after": 0, "remaining": 1, "limit": 1}

    monkeypatch.setattr("app.workbench.router.get_current_user_ws", fake_get_user_ws)
    monkeypatch.setattr("app.workbench.router_chat.get_current_user_ws", fake_get_user_ws)
    monkeypatch.setattr("app.execution.ws.get_current_user_ws", fake_get_user_ws)
    monkeypatch.setattr("app.common.rate_limiter.chat_limiter.check", fake_rate_check)
    monkeypatch.setattr("app.common.ws_session.user_ws_session_limiter.max_connections", 2)
    monkeypatch.setattr("app.config.settings.WS_HEARTBEAT_INTERVAL_SECONDS", 0.05, raising=False)

    from app.common.exceptions import AppError, app_error_handler
    from app.execution.ws import ws_router
    from app.workbench.router import router as workbench_router
    from app.workbench.router_chat import router as workbench_chat_router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(workbench_router, prefix="/api/skills")
    app.include_router(workbench_chat_router, prefix="/api/skills")
    app.include_router(ws_router, prefix="/api/executions")

    client = TestClient(app)
    yield client
    client.close()


def test_architect_stream_supports_ping_and_heartbeat(runtime_ws_client, monkeypatch):
    gate = asyncio.Event()

    async def fake_find_similar_skills(*_args, **_kwargs):
        return []

    async def fake_synthesize_skill_from_interview(*_args, **_kwargs):
        await gate.wait()
        return {"meta": {"name": "测试 Skill", "department": "AI小组"}}

    class _LintReport:
        def to_dict(self):
            return {"passed": True}

    monkeypatch.setattr("app.workbench.retrieval.find_similar_skills", fake_find_similar_skills)
    monkeypatch.setattr(
        "app.workbench.architect.synthesize_skill_from_interview",
        fake_synthesize_skill_from_interview,
    )
    monkeypatch.setattr("app.skills.lint.lint_skill", lambda *_args, **_kwargs: _LintReport())

    with runtime_ws_client.websocket_connect("/api/skills/architect/stream") as ws:
        ws.send_json({"description": "请生成一个客服质检 Skill", "answers": {}, "mode": "basic"})
        ws.send_json({"type": "ping"})

        saw_pong = False
        saw_heartbeat = False
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "pong":
                saw_pong = True
            if msg.get("type") == "heartbeat":
                saw_heartbeat = True
            if saw_pong and saw_heartbeat:
                break

        assert saw_pong is True
        assert saw_heartbeat is True

        gate.set()
        done = None
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "done":
                done = msg
                break

        assert done is not None
        assert done["result"]["can_publish"] is True


def test_architect_stream_enforces_per_user_connection_limit(runtime_ws_client, monkeypatch):
    gate = asyncio.Event()

    async def fake_find_similar_skills(*_args, **_kwargs):
        return []

    async def fake_synthesize_skill_from_interview(*_args, **_kwargs):
        await gate.wait()
        return {"meta": {"name": "阻塞 Skill", "department": "AI小组"}}

    class _LintReport:
        def to_dict(self):
            return {"passed": True}

    monkeypatch.setattr("app.workbench.retrieval.find_similar_skills", fake_find_similar_skills)
    monkeypatch.setattr(
        "app.workbench.architect.synthesize_skill_from_interview",
        fake_synthesize_skill_from_interview,
    )
    monkeypatch.setattr("app.skills.lint.lint_skill", lambda *_args, **_kwargs: _LintReport())
    monkeypatch.setattr("app.common.ws_session.user_ws_session_limiter.max_connections", 1)

    with runtime_ws_client.websocket_connect("/api/skills/architect/stream") as ws1:
        ws1.send_json({"description": "第一个连接", "answers": {}, "mode": "basic"})
        assert ws1.receive_json()["type"] == "phase"

        with runtime_ws_client.websocket_connect("/api/skills/architect/stream") as ws2:
            ws2.send_json({"description": "第二个连接", "answers": {}, "mode": "basic"})
            error = ws2.receive_json()
            assert error["type"] == "error"
            assert "旧连接" in error["message"]
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws2.receive_json()
            assert exc_info.value.code == 4413

        gate.set()
        for _ in range(40):
            msg = ws1.receive_json()
            if msg.get("type") == "done":
                break


def test_ws_execute_supports_ping_heartbeat_and_result(runtime_ws_client, monkeypatch):
    gate = asyncio.Event()

    async def fake_ensure_skill_access(*_args, **_kwargs):
        return None

    async def fake_execute_skill(*_args, **_kwargs):
        await gate.wait()
        return {"run_id": "run-exec-1", "output": {"ok": True}}

    monkeypatch.setattr("app.skills.core.service_shared.ensure_skill_access", fake_ensure_skill_access)
    monkeypatch.setattr("app.execution.execution_service.execution_service.execute_skill", fake_execute_skill)

    with runtime_ws_client.websocket_connect("/api/executions/ws/execute?skill_id=skill-1") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        ws.send_json({"type": "params", "params": {"foo": "bar"}})
        started = ws.receive_json()
        assert started == {"type": "started", "skill_id": "skill-1"}

        ws.send_json({"type": "ping"})
        saw_pong = False
        saw_heartbeat = False
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "pong":
                saw_pong = True
            if msg.get("type") == "heartbeat":
                saw_heartbeat = True
            if saw_pong and saw_heartbeat:
                break

        assert saw_pong is True
        assert saw_heartbeat is True

        gate.set()
        result = None
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "result":
                result = msg
                break

        assert result is not None
        assert result["data"]["run_id"] == "run-exec-1"


def test_workbench_coding_stream_supports_ping_heartbeat_and_interrupt(runtime_ws_client, monkeypatch):
    gate = asyncio.Event()

    async def fake_ensure_skill_access(*_args, **_kwargs):
        return None

    async def fake_handle_coding_chat_stream(*_args, **_kwargs):
        yield {"type": "session_ready", "session_id": "coding-session-1"}
        await gate.wait()
        yield {"type": "done"}

    async def fake_interrupt(*_args, **_kwargs):
        gate.set()

    monkeypatch.setattr("app.workbench.router_chat.check_skill_read_access", fake_ensure_skill_access)
    monkeypatch.setattr(
        "app.workbench.service.workbench_service.handle_coding_chat_stream",
        fake_handle_coding_chat_stream,
    )
    monkeypatch.setattr("app.workbench.service.workbench_service.coding_interrupt", fake_interrupt)

    with runtime_ws_client.websocket_connect("/api/skills/skill-1/workbench/coding/stream") as ws:
        ws.send_json({"type": "user_message", "content": "帮我调整规则"})
        assert ws.receive_json() == {"type": "session_ready", "session_id": "coding-session-1"}

        ws.send_json({"type": "ping"})

        saw_pong = False
        saw_heartbeat = False
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "pong":
                saw_pong = True
            if msg.get("type") == "heartbeat":
                saw_heartbeat = True
            if saw_pong and saw_heartbeat:
                break

        assert saw_pong is True
        assert saw_heartbeat is True

        ws.send_json({"type": "interrupt"})

        done = None
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "done":
                done = msg
                break

        assert done == {"type": "done"}


def test_workbench_coding_stream_reports_missing_terminal_event(runtime_ws_client, monkeypatch):
    async def fake_ensure_skill_access(*_args, **_kwargs):
        return None

    async def fake_handle_coding_chat_stream(*_args, **_kwargs):
        yield {"type": "session_ready", "session_id": "coding-session-incomplete"}

    monkeypatch.setattr("app.workbench.router_chat.check_skill_read_access", fake_ensure_skill_access)
    monkeypatch.setattr(
        "app.workbench.service.workbench_service.handle_coding_chat_stream",
        fake_handle_coding_chat_stream,
    )

    with runtime_ws_client.websocket_connect("/api/skills/skill-1/workbench/coding/stream") as ws:
        ws.send_json({"type": "user_message", "content": "帮我调整规则"})
        assert ws.receive_json() == {"type": "session_ready", "session_id": "coding-session-incomplete"}

        error = None
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "error":
                error = msg
                break

        assert error is not None
        assert error["code"] == "CODING_STREAM_ENDED_WITHOUT_DONE"


def test_workbench_coding_stream_enforces_per_user_connection_limit(runtime_ws_client, monkeypatch):
    gate = asyncio.Event()

    async def fake_ensure_skill_access(*_args, **_kwargs):
        return None

    async def fake_handle_coding_chat_stream(*_args, **_kwargs):
        yield {"type": "session_ready", "session_id": "coding-session-limit"}
        await gate.wait()
        yield {"type": "done"}

    monkeypatch.setattr("app.workbench.router_chat.check_skill_read_access", fake_ensure_skill_access)
    monkeypatch.setattr(
        "app.workbench.service.workbench_service.handle_coding_chat_stream",
        fake_handle_coding_chat_stream,
    )
    monkeypatch.setattr("app.common.ws_session.user_ws_session_limiter.max_connections", 1)

    with runtime_ws_client.websocket_connect("/api/skills/skill-1/workbench/coding/stream") as ws1:
        ws1.send_json({"type": "user_message", "content": "第一个 coding 流"})
        assert ws1.receive_json() == {"type": "session_ready", "session_id": "coding-session-limit"}

        with runtime_ws_client.websocket_connect("/api/skills/skill-1/workbench/coding/stream") as ws2:
            error = ws2.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "WS_TOO_MANY_CONNECTIONS"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws2.receive_json()
            assert exc_info.value.code == 4413

        gate.set()
        for _ in range(40):
            msg = ws1.receive_json()
            if msg.get("type") == "done":
                break
