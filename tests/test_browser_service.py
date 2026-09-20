import pytest
from sqlalchemy.dialects import postgresql

from app.browser.collector_base import BrowserRetryableError


@pytest.mark.asyncio
async def test_fetch_json_uses_generic_collector(monkeypatch):
    from app.browser import service

    class FakeCollector:
        async def run(self, ws_url, task_type, params):
            assert ws_url == "ws://example"
            assert task_type == "extract"
            assert "fetch(" in params["js"]
            return {"type": "js_result", "data": {"status": 200, "data": {"ok": True}}}

    async def fake_check(_cdp_url):
        return True

    async def fake_ws(_cdp_url):
        return "ws://example"

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "_get_cdp_ws_url", fake_ws)
    monkeypatch.setattr("app.browser.collector_base.get_collector", lambda platform: FakeCollector())

    result = await service.fetch_json(url="https://api.example.com/data")
    assert result["success"] is True
    assert result["data"]["status"] == 200
    assert result["data"]["data"] == {"ok": True}
    assert result["proof"]["response_hash"]
    assert result["proof"]["api_url"] == "https://api.example.com/data"


@pytest.mark.asyncio
async def test_fetch_json_returns_error_when_browser_down(monkeypatch):
    from app.browser import service

    async def fake_check(_cdp_url):
        return False

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    result = await service.fetch_json(url="https://api.example.com/data")
    assert result["success"] is False
    assert result["error"] == "浏览器未运行"


@pytest.mark.asyncio
async def test_fetch_json_rejects_missing_response_shape(monkeypatch):
    from app.browser import service

    class FakeCollector:
        async def run(self, ws_url, task_type, params):
            return {"type": "js_result", "data": {}}

    async def fake_check(_cdp_url):
        return True

    async def fake_ws(_cdp_url):
        return "ws://example"

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "_get_cdp_ws_url", fake_ws)
    monkeypatch.setattr("app.browser.collector_base.get_collector", lambda platform: FakeCollector())

    result = await service.fetch_json(url="https://api.example.com/data")

    assert result["success"] is False
    assert result["error"] == "浏览器 fetch_json 未返回响应状态"
    assert result["proof"]["api_url"] == "https://api.example.com/data"


# ─────────────── ws_url 缓存 ───────────────


@pytest.mark.asyncio
async def test_get_cdp_ws_url_cache_hit(monkeypatch):
    from app.browser import service

    calls = {"n": 0}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            calls["n"] += 1
            return FakeResp([
                {"type": "page", "webSocketDebuggerUrl": "ws://example/page"},
            ])

    monkeypatch.setattr(service.httpx, "AsyncClient", FakeClient)
    service._WS_URL_CACHE.clear()

    url1 = await service._get_cdp_ws_url("http://localhost:9222")
    url2 = await service._get_cdp_ws_url("http://localhost:9222")
    assert url1 == url2 == "ws://example/page"
    assert calls["n"] == 1, "第二次应命中缓存，不发 HTTP 请求"

    # 强制刷新会绕过缓存
    await service._get_cdp_ws_url("http://localhost:9222", force_refresh=True)
    assert calls["n"] == 2


# ─────────────── 自动重试 ───────────────


