"""cookie 心跳后台任务 (background_jobs.cookie_heartbeat_loop) 单元测试。"""
import asyncio

import pytest

from app.bootstrap import background_jobs


class _StopLoop(Exception):
    """用来在第一次 sleep(interval) 时打断循环。"""


@pytest.mark.asyncio
async def test_heartbeat_disabled(monkeypatch):
    """BROWSER_HEARTBEAT_ENABLED=False → 立即返回，不调 verify_login。"""
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_ENABLED", False, raising=False,
    )
    called = {"verify": 0}

    async def fake_verify(*args, **kwargs):
        called["verify"] += 1
        return {}

    monkeypatch.setattr(
        "app.browser.service.verify_login", fake_verify, raising=False,
    )

    await background_jobs.cookie_heartbeat_loop()
    assert called["verify"] == 0


@pytest.mark.asyncio
async def test_heartbeat_calls_verify_login_for_each_platform(monkeypatch):
    """跑一轮：调 verify_login 1 次（PLATFORM_LOGIN_PROBE 当前只有 sycm）。"""
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_ENABLED", True, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_INTERVAL", 60, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_FIRST_DELAY", 0, raising=False,
    )

    captured = []

    async def fake_verify(db, *, source_id, user_id="heartbeat"):
        captured.append((source_id, user_id))
        return {"source_id": source_id, "verified": True, "status": "valid"}

    monkeypatch.setattr(
        "app.browser.service.verify_login", fake_verify, raising=False,
    )

    # 用 fake session_factory 避免连真 DB
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.database.async_session_factory",
        lambda: _FakeSession(),
        raising=True,
    )

    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if seconds == 60:  # 第二段 sleep（interval）→ 让循环退出
            raise _StopLoop()
        await real_sleep(0)

    monkeypatch.setattr(background_jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        await background_jobs.cookie_heartbeat_loop()

    # 至少跑了一轮 verify
    source_ids = [c[0] for c in captured]
    assert "platform-sycm" in source_ids
    assert all(c[1] == "heartbeat" for c in captured)


@pytest.mark.asyncio
async def test_heartbeat_swallows_per_platform_errors(monkeypatch):
    """单平台异常不应阻塞其它平台 / 下一轮。"""
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_ENABLED", True, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_INTERVAL", 60, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "BROWSER_HEARTBEAT_FIRST_DELAY", 0, raising=False,
    )

    # 多挂一个平台让我们观察"上一个炸了下一个还跑"
    monkeypatch.setattr(
        "app.browser.service.PLATFORM_LOGIN_PROBE",
        {"platform-sycm": ("sycm", "check_login"), "platform-fake": ("generic", "x")},
        raising=False,
    )

    seen = []

    async def fake_verify(db, *, source_id, user_id="heartbeat"):
        seen.append(source_id)
        if source_id == "platform-sycm":
            raise RuntimeError("boom")
        return {"source_id": source_id, "verified": True}

    monkeypatch.setattr(
        "app.browser.service.verify_login", fake_verify, raising=False,
    )

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.database.async_session_factory",
        lambda: _FakeSession(),
        raising=True,
    )

    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        if seconds == 60:
            raise _StopLoop()
        await real_sleep(0)

    monkeypatch.setattr(background_jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        await background_jobs.cookie_heartbeat_loop()

    # 两个平台都被尝试过（即使第一个抛了异常）
    assert "platform-sycm" in seen
    assert "platform-fake" in seen


@pytest.mark.asyncio
async def test_training_collect_due_loop_disabled(monkeypatch):
    monkeypatch.setattr(
        background_jobs.settings, "TRAINING_COLLECT_DUE_ENABLED", False, raising=False,
    )

    await background_jobs.training_collect_due_loop()


@pytest.mark.asyncio
async def test_training_collect_due_loop_runs_one_scan(monkeypatch):
    monkeypatch.setattr(
        background_jobs.settings, "TRAINING_COLLECT_DUE_ENABLED", True, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "TRAINING_COLLECT_DUE_INTERVAL", 60, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "TRAINING_COLLECT_DUE_STALE_SECONDS", 120, raising=False,
    )
    monkeypatch.setattr(
        background_jobs.settings, "TRAINING_COLLECT_DUE_MAX_JOBS", 3, raising=False,
    )

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.database.async_session_factory",
        lambda: _FakeSession(),
        raising=True,
    )

    calls = []

    async def fake_collect(db, user, *, stale_seconds, max_jobs):
        calls.append({
            "db": db,
            "user_id": user.id,
            "role": user.role,
            "can_view_all": user.can_view_all,
            "stale_seconds": stale_seconds,
            "max_jobs": max_jobs,
        })
        return {"items": [], "total": 0, "stats": {"collected": 0, "skipped": 0, "failed": 0}}

    monkeypatch.setattr(
        "app.training.service.collect_due_training_job_results",
        fake_collect,
        raising=True,
    )

    real_sleep = asyncio.sleep
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise _StopLoop()
        await real_sleep(0)

    monkeypatch.setattr(background_jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        await background_jobs.training_collect_due_loop()

    assert sleeps == [60, 60]
    assert len(calls) == 1
    assert isinstance(calls[0]["db"], _FakeSession)
    assert calls[0]["user_id"] == "training_reconciler"
    assert calls[0]["role"] == "system_admin"
    assert calls[0]["can_view_all"] is True
    assert calls[0]["stale_seconds"] == 120
    assert calls[0]["max_jobs"] == 3
