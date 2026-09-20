"""H8 回归：_notify_reload_all_instances 必须并发执行。

修复前：
  对 N 个 active 实例串行 await client.post，每个 60s 超时 →
  approve_review 链路阻塞 N×60s（5 实例 ≈ 5min），不可接受。

修复后：
  asyncio.gather 并发；单实例失败不阻塞其他实例；失败入审计日志。
  本测试用 mock httpx 让每个实例 sleep 1s，验证：
    1. 5 实例并发耗时 < 3s（≪ 5s 串行下界）
    2. 单实例 raise 不影响其他实例完成
    3. 失败时调一次 audit.log
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_instances(n: int):
    """生成 n 个 mock OpenClawInstance。"""
    instances = []
    for i in range(n):
        inst = MagicMock()
        inst.id = f"openclaw-{i}"
        inst.reload_hook_url = f"http://gw-{i}.test"
        inst.reload_token = f"tok-{i}"
        instances.append(inst)
    return instances


def test_build_reload_endpoint_url_rejects_empty_and_path_only():
    from app.execution.sync_service import build_reload_endpoint_url

    assert build_reload_endpoint_url("")[1] == "INVALID_RELOAD_HOOK_URL: empty"
    assert build_reload_endpoint_url("/reload")[1] == "INVALID_RELOAD_HOOK_URL: missing host"
    assert build_reload_endpoint_url("host:9000") == ("http://host:9000/reload", None)
    assert build_reload_endpoint_url("http://host:9000/reload") == ("http://host:9000/reload", None)
    assert build_reload_endpoint_url("http://host:9000/base/reload") == ("http://host:9000/base/reload", None)


def test_reload_auth_headers_omits_empty_token():
    from app.execution.sync_service import _reload_auth_headers

    assert _reload_auth_headers("") == {}
    assert _reload_auth_headers("   ") == {}
    assert _reload_auth_headers(None) == {}
    assert _reload_auth_headers("tok") == {"Authorization": "Bearer tok"}


class _FakeAsyncCtx:
    """模拟 httpx.AsyncClient 的 async ctx；post 用 client_callable 控制。"""
    def __init__(self, post_callable):
        self._post = post_callable

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, tb):
        return False

    async def post(self, url, headers=None):
        return await self._post(url, headers=headers)


@pytest.mark.asyncio
async def test_notify_reload_all_instances_runs_concurrently():
    """5 实例 × 1s 假延迟 → 总耗时应 < 3s（并发证据，串行下界 5s）。"""
    from app.execution.sync_service import sync_service

    instances = _make_instances(5)

    # 每次请求 sleep 1s 后返回 ok
    async def slow_post(url, headers=None):
        await asyncio.sleep(1.0)
        resp = MagicMock()
        resp.json = MagicMock(return_value={"ok": True})
        resp.content = b'{"ok":true}'
        return resp

    # 假 session 仅返回 instances 列表
    fake_session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = instances
    fake_session.execute = AsyncMock(return_value=exec_result)

    class _FakeSessionCtx:
        async def __aenter__(self): return fake_session
        async def __aexit__(self, *a): return False

    with patch("app.database.async_session_factory", return_value=_FakeSessionCtx()):
        with patch("httpx.AsyncClient", return_value=_FakeAsyncCtx(slow_post)):
            t0 = time.monotonic()
            results = await sync_service._notify_reload_all_instances()
            elapsed = time.monotonic() - t0

    assert len(results) == 5, "应返回 5 实例的结果"
    assert all(r["ok"] for r in results), "所有实例都该成功"
    assert elapsed < 3.0, f"5 实例并发应 < 3s，实测 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_notify_reload_partial_failure_does_not_block_others():
    """1 实例 raise，其他 4 实例仍完成；失败实例触发 audit.log。"""
    from app.execution.sync_service import sync_service

    instances = _make_instances(5)

    async def post_with_first_failing(url, headers=None):
        await asyncio.sleep(0.2)
        if "gw-0" in url:
            raise RuntimeError("simulated network error")
        resp = MagicMock()
        resp.json = MagicMock(return_value={"ok": True})
        resp.content = b'{"ok":true}'
        return resp

    fake_session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = instances
    fake_session.execute = AsyncMock(return_value=exec_result)

    class _FakeSessionCtx:
        async def __aenter__(self): return fake_session
        async def __aexit__(self, *a): return False

    audit_calls: list[tuple] = []

    async def fake_audit_log(user_id, action, target_type=None, target_id=None, detail=None, ip_address=None, prompt_hash=None):
        audit_calls.append((user_id, action, target_type, target_id, detail))

    with patch("app.database.async_session_factory", return_value=_FakeSessionCtx()):
        with patch("httpx.AsyncClient", return_value=_FakeAsyncCtx(post_with_first_failing)):
            with patch("app.common.audit.audit.log", new=AsyncMock(side_effect=fake_audit_log)):
                results = await sync_service._notify_reload_all_instances()

    assert len(results) == 5, "失败的实例也要在结果列表里"
    failed = [r for r in results if not r["ok"]]
    succeeded = [r for r in results if r["ok"]]
    assert len(failed) == 1
    assert len(succeeded) == 4
    assert failed[0]["instance"] == "openclaw-0"
    assert "error" in failed[0]

    # 至少调了一次 audit.log（针对 openclaw-0 的失败）
    matched = [c for c in audit_calls if c[1] == "execution.reload_failed" and c[3] == "openclaw-0"]
    assert len(matched) == 1, f"openclaw-0 失败应触发一次 audit，实际 audit_calls={audit_calls}"


@pytest.mark.asyncio
async def test_notify_reload_cancelled_error_coerced_to_failure():
    """Wave 2 H3：协程抛 CancelledError（BaseException 子类）不应让整批 raise。

    return_exceptions=True + 后处理，把 BaseException 映射成 {ok: False, error: ...}。
    """
    from app.execution.sync_service import sync_service

    instances = _make_instances(3)

    async def post_with_cancel_on_second(url, headers=None):
        await asyncio.sleep(0.05)
        if "gw-1" in url:
            raise asyncio.CancelledError("simulated cancel")
        resp = MagicMock()
        resp.json = MagicMock(return_value={"ok": True})
        resp.content = b'{"ok":true}'
        return resp

    fake_session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = instances
    fake_session.execute = AsyncMock(return_value=exec_result)

    class _FakeSessionCtx:
        async def __aenter__(self): return fake_session
        async def __aexit__(self, *a): return False

    with patch("app.database.async_session_factory", return_value=_FakeSessionCtx()):
        with patch("httpx.AsyncClient", return_value=_FakeAsyncCtx(post_with_cancel_on_second)):
            # 不应 raise CancelledError；应返回 3 个结果，其中 gw-1 标记 failed
            results = await sync_service._notify_reload_all_instances()

    assert len(results) == 3, "CancelledError 不应让 gather 丢失结果"
    cancelled = [r for r in results if r["instance"] == "openclaw-1"]
    assert len(cancelled) == 1
    assert cancelled[0]["ok"] is False
    assert "CancelledError" in cancelled[0]["error"]


@pytest.mark.asyncio
async def test_notify_reload_no_active_instances_returns_empty():
    """无 active 实例 → 空列表，不抛异常，不发 HTTP。"""
    from app.execution.sync_service import sync_service

    fake_session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    fake_session.execute = AsyncMock(return_value=exec_result)

    class _FakeSessionCtx:
        async def __aenter__(self): return fake_session
        async def __aexit__(self, *a): return False

    with patch("app.database.async_session_factory", return_value=_FakeSessionCtx()):
        results = await sync_service._notify_reload_all_instances()
    assert results == []


@pytest.mark.asyncio
async def test_notify_reload_invalid_hook_does_not_call_http_client():
    """空 reload_hook_url 应直接报配置错误，不能拼成 http:///reload 交给 httpx。"""
    from app.execution.sync_service import sync_service

    inst = MagicMock()
    inst.id = "node-empty-hook"
    inst.reload_hook_url = ""
    inst.reload_token = ""

    fake_session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [inst]
    fake_session.execute = AsyncMock(return_value=exec_result)

    class _FakeSessionCtx:
        async def __aenter__(self): return fake_session
        async def __aexit__(self, *a): return False

    with patch("app.database.async_session_factory", return_value=_FakeSessionCtx()):
        with patch("httpx.AsyncClient") as http_client:
            with patch("app.common.audit.audit.log", new=AsyncMock()):
                results = await sync_service._notify_reload_all_instances()

    assert results == [{
        "instance": "node-empty-hook",
        "ok": False,
        "error": "INVALID_RELOAD_HOOK_URL: empty",
    }]
    http_client.assert_not_called()


@pytest.mark.asyncio
async def test_notify_reload_empty_token_omits_authorization_header():
    from app.execution.sync_service import sync_service

    inst = MagicMock()
    inst.id = "node-empty-token"
    inst.reload_hook_url = "http://reload.test"
    inst.reload_token = ""

    captured_headers = []

    async def fake_post(url, headers=None):
        captured_headers.append(headers)
        resp = MagicMock()
        resp.json = MagicMock(return_value={"ok": True})
        resp.content = b'{"ok":true}'
        return resp

    fake_session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [inst]
    fake_session.execute = AsyncMock(return_value=exec_result)

    class _FakeSessionCtx:
        async def __aenter__(self): return fake_session
        async def __aexit__(self, *a): return False

    with patch("app.database.async_session_factory", return_value=_FakeSessionCtx()):
        with patch("httpx.AsyncClient", return_value=_FakeAsyncCtx(fake_post)):
            results = await sync_service._notify_reload_all_instances()

    assert results == [{"instance": "node-empty-token", "ok": True}]
    assert captured_headers == [{}]