@pytest.mark.asyncio
async def test_run_collection_retries_on_transient_error(monkeypatch):
    """瞬态 BrowserRetryableError → 应被重试，最终成功。"""
    from app.browser import service

    attempts = {"n": 0}

    class FlakeCollector:
        async def run(self, ws_url, task_type, params):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise BrowserRetryableError("CDP transient")
            return {"ok": True, "platform": "test", "task_type": task_type}

    async def fake_check(_url):
        return True

    async def fake_ws(_url, force_refresh=False):
        return "ws://ok"

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "_get_cdp_ws_url", fake_ws)
    monkeypatch.setattr(
        "app.browser.collector_base.get_collector",
        lambda platform: FlakeCollector(),
    )
    # 让重试不真等
    monkeypatch.setattr("asyncio.sleep", lambda *_: _noop())

    db = _FakeDb()
    result = await service.run_collection(
        db, platform="generic", task_type="x", params={}, user_id="u",
    )
    assert result["status"] == "success"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_run_collection_no_retry_on_validation_error(monkeypatch):
    """schema 校验失败不应被重试，且 error_kind=validation。"""
    from app.browser import service
    from app.browser.schemas import CollectionValidationError

    attempts = {"n": 0}

    class BadSchemaCollector:
        async def run(self, ws_url, task_type, params):
            attempts["n"] += 1
            raise CollectionValidationError("sycm", "dashboard", "metrics: 不能为空")

    async def fake_check(_url):
        return True

    async def fake_ws(_url, force_refresh=False):
        return "ws://ok"

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "_get_cdp_ws_url", fake_ws)
    monkeypatch.setattr(
        "app.browser.collector_base.get_collector",
        lambda platform: BadSchemaCollector(),
    )
    monkeypatch.setattr("asyncio.sleep", lambda *_: _noop())

    db = _FakeDb()
    result = await service.run_collection(
        db, platform="sycm", task_type="dashboard", params={}, user_id="u",
    )
    assert result["status"] == "failed"
    assert result["error_kind"] == "validation"
    assert attempts["n"] == 1, "validation 错误不应重试"


# ─────────────── verify_login ───────────────


@pytest.mark.asyncio
async def test_verify_login_unsupported_source():
    from app.browser import service

    db = _FakeDb()
    result = await service.verify_login(db, source_id="platform-unknown")
    assert result["status"] == "unsupported"
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_verify_login_browser_down(monkeypatch):
    from app.browser import service

    async def fake_check(_url):
        return False

    monkeypatch.setattr(service, "_check_cdp", fake_check)

    captured = {}

    async def fake_write(db, source_id, *, status, detail=""):
        captured["status"] = status
        captured["detail"] = detail

    monkeypatch.setattr(service, "_write_verify_status", fake_write)

    db = _FakeDb()
    result = await service.verify_login(db, source_id="platform-sycm")
    assert result["status"] == "unknown"
    assert result["verified"] is False
    assert captured["status"] == "unknown"


@pytest.mark.asyncio
async def test_verify_login_success_writes_valid_status(monkeypatch):
    from app.browser import service

    async def fake_check(_url):
        return True

    async def fake_run(db, **kwargs):
        return {
            "status": "success",
            "result": {
                "logged_in": True,
                "current_url": "https://sycm.taobao.com/portal/home.htm",
                "detail": "业务 API 200",
            },
        }

    captured = {}

    async def fake_write(db, source_id, *, status, detail=""):
        captured["source_id"] = source_id
        captured["status"] = status
        captured["detail"] = detail

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "run_collection", fake_run)
    monkeypatch.setattr(service, "_write_verify_status", fake_write)

    db = _FakeDb()
    result = await service.verify_login(db, source_id="platform-sycm")
    assert result["verified"] is True
    assert result["status"] == "valid"
    assert captured["status"] == "valid"
    assert captured["source_id"] == "platform-sycm"


@pytest.mark.asyncio
async def test_verify_login_direct_probe_short_circuits_browser(monkeypatch):
    from app.browser import service

    async def fake_direct(db, source_id):
        assert source_id == "platform-sycm"
        return {
            "source_id": source_id,
            "verified": True,
            "status": "valid",
            "detail": "商品排行 API 200 code=0",
            "cdp_role": "direct_cookie",
        }

    async def fail_check(*args, **kwargs):
        raise AssertionError("direct source verification should not touch Chrome")

    monkeypatch.setattr(service, "_direct_verify_source_cookie", fake_direct)
    monkeypatch.setattr(service, "_check_cdp", fail_check)

    result = await service.verify_login(_FakeDb(), source_id="platform-sycm")
    assert result["verified"] is True
    assert result["status"] == "valid"
    assert result["cdp_role"] == "direct_cookie"


