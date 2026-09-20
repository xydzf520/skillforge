"""
Phase 1: app/coding_agent/ 桥接模块单元测试。

不依赖真实 Node 子进程：
- stream_protocol / permission_policy 用纯函数测试
- subprocess_session / session_pool 用 mock subprocess 模拟 stream-json 输入输出
- session_service 的事件翻译用注入的"假事件流"测试
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.coding_agent.permission_policy import PermissionPolicy
from app.coding_agent.schemas import (
    CodingAgentErrorCode,
    Event,
    EventType,
    PermissionEvaluation,
)
from app.coding_agent.stream_protocol import (
    build_interrupt_request,
    build_permission_response,
    build_user_message,
    decode_line,
    encode_message,
)


# ─────────────────────────────────────────────────────────
# stream_protocol
# ─────────────────────────────────────────────────────────

class TestStreamProtocol:
    def test_encode_user_message_round_trip(self):
        msg = build_user_message("hello world")
        encoded = encode_message(msg)
        assert encoded.endswith(b"\n")
        assert b"hello world" in encoded
        decoded = decode_line(encoded)
        assert decoded == msg

    def test_encode_handles_unicode(self):
        msg = build_user_message("把第3步的ROI改成1.5")
        encoded = encode_message(msg)
        decoded = decode_line(encoded)
        assert decoded["message"]["content"] == "把第3步的ROI改成1.5"

    def test_decode_skips_non_json_lines(self):
        assert decode_line("") is None
        assert decode_line("hello world") is None
        assert decode_line("[1,2,3]") is None  # 顶层 array 也不接受
        assert decode_line(b"\xff\xfe") is None
        assert decode_line('{"type":"system"}') == {"type": "system"}

    def test_build_permission_response_allow(self):
        resp = build_permission_response("req-1", "allow", updated_input={"command": "ls"})
        assert resp["type"] == "control_response"
        assert resp["response"]["request_id"] == "req-1"
        assert resp["response"]["response"]["behavior"] == "allow"
        assert resp["response"]["response"]["updatedInput"] == {"command": "ls"}

    def test_build_permission_response_deny(self):
        resp = build_permission_response("req-1", "deny", message="not allowed")
        assert resp["response"]["response"]["behavior"] == "deny"
        assert resp["response"]["response"]["message"] == "not allowed"

    def test_build_permission_response_invalid_behavior(self):
        with pytest.raises(ValueError):
            build_permission_response("r", "maybe")

    def test_build_interrupt(self):
        req = build_interrupt_request()
        assert req["type"] == "control_request"
        assert req["request"]["subtype"] == "interrupt"
        assert "request_id" in req


# ─────────────────────────────────────────────────────────
# local_path_guard
# ─────────────────────────────────────────────────────────

class TestLocalPathGuard:
    def test_detects_unavailable_windows_path_on_linux(self):
        from app.coding_agent.local_path_guard import find_first_unavailable_local_path

        issue = find_first_unavailable_local_path(
            r'检查 "C:\Users\skillforge\Documents\Default Project\golutra-master" 是否有 LICENSE',
            platform="linux",
            path_exists=lambda _path: False,
        )

        assert issue is not None
        assert issue.code == CodingAgentErrorCode.LOCAL_PATH_UNAVAILABLE.value
        assert issue.original_path == r"C:\Users\skillforge\Documents\Default Project\golutra-master"
        assert issue.mapped_path == "/mnt/c/Users/skillforge/Documents/Default Project/golutra-master"
        assert "当前服务器无法访问该目录" in issue.detail()["detail"]
        assert any("Get-ChildItem" in cmd for cmd in issue.detail()["windows_checks"])

    def test_allows_mounted_windows_path_on_linux(self):
        from app.coding_agent.local_path_guard import find_first_unavailable_local_path

        issue = find_first_unavailable_local_path(
            r"C:\Users\skillforge\Documents\Default Project\golutra-master",
            platform="linux",
            path_exists=lambda _path: True,
        )

        assert issue is None

    def test_ignores_non_windows_paths(self):
        from app.coding_agent.local_path_guard import find_first_unavailable_local_path

        issue = find_first_unavailable_local_path(
            "/home/skillforge/skillforge/golutra-master",
            platform="linux",
            path_exists=lambda _path: False,
        )

        assert issue is None


# ─────────────────────────────────────────────────────────
# permission_policy
# ─────────────────────────────────────────────────────────

class TestPermissionPolicy:
    @pytest.fixture
    def policy_dir(self, tmp_path: Path) -> Path:
        skill_dir = tmp_path / "skill_a"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# test")
        return skill_dir

    def test_balanced_read_within_dir(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Read", {"file_path": str(policy_dir / "SKILL.md")})
        assert ev.decision == "auto_allow"

    def test_balanced_edit_within_dir_notifies(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Edit", {"file_path": str(policy_dir / "SKILL.md")})
        assert ev.decision == "notify_allow"

    def test_strict_edit_asks_user(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "strict")
        ev = p.evaluate("Edit", {"file_path": str(policy_dir / "SKILL.md")})
        assert ev.decision == "ask_user"

    def test_loose_edit_auto_allow(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "loose")
        ev = p.evaluate("Edit", {"file_path": str(policy_dir / "SKILL.md")})
        assert ev.decision == "auto_allow"

    def test_bash_dangerous_asks_user(self, policy_dir: Path):
        """危险 Bash 命令（sudo / rm / git push / systemctl 等）三档策略下都需用户批准。"""
        # 字面清晰表达 dangerous，落到实机即使执行也被 sudo 密码挡住
        dangerous_cmd = "sudo apt-get update"
        for strategy in ("strict", "balanced", "loose"):
            p = PermissionPolicy(policy_dir, strategy)
            ev = p.evaluate("Bash", {"command": dangerous_cmd})
            assert ev.decision == "ask_user", f"strategy={strategy}"

    def test_bash_safe_command_notify_allowed_in_balanced(self, policy_dir: Path):
        """balanced 策略下 Bash 默认放行并通知前端。"""
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Bash", {"command": "ls"})
        assert ev.decision == "notify_allow"

    def test_bash_absolute_path_outside_dir_denied(self, policy_dir: Path):
        """Bash 默认放行的前提是路径不越过当前 skill/session 边界。"""
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Bash", {"command": "cat /etc/passwd"})
        assert ev.decision == "deny"

    def test_bash_windows_path_unavailable_denied(self, monkeypatch, policy_dir: Path):
        """Linux agent 不能替 Windows 本机路径做本地文件核查。"""
        monkeypatch.setattr("app.coding_agent.local_path_guard._current_platform", lambda: "linux")
        monkeypatch.setattr("app.coding_agent.local_path_guard.Path.exists", lambda _path: False)
        p = PermissionPolicy(policy_dir, "balanced")

        ev = p.evaluate(
            "Bash",
            {"command": r'dir /s /b "C:\Users\skillforge\Documents\Default Project\golutra-master"'},
        )

        assert ev.decision == "deny"
        assert "当前服务器无法访问该目录" in ev.reason

    def test_bash_external_side_effect_asks_user(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Bash", {"command": "curl --request DELETE https://example.com/item/1"})
        assert ev.decision == "ask_user"

    def test_outside_dir_denied(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Read", {"file_path": "/etc/passwd"})
        assert ev.decision == "deny"

    def test_path_traversal_denied(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("Edit", {"file_path": "../../etc/passwd"})
        assert ev.decision == "deny"

    def test_path_traversal_with_skill_prefix_denied(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        # 看似在 skill_dir 里，但 ../../ 会逃逸
        evil = str(policy_dir / ".." / ".." / "etc" / "passwd")
        ev = p.evaluate("Edit", {"file_path": evil})
        assert ev.decision == "deny"

    def test_unknown_tool_asks_user(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        ev = p.evaluate("MysteryTool", {"foo": "bar"})
        assert ev.decision == "ask_user"

    def test_relative_path_resolves_to_skill_dir(self, policy_dir: Path):
        p = PermissionPolicy(policy_dir, "balanced")
        # 相对路径默认相对 skill_dir
        ev = p.evaluate("Read", {"file_path": "SKILL.md"})
        assert ev.decision == "auto_allow"


# ─────────────────────────────────────────────────────────
# [H9] respond_permission deny 主动 dec pending_tool_calls
# ─────────────────────────────────────────────────────────

class TestRespondPermissionPendingDecrement:
    """[H9] deny 时主动 dec pending_tool_calls 防止 GC 永远不清理死会话。"""

    @pytest.mark.asyncio
    async def test_deny_decrements_pending_counter(self, tmp_path):
        """deny 时应自动减少 pending_tool_calls 计数。"""
        from app.coding_agent.subprocess_session import SubprocessSession

        s = SubprocessSession.__new__(SubprocessSession)
        s.skill_id = "s1"
        s.user_id = "u1"
        s.work_dir = tmp_path
        s._closed = True  # 阻止 _send 真发到 stdin
        s.proc = None
        s._stdin_lock = asyncio.Lock()
        s.pending_tool_calls = 3  # 模拟有 3 个 pending tool calls

        # _send 因 _closed=True 会抛 AppError, 用 try 吞掉, 验证 dec 仍然发生
        with pytest.raises(Exception):
            await s.respond_permission("req-1", "deny", message="user denied")
        # 即便 _send 失败, dec 也不会发生 (因为 _send 在 dec 之前)
        # 实际场景: _send 成功 → 然后 dec
        assert s.pending_tool_calls == 3

        # 重新打开 stdin (模拟正常发送), 验证 dec
        s._closed = False
        # mock proc.stdin
        from unittest.mock import MagicMock, AsyncMock
        s.proc = MagicMock()
        s.proc.stdin = MagicMock()
        s.proc.stdin.is_closing = MagicMock(return_value=False)
        s.proc.stdin.write = MagicMock()
        s.proc.stdin.drain = AsyncMock()

        await s.respond_permission("req-2", "deny", message="auto denied")
        assert s.pending_tool_calls == 2  # 从 3 dec 到 2

    @pytest.mark.asyncio
    async def test_deny_does_not_underflow(self, tmp_path):
        """pending_tool_calls 已经 0 时, deny 不应使其变成 -1。"""
        from app.coding_agent.subprocess_session import SubprocessSession
        from unittest.mock import MagicMock, AsyncMock

        s = SubprocessSession.__new__(SubprocessSession)
        s.skill_id = "s1"
        s.user_id = "u1"
        s.work_dir = tmp_path
        s._closed = False
        s._stdin_lock = asyncio.Lock()
        s.pending_tool_calls = 0
        s.proc = MagicMock()
        s.proc.stdin = MagicMock()
        s.proc.stdin.is_closing = MagicMock(return_value=False)
        s.proc.stdin.write = MagicMock()
        s.proc.stdin.drain = AsyncMock()

        await s.respond_permission("req-3", "deny")
        assert s.pending_tool_calls == 0  # 不变成 -1

    @pytest.mark.asyncio
    async def test_allow_does_not_decrement(self, tmp_path):
        """allow 不应触发 dec — 工具会真实执行, 等 reader_loop 的 tool_result 来 dec。"""
        from app.coding_agent.subprocess_session import SubprocessSession
        from unittest.mock import MagicMock, AsyncMock

        s = SubprocessSession.__new__(SubprocessSession)
        s.skill_id = "s1"
        s.user_id = "u1"
        s.work_dir = tmp_path
        s._closed = False
        s._stdin_lock = asyncio.Lock()
        s.pending_tool_calls = 2
        s.proc = MagicMock()
        s.proc.stdin = MagicMock()
        s.proc.stdin.is_closing = MagicMock(return_value=False)
        s.proc.stdin.write = MagicMock()
        s.proc.stdin.drain = AsyncMock()

        await s.respond_permission("req-4", "allow", updated_input={"file_path": "x"})
        assert s.pending_tool_calls == 2  # allow 不动计数


# ─────────────────────────────────────────────────────────
# session_service 事件翻译（不需要真子进程）
# ─────────────────────────────────────────────────────────

class TestSessionServiceTranslation:
    """直接调内部 _translate_event 方法验证事件转换逻辑。"""

    @pytest.fixture
    def service(self, tmp_path: Path):
        from app.coding_agent.session_service import SessionService
        svc = SessionService.__new__(SessionService)
        svc._policies = {}
        svc._pending_perms = {}
        # 给 (s1, u1) 注入一个 policy
        svc._policies[("s1", "u1")] = PermissionPolicy(tmp_path, "balanced")
        return svc

    @pytest.fixture
    def fake_session(self, tmp_path: Path):
        """构造一个不真启动子进程的 SubprocessSession 替身。"""
        from app.coding_agent.subprocess_session import SubprocessSession

        s = SubprocessSession.__new__(SubprocessSession)
        s.skill_id = "s1"
        s.user_id = "u1"
        s.work_dir = tmp_path
        s._closed = False
        s.proc = None  # 不真发响应
        s.session_id = "fake-sess"
        s.model = "claude-sonnet-4-6"
        s.tools = []
        s.prompt_hash = "phash123"
        s.config_dir = tmp_path / ".aiclawcode"
        s.runtime_profile = {"vendor_version": "0.0.1", "permission_strategy": "balanced"}

        async def _noop(*a, **k):
            return None
        s.respond_permission = _noop  # type: ignore
        return s

    def test_assistant_text_to_text_delta(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        })
        assert len(events) == 1
        assert events[0].type == EventType.TEXT_DELTA
        assert events[0].payload["content"] == "hello"

    def test_tool_use_emits_tool_call(self, service, fake_session, tmp_path):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "tu1",
                "name": "Read",
                "input": {"file_path": str(tmp_path / "x.md")},
            }]},
        })
        assert any(e.type == EventType.TOOL_CALL for e in events)
        tc = next(e for e in events if e.type == EventType.TOOL_CALL)
        assert tc.payload["tool"] == "Read"
        assert tc.payload["decision"] == "auto_allow"
        assert tc.payload["prompt_hash"] == "phash123"

    def test_edit_tool_emits_file_change(self, service, fake_session, tmp_path):
        fake_session.work_dir = tmp_path
        target = tmp_path / "SKILL.md"
        target.write_text("foo")
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "tu2",
                "name": "Edit",
                "input": {
                    "file_path": str(target),
                    "old_string": "foo",
                    "new_string": "bar",
                },
            }]},
        })
        types = [e.type for e in events]
        assert EventType.TOOL_CALL in types
        assert EventType.FILE_CHANGE in types
        fc = next(e for e in events if e.type == EventType.FILE_CHANGE)
        assert fc.payload["path"] == "SKILL.md"
        assert fc.payload["operation"] == "edit"
        assert fc.payload["old_string"] == "foo"
        assert fc.payload["new_string"] == "bar"

    def test_user_role_tool_result_emits_tool_result(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tu1",
                "content": "file content here",
                "is_error": False,
            }]},
        })
        assert len(events) == 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].payload["id"] == "tu1"
        assert events[0].payload["is_error"] is False

    def test_system_init_emits_runtime_metadata(self, service, fake_session):
        fake_session.session_id = None
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "system",
            "subtype": "init",
            "session_id": "sess-1",
            "model": "glm-5",
            "tools": ["Read", "Edit"],
        })
        ready = next(e for e in events if e.type == EventType.SESSION_READY)
        assert ready.payload["prompt_hash"] == "phash123"
        assert ready.payload["config_dir"].endswith(".aiclawcode")
        assert ready.payload["runtime_profile"]["vendor_version"] == "0.0.1"

    def test_result_event_emits_done(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1234,
            "total_cost_usd": 0.005,
            "stop_reason": "end_turn",
        })
        assert len(events) == 1
        assert events[0].type == EventType.DONE
        assert events[0].payload["duration_ms"] == 1234

    def test_assistant_usage_emits_usage_event(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        })
        usage_evs = [e for e in events if e.type == EventType.USAGE]
        assert len(usage_evs) == 1
        assert usage_evs[0].payload["input_tokens"] == 10
        assert usage_evs[0].payload["output_tokens"] == 5

    @pytest.mark.asyncio
    async def test_control_request_auto_allow_does_not_emit_request(self, service, fake_session, tmp_path):
        target = tmp_path / "SKILL.md"
        target.write_text("x")
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "control_request",
            "request_id": "req-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Read",  # auto_allow
                "input": {"file_path": str(target)},
            },
        })
        # auto_allow → 后端直接代答, 不发 PERMISSION_REQUEST 给前端
        assert not any(e.type == EventType.PERMISSION_REQUEST for e in events)
        # 让代答的 task 跑完
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_control_request_ask_user_emits_request(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "control_request",
            "request_id": "req-2",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "rm -rf ./tmp"},
            },
        })
        perm_reqs = [e for e in events if e.type == EventType.PERMISSION_REQUEST]
        assert len(perm_reqs) == 1
        assert perm_reqs[0].payload["request_id"] == "req-2"
        assert perm_reqs[0].payload["policy"] == "ask_user"
        # 原始 input 缓存了
        assert "req-2" in service._pending_perms[("s1", "u1")]

    @pytest.mark.asyncio
    async def test_control_request_notify_allow_does_not_cache_pending(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "control_request",
            "request_id": "req-bash",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "ls -la"},
            },
        })
        perm_reqs = [e for e in events if e.type == EventType.PERMISSION_REQUEST]
        assert len(perm_reqs) == 1
        assert perm_reqs[0].payload["auto_approved"] is True
        assert service._pending_perms.get(("s1", "u1"), {}) == {}
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_control_request_deny_emits_error(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "control_request",
            "request_id": "req-3",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Edit",
                "input": {"file_path": "/etc/passwd"},
            },
        })
        err = [e for e in events if e.type == EventType.ERROR]
        assert len(err) == 1
        assert err[0].payload["code"] == CodingAgentErrorCode.PERMISSION_VIOLATION.value
        await asyncio.sleep(0.05)

    def test_tool_result_missing_path_is_classified(self, service, fake_session):
        events = service._translate_event("s1", "u1", fake_session, {
            "type": "user",
            "message": {
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "is_error": True,
                    "content": "ENOENT: no such file or directory, open '/tmp/coding-agent/x/tool-results/1.json'",
                }],
            },
        })
        results = [e for e in events if e.type == EventType.TOOL_RESULT]
        assert results[0].payload["error_kind"] == "missing_path"
        assert "已过期" in results[0].payload["error_detail"]


# ─────────────────────────────────────────────────────────
# session_pool 容量管理（用 fake session 不真起子进程）
# ─────────────────────────────────────────────────────────

class TestSessionPool:
    @pytest.fixture
    def fake_session_class(self):
        """生成永远 alive 的假 SubprocessSession，用于测试 LRU 而不真起进程。"""
        from app.coding_agent.subprocess_session import SubprocessSession

        class FakeSession(SubprocessSession):
            def __init__(self, *, skill_id, user_id, work_dir, env=None,
                         provider=None, model=None,
                         permission_mode="acceptEdits", max_turns=30,
                         system_prompt_append=None,
                         mcp_config_json=None):
                self.skill_id = skill_id
                self.user_id = user_id
                self.work_dir = Path(work_dir)
                self._closed = False
                self.proc = None
                self.session_id = f"fake-{skill_id}-{user_id}"
                self.model = model or "fake-model"
                self.tools = []
                self._provider = provider
                self._model = model
                self._permission_mode = permission_mode
                self._max_turns = max_turns
                self._system_prompt_append = system_prompt_append
                self._mcp_config_json = mcp_config_json
                self._env_override = env or {}
                self._event_queue = asyncio.Queue()
                self._reader_task = None
                self._stderr_task = None
                self._stdin_lock = asyncio.Lock()
                self.last_active_at = asyncio.get_event_loop().time()
                self.prompt_hash = None
                self.config_dir = None
                self.runtime_profile = None

            async def start(self):
                return None

            @property
            def is_alive(self):
                return not self._closed

            async def close(self, *, kill=False):
                self._closed = True

        return FakeSession

    @pytest.mark.asyncio
    async def test_pool_acquire_reuses_same_key(self, monkeypatch, fake_session_class, tmp_path):
        from app.coding_agent.session_pool import SessionPool
        monkeypatch.setattr(
            "app.coding_agent.session_pool.SubprocessSession", fake_session_class
        )
        pool = SessionPool()
        s1 = await pool.acquire(skill_id="A", user_id="u1", work_dir=tmp_path)
        s2 = await pool.acquire(skill_id="A", user_id="u1", work_dir=tmp_path)
        assert s1 is s2

    @pytest.mark.asyncio
    async def test_pool_evict_lru_when_full(self, monkeypatch, fake_session_class, tmp_path):
        from app.coding_agent.session_pool import SessionPool
        from app.config import settings
        monkeypatch.setattr(
            "app.coding_agent.session_pool.SubprocessSession", fake_session_class
        )
        monkeypatch.setattr(settings, "CODING_AGENT_MAX_SESSIONS", 2)
        pool = SessionPool()
        await pool.acquire(skill_id="A", user_id="u1", work_dir=tmp_path)
        await pool.acquire(skill_id="B", user_id="u1", work_dir=tmp_path)
        await pool.acquire(skill_id="C", user_id="u1", work_dir=tmp_path)  # 触发 evict
        # 最久没碰的 A 应该被踢
        assert ("A", "u1") not in pool._sessions
        assert ("B", "u1") in pool._sessions
        assert ("C", "u1") in pool._sessions

    @pytest.mark.asyncio
    async def test_pool_release_by_key(self, monkeypatch, fake_session_class, tmp_path):
        from app.coding_agent.session_pool import SessionPool
        monkeypatch.setattr(
            "app.coding_agent.session_pool.SubprocessSession", fake_session_class
        )
        pool = SessionPool()
        s = await pool.acquire(skill_id="A", user_id="u1", work_dir=tmp_path)
        await pool.release_by_key(skill_id="A", user_id="u1")
        assert s._closed

    @pytest.mark.asyncio
    async def test_pool_propagates_system_prompt(
        self, monkeypatch, fake_session_class, tmp_path
    ):
        from app.coding_agent.session_pool import SessionPool
        monkeypatch.setattr(
            "app.coding_agent.session_pool.SubprocessSession", fake_session_class
        )
        pool = SessionPool()
        prompt = "test prompt with 你好 unicode"
        s = await pool.acquire(
            skill_id="A", user_id="u1", work_dir=tmp_path,
            system_prompt_append=prompt,
        )
        assert s._system_prompt_append == prompt

    @pytest.mark.asyncio
    async def test_pool_recreates_when_prompt_hash_changes(
        self, monkeypatch, fake_session_class, tmp_path
    ):
        from app.coding_agent.session_pool import SessionPool
        monkeypatch.setattr(
            "app.coding_agent.session_pool.SubprocessSession", fake_session_class
        )
        pool = SessionPool()
        s1 = await pool.acquire(
            skill_id="A", user_id="u1", work_dir=tmp_path, prompt_hash="p1"
        )
        s2 = await pool.acquire(
            skill_id="A", user_id="u1", work_dir=tmp_path, prompt_hash="p2"
        )
        assert s1 is not s2
        assert s1._closed is True
        assert s2.prompt_hash == "p2"

    @pytest.mark.asyncio
    async def test_pool_recreates_when_config_dir_changes(
        self, monkeypatch, fake_session_class, tmp_path
    ):
        from app.coding_agent.session_pool import SessionPool
        monkeypatch.setattr(
            "app.coding_agent.session_pool.SubprocessSession", fake_session_class
        )
        pool = SessionPool()
        cfg1 = tmp_path / "cfg1"
        cfg2 = tmp_path / "cfg2"
        cfg1.mkdir()
        cfg2.mkdir()
        s1 = await pool.acquire(
            skill_id="A", user_id="u1", work_dir=tmp_path, config_dir=cfg1
        )
        s2 = await pool.acquire(
            skill_id="A", user_id="u1", work_dir=tmp_path, config_dir=cfg2
        )
        assert s1 is not s2
        assert s2.config_dir.resolve() == cfg2.resolve()


class TestMcpConfig:
    """A-Phase: MCP server 配置校验 + JSON 构造测试。"""

    def test_validate_stdio_server(self):
        from app.coding_agent.mcp_config import validate_server_config

        cfg = validate_server_config({
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {"FOO": "bar"},
        })
        assert cfg["type"] == "stdio"
        assert cfg["command"] == "npx"
        assert cfg["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        assert cfg["env"] == {"FOO": "bar"}
        assert cfg["enabled"] is True  # 默认 True

    def test_validate_http_server(self):
        from app.coding_agent.mcp_config import validate_server_config

        cfg = validate_server_config({
            "type": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer xxx"},
            "enabled": False,
        })
        assert cfg["type"] == "http"
        assert cfg["url"] == "https://example.com/mcp"
        assert cfg["headers"] == {"Authorization": "Bearer xxx"}
        assert cfg["enabled"] is False

    def test_validate_default_transport_stdio(self):
        from app.coding_agent.mcp_config import validate_server_config

        cfg = validate_server_config({"command": "echo"})
        assert cfg["type"] == "stdio"

    def test_validate_rejects_unknown_transport(self):
        from app.coding_agent.mcp_config import McpConfigError, validate_server_config

        with pytest.raises(McpConfigError):
            validate_server_config({"type": "ws", "url": "ws://x"})

    def test_validate_rejects_stdio_without_command(self):
        from app.coding_agent.mcp_config import McpConfigError, validate_server_config

        with pytest.raises(McpConfigError):
            validate_server_config({"type": "stdio", "args": ["x"]})

    def test_validate_rejects_http_without_url(self):
        from app.coding_agent.mcp_config import McpConfigError, validate_server_config

        with pytest.raises(McpConfigError):
            validate_server_config({"type": "http"})

    def test_validate_rejects_http_invalid_url(self):
        from app.coding_agent.mcp_config import McpConfigError, validate_server_config

        with pytest.raises(McpConfigError):
            validate_server_config({"type": "http", "url": "not-a-url"})

    def test_validate_server_name(self):
        from app.coding_agent.mcp_config import McpConfigError, validate_server_name

        # 合法
        validate_server_name("filesystem")
        validate_server_name("github_api_v2")
        validate_server_name("a")
        # 非法
        with pytest.raises(McpConfigError):
            validate_server_name("UPPER")
        with pytest.raises(McpConfigError):
            validate_server_name("0starts-with-digit")
        with pytest.raises(McpConfigError):
            validate_server_name("has space")
        with pytest.raises(McpConfigError):
            validate_server_name("a" * 60)
        with pytest.raises(McpConfigError):
            validate_server_name("")

    def test_build_json_filters_disabled(self):
        from app.coding_agent.mcp_config import build_mcp_config_json
        import json

        json_str = build_mcp_config_json({
            "fs": {"enabled": True, "type": "stdio", "command": "fs"},
            "gh": {"enabled": False, "type": "http", "url": "https://x"},
        })
        assert json_str is not None
        data = json.loads(json_str)
        assert "fs" in data["mcpServers"]
        assert "gh" not in data["mcpServers"]
        # enabled 字段不应进入 aiclawcode 看到的 json
        assert "enabled" not in data["mcpServers"]["fs"]

    def test_build_json_returns_none_when_all_disabled(self):
        from app.coding_agent.mcp_config import build_mcp_config_json

        result = build_mcp_config_json({
            "fs": {"enabled": False, "type": "stdio", "command": "fs"},
        })
        assert result is None

    def test_build_json_returns_none_for_empty(self):
        from app.coding_agent.mcp_config import build_mcp_config_json

        assert build_mcp_config_json({}) is None


class TestSystemPromptBuilder:
    """B-Phase: 验证 build_system_prompt 引导内容包含关键约束。"""

    def test_build_system_prompt_includes_work_dir(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt

        prompt = build_system_prompt(tmp_path)
        assert str(tmp_path) in prompt

    def test_build_system_prompt_includes_context_pack_key_guidance(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt

        (tmp_path / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")
        (tmp_path / "contract.json").write_text(
            '{"input":{"type":"object"},"output":{"type":"object"},"endpoint":"/api/demo"}',
            encoding="utf-8",
        )
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "main.py").write_text(
            '"""main entry point for real collection"""\n',
            encoding="utf-8",
        )
        (tmp_path / "policy.yaml").write_text(
            "permissions:\n  network: true\n  files: read\n",
            encoding="utf-8",
        )
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        (fixtures_dir / "sample_input.json").write_text('{"demo": true}', encoding="utf-8")
        logs_dir = tmp_path / "runs"
        logs_dir.mkdir()
        (logs_dir / "latest.log").write_text("failed once\n", encoding="utf-8")

        prompt = build_system_prompt(tmp_path)
        assert "AI 助手资料包 / context pack" in prompt
        assert "scripts/main.py" in prompt
        assert "contract.json" in prompt
        assert "fixtures/sample_input.json" in prompt
        assert "fixtures/sample_input.json` 只用于测试" in prompt
        assert "不要猜一堆 endpoint" in prompt
        assert "最近失败/运行日志入口" in prompt

    def test_build_system_prompt_enforces_read_first(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt

        prompt = build_system_prompt(tmp_path)
        # 改为预读注入模式：不再要求 "Read SKILL.md"，而是 "已预读注入"
        assert "SKILL.md" in prompt
        assert "预读" in prompt or "不读就答" in prompt

    def test_build_system_prompt_recommends_grep_for_counting(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt

        prompt = build_system_prompt(tmp_path)
        assert "grep -c" in prompt
        assert "肉眼数" in prompt or "模型计数能力" in prompt

    def test_build_system_prompt_says_token_unlimited(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt

        prompt = build_system_prompt(tmp_path)
        assert "token" in prompt.lower()
        assert "准确性优先" in prompt or "不怕浪费" in prompt

    def test_build_system_prompt_includes_browser_and_todo_sections(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt

        prompt = build_system_prompt(tmp_path)
        assert "browser_capture_apis" in prompt
        assert "todo_build_dispatch" in prompt

    def test_build_system_prompt_with_hash(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt_with_hash

        prompt, prompt_hash = build_system_prompt_with_hash(tmp_path)
        assert str(tmp_path) in prompt
        assert isinstance(prompt_hash, str)
        assert len(prompt_hash) == 12

    @pytest.mark.asyncio
    async def test_builtin_mcp_servers_include_browser_and_internal(self):
        from app.coding_agent.session_service import SessionService

        servers = await SessionService._build_builtin_mcp_servers("skill-a", "user-a")
        assert {"browser", "tmall", "yuyidata", "skillforge_internal"} <= set(servers)
        assert servers["browser"]["args"][0].endswith("browser_mcp_server.py")
        assert servers["tmall"]["args"][0].endswith("tmall_mcp_server.py")
        assert servers["yuyidata"]["args"][0].endswith("yuyidata_mcp_server.py")
        assert servers["skillforge_internal"]["args"][0].endswith("skillforge_mcp_server.py")

    @pytest.mark.asyncio
    async def test_creation_mcp_selection_preserves_platform_policy(self):
        from app.coding_agent.session_service import SessionService
        from app.coding_agent.mcp_config import build_mcp_config_json

        builtin = await SessionService._build_builtin_mcp_servers("skill-a", "user-a")
        servers = {**builtin, "remote_search": {"enabled": True, "type": "http", "url": "https://example.com/mcp"}}
        selected = SessionService._select_creation_mcp_servers(servers, builtin)
        data = json.loads(build_mcp_config_json(selected))
        assert set(data["mcpServers"]) == set(builtin)
        assert "remote_search" not in data["mcpServers"]

    def test_review_mode_changes_prompt_hash(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt_with_hash, _short_hash

        prompt, prompt_hash = build_system_prompt_with_hash(tmp_path, mode="edit")
        review_prompt, review_hash = build_system_prompt_with_hash(tmp_path, mode="review")
        assert review_hash != prompt_hash
        assert "审批模式" in review_prompt

    def test_explain_mode_changes_prompt_hash(self, tmp_path):
        from app.coding_agent.session_service import build_system_prompt_with_hash

        prompt, prompt_hash = build_system_prompt_with_hash(tmp_path, mode="edit")
        explain_prompt, explain_hash = build_system_prompt_with_hash(tmp_path, mode="explain")
        assert explain_hash != prompt_hash
        assert "讲解模式" in explain_prompt

    def test_runtime_isolation_builds_scoped_config_dir(self, tmp_path, monkeypatch):
        from app.coding_agent.session_service import SessionService
        from app.config import settings

        monkeypatch.setattr(settings, "CODING_AGENT_CONFIG_BASE_DIR", str(tmp_path / "base"))
        env, cfg = SessionService._inject_runtime_isolation(
            {"ANTHROPIC_API_KEY": "x"},
            user_id="u/1",
            session_scope="skill_A",
        )
        assert env["CLAUDE_CONFIG_DIR"] == str(cfg)
        assert cfg.exists()
        assert "u_1" in str(cfg)
        assert "skill_A" in str(cfg)

    @pytest.mark.asyncio
    async def test_build_subprocess_env_enables_bare_mode(self, monkeypatch):
        from app.coding_agent.session_service import SessionService

        async def fake_cfg():
            return {
                "coding_agent.provider": "",
                "coding_agent.api_base": "https://example.com",
                "coding_agent.api_key": "sk-test",
                "coding_agent.model": "glm-5",
                "coding_agent.bare_mode": True,
            }

        monkeypatch.setattr("app.common.ai.get_coding_agent_config", fake_cfg)
        env, provider, model = await SessionService._build_subprocess_env_provider_model()
        assert env["CLAUDE_CODE_SIMPLE"] == "1"
        assert env["ANTHROPIC_BASE_URL"] == "https://example.com"
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert model == "glm-5"
        assert provider is None

    @pytest.mark.asyncio
    async def test_build_subprocess_env_can_disable_bare_mode(self, monkeypatch):
        from app.coding_agent.session_service import SessionService

        async def fake_cfg():
            return {
                "coding_agent.provider": "glm",
                "coding_agent.api_base": "",
                "coding_agent.api_key": "glm-key",
                "coding_agent.model": "glm-5",
                "coding_agent.bare_mode": False,
            }

        monkeypatch.setattr("app.common.ai.get_coding_agent_config", fake_cfg)
        env, provider, _model = await SessionService._build_subprocess_env_provider_model()
        assert "CLAUDE_CODE_SIMPLE" not in env
        assert env["GLM_API_KEY"] == "glm-key"
        assert provider == "glm"

    @pytest.mark.asyncio
    async def test_build_subprocess_env_always_suppresses_telemetry(self, monkeypatch):
        """所有 provider 配置下都必须注入遥测关闭变量"""
        from app.coding_agent.session_service import SessionService

        for provider_name in ["", "glm", "tencent", "kimi"]:
            async def fake_cfg(p=provider_name):
                return {
                    "coding_agent.provider": p,
                    "coding_agent.api_base": "https://example.com",
                    "coding_agent.api_key": "key",
                    "coding_agent.model": "model",
                    "coding_agent.bare_mode": p == "",
                }

            monkeypatch.setattr("app.common.ai.get_coding_agent_config", fake_cfg)
            env, _, _ = await SessionService._build_subprocess_env_provider_model()
            assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1", f"provider={provider_name}"
            assert env.get("DISABLE_TELEMETRY") == "1", f"provider={provider_name}"

    def test_preread_reads_skill_md_and_policy_pack(self, tmp_path):
        """预读正常文件"""
        from app.coding_agent.session_service import _read_key_file_contents

        (tmp_path / "SKILL.md").write_text("# 测试 Skill\n功能说明", encoding="utf-8")
        (tmp_path / "policy_pack.yaml").write_text("roi: 1.5\nmode: strict", encoding="utf-8")
        result = _read_key_file_contents(tmp_path)
        assert "# 测试 Skill" in result
        assert "roi: 1.5" in result
        assert "预读" in result

    def test_preread_empty_on_missing_files(self, tmp_path):
        """缺文件时返回空"""
        from app.coding_agent.session_service import _read_key_file_contents

        result = _read_key_file_contents(tmp_path)
        assert result == ""

    def test_preread_truncates_large_files(self, tmp_path):
        """超 15KB 截断"""
        from app.coding_agent.session_service import _read_key_file_contents, _PREREAD_MAX_BYTES

        (tmp_path / "SKILL.md").write_text("x" * 20_000, encoding="utf-8")
        result = _read_key_file_contents(tmp_path)
        assert "已截断" in result
        # 截断后总长度 < 原始 20KB
        assert len(result) < 20_000

    def test_preread_nonexistent_dir(self, tmp_path):
        """不存在目录返回空"""
        from app.coding_agent.session_service import _read_key_file_contents

        result = _read_key_file_contents(tmp_path / "nonexistent")
        assert result == ""

    def test_preread_rejects_symlink(self, tmp_path):
        """符号链接文件不预读（防越界）"""
        import os
        from app.coding_agent.session_service import _read_key_file_contents

        secret = tmp_path / "secret.txt"
        secret.write_text("SENSITIVE DATA", encoding="utf-8")
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        os.symlink(str(secret), str(skill_dir / "SKILL.md"))
        result = _read_key_file_contents(skill_dir)
        assert "SENSITIVE DATA" not in result

    def test_preread_hash_changes_on_content_change(self, tmp_path):
        """预读内容变化时 prompt hash 也变"""
        from app.coding_agent.session_service import build_system_prompt_with_hash

        (tmp_path / "SKILL.md").write_text("version 1", encoding="utf-8")
        _, hash1 = build_system_prompt_with_hash(tmp_path)

        (tmp_path / "SKILL.md").write_text("version 2", encoding="utf-8")
        _, hash2 = build_system_prompt_with_hash(tmp_path)

        assert hash1 != hash2

    def test_preread_injected_into_system_prompt(self, tmp_path):
        """预读内容出现在 system prompt 中"""
        from app.coding_agent.session_service import build_system_prompt

        (tmp_path / "SKILL.md").write_text("# Unique-Marker-12345\npurpose: 测试", encoding="utf-8")
        prompt = build_system_prompt(tmp_path)
        assert "Unique-Marker-12345" in prompt


# ─────────────────────────────────────────────────────────
# subprocess_session 错误路径（不需要真 Node）
# ─────────────────────────────────────────────────────────

class TestSubprocessSessionErrors:
    @pytest.mark.asyncio
    async def test_start_skill_dir_not_found(self, tmp_path):
        from app.coding_agent.subprocess_session import SubprocessSession
        from app.common.exceptions import AppError

        bogus = tmp_path / "does_not_exist"
        sess = SubprocessSession(skill_id="x", user_id="y", work_dir=bogus)
        with pytest.raises(AppError) as ei:
            await sess.start()
        assert ei.value.code == CodingAgentErrorCode.SKILL_DIR_NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_old_environment_cannot_launch_runtime(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock
        from app.coding_agent.subprocess_session import SubprocessSession
        from app.common.exceptions import AppError
        from app.config import settings
        from app.coding_agent.runtime_availability import RUNTIME_UNAVAILABLE

        monkeypatch.setattr(settings, "CODING_AGENT_ENABLED", True)
        monkeypatch.setenv("CODING_AGENT_DIST_PATH", str(tmp_path / "cli.js"))
        spawn = AsyncMock()
        monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
        sess = SubprocessSession(skill_id="x", user_id="y", work_dir=tmp_path)
        with pytest.raises(AppError) as ei:
            await sess.start()
        assert ei.value.code == RUNTIME_UNAVAILABLE
        assert ei.value.status == 503
        spawn.assert_not_called()
        assert sess.proc is None

    @pytest.mark.asyncio
    async def test_send_user_message_rejects_unavailable_windows_path(self, monkeypatch, tmp_path):
        from app.coding_agent.subprocess_session import SubprocessSession
        from app.common.exceptions import AppError

        monkeypatch.setattr("app.coding_agent.local_path_guard._current_platform", lambda: "linux")
        monkeypatch.setattr("app.coding_agent.local_path_guard.Path.exists", lambda _path: False)
        sess = SubprocessSession(skill_id="x", user_id="y", work_dir=tmp_path)

        with pytest.raises(AppError) as ei:
            await sess.send_user_message(
                r'用户要求检查 "C:\Users\skillforge\Documents\Default Project\golutra-master" 中的 LICENSE'
            )

        assert ei.value.code == CodingAgentErrorCode.LOCAL_PATH_UNAVAILABLE.value
        assert ei.value.status == 409
        assert ei.value.detail["mapped_path"] == "/mnt/c/Users/skillforge/Documents/Default Project/golutra-master"

class TestCodingAgentRouter:
    @pytest.mark.asyncio
    async def test_config_status_includes_bare_mode(self, monkeypatch):
        from app.coding_agent.router import config_status

        async def fake_cfg():
            return {
                "coding_agent.provider": "glm",
                "coding_agent.api_base": "https://example.com",
                "coding_agent.api_key": "secret-1234",
                "coding_agent.model": "glm-5",
                "coding_agent.max_tokens": 8192,
                "coding_agent.permission_strategy": "balanced",
                "coding_agent.bare_mode": True,
                "coding_agent.enabled": True,
            }

        monkeypatch.setattr("app.coding_agent.router.get_coding_agent_config", fake_cfg)
        user = type("U", (), {"id": "admin"})()
        result = await config_status(current_user=user)
        assert result["provider"] == "glm"
        assert result["bare_mode"] is True
        assert result["api_key_set"] is True
        assert result["enabled"] is False
        assert result["requested_enabled"] is True
        assert result["runtime"]["state"] == "adapter_pending"
        assert "secret-1234" not in str(result)

    @pytest.mark.asyncio
    async def test_test_connection_does_not_read_credentials_or_spawn(self, monkeypatch):
        from unittest.mock import AsyncMock
        from app.coding_agent.router import test_connection, test_mcp_server
        from app.coding_agent.runtime_availability import RUNTIME_UNAVAILABLE

        credentials = AsyncMock(side_effect=AssertionError("must not read credentials"))
        spawn = AsyncMock(side_effect=AssertionError("must not launch a process"))
        monkeypatch.setattr("app.coding_agent.router.get_coding_agent_config", credentials)
        monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
        user = type("U", (), {"id": "admin"})()
        result = await test_connection(current_user=user)
        assert result.ok is False
        assert result.error_code == RUNTIME_UNAVAILABLE
        assert result.duration_ms == 0
        mcp_result = await test_mcp_server("example", current_user=user)
        assert mcp_result.ok is False
        credentials.assert_not_called()
        spawn.assert_not_called()
