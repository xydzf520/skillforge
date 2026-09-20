"""Coding Agent timeline persistence regression tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.coding_agent.schemas import Event, EventType
from app.coding_agent.session_service import session_service as cas_session_service
import app.database as db_mod
from app.workbench.message_persistence import (
    build_coding_session_id,
    ensure_session_row,
    load_coding_timeline,
    load_recent_ai_turns,
)
from app.workbench.models import SkillWorkbenchSession
from app.workbench.service import workbench_service


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_coding_timeline_persists_user_tool_and_done(client, monkeypatch):
    monkeypatch.setattr("app.common.audit.audit.log", _noop)

    fake_session = SimpleNamespace(session_id=None, prompt_hash="prompt-hash")

    async def fake_ensure_session(*args, **kwargs):
        return fake_session

    async def fake_chat(*args, **kwargs):
        yield Event(EventType.SESSION_READY, {"session_id": "remote-session-1", "model": "mock"})
        yield Event(EventType.TEXT_DELTA, {"content": "hello "})
        yield Event(
            EventType.TOOL_CALL,
            {
                "id": "tool-1",
                "tool": "Edit",
                "input": {"file_path": "skills/demo.py"},
                "decision": "ask_user",
                "decision_reason": "need approval",
            },
        )
        yield Event(EventType.TOOL_RESULT, {"id": "tool-1", "is_error": False, "content": "ok"})
        yield Event(EventType.FILE_CHANGE, {"path": "skills/demo.py", "operation": "edit"})
        yield Event(EventType.TEXT_DELTA, {"content": "world"})
        yield Event(EventType.DONE, {"finish_reason": "stop"})

    monkeypatch.setattr(cas_session_service, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(cas_session_service, "chat", fake_chat)

    events = []
    async for ev in workbench_service.handle_coding_chat_stream(
        skill_id="skill-a",
        user_id="user-a",
        message="请帮我改一下",
        mode="edit",
    ):
        events.append(ev)

    assert [e["type"] for e in events] == [
        "session_ready",
        "text_delta",
        "tool_call",
        "tool_result",
        "file_change",
        "text_delta",
        "done",
    ]

    temp_session_id = build_coding_session_id("skill-a", "user-a")
    async with db_mod.async_session_factory() as db:
        result = await db.execute(
            select(SkillWorkbenchSession).where(SkillWorkbenchSession.id.in_([temp_session_id, "remote-session-1"]))
        )
        session_ids = {row.id for row in result.scalars().all()}
    assert session_ids == {temp_session_id, "remote-session-1"}

    timeline = await load_coding_timeline("skill-a", "user-a", limit=20)
    assert any(item["role"] == "user" and item["content"] == "请帮我改一下" for item in timeline)
    assert any(item["role"] == "assistant" and item["content"] == "hello world" for item in timeline)
    assert any(item["role"] == "tool" and item["content"].startswith("tool_call:Edit") for item in timeline)
    assert any(item["role"] == "result" and item["content"].startswith("tool_result:tool-1") for item in timeline)
    assert any(item["role"] == "tool" and item["content"].startswith("file_change:skills/demo.py") for item in timeline)
    assert any(item["role"] == "system" and item["content"] == "session_ready" for item in timeline)
    assert any(item["role"] == "system" and item["content"] == "done" for item in timeline)
    assert {item["session_id"] for item in timeline} == {temp_session_id, "remote-session-1"}


@pytest.mark.asyncio
async def test_coding_timeline_persists_error_terminal(client, monkeypatch):
    monkeypatch.setattr("app.common.audit.audit.log", _noop)

    fake_session = SimpleNamespace(session_id=None, prompt_hash="prompt-hash")

    async def fake_ensure_session(*args, **kwargs):
        return fake_session

    async def fake_chat(*args, **kwargs):
        yield Event(EventType.SESSION_READY, {"session_id": "remote-session-2", "model": "mock"})
        yield Event(EventType.TEXT_DELTA, {"content": "partial "})
        yield Event(EventType.TOOL_CALL, {"id": "tool-2", "tool": "Write", "input": {"file_path": "skills/x.py"}})
        yield Event(EventType.FILE_CHANGE, {"path": "skills/x.py", "operation": "write"})
        yield Event(EventType.ERROR, {"code": "CODING_AGENT_INTERNAL", "error": "boom"})

    monkeypatch.setattr(cas_session_service, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(cas_session_service, "chat", fake_chat)

    events = []
    async for ev in workbench_service.handle_coding_chat_stream(
        skill_id="skill-b",
        user_id="user-b",
        message="出错也要记",
        mode="edit",
    ):
        events.append(ev)

    assert events[-1]["type"] == "error"
    assert any(ev["type"] == "file_change" for ev in events)

    timeline = await load_coding_timeline("skill-b", "user-b", limit=20)
    assert any(item["role"] == "user" and item["content"] == "出错也要记" for item in timeline)
    assert any(item["role"] == "assistant" and item["content"] == "partial" for item in timeline)
    assert any(item["role"] == "tool" and item["content"].startswith("tool_call:Write") for item in timeline)
    assert any(item["role"] == "tool" and item["content"].startswith("file_change:skills/x.py") for item in timeline)
    assert any(item["role"] == "system" and item["content"] == "error" for item in timeline)
    assert any(item["intent"].get("code") == "CODING_AGENT_INTERNAL" for item in timeline if item["role"] == "system")


@pytest.mark.asyncio
async def test_coding_stream_auto_commits_signature_changes_without_file_event(client, monkeypatch):
    monkeypatch.setattr("app.common.audit.audit.log", _noop)

    fake_session = SimpleNamespace(session_id=None, prompt_hash="prompt-hash")

    async def fake_ensure_session(*args, **kwargs):
        return fake_session

    async def fake_chat(*args, **kwargs):
        yield Event(EventType.SESSION_READY, {"session_id": "remote-session-3", "model": "mock"})
        yield Event(EventType.TEXT_DELTA, {"content": "已通过脚本改完"})
        yield Event(EventType.DONE, {"finish_reason": "stop"})

    signatures = iter(["before", "after"])
    commits: list[dict] = []

    async def fake_signature(skill_id):
        return next(signatures)

    async def fake_auto_commit(**kwargs):
        commits.append(kwargs)
        return "deadbeefcafebabefeedface1234567890abcdef"

    async def fake_diff_summary(skill_id, commit_sha):
        return {
            "files": [{"path": "scripts/main.py", "status": "M", "insertions": 3, "deletions": 1}],
            "total_files": 1,
            "total_insertions": 3,
            "total_deletions": 1,
        }

    monkeypatch.setattr(cas_session_service, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(cas_session_service, "chat", fake_chat)
    monkeypatch.setattr(workbench_service, "_skill_git_signature", fake_signature)
    monkeypatch.setattr(workbench_service, "_auto_commit_ai_changes", fake_auto_commit)
    monkeypatch.setattr(workbench_service, "_git_commit_diff_summary", fake_diff_summary)

    events = []
    async for ev in workbench_service.handle_coding_chat_stream(
        skill_id="skill-c",
        user_id="user-c",
        message="用 Bash 更新脚本",
        mode="edit",
    ):
        events.append(ev)

    assert commits == [{
        "skill_id": "skill-c",
        "user_id": "user-c",
        "prompt": "用 Bash 更新脚本",
    }]
    assert events[-1]["type"] == "done"
    assert events[-1]["git_commit"] == "deadbeef"
    assert events[-1]["changed_files"] == ["scripts/main.py"]
    assert events[-1]["diff_summary"]["total_files"] == 1

    timeline = await load_coding_timeline("skill-c", "user-c", limit=20)
    assert any(item["role"] == "system" and item["content"] == "git_commit" for item in timeline)
    assert any(item["turn_id"] for item in timeline if item["role"] == "system")

    turns = await load_recent_ai_turns("skill-c", "user-c", limit=5)
    assert len(turns) == 1
    assert turns[0]["user_prompt"] == "用 Bash 更新脚本"
    assert turns[0]["git_commit"] == "deadbeef"
    assert turns[0]["changed_files"] == ["scripts/main.py"]
    assert turns[0]["diff_summary"]["total_insertions"] == 3
    assert turns[0]["status"] == "success"
    assert turns[0]["quality"]["score"] >= 45
    assert any(item["key"] == "git_commit" and item["passed"] for item in turns[0]["quality"]["items"])


@pytest.mark.asyncio
async def test_recent_ai_turns_maps_stream_incomplete_reason(client, monkeypatch):
    monkeypatch.setattr("app.common.audit.audit.log", _noop)

    fake_session = SimpleNamespace(session_id=None, prompt_hash="prompt-hash")

    async def fake_ensure_session(*args, **kwargs):
        return fake_session

    async def fake_chat(*args, **kwargs):
        yield Event(EventType.SESSION_READY, {"session_id": "remote-session-incomplete", "model": "mock"})
        yield Event(EventType.TEXT_DELTA, {"content": "partial"})

    monkeypatch.setattr(cas_session_service, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(cas_session_service, "chat", fake_chat)

    events = []
    async for ev in workbench_service.handle_coding_chat_stream(
        skill_id="skill-incomplete",
        user_id="user-incomplete",
        message="会中断",
        mode="edit",
    ):
        events.append(ev)

    assert events[-1]["code"] == "CODING_AGENT_STREAM_INCOMPLETE"
    assert events[-1]["error_reason"]["key"] == "STREAM_INCOMPLETE"

    turns = await load_recent_ai_turns("skill-incomplete", "user-incomplete", limit=5)
    assert turns[0]["status"] == "error"
    assert turns[0]["error_reason"]["key"] == "STREAM_INCOMPLETE"
    assert "输出流中断" in turns[0]["error_reason"]["reason"]


@pytest.mark.asyncio
async def test_coding_stream_skips_auto_commit_when_signature_unchanged(client, monkeypatch):
    monkeypatch.setattr("app.common.audit.audit.log", _noop)

    fake_session = SimpleNamespace(session_id=None, prompt_hash="prompt-hash")

    async def fake_ensure_session(*args, **kwargs):
        return fake_session

    async def fake_chat(*args, **kwargs):
        yield Event(EventType.SESSION_READY, {"session_id": "remote-session-4", "model": "mock"})
        yield Event(EventType.TEXT_DELTA, {"content": "只是解释"})
        yield Event(EventType.DONE, {"finish_reason": "stop"})

    async def fake_signature(skill_id):
        return "same"

    async def fake_auto_commit(**kwargs):
        raise AssertionError("unchanged worktree should not auto commit")

    monkeypatch.setattr(cas_session_service, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(cas_session_service, "chat", fake_chat)
    monkeypatch.setattr(workbench_service, "_skill_git_signature", fake_signature)
    monkeypatch.setattr(workbench_service, "_auto_commit_ai_changes", fake_auto_commit)

    events = []
    async for ev in workbench_service.handle_coding_chat_stream(
        skill_id="skill-d",
        user_id="user-d",
        message="解释一下",
        mode="edit",
    ):
        events.append(ev)

    assert events[-1]["type"] == "done"
    assert "git_commit" not in events[-1]


@pytest.mark.asyncio
async def test_coding_session_row_scopes_long_or_conflicting_ids(client):
    long_id = "remote-" + ("x" * 80)
    safe_long_id = await ensure_session_row(
        session_id=long_id,
        skill_id="skill-long",
        user_id="user-long",
    )
    assert safe_long_id != long_id
    assert safe_long_id.startswith("coding-")
    assert len(safe_long_id) <= 50

    first_id = await ensure_session_row(
        session_id="remote-shared",
        skill_id="skill-one",
        user_id="user-one",
    )
    second_id = await ensure_session_row(
        session_id="remote-shared",
        skill_id="skill-two",
        user_id="user-two",
    )
    assert first_id == "remote-shared"
    assert second_id != first_id

    async with db_mod.async_session_factory() as db:
        result = await db.execute(
            select(SkillWorkbenchSession).where(SkillWorkbenchSession.id.in_([safe_long_id, first_id, second_id]))
        )
        rows = {row.id: row for row in result.scalars().all()}

    assert rows[safe_long_id].skill_id == "skill-long"
    assert rows[first_id].skill_id == "skill-one"
    assert rows[second_id].skill_id == "skill-two"