def test_cookie_pool_runtime_order_prefers_healthy_pool():
    from app.browser import service
    from app.datasources.models import PlatformCookiePool

    stmt = (
        PlatformCookiePool.__table__.select()
        .order_by(*service._cookie_pool_runtime_order())
        .limit(1)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "platform_cookie_pool.health_score DESC" in sql
    assert sql.index("platform_cookie_pool.priority DESC") < sql.index("platform_cookie_pool.health_score DESC")
    assert sql.index("platform_cookie_pool.health_score DESC") < sql.index("platform_cookie_pool.last_used_at ASC NULLS FIRST")


@pytest.mark.asyncio
async def test_inject_stored_cookies_keeps_selected_connector_key(monkeypatch):
    from app.browser import service
    from app.datasources import service as datasource_service

    class Pool:
        platform = "taobao"
        shop_id = "default"
        owner_user_id = "owner-1"
        connector_key_id = "connector-key-1"

    class Result:
        def __init__(self, row):
            self.row = row

        def scalar_one_or_none(self):
            return self.row

    class FakeDb:
        def __init__(self):
            self.rows = iter([Pool(), None, None, None, None, None])

        async def execute(self, _stmt):
            return Result(next(self.rows))

    calls = []

    async def fake_check(_url):
        return True

    async def fake_browser_ws(_url):
        return "ws://browser"

    async def fake_details(_db, source_id, **kwargs):
        if kwargs:
            calls.append(("details", source_id, kwargs))
        return []

    async def fake_cookies(_db, source_id, **kwargs):
        if kwargs:
            calls.append(("cookies", source_id, kwargs))
        return None

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "_get_browser_ws_url", fake_browser_ws)
    monkeypatch.setattr(datasource_service, "get_cookie_details", fake_details)
    monkeypatch.setattr(datasource_service, "get_cookies", fake_cookies)

    result = await service.inject_stored_cookies(FakeDb(), "http://browser")

    assert result["platform-taobao"] == "无 cookies"
    assert [call[0] for call in calls] == ["details", "cookies"]
    for _, source_id, kwargs in calls:
        assert source_id == "platform-taobao"
        assert kwargs == {
            "platform": "taobao",
            "shop_id": "default",
            "owner_user_id": "owner-1",
            "connector_key_id": "connector-key-1",
        }


@pytest.mark.asyncio
async def test_verify_login_heartbeat_skips_when_verify_browser_busy(monkeypatch):
    from app.browser import service

    async def fake_select_verify_cdp_url():
        return "http://verify-browser", "verify"

    async def fake_check(_url):
        return True

    async def fail_run(*args, **kwargs):
        raise AssertionError("heartbeat should skip instead of running check_login")

    async def fail_write(*args, **kwargs):
        raise AssertionError("busy heartbeat skip should not write verify status")

    monkeypatch.setattr(service, "select_verify_cdp_url", fake_select_verify_cdp_url)
    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "run_collection", fail_run)
    monkeypatch.setattr(service, "_write_verify_status", fail_write)

    db = _FakeDb()
    async with service._VERIFY_BROWSER_USE_LOCK:
        result = await service.verify_login(db, source_id="platform-sycm", user_id="heartbeat")

    assert result["status"] == "unknown"
    assert result["detail"] == "SKIPPED_BROWSER_BUSY"
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_verify_login_verify_browser_clears_then_injects_best_effort(monkeypatch):
    from app.browser import service

    calls = []

    async def fake_select_verify_cdp_url():
        return "http://verify-browser", "verify"

    async def fake_check(_url):
        return True

    async def fake_clear(cdp_url):
        calls.append(("clear", cdp_url))

    async def fake_inject(db, cdp_url):
        calls.append(("inject", cdp_url))
        raise RuntimeError("no stored cookies")

    async def fake_run(db, **kwargs):
        calls.append(("run", kwargs["cdp_url"], kwargs["force_new_page"]))
        return {
            "status": "success",
            "result": {
                "logged_in": False,
                "status": "unknown",
                "detail": "业务 API 200 但业务码无结论",
            },
        }

    async def fake_write(db, source_id, *, status, detail=""):
        calls.append(("write", source_id, status, detail))

    monkeypatch.setattr(service, "select_verify_cdp_url", fake_select_verify_cdp_url)
    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "clear_browser_cookies", fake_clear)
    monkeypatch.setattr(service, "inject_stored_cookies", fake_inject)
    monkeypatch.setattr(service, "run_collection", fake_run)
    monkeypatch.setattr(service, "_write_verify_status", fake_write)

    db = _FakeDb()
    result = await service.verify_login(db, source_id="platform-sycm", user_id="manual")

    assert result["status"] == "unknown"
    assert result["cdp_role"] == "verify"
    assert calls[:3] == [
        ("clear", "http://verify-browser"),
        ("inject", "http://verify-browser"),
        ("run", "http://verify-browser", True),
    ]
    assert calls[3][0:3] == ("write", "platform-sycm", "unknown")


