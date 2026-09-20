"""数据源管理模块测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


async def _enable_legacy_connector_grace(hours: int = 1) -> None:
    from datetime import timedelta

    from app.common.models import SystemConfig
    from app.common.time_utils import isoformat_bjt, now_bjt
    from app.database import async_session_factory

    async with async_session_factory() as session:
        value = isoformat_bjt(now_bjt() + timedelta(hours=hours))
        row = await session.get(SystemConfig, "connector_keys.legacy_grace_until")
        if row:
            row.value = value
        else:
            session.add(SystemConfig(key="connector_keys.legacy_grace_until", value=value))
        await session.commit()


# ===== 创建数据源 =====


@pytest.mark.asyncio
async def test_create_csv_source():
    """测试创建CSV类型数据源"""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.datasources.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.datasources.service import create_source
        result = await create_source(
            mock_db,
            source_id="ds-csv-01",
            name="投放数据",
            department="电商",
            source_type="csv_upload",
            config={},
            quality_rules={"required_columns": ["日期", "ROI"]},
            user_id="admin",
        )

    assert result["source_id"] == "ds-csv-01"
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_api_source():
    """测试创建API拉取类型数据源"""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.datasources.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.datasources.service import create_source
        result = await create_source(
            mock_db,
            source_id="ds-api-01",
            name="生意参谋数据",
            department="电商",
            source_type="api_pull",
            config={
                "api_url": "https://api.example.com/data",
                "auth_type": "bearer",
                "auth_token": "test_token",
                "params": {"date": "{{yesterday}}"},
            },
            user_id="admin",
        )

    assert result["source_id"] == "ds-api-01"


# ===== CSV上传（质量校验通过） =====


@pytest.mark.asyncio
async def test_upload_csv_pass(tmp_path, monkeypatch):
    """测试上传CSV——质量校验通过"""
    from app.datasources.models import DataSource

    monkeypatch.chdir(tmp_path)

    mock_source = MagicMock(spec=DataSource)
    mock_source.id = "ds-csv-01"
    mock_source.quality_rules = {"required_columns": ["日期", "ROI"]}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source

    # 模拟无历史记录（prev_row_count=None）
    mock_prev_result = MagicMock()
    mock_prev_result.scalar_one_or_none.return_value = None

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_result  # 数据源查询
        return mock_prev_result  # 上次行数查询

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    csv_content = "日期,ROI\n2026-03-01,1.5\n2026-03-02,2.0\n".encode("utf-8")

    with patch("app.datasources.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.datasources.service import upload_csv
        result = await upload_csv(
            mock_db,
            source_id="ds-csv-01",
            file_content=csv_content,
            file_name="投放数据.csv",
            user_id="admin",
        )

    assert result["status"] == "success"
    assert result["row_count"] == 2
    assert "日期" in result["columns"]
    assert "ROI" in result["columns"]
    assert result["quality_report"]["has_errors"] is False


# ===== CSV上传（质量校验失败：缺少必填字段） =====


@pytest.mark.asyncio
async def test_upload_csv_missing_required_fields(tmp_path, monkeypatch):
    """测试上传CSV——缺少必填列应报错"""
    from app.datasources.models import DataSource

    monkeypatch.chdir(tmp_path)

    mock_source = MagicMock(spec=DataSource)
    mock_source.id = "ds-csv-02"
    mock_source.quality_rules = {"required_columns": ["日期", "ROI", "消耗"]}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source

    mock_prev_result = MagicMock()
    mock_prev_result.scalar_one_or_none.return_value = None

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_result
        return mock_prev_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    # CSV缺少"消耗"列
    csv_content = "日期,ROI\n2026-03-01,1.5\n".encode("utf-8")

    with patch("app.datasources.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.datasources.service import upload_csv
        result = await upload_csv(
            mock_db,
            source_id="ds-csv-02",
            file_content=csv_content,
            file_name="缺列.csv",
            user_id="admin",
        )

    assert result["status"] == "failed"
    assert result["quality_report"]["has_errors"] is True
    # 检查错误信息包含缺失列
    checks = result["quality_report"]["checks"]
    error_msgs = [c["detail"] for c in checks if c["status"] == "error"]
    assert any("消耗" in msg for msg in error_msgs)


# ===== CSV上传——空文件 =====


@pytest.mark.asyncio
async def test_upload_csv_empty():
    """测试上传空CSV——应报错"""
    from app.common.exceptions import AppError
    from app.datasources.models import DataSource

    mock_source = MagicMock(spec=DataSource)
    mock_source.id = "ds-csv-03"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.datasources.service import upload_csv
        await upload_csv(mock_db, "ds-csv-03", b"", "empty.csv", "admin")

    assert exc_info.value.code == "DATASOURCE_UPLOAD_EMPTY"


# ===== API拉取（mock httpx） =====


@pytest.mark.asyncio
async def test_pull_api_source_success():
    """测试API拉取——成功获取数据"""
    from app.datasources.models import DataSource

    mock_source = MagicMock(spec=DataSource)
    mock_source.id = "ds-api-01"
    mock_source.source_type = "api_pull"
    mock_source.config = {
        "api_url": "https://api.example.com/data",
        "auth_type": "bearer",
        "auth_token": "test",
        "params": {},
        "json_paths": ["$.data[*]"],
    }
    mock_source.quality_rules = {}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source

    mock_prev_result = MagicMock()
    mock_prev_result.scalar_one_or_none.return_value = None

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_result
        return mock_prev_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"name": "计划A", "roi": 1.5},
            {"name": "计划B", "roi": 0.8},
        ]
    }

    with patch("app.datasources.service.httpx.AsyncClient") as MockClient, \
         patch("app.datasources.service.audit") as mock_audit:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.get = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_instance
        mock_audit.log = AsyncMock()

        from app.datasources.service import pull_api_source
        result = await pull_api_source(mock_db, "ds-api-01", "admin")

    assert result["status"] == "success"
    assert result["row_count"] == 2


# ===== 路由集成：筛选 / 平台连接 / API 发现 =====


@pytest.mark.asyncio
async def test_list_sources_supports_filters_and_pagination(client):
    from app.database import async_session_factory
    from app.datasources.models import DataSource

    async with async_session_factory() as session:
        session.add_all([
            DataSource(
                id="ds-csv-1", name="CSV1", department="EC",
                source_type="csv_upload", config={}, is_active=True,
            ),
            DataSource(
                id="ds-api-1", name="API1", department="EC",
                source_type="api_pull", config={}, is_active=False,
            ),
            DataSource(
                id="ds-platform-1", name="平台1", department="EC",
                source_type="platform_cookies", config={"platform": "taobao"}, is_active=True,
            ),
        ])
        await session.commit()

    resp = await client.get("/api/data-sources/", params={
        "source_type": "api",
        "is_active": False,
        "page": 1,
        "page_size": 10,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == "ds-api-1"


@pytest.mark.asyncio
async def test_rotate_connector_api_key_returns_new_key(client):
    from app.common.exceptions import AppError
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.datasources import service

    first = await client.get("/api/data-sources/connector-api-key")
    assert first.status_code == 200
    key1 = first.json()["api_key"]

    second = await client.post("/api/data-sources/connector-api-key/rotate")
    assert second.status_code == 200
    key2 = second.json()["api_key"]

    assert key1 != key2
    async with async_session_factory() as session:
        assert await session.get(SystemConfig, "connector_keys.legacy_grace_until") is None
        with pytest.raises(AppError) as exc_info:
            await service.verify_connector_api_key(session, key2)
        assert exc_info.value.code == "LEGACY_BLOCKED"


@pytest.mark.asyncio
async def test_platform_disconnect_clears_cookies_but_keeps_platform(client):
    from app.database import async_session_factory
    from app.datasources.models import DataSource

    async with async_session_factory() as session:
        session.add(DataSource(
            id="platform-taobao",
            name="淘宝连接",
            department="EC",
            source_type="platform_cookies",
            config={
                "platform": "taobao",
                "connected": True,
                "encrypted_cookies": "secret",
                "encrypted_cookie_details": "detail-secret",
                "domain": ".taobao.com",
                "user_agent": "ua",
            },
            is_active=True,
        ))
        await session.commit()

    resp = await client.post("/api/data-sources/platform-taobao/disconnect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disconnected"] is True

    async with async_session_factory() as session:
        ds = await session.get(DataSource, "platform-taobao")
        assert ds.config["platform"] == "taobao"
        assert ds.config["connected"] is False
        assert "encrypted_cookies" not in ds.config
        assert "encrypted_cookie_details" not in ds.config
        assert "domain" not in ds.config


@pytest.mark.asyncio
async def test_push_cookies_auto_registers_known_platform_on_fresh_server(client):
    from app.database import async_session_factory
    from app.datasources.models import DataSource

    key_resp = await client.get("/api/data-sources/connector-api-key")
    api_key = key_resp.json()["api_key"]
    await _enable_legacy_connector_grace()

    with patch("app.datasources.service._schedule_cookie_sync_to_browser"):
        resp = await client.post(
            "/api/data-sources/platform-alimama/push-cookies",
            headers={"X-SF-API-Key": api_key},
            json={
                "cookies": "cookie_a=1; cookie_b=2",
                "domain": ".alimama.com",
                "user_agent": "pytest",
                "cookie_details": [
                    {
                        "name": "cookie_a",
                        "value": "1",
                        "domain": ".alimama.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                        "sameSite": "no_restriction",
                    },
                    {
                        "name": "cookie_b",
                        "value": "2",
                        "domain": ".taobao.com",
                        "path": "/",
                    },
                ],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with async_session_factory() as session:
        ds = await session.get(DataSource, "platform-alimama")
        assert ds is not None
        assert ds.source_type == "platform_cookies"
        assert ds.config["platform"] == "alimama"
        assert ds.config["connected"] is True
        assert ds.config["domain"] == ".alimama.com"
        from app.datasources.service import get_cookie_details
        details = await get_cookie_details(session, "platform-alimama")
        assert [d["name"] for d in details] == ["cookie_a", "cookie_b"]
        assert details[0]["domain"] == ".alimama.com"


@pytest.mark.asyncio
async def test_connector_key_cookie_pool_keeps_same_shop_separate_by_owner(client, monkeypatch):
    from app.database import async_session_factory
    from app.datasources.models import DataSource, PlatformCookiePool
    from app.datasources.service import get_cookies
    from sqlalchemy import select

    key_a = (await client.post("/api/data-sources/connector-keys", json={
        "name": "owner-a",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
        "platforms": ["taobao"],
    })).json()["api_key"]
    key_b = (await client.post("/api/data-sources/connector-keys", json={
        "name": "owner-b",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-b",
        "platforms": ["taobao"],
    })).json()["api_key"]

    resp_a = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": key_a},
        json={
            "cookies": "owner=a",
            "platform": "taobao",
            "shop_id": "shop-001",
            "account_login": "login-a",
            "domain": ".taobao.com",
        },
    )
    assert resp_a.status_code == 200
    resp_b = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": key_b},
        json={
            "cookies": "owner=b",
            "platform": "taobao",
            "shop_id": "shop-001",
            "account_login": "login-b",
            "domain": ".taobao.com",
        },
    )
    assert resp_b.status_code == 200

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(PlatformCookiePool).order_by(PlatformCookiePool.owner_user_id)
        )).scalars().all()
        assert [(r.owner_user_id, r.shop_id, r.account_login) for r in rows] == [
            ("owner-a", "shop-001", "login-a"),
            ("owner-b", "shop-001", "login-b"),
        ]
        ds = await session.get(DataSource, "platform-taobao")
        assert "encrypted_cookies" not in ds.config
        assert ds.config["connected"] is True
        assert ds.config["pushed_at"] == resp_b.json()["pushed_at"]
        assert ds.config["shop_id"] == "shop-001"
        assert await get_cookies(
            session,
            "platform-taobao",
            platform="taobao",
            shop_id="shop-001",
            owner_user_id="owner-a",
        ) == "owner=a"
        assert await get_cookies(
            session,
            "platform-taobao",
            platform="taobao",
            shop_id="shop-001",
            owner_user_id="owner-b",
        ) == "owner=b"

    summary = await client.get("/api/data-sources/cookie-pools")
    assert summary.status_code == 200
    assert summary.json()["items"][0]["active_count"] == 2
    detail = await client.get("/api/data-sources/cookie-pools/taobao/shop-001?include_audit=1")
    assert detail.status_code == 200
    assert len(detail.json()["credentials"]) == 2
    assert "encrypted_cookies" not in detail.json()["credentials"][0]
    credential_id = detail.json()["credentials"][0]["id"]
    monkeypatch.setitem(
        __import__("app.browser.service", fromlist=["PLATFORM_LOGIN_PROBE"]).PLATFORM_LOGIN_PROBE,
        "platform-taobao",
        ("taobao", "check_login"),
    )

    async def fake_inject_cookie_pool_to_browser(db, row, **kwargs):
        return {"injected": 1}

    async def fake_run_collection(db, **kwargs):
        return {
            "task_id": 123,
            "status": "success",
            "result": {"status": "valid", "logged_in": True, "detail": "pytest ok"},
        }

    monkeypatch.setattr(
        "app.browser.service.inject_cookie_pool_to_browser",
        fake_inject_cookie_pool_to_browser,
    )
    monkeypatch.setattr("app.browser.service.run_collection", fake_run_collection)
    verify = await client.post(f"/api/data-sources/cookie-pools/credentials/{credential_id}/verify")
    assert verify.status_code == 200
    disabled = await client.post(
        f"/api/data-sources/cookie-pools/credentials/{credential_id}/disable",
        json={"reason": "pytest manual disable"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False


@pytest.mark.asyncio
async def test_verify_cookie_pool_unknown_preserves_active_credential(client, monkeypatch):
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookiePool
    from sqlalchemy import select

    key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "owner-a",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
        "platforms": ["taobao"],
    })).json()["api_key"]
    push = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": key},
        json={
            "cookies": "owner=a",
            "platform": "taobao",
            "shop_id": "shop-001",
            "account_login": "login-a",
            "domain": ".taobao.com",
        },
    )
    assert push.status_code == 200

    async with async_session_factory() as session:
        row = (await session.execute(select(PlatformCookiePool))).scalar_one()
        row.verification_status = "active"
        row.health_score = 100
        row.last_error_code = None
        credential_id = str(row.id)
        await session.commit()

    monkeypatch.setitem(
        __import__("app.browser.service", fromlist=["PLATFORM_LOGIN_PROBE"]).PLATFORM_LOGIN_PROBE,
        "platform-taobao",
        ("taobao", "check_login"),
    )

    async def fake_select_verify_cdp_url():
        return "http://verify-browser", "verify"

    async def fake_inject_cookie_pool_to_browser(db, row, **kwargs):
        assert kwargs["cdp_url"] == "http://verify-browser"
        assert kwargs["clear_existing"] is True
        return {"injected": 1}

    async def fake_run_collection(db, **kwargs):
        assert kwargs["cdp_url"] == "http://verify-browser"
        assert kwargs["force_new_page"] is True
        return {
            "task_id": 123,
            "status": "success",
            "result": {
                "status": "unknown",
                "logged_in": False,
                "detail": "业务 API 200 但业务码无结论",
            },
        }

    monkeypatch.setattr("app.browser.service.select_verify_cdp_url", fake_select_verify_cdp_url)
    monkeypatch.setattr(
        "app.browser.service.inject_cookie_pool_to_browser",
        fake_inject_cookie_pool_to_browser,
    )
    monkeypatch.setattr("app.browser.service.run_collection", fake_run_collection)

    verify = await client.post(f"/api/data-sources/cookie-pools/credentials/{credential_id}/verify")
    assert verify.status_code == 200
    payload = verify.json()
    assert payload["status"] == "unknown"
    assert payload["verification_status"] == "active"
    assert payload["health_score"] == 100
    assert payload["last_error_code"] is None
    assert payload["capability_json"]["last_verify_unknown_detail"] == "业务 API 200 但业务码无结论"


@pytest.mark.asyncio
async def test_verify_sycm_cookie_pool_prefers_direct_business_probe(client, monkeypatch):
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookieAudit, PlatformCookiePool
    from sqlalchemy import select

    async def fake_sycm_direct(cookie_header, *, user_agent=None, timeout=8):
        assert "owner=a" in cookie_header
        return {
            "status": "valid",
            "logged_in": True,
            "detail": "商品排行 API 200 code=0",
            "probe": {"status": 200, "data": {"code": 0}},
        }

    async def fail_browser_verify(*args, **kwargs):
        raise AssertionError("valid SYCM credential should not use browser verification")

    monkeypatch.setattr("app.browser.collectors.sycm.verify_sycm_cookie_header", fake_sycm_direct)
    monkeypatch.setattr("app.browser.service.inject_cookie_pool_to_browser", fail_browser_verify)
    monkeypatch.setattr("app.browser.service.run_collection", fail_browser_verify)

    key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "owner-a-sycm",
        "source_id": "platform-sycm",
        "owner_user_id": "owner-a",
        "platforms": ["sycm"],
    })).json()["api_key"]
    push = await client.post(
        "/api/data-sources/platform-sycm/push-cookies",
        headers={"X-SF-API-Key": key},
        json={
            "cookies": "owner=a",
            "platform": "sycm",
            "shop_id": "default",
            "account_login": "login-a",
            "domain": ".sycm.taobao.com",
            "user_agent": "pytest",
        },
    )
    assert push.status_code == 200

    async with async_session_factory() as session:
        row = (await session.execute(select(PlatformCookiePool))).scalar_one()
        row.verification_status = "unknown"
        row.health_score = 100
        row.last_error_code = "业务 API 200 但业务码无结论"
        credential_id = str(row.id)
        await session.commit()

    verify = await client.post(f"/api/data-sources/cookie-pools/credentials/{credential_id}/verify")
    assert verify.status_code == 200
    payload = verify.json()
    assert payload["status"] == "valid"
    assert payload["verification_status"] == "active"
    assert payload["health_score"] == 100
    assert payload["last_error_code"] is None

    async with async_session_factory() as session:
        audit = (await session.execute(
            select(PlatformCookieAudit)
            .where(PlatformCookieAudit.cookie_pool_id == int(credential_id))
            .where(PlatformCookieAudit.action == "verify")
            .order_by(PlatformCookieAudit.id.desc())
            .limit(1)
        )).scalar_one()
        assert audit.detail["cdp_role"] == "direct_cookie"
        assert audit.detail["probe_status"] == "valid"


@pytest.mark.asyncio
async def test_new_connector_key_uses_key_context_without_shop_fields(client):
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookiePool
    from sqlalchemy import select

    key_resp = await client.post("/api/data-sources/connector-keys", json={
        "device_label": "张三 Chrome",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
    })
    assert key_resp.status_code == 200

    resp = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": key_resp.json()["api_key"]},
        json={"cookies": "a=1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "taobao"
    assert body["shop_id"] == "default"
    assert body["account_login"] is None

    async with async_session_factory() as session:
        row = (await session.execute(select(PlatformCookiePool))).scalar_one()
        assert row.shop_id == "default"
        assert row.device_label == "张三 Chrome"
        assert row.account_login is None


@pytest.mark.asyncio
async def test_same_owner_multiple_connector_keys_share_default_shop_pool(client):
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookiePool
    from sqlalchemy import select

    first = (await client.post("/api/data-sources/connector-keys", json={
        "device_label": "maintainer",
        "source_id": "platform-alimama",
        "owner_user_id": "admin",
        "platforms": ["alimama"],
    })).json()
    second = (await client.post("/api/data-sources/connector-keys", json={
        "device_label": "示例成员",
        "source_id": "platform-alimama",
        "owner_user_id": "admin",
        "platforms": ["alimama"],
    })).json()

    resp_a = await client.post(
        "/api/data-sources/platform-alimama/push-cookies",
        headers={"X-SF-API-Key": first["api_key"]},
        json={"cookies": "owner=maintainer", "platform": "alimama"},
    )
    resp_b = await client.post(
        "/api/data-sources/platform-alimama/push-cookies",
        headers={"X-SF-API-Key": second["api_key"]},
        json={"cookies": "owner=examplemember", "platform": "alimama"},
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["shop_id"] == "default"
    assert resp_b.json()["shop_id"] == "default"

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(PlatformCookiePool).order_by(PlatformCookiePool.device_label)
        )).scalars().all()
    assert [(row.device_label, row.shop_id, row.owner_user_id) for row in rows] == [
        ("maintainer", "default", "admin"),
        ("示例成员", "default", "admin"),
    ]

    summary = await client.get("/api/data-sources/cookie-pools?platform=alimama")
    assert summary.status_code == 200
    item = summary.json()["items"][0]
    assert item["shop_id"] == "default"
    assert item["active_count"] == 2
    assert item["total_count"] == 2


@pytest.mark.asyncio
async def test_new_cookie_push_disables_owner_previous_pool(client):
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookiePool
    from sqlalchemy import select

    first_key_body = (await client.post("/api/data-sources/connector-keys", json={
        "device_label": "owner Chrome",
        "source_id": "platform-alimama",
        "owner_user_id": "owner-a",
        "platforms": ["alimama"],
    })).json()
    first_key = first_key_body["api_key"]
    first = await client.post(
        "/api/data-sources/platform-alimama/push-cookies",
        headers={"X-SF-API-Key": first_key},
        json={"cookies": "old=1", "platform": "alimama", "shop_id": "shop-old"},
    )
    assert first.status_code == 200
    old_pool_id = first.json()["pool_id"]

    rotated = await client.post(f"/api/data-sources/connector-keys/{first_key_body['id']}/rotate")
    assert rotated.status_code == 200
    second_key = rotated.json()["api_key"]

    second = await client.post(
        "/api/data-sources/platform-alimama/push-cookies",
        headers={"X-SF-API-Key": second_key},
        json={"cookies": "new=1", "platform": "alimama", "shop_id": "shop-new"},
    )
    assert second.status_code == 200

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(PlatformCookiePool).order_by(PlatformCookiePool.shop_id)
        )).scalars().all()
    by_shop = {row.shop_id: row for row in rows}
    assert by_shop["shop-old"].is_active is False
    assert by_shop["shop-old"].status == "disabled"
    assert by_shop["shop-old"].last_error_code == "SUPERSEDED_BY_NEW_COOKIE"
    assert by_shop["shop-old"].id == old_pool_id
    assert by_shop["shop-new"].is_active is True


@pytest.mark.asyncio
async def test_revoke_connector_key_disables_cookies_and_blocks_reuse(client):
    from app.database import async_session_factory
    from app.datasources.models import PlatformCookieAudit, PlatformCookiePool
    from sqlalchemy import select

    created = await client.post("/api/data-sources/connector-keys", json={
        "name": "revoke-me",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
        "platforms": ["taobao"],
    })
    key_body = created.json()
    push = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": key_body["api_key"]},
        json={"cookies": "a=1", "platform": "taobao", "shop_id": "shop-r"},
    )
    assert push.status_code == 200

    revoked = await client.post(f"/api/data-sources/connector-keys/{key_body['id']}/revoke", json={
        "reason": "pytest",
    })
    assert revoked.status_code == 200
    assert revoked.json()["disabled_cookies"] == 1

    retry = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": key_body["api_key"]},
        json={"cookies": "a=2", "platform": "taobao", "shop_id": "shop-r"},
    )
    assert retry.status_code == 401
    assert retry.json()["error"]["code"] == "CONNECTOR_KEY_REVOKED"

    async with async_session_factory() as session:
        row = (await session.execute(select(PlatformCookiePool))).scalar_one()
        assert row.is_active is False
        assert row.status == "disabled"
        actions = (await session.execute(
            select(PlatformCookieAudit.action).order_by(PlatformCookieAudit.id)
        )).scalars().all()
        assert actions == ["push", "revoke", "disable"]


@pytest.mark.asyncio
async def test_rotate_connector_key_revokes_old_and_returns_new_once(client):
    created = await client.post("/api/data-sources/connector-keys", json={
        "name": "rotate-me",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
    })
    body = created.json()
    rotated = await client.post(f"/api/data-sources/connector-keys/{body['id']}/rotate")
    assert rotated.status_code == 200
    rotated_body = rotated.json()
    assert rotated_body["api_key"] != body["api_key"]
    assert rotated_body["rotated_from_key_id"] == body["id"]

    old_retry = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": body["api_key"]},
        json={"cookies": "a=1", "platform": "taobao", "shop_id": "shop-rot"},
    )
    assert old_retry.status_code == 401
    assert old_retry.json()["error"]["code"] == "CONNECTOR_KEY_REVOKED"

    new_push = await client.post(
        "/api/data-sources/platform-taobao/push-cookies",
        headers={"X-SF-API-Key": rotated_body["api_key"]},
        json={"cookies": "a=2", "platform": "taobao", "shop_id": "shop-rot"},
    )
    assert new_push.status_code == 200

    listed = await client.get("/api/data-sources/connector-keys")
    assert listed.status_code == 200
    listed_keys = {item["id"]: item.get("api_key") for item in listed.json()["items"]}
    assert listed_keys[rotated_body["id"]] == rotated_body["api_key"]
    assert listed_keys[body["id"]] == body["api_key"]


@pytest.mark.asyncio
async def test_connector_key_scope_denied_and_source_mismatch(client):
    taobao_key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "taobao-only",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
    })).json()["api_key"]

    mismatch = await client.post(
        "/api/data-sources/platform-jd/push-cookies",
        headers={"X-SF-API-Key": taobao_key},
        json={"cookies": "a=1", "platform": "jd", "shop_id": "shop-1"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "CONNECTOR_SOURCE_MISMATCH"

    scoped_key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "shop-scoped",
        "source_id": "platform-jd",
        "owner_user_id": "owner-b",
        "platforms": ["jd"],
        "shop_ids": ["shop-allowed"],
    })).json()["api_key"]
    denied = await client.post(
        "/api/data-sources/platform-jd/push-cookies",
        headers={"X-SF-API-Key": scoped_key},
        json={"cookies": "b=1", "platform": "jd", "shop_id": "shop-denied"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CONNECTOR_SCOPE_DENIED"


@pytest.mark.asyncio
async def test_sync_bundle_requires_dedicated_scope(client):
    normal_key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "push-only",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
    })).json()["api_key"]

    denied = await client.get("/api/data-sources/sync-bundle", headers={"X-SF-API-Key": normal_key})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CONNECTOR_SCOPE_DENIED"

    sync_key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "sync-bundle",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
        "scopes": ["sync:bundle"],
    })).json()["api_key"]

    allowed = await client.get("/api/data-sources/sync-bundle", headers={"X-SF-API-Key": sync_key})
    assert allowed.status_code == 200
    assert "platform_connections" in allowed.json()


@pytest.mark.asyncio
async def test_sync_bundle_exports_owner_cookie_pool(client):
    push_key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "push-owner",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
        "platforms": ["taobao"],
        "shop_ids": ["shop-bundle"],
    })).json()["api_key"]
    sync_key = (await client.post("/api/data-sources/connector-keys", json={
        "name": "sync-owner",
        "source_id": "platform-taobao",
        "owner_user_id": "owner-a",
        "platforms": ["taobao"],
        "shop_ids": ["shop-bundle"],
        "scopes": ["sync:bundle"],
    })).json()["api_key"]

    with patch("app.datasources.service._schedule_cookie_sync_to_browser"):
        pushed = await client.post(
            "/api/data-sources/platform-taobao/push-cookies",
            headers={"X-SF-API-Key": push_key},
            json={
                "cookies": "sid=owner-a",
                "platform": "taobao",
                "shop_id": "shop-bundle",
                "account_login": "login-a",
                "cookie_details": [{"name": "sid", "value": "owner-a", "domain": ".taobao.com", "path": "/"}],
            },
        )
    assert pushed.status_code == 200

    bundled = await client.get("/api/data-sources/sync-bundle", headers={"X-SF-API-Key": sync_key})
    assert bundled.status_code == 200
    platforms = bundled.json()["platform_connections"]
    assert len(platforms) == 1
    assert platforms[0]["config"]["platform"] == "taobao"
    assert platforms[0]["config"]["shop_id"] == "shop-bundle"
    assert platforms[0]["owner_user_id"] == "owner-a"
    assert platforms[0]["account_login"] == "login-a"
    assert platforms[0]["cookies"] == "sid=owner-a"
    assert platforms[0]["cookie_details"][0]["name"] == "sid"


@pytest.mark.asyncio
async def test_non_admin_cannot_self_issue_sync_bundle_key(client):
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.datasources.router import CreateConnectorKeyRequest, create_connector_key

    async with async_session_factory() as session:
        with pytest.raises(AppError) as exc_info:
            await create_connector_key(
                CreateConnectorKeyRequest(name="bad-sync", scopes=["sync:bundle"]),
                current_user=_datasource_user("owner-a", "observer", "EC"),
                db=session,
            )

    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_legacy_connector_key_requires_grace_until(client):
    from datetime import timedelta

    from app.common.exceptions import AppError
    from app.common.models import SystemConfig
    from app.common.time_utils import isoformat_bjt, now_bjt
    from app.database import async_session_factory
    from app.datasources import service

    async with async_session_factory() as session:
        session.add(SystemConfig(key="connector.api_key", value="legacy-secret"))
        await session.commit()

        with pytest.raises(AppError) as exc_info:
            await service.verify_connector_api_key(session, "legacy-secret")
        assert exc_info.value.code == "LEGACY_BLOCKED"

        session.add(SystemConfig(
            key="connector_keys.legacy_grace_until",
            value=isoformat_bjt(now_bjt() + timedelta(hours=1)),
        ))
        session.add(SystemConfig(key="connector_keys.legacy_owner_user_id", value="owner-legacy"))
        await session.commit()

        context = await service.verify_connector_api_key(session, "legacy-secret")

    assert context is not None
    assert context.auth_source == service.CONNECTOR_AUTH_SOURCE_LEGACY
    assert context.owner_user_id == "owner-legacy"


@pytest.mark.asyncio
async def test_disable_cookie_pool_reason_requires_min_length(client):
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.datasources import service

    async with async_session_factory() as session:
        with pytest.raises(AppError) as exc_info:
            await service.disable_cookie_pool_credential(
                session,
                credential_id="1",
                actor_user_id="admin",
                reason="short",
            )

    assert exc_info.value.code == "COOKIE_REASON_REQUIRED"
    assert exc_info.value.detail["min_length"] == 10


@pytest.mark.asyncio
async def test_disable_user_revokes_connector_keys_and_cookie_pools(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.datasources.models import ConnectorApiKey, PlatformCookieAudit, PlatformCookiePool
    from app.users import service as user_service
    from sqlalchemy import select

    async with async_session_factory() as session:
        operator = User(
            id="operator-admin",
            username="operator-admin",
            name="Operator",
            role="system_admin",
            state="active",
            is_active=True,
        )
        target = User(
            id="leaver",
            username="leaver",
            name="Leaver",
            role="observer",
            department="EC",
            state="active",
            is_active=True,
        )
        session.add_all([
            operator,
            target,
            ConnectorApiKey(
                id="key-leaver",
                name="Leaver Key",
                key_hash="hash-leaver",
                key_prefix="prefix-leaver",
                key_last4="last",
                owner_user_id="leaver",
                status="active",
                scopes=["cookies:push"],
            ),
            PlatformCookiePool(
                id=201,
                source_id="platform-taobao",
                platform="taobao",
                shop_id="shop-leaver",
                owner_user_id="leaver",
                encrypted_cookies="enc",
                status="active",
                is_active=True,
            ),
        ])
        await session.commit()

        await user_service.disable_user(session, "leaver", operator=operator)
        await session.commit()

        key = await session.get(ConnectorApiKey, "key-leaver")
        pool = await session.get(PlatformCookiePool, 201)
        audit_actions = (
            await session.execute(select(PlatformCookieAudit.action).where(PlatformCookieAudit.cookie_pool_id == 201))
        ).scalars().all()

    assert key.status == "revoked"
    assert pool.is_active is False
    assert pool.status == "disabled"
    assert audit_actions == ["user_disabled"]


def _datasource_user(user_id: str, role: str, department: str = "EC", can_view_all: bool = False):
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    return user


@pytest.mark.asyncio
async def test_cookie_pool_summary_filters_by_owner_dept_and_admin(client):
    from app.database import async_session_factory
    from app.datasources import service
    from app.datasources.models import ConnectorApiKey, DataSource, PlatformCookiePool

    async with async_session_factory() as session:
        session.add_all([
            DataSource(id="platform-taobao", name="淘宝", department="EC", source_type="platform_cookies", config={}),
            DataSource(id="platform-jd", name="京东", department="BD", source_type="platform_cookies", config={}),
            PlatformCookiePool(
                source_id="platform-taobao", platform="taobao", shop_id="shop-1",
                owner_user_id="owner-a", connector_key_id="key-a", encrypted_cookies="enc-a",
            ),
            PlatformCookiePool(
                source_id="platform-taobao", platform="taobao", shop_id="shop-1",
                owner_user_id="owner-b", connector_key_id="key-b", encrypted_cookies="enc-b",
            ),
            PlatformCookiePool(
                source_id="platform-jd", platform="jd", shop_id="shop-2",
                owner_user_id="owner-c", connector_key_id="key-c", encrypted_cookies="enc-c",
            ),
            ConnectorApiKey(
                id="key-a", name="A", key_hash="hash-a", key_prefix="prefix-a", key_last4="aaaa",
                source_id="platform-taobao", owner_user_id="owner-a",
            ),
            ConnectorApiKey(
                id="key-c", name="C", key_hash="hash-c", key_prefix="prefix-c", key_last4="cccc",
                source_id="platform-jd", owner_user_id="owner-c",
            ),
        ])
        await session.commit()

        owner_view = await service.list_cookie_pool_summary(
            session,
            current_user=_datasource_user("owner-a", "observer", "EC"),
        )
        dept_view = await service.list_cookie_pool_summary(
            session,
            current_user=_datasource_user("dept-admin", "dept_admin", "EC"),
        )
        admin_view = await service.list_cookie_pool_summary(
            session,
            current_user=_datasource_user("admin", "admin", "AI"),
        )
        keys = (await service.list_connector_api_keys(
            session,
            current_user=_datasource_user("owner-a", "observer", "EC"),
        ))["items"]

    assert [(item["platform"], item["active_count"]) for item in owner_view["items"]] == [("taobao", 1)]
    assert [(item["platform"], item["active_count"]) for item in dept_view["items"]] == [("taobao", 2)]
    assert {item["platform"] for item in admin_view["items"]} == {"taobao", "jd"}
    assert [item["id"] for item in keys] == ["key-a"]


@pytest.mark.asyncio
async def test_cookie_pool_detail_hides_sensitive_ids_and_limits_audit(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.datasources import service
    from app.datasources.models import DataSource, PlatformCookieAudit, PlatformCookiePool
    from datetime import timedelta

    async with async_session_factory() as session:
        session.add(DataSource(
            id="platform-taobao", name="淘宝", department="EC",
            source_type="platform_cookies", config={},
        ))
        session.add(PlatformCookiePool(
            id=101, source_id="platform-taobao", platform="taobao", shop_id="shop-1",
            owner_user_id="owner-a", connector_key_id="real-key", encrypted_cookies="enc",
        ))
        session.add_all([
            PlatformCookieAudit(
                cookie_pool_id=101, source_id="platform-taobao", platform="taobao", shop_id="shop-1",
                owner_user_id="owner-a", connector_key_id="real-key", action="push",
                auth_source="connector_api_key", actor_user_id="owner-a",
                created_at=now_bjt() - timedelta(days=31),
            ),
            PlatformCookieAudit(
                cookie_pool_id=101, source_id="platform-taobao", platform="taobao", shop_id="shop-1",
                owner_user_id="owner-a", connector_key_id="real-key", action="verify",
                auth_source="connector_api_key", actor_user_id="owner-a",
                created_at=now_bjt() - timedelta(days=1),
            ),
        ])
        await session.commit()

        owner_detail = await service.get_cookie_pool_detail(
            session,
            platform="taobao",
            shop_id="shop-1",
            include_audit=True,
            current_user=_datasource_user("owner-a", "observer", "EC"),
        )
        admin_detail = await service.get_cookie_pool_detail(
            session,
            platform="taobao",
            shop_id="shop-1",
            include_audit=True,
            current_user=_datasource_user("admin", "admin", "AI"),
        )

    assert owner_detail["credentials"][0]["id"] != "101"
    assert owner_detail["credentials"][0]["connector_key_id"] is None
    assert len(owner_detail["audit"]) == 1
    assert owner_detail["audit"][0]["cookie_pool_id"] is None
    assert owner_detail["audit"][0]["connector_key_id"] is None
    assert admin_detail["credentials"][0]["id"] == "101"
    assert admin_detail["credentials"][0]["connector_key_id"] == "real-key"


@pytest.mark.asyncio
async def test_platform_api_drift_alerts_only_valid_and_ack_persists(client):
    from app.collection.models import PlatformApiSchemaSnapshot
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.datasources import service
    from app.datasources.models import DataSource, PlatformApiDriftAck, PlatformCookiePool
    from app.todos.models import AITodo, DecisionRequest
    from datetime import timedelta
    from sqlalchemy import select

    base = now_bjt()

    def snap(endpoint_hash, keys, row_count, minutes, run):
        return PlatformApiSchemaSnapshot(
            platform="taobao",
            shop_id="shop-1",
            endpoint_hash=endpoint_hash,
            endpoint=f"https://example.com/{endpoint_hash}",
            response_keys=keys,
            row_count=row_count,
            source_run_id=run,
            captured_at=base + timedelta(minutes=minutes),
        )

    async with async_session_factory() as session:
        session.add(DataSource(
            id="platform-taobao", name="淘宝", department="EC",
            source_type="platform_cookies", config={},
        ))
        session.add(PlatformCookiePool(
            source_id="platform-taobao", platform="taobao", shop_id="shop-1",
            owner_user_id="owner-a", connector_key_id="key-a", encrypted_cookies="enc",
        ))
        session.add_all([
            snap("field-added", ["a"], 10, 0, "fa-1"),
            snap("field-added", ["a", "b"], 10, 1, "fa-2"),
            snap("field-missing", ["a", "b"], 10, 0, "fm-1"),
            snap("field-missing", ["a"], 10, 1, "fm-2"),
            snap("type-change", {"a": "string"}, 10, 0, "tc-1"),
            snap("type-change", {"a": "number"}, 10, 1, "tc-2"),
            snap("row-spike", ["a"], 10, 0, "rs-1"),
            snap("row-spike", ["a"], 18, 10, "rs-2"),
            snap("snapshot-only", ["a"], 10, 0, "so-1"),
        ])
        await session.commit()

        listed = await service.list_platform_api_drift_alerts(
            session,
            current_user=_datasource_user("owner-a", "observer", "EC"),
        )
        change_types = {item["change_type"] for item in listed["items"]}
        alert_id = next(item["id"] for item in listed["items"] if item["change_type"] == "field_missing")

        acked = await service.ack_platform_api_drift_alert(
            session,
            alert_id=alert_id,
            current_user=_datasource_user("owner-a", "observer", "EC"),
        )
        ack_row = await session.get(PlatformApiDriftAck, alert_id)
        request = await session.get(DecisionRequest, acked["review_task_id"])
        todos = (await session.execute(
            select(AITodo).where(AITodo.request_id == acked["review_task_id"])
        )).scalars().all()
        listed_after_ack = await service.list_platform_api_drift_alerts(
            session,
            current_user=_datasource_user("owner-a", "observer", "EC"),
        )

    assert change_types == {"field_missing", "type_change", "row_count_spike"}
    assert ack_row is not None
    assert ack_row.acknowledged_by == "owner-a"
    assert request is not None
    assert request.source_type == "platform_api_drift"
    assert todos and todos[0].assignee == "owner-a"
    assert any(item["id"] == alert_id and item["acknowledged"] for item in listed_after_ack["items"])


@pytest.mark.asyncio
async def test_api_discovery_report_and_list(client):
    key_resp = await client.get("/api/data-sources/connector-api-key")
    api_key = key_resp.json()["api_key"]
    await _enable_legacy_connector_grace()

    list_before = await client.get("/api/data-sources/api-discovery")
    assert list_before.status_code == 200

    report = await client.post(
        "/api/data-sources/api-discovery",
        headers={"X-SF-API-Key": api_key},
        json={
            "page_url": "https://sub.taobao.com/dashboard",
            "page_title": "淘宝后台",
            "apis": [
                {"method": "GET", "url": "https://sub.taobao.com/api/orders?page=1"},
                {"method": "POST", "url": "https://sub.taobao.com/api/export"},
            ],
            "reported_at": "2026-04-09T00:00:00",
        },
    )
    assert report.status_code == 200
    assert report.json()["domain"] == "sub.taobao.com"

    listed = await client.get("/api/data-sources/api-discovery", params={"domain": "sub.taobao.com"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["domain"] == "sub.taobao.com"
    assert body["items"][0]["api_count"] == 2


@pytest.mark.asyncio
async def test_pull_api_source_wrong_type():
    """测试对csv_upload类型的数据源执行API拉取——应报错"""
    from app.common.exceptions import AppError
    from app.datasources.models import DataSource

    mock_source = MagicMock(spec=DataSource)
    mock_source.id = "ds-csv-wrong"
    mock_source.source_type = "csv_upload"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.datasources.service import pull_api_source
        await pull_api_source(mock_db, "ds-csv-wrong", "admin")

    assert exc_info.value.code == "DATASOURCE_PARSE_ERROR"


@pytest.mark.asyncio
async def test_pull_from_remote_rejects_private_remote_url():
    from app.common.exceptions import AppError
    from app.datasources.router import PullRemoteRequest, pull_from_remote

    with pytest.raises(AppError) as exc_info:
        await pull_from_remote(
            PullRemoteRequest(remote_url="http://127.0.0.1:8000", api_key="test"),
            current_user=MagicMock(),
            db=AsyncMock(),
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert "内网" in exc_info.value.detail["detail"]


@pytest.mark.asyncio
async def test_pull_from_remote_builds_sync_bundle_url(monkeypatch):
    from app.common.exceptions import AppError
    from app.datasources.router import PullRemoteRequest, pull_from_remote

    captured = {}

    class FakeResponse:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("app.browser.service.guard_discover_url", lambda url, **kwargs: url)
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    with pytest.raises(AppError) as exc_info:
        await pull_from_remote(
            PullRemoteRequest(remote_url="https://example.com/base/", api_key="secret"),
            current_user=MagicMock(),
            db=AsyncMock(),
        )

    assert exc_info.value.code == "AUTH_INVALID_CREDENTIALS"
    assert captured["timeout"] == 30
    assert captured["url"] == "https://example.com/base/api/data-sources/sync-bundle"
    assert captured["headers"] == {"X-SF-API-Key": "secret"}


# ===== 质量校验——行数delta警告 =====


def test_quality_check_row_count_delta_warning():
    """测试质量校验：行数变化超过50%时应给出警告"""
    from app.datasources.service import _run_quality_checks

    rows = [{"a": 1}] * 10  # 当前10行
    columns = ["a"]
    rules = {}
    prev_row_count = 100  # 上次100行，减少了90%

    report = _run_quality_checks(rows, columns, rules, prev_row_count=prev_row_count)

    assert report["has_warnings"] is True
    delta_checks = [c for c in report["checks"] if c["rule"] == "行数波动检查"]
    assert len(delta_checks) == 1
    assert delta_checks[0]["status"] == "warning"
    assert "减少" in delta_checks[0]["detail"]


def test_quality_check_row_count_delta_pass():
    """测试质量校验：行数变化在正常范围内不警告"""
    from app.datasources.service import _run_quality_checks

    rows = [{"a": 1}] * 80  # 当前80行
    columns = ["a"]
    rules = {}
    prev_row_count = 100  # 上次100行，减少了20%（在50%以内）

    report = _run_quality_checks(rows, columns, rules, prev_row_count=prev_row_count)

    delta_checks = [c for c in report["checks"] if c["rule"] == "行数波动检查"]
    assert len(delta_checks) == 1
    assert delta_checks[0]["status"] == "pass"


def test_quality_check_empty_data():
    """测试质量校验：空数据应报error"""
    from app.datasources.service import _run_quality_checks

    report = _run_quality_checks([], [], {})

    assert report["has_errors"] is True
    assert any(c["status"] == "error" for c in report["checks"])


def test_quality_check_non_negative_columns():
    """测试质量校验：非负数值检查"""
    from app.datasources.service import _run_quality_checks

    rows = [
        {"消耗": "100", "ROI": "1.5"},
        {"消耗": "-50", "ROI": "0.8"},
    ]
    columns = ["消耗", "ROI"]
    rules = {"non_negative_columns": ["消耗"]}

    report = _run_quality_checks(rows, columns, rules)

    assert report["has_errors"] is True
    neg_checks = [c for c in report["checks"] if "不能为负" in c["rule"]]
    assert len(neg_checks) == 1
    assert neg_checks[0]["status"] == "error"


def test_quality_check_null_values():
    """测试质量校验：必填列有空值"""
    from app.datasources.service import _run_quality_checks

    rows = [
        {"日期": "2026-01-01", "ROI": "1.5"},
        {"日期": "", "ROI": "0.8"},
    ]
    columns = ["日期", "ROI"]
    rules = {"required_columns": ["日期"]}

    report = _run_quality_checks(rows, columns, rules)

    assert report["has_errors"] is True


# ===== 数据源不存在 =====


@pytest.mark.asyncio
async def test_get_source_not_found():
    """测试获取不存在的数据源——应返回404"""
    from app.common.exceptions import AppError

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.datasources.service import get_source
        await get_source(mock_db, "not-exist")

    assert exc_info.value.code == "DATASOURCE_NOT_FOUND"


# ===== 变量替换 =====


def test_variable_substitution():
    """测试API参数中的模板变量替换"""
    from app.datasources.service import _substitute_variables
    from datetime import timedelta
    from app.common.time_utils import now_bjt

    params = {"date": "{{yesterday}}", "today": "{{today}}", "fixed": "value"}
    result = _substitute_variables(params)

    expected_yesterday = (now_bjt().date() - timedelta(days=1)).isoformat()
    expected_today = now_bjt().date().isoformat()

    assert result["date"] == expected_yesterday
    assert result["today"] == expected_today
    assert result["fixed"] == "value"


# ===== JSON数据提取 =====


def test_extract_json_data_with_path():
    """测试从JSON响应中提取数据——使用jsonpath"""
    from app.datasources.service import _extract_json_data

    response = {"data": [{"name": "A"}, {"name": "B"}]}
    rows = _extract_json_data(response, ["$.data[*]"])
    assert len(rows) == 2
    assert rows[0]["name"] == "A"


def test_extract_json_data_auto_detect():
    """测试从JSON响应中提取数据——自动检测常见字段"""
    from app.datasources.service import _extract_json_data

    response = {"items": [{"id": 1}, {"id": 2}]}
    rows = _extract_json_data(response, [])
    assert len(rows) == 2


def test_extract_json_data_direct_list():
    """测试从JSON响应中提取数据——响应本身是列表"""
    from app.datasources.service import _extract_json_data

    response = [{"id": 1}, {"id": 2}]
    rows = _extract_json_data(response, [])
    assert len(rows) == 2


# ===== 字段映射 =====


def test_field_mapping():
    """测试字段映射重命名"""
    from app.datasources.service import _apply_field_mapping

    rows = [{"原始列": "值A", "其他列": "值B"}]
    mapping = {"原始列": "目标列"}
    result = _apply_field_mapping(rows, mapping)

    assert result[0]["目标列"] == "值A"
    assert result[0]["其他列"] == "值B"


# ===== Auth headers =====


def test_build_auth_headers_bearer():
    """测试构建Bearer认证头"""
    from app.datasources.service import _build_auth_headers

    headers = _build_auth_headers({"auth_type": "bearer", "auth_token": "test123"})
    assert headers["Authorization"] == "Bearer test123"


def test_build_auth_headers_api_key():
    """测试构建API Key认证头"""
    from app.datasources.service import _build_auth_headers

    headers = _build_auth_headers({"auth_type": "api_key", "auth_token": "mykey"})
    assert headers["X-API-Key"] == "mykey"


def test_build_auth_headers_none():
    """测试无认证时返回空头"""
    from app.datasources.service import _build_auth_headers

    headers = _build_auth_headers({"auth_type": "none"})
    assert len(headers) == 0
