"""scripts/skillforge_mcp_server.py (stdio MCP server) 的协议/并发/超时测试。

覆盖 2026-04-20 从同步 stdin 循环改成 asyncio 后的关键不变式:
    1. JSON-RPC 2.0 协议: initialize / tools/list / ping / unknown method
    2. tools/call 可并发 (subprocess 实测)
    3. tools/call 单个 timeout / 异常 → 返回 isError=True, 不拖死 server
    4. _effective_timeout 对 skill_run_script 动态放宽
    5. 原有的工具单元覆盖 (skill_list_scripts / todo_build_dispatch)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "skillforge_mcp_server.py"


def _load_skillforge_mcp_server():
    spec = importlib.util.spec_from_file_location("skillforge_mcp_server", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["skillforge_mcp_server"] = module
    spec.loader.exec_module(module)
    return module


# ─────────────────────────────────────────────────────
# subprocess-based 协议 / 并发 测试
# ─────────────────────────────────────────────────────

def _spawn_server(skill_id: str = "neosight-analyzer") -> subprocess.Popen:
    env = os.environ.copy()
    env["SKILLFORGE_SKILL_ID"] = skill_id
    env["SKILLFORGE_USER_ID"] = "test"
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )


def _rpc(p: subprocess.Popen, method: str, params: dict | None = None, req_id: int = 1) -> None:
    line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
    p.stdin.write(line + "\n")
    p.stdin.flush()


def _read_response(p: subprocess.Popen, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("no response from MCP server within timeout")
        r, _, _ = select.select([p.stdout], [], [], remaining)
        if not r:
            continue
        line = p.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout unexpectedly")
        return json.loads(line)


def _close(p: subprocess.Popen) -> None:
    try:
        p.stdin.close()
    except Exception:
        pass
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()


def test_initialize_returns_protocol_handshake():
    p = _spawn_server()
    try:
        _rpc(p, "initialize", req_id=1)
        resp = _read_response(p)
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"]
        assert result["serverInfo"]["name"] == "skillforge"
        assert "tools" in result["capabilities"]
    finally:
        _close(p)


def test_tools_list_returns_expected_tools():
    p = _spawn_server()
    try:
        _rpc(p, "tools/list", req_id=2)
        resp = _read_response(p)
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        tool_names = set(tools)
        assert "skill_get_manifest" in tool_names
        assert "skill_list_scripts" in tool_names
        assert "skill_run_script" in tool_names
        assert "skillforge_agent_coverage" in tool_names
        assert "skillforge_ai_analyze" in tool_names
        ai_schema = tools["skillforge_ai_analyze"]["inputSchema"]
        assert ai_schema["type"] == "object"
        assert "context_pack" in ai_schema["properties"]
        for keyword in ("oneOf", "anyOf", "allOf", "enum", "not"):
            assert keyword not in ai_schema
        assert "skillforge_raw_data_query" in tool_names
        assert "skillforge_run_analyze" in tool_names
        assert "skillforge_sf_data_write" in tool_names
        assert "skillforge_sf_data_list" in tool_names
        assert "skillforge_sf_data_get" in tool_names
        assert "todo_build_dispatch" in tool_names
    finally:
        _close(p)


def test_ping_returns_empty_result():
    p = _spawn_server()
    try:
        _rpc(p, "ping", req_id=3)
        resp = _read_response(p)
        assert resp["result"] == {}
    finally:
        _close(p)


def test_unknown_method_returns_json_rpc_error():
    p = _spawn_server()
    try:
        _rpc(p, "no_such_method", req_id=4)
        resp = _read_response(p)
        assert resp["error"]["code"] == -32601
        assert "no_such_method" in resp["error"]["message"]
    finally:
        _close(p)


def test_two_tools_call_both_return():
    """并发验证: 连发两个 tools/call 不等第一个响应, 两个都要回来。"""
    p = _spawn_server()
    try:
        _rpc(p, "initialize", req_id=0)
        _read_response(p)
        _rpc(p, "tools/call", {"name": "skill_list_scripts", "arguments": {}}, req_id=10)
        _rpc(p, "tools/call", {"name": "skill_list_scripts", "arguments": {}}, req_id=11)
        resps = [_read_response(p) for _ in range(2)]
        ids = sorted(r["id"] for r in resps)
        assert ids == [10, 11]
        for r in resps:
            assert "result" in r
            assert r["result"]["content"][0]["type"] == "text"
    finally:
        _close(p)


# ─────────────────────────────────────────────────────
# in-process 超时 / 异常路径 (monkey-patch handle_tool_call)
# ─────────────────────────────────────────────────────

async def test_slow_tool_triggers_timeout_isError(monkeypatch, capsys):
    mod = _load_skillforge_mcp_server()

    async def slow_tool(name, args):
        await asyncio.sleep(5)
        return "should not reach here"

    monkeypatch.setattr(mod, "handle_tool_call", slow_tool)
    monkeypatch.setattr(mod, "TOOL_CALL_DEFAULT_TIMEOUT_SEC", 0.2)

    await mod._handle_tool_call_request(42, {"name": "slow_mock", "arguments": {}})

    out_lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert out_lines
    resp = json.loads(out_lines[-1])
    assert resp["id"] == 42
    assert resp["result"]["isError"] is True
    assert "超时" in resp["result"]["content"][0]["text"]


async def test_exception_in_tool_returns_isError(monkeypatch, capsys):
    mod = _load_skillforge_mcp_server()

    async def raising_tool(name, args):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "handle_tool_call", raising_tool)
    await mod._handle_tool_call_request(99, {"name": "boom", "arguments": {}})

    out_lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    resp = json.loads(out_lines[-1])
    assert resp["id"] == 99
    assert resp["result"]["isError"] is True
    assert "boom" in resp["result"]["content"][0]["text"]


# ─────────────────────────────────────────────────────
# _effective_timeout
# ─────────────────────────────────────────────────────

def test_effective_timeout_default():
    mod = _load_skillforge_mcp_server()
    assert mod._effective_timeout("skill_get_manifest", {}) == mod.TOOL_CALL_DEFAULT_TIMEOUT_SEC
    assert mod._effective_timeout("skill_list_scripts", {}) == mod.TOOL_CALL_DEFAULT_TIMEOUT_SEC


def test_effective_timeout_run_script_adds_buffer():
    mod = _load_skillforge_mcp_server()
    # 用户传 120s subprocess timeout → 外层至少 130s 容得下它 + 余量
    assert mod._effective_timeout("skill_run_script", {"timeout": 120}) >= 130
    # 用户没传 → 沿用默认
    assert mod._effective_timeout("skill_run_script", {}) == mod.TOOL_CALL_DEFAULT_TIMEOUT_SEC


# ─────────────────────────────────────────────────────
# 原有业务工具覆盖 (handle_tool_call 现在是 async, 用 pytest-asyncio auto mode)
# ─────────────────────────────────────────────────────

async def test_skill_list_scripts_tool(monkeypatch):
    mod = _load_skillforge_mcp_server()
    monkeypatch.setenv("SKILLFORGE_SKILL_ID", "EC-投放-01")
    monkeypatch.setattr(
        mod.skill_service, "list_scripts",
        lambda skill_id: [{"path": "scripts/main.py"}],
    )
    text = await mod.handle_tool_call("skill_list_scripts", {})
    assert "scripts/main.py" in text


async def test_skill_read_module_accepts_file_path(monkeypatch):
    mod = _load_skillforge_mcp_server()
    monkeypatch.setenv("SKILLFORGE_SKILL_ID", "EC-投放-01")
    monkeypatch.setattr(
        mod.git_service,
        "read_file",
        lambda skill_id, path, errors="strict": "print('ok')\n"
        if path == "scripts/main.py"
        else None,
    )

    text = await mod.handle_tool_call("skill_read_module", {"module": "scripts/main.py"})
    payload = json.loads(text)
    assert payload["path"] == "scripts/main.py"
    assert payload["content"] == "print('ok')\n"
    assert payload["num_lines"] == 1


async def test_todo_build_dispatch_tool(monkeypatch):
    mod = _load_skillforge_mcp_server()
    monkeypatch.setenv("SKILLFORGE_SKILL_ID", "EC-投放-01")
    text = await mod.handle_tool_call(
        "todo_build_dispatch",
        {
            "title": "整改任务",
            "tasks": [{"executor": "alice", "content": "处理 SKU-1"}],
            "reviewers": ["mgr1"],
        },
    )
    assert '"kind": "dispatch"' in text
    assert '"executor": "alice"' in text


async def test_skillforge_internal_platform_ai_tool_dispatches_and_sanitizes(monkeypatch):
    mod = _load_skillforge_mcp_server()

    monkeypatch.setenv("SKILLFORGE_SKILL_ID", "mcp-ai-skill")
    captured = {}

    async def fake_run_platform_mcp_tool(tool_name, skill_id, arguments):
        captured["tool_name"] = tool_name
        captured["skill_id"] = skill_id
        captured["arguments"] = arguments
        return {
            "ok": True,
            "model": mod.codex_service.PLATFORM_AI_MCP_MODEL,
            "credential_location": "platform_only",
            "output": "平台 AI 分析完成",
        }

    monkeypatch.setattr(mod, "_run_platform_mcp_tool", fake_run_platform_mcp_tool)

    sanitized = mod._sanitize_platform_ai_arguments({
        "prompt": "分析当前 Skill",
        "context_pack": {"token": "secret-token-value", "items": [1, 2]},
    })
    assert sanitized["context_pack"]["token"] == "[REDACTED]"

    text = await mod.handle_tool_call(
        "skillforge_ai_analyze",
        {
            "prompt": "分析当前 Skill",
            "context_pack": {"token": "secret-token-value", "items": [1, 2]},
        },
    )
    payload = json.loads(text)
    assert payload["model"] == mod.codex_service.PLATFORM_AI_MCP_MODEL
    assert payload["credential_location"] == "platform_only"
    assert payload["output"] == "平台 AI 分析完成"
    assert captured["tool_name"] == "skillforge_ai_analyze"
    assert captured["skill_id"] == "mcp-ai-skill"
    assert captured["arguments"]["context_pack"]["token"] == "secret-token-value"


async def test_skillforge_internal_agent_coverage_tool_dispatches(monkeypatch):
    mod = _load_skillforge_mcp_server()

    monkeypatch.setenv("SKILLFORGE_SKILL_ID", "mcp-agent-coverage-skill")
    captured = {}

    async def fake_run_platform_mcp_tool(tool_name, skill_id, arguments):
        captured["tool_name"] = tool_name
        captured["skill_id"] = skill_id
        captured["arguments"] = arguments
        return {
            "ok": True,
            "items": [{
                "department": "EC",
                "status": "fallback",
                "capabilities": {
                    "skill_runtime": {"ready": True, "online": 1, "count": 1},
                    "analysis": {"ready": False, "fallback_ready": True, "fallback_count": 1},
                    "training": {"ready": False, "fallback_ready": True, "fallback_count": 1},
                },
            }],
            "summary": {"ready": 0, "fallback": 1, "missing": 0},
            "scope": {"payload_redacted": True},
        }

    monkeypatch.setattr(mod, "_run_platform_mcp_tool", fake_run_platform_mcp_tool)

    text = await mod.handle_tool_call(
        "skillforge_agent_coverage",
        {"department": "EC", "include_agents": False},
    )
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["items"][0]["department"] == "EC"
    assert payload["summary"]["fallback"] == 1
    assert captured == {
        "tool_name": "skillforge_agent_coverage",
        "skill_id": "mcp-agent-coverage-skill",
        "arguments": {"department": "EC", "include_agents": False},
    }


async def test_skillforge_internal_raw_data_query_tool_dispatches(monkeypatch):
    mod = _load_skillforge_mcp_server()

    monkeypatch.setenv("SKILLFORGE_SKILL_ID", "mcp-raw-skill")
    captured = {}

    async def fake_run_platform_mcp_tool(tool_name, skill_id, arguments):
        captured["tool_name"] = tool_name
        captured["skill_id"] = skill_id
        captured["arguments"] = arguments
        return {
            "ok": True,
            "source": "decision_logs",
            "count": 1,
            "items": [{
                "input_snapshot": {"query": "原始输入", "authorization": "[REDACTED]"},
                "output_result": {"summary": "原始输出"},
            }],
            "scope": {"payload_redacted": True},
        }

    monkeypatch.setattr(mod, "_run_platform_mcp_tool", fake_run_platform_mcp_tool)

    text = await mod.handle_tool_call(
        "skillforge_raw_data_query",
        {"source": "decision_logs", "run_id": "mcp-raw-run-1", "limit": 5},
    )
    payload = json.loads(text)
    assert payload["source"] == "decision_logs"
    assert payload["count"] >= 1
    item = payload["items"][0]
    assert item["input_snapshot"]["query"] == "原始输入"
    assert item["input_snapshot"]["authorization"] == "[REDACTED]"
    assert captured["tool_name"] == "skillforge_raw_data_query"
    assert captured["skill_id"] == "mcp-raw-skill"
    assert captured["arguments"]["run_id"] == "mcp-raw-run-1"


@pytest.mark.asyncio
async def test_build_builtin_mcp_servers():
    from app.coding_agent.session_service import SessionService

    servers = await SessionService._build_builtin_mcp_servers("SK-1", "u1")
    assert "skillforge_internal" in servers
    assert "tmall" in servers
    assert "yuyidata" in servers
    cfg = servers["skillforge_internal"]
    assert cfg["type"] == "stdio"
    assert cfg["env"]["SKILLFORGE_SKILL_ID"] == "SK-1"
    assert cfg["env"]["SKILLFORGE_USER_ID"] == "u1"