@pytest.mark.asyncio
async def test_run_collection_force_new_page_closes_temporary_target(monkeypatch):
    from app.browser import service

    calls = []

    class FakeCollector:
        async def run(self, ws_url, task_type, params):
            calls.append(("run", ws_url, task_type, params))
            return {"ok": True}

    async def fake_check(_url):
        return True

    async def fake_create(cdp_url):
        calls.append(("create", cdp_url))
        return "ws://temporary-page", "target-1"

    async def fake_close(cdp_url, target_id):
        calls.append(("close", cdp_url, target_id))

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "_create_page_target", fake_create)
    monkeypatch.setattr(service, "_close_target", fake_close)
    monkeypatch.setattr(
        "app.browser.collector_base.get_collector",
        lambda platform: FakeCollector(),
    )

    db = _FakeDb()
    result = await service.run_collection(
        db,
        platform="generic",
        task_type="check_login",
        params={"x": 1},
        user_id="u",
        cdp_url="http://verify-browser",
        force_new_page=True,
    )

    assert result["status"] == "success"
    assert calls == [
        ("create", "http://verify-browser"),
        ("run", "ws://temporary-page", "check_login", {"x": 1}),
        ("close", "http://verify-browser", "target-1"),
    ]


# ─────────────── run_discover ───────────────


@pytest.mark.asyncio
async def test_run_discover_sycm_path(monkeypatch):
    """sycm 平台走专属 discover 任务。"""
    from app.browser import service

    captured = {}

    async def fake_run_collection(db, *, platform, task_type, params, user_id):
        captured["platform"] = platform
        captured["task_type"] = task_type
        captured["params"] = params
        return {
            "status": "success",
            "task_id": 99,
            "result": {
                "discovered_apis": 7,
                "domain": "sycm.taobao.com",
                "page_path": "/portal/home.htm",
                "page_url": "https://sycm.taobao.com/portal/home.htm",
            },
        }

    monkeypatch.setattr(service, "run_collection", fake_run_collection)

    db = _FakeDb()
    result = await service.run_discover(
        db,
        url="https://sycm.taobao.com/portal/home.htm",
        platform="sycm",
        user_id="admin",
    )
    assert result["discovered_apis"] == 7
    assert result["domain"] == "sycm.taobao.com"
    assert result["task_id"] == 99
    assert captured["platform"] == "sycm"
    assert captured["task_type"] == "discover"


@pytest.mark.asyncio
async def test_run_discover_generic_path_propagates_error(monkeypatch):
    """generic 路径下 capture_apis 失败应原样返回错误。"""
    from app.browser import service

    async def fake_run_collection(db, *, platform, task_type, **kwargs):
        return {"status": "failed", "error": "浏览器未运行", "task_id": 5}

    monkeypatch.setattr(service, "run_collection", fake_run_collection)

    db = _FakeDb()
    result = await service.run_discover(
        db, url="https://example.com/dashboard", platform="generic",
    )
    assert result["discovered_apis"] == 0
    assert result["error"] == "浏览器未运行"


@pytest.mark.asyncio
async def test_replay_platform_api_updates_registry_meta(monkeypatch):
    from app.browser import service
    from app.browser.models import PlatformApiCache

    row = PlatformApiCache(
        id=42,
        domain="example.com",
        page_path="/dashboard",
        apis_json={
            "data_apis": [
                {"url": "https://api.example.com/data", "method": "GET"},
            ],
            "_registry_meta": {"page_url": "https://example.com/dashboard"},
        },
        api_count=1,
    )

    async def fake_fetch_json(**kwargs):
        assert kwargs["url"] == "https://api.example.com/data"
        return {
            "success": True,
            "data": {"url": kwargs["url"], "status": 200, "ok": True, "data": {"items": [1, 2]}},
            "proof": {
                "verified_at": "2026-04-21T00:00:00",
                "api_url": kwargs["url"],
                "page_url": kwargs["page_url"],
                "response_hash": "abc123",
                "data_keys": ["items"],
                "row_count": None,
                "status": 200,
                "ok": True,
            },
        }

    monkeypatch.setattr(service, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(service, "guard_discover_url", lambda url, **kwargs: url)

    db = _FakeDb({42: row})
    result = await service.replay_platform_api(db, registry_id=42, api_index=0)

    assert result["success"] is True
    assert result["proof"]["verification_status"] == "verified"
    assert row.apis_json["_registry_meta"]["last_verified_at"] == "2026-04-21T00:00:00"
    assert row.apis_json["_registry_meta"]["verification_status"] == "verified"
    assert row.apis_json["data_apis"][0]["last_replay_proof"]["response_hash"] == "abc123"


@pytest.mark.asyncio
async def test_replay_platform_api_marks_failed(monkeypatch):
    from app.browser import service
    from app.browser.models import PlatformApiCache

    row = PlatformApiCache(
        id=43,
        domain="example.com",
        page_path="/dashboard",
        apis_json={"data_apis": [{"url": "https://api.example.com/data"}]},
        api_count=1,
    )

    async def fake_fetch_json(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(service, "guard_discover_url", lambda url, **kwargs: url)

    db = _FakeDb({43: row})
    result = await service.replay_platform_api(db, registry_id=43, api_index=0)

    assert result["success"] is False
    assert row.apis_json["_registry_meta"]["verification_status"] == "failed"
    assert row.apis_json["_registry_meta"]["last_verified_at"]


@pytest.mark.asyncio
async def test_verify_login_expired_writes_expired_status(monkeypatch):
    from app.browser import service

    async def fake_check(_url):
        return True

    async def fake_run(db, **kwargs):
        return {
            "status": "success",
            "result": {
                "logged_in": False,
                "current_url": "https://login.taobao.com/passport",
                "detail": "业务 API 401",
            },
        }

    captured = {}

    async def fake_write(db, source_id, *, status, detail=""):
        captured["status"] = status

    monkeypatch.setattr(service, "_check_cdp", fake_check)
    monkeypatch.setattr(service, "run_collection", fake_run)
    monkeypatch.setattr(service, "_write_verify_status", fake_write)

    db = _FakeDb()
    result = await service.verify_login(db, source_id="platform-sycm")
    assert result["verified"] is False
    assert result["status"] == "expired"
    assert captured["status"] == "expired"


# ─────────────── helpers ───────────────


async def _noop():
    return None


class _FakeDb:
    """最小可用的 AsyncSession 替身：只支持 add/commit/refresh，不真连 PG。"""

    def __init__(self, rows=None):
        self.added = []
        self.rows = rows or {}

    def add(self, obj):
        # 模拟 PK 自增
        if not hasattr(obj, "id") or obj.id is None:
            obj.id = len(self.added) + 1
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def rollback(self):
        return None

    async def get(self, model, item_id):
        return self.rows.get(item_id)
