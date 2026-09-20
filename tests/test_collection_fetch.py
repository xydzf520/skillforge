import importlib.util
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.collection.models import CollectionProof, PlatformApiSchemaSnapshot  # noqa: F401
from app.collection.router import router as collection_router
from app.common.exceptions import AppError, app_error_handler
from app.common.time_utils import now_bjt
from app.database import Base, get_db


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_runtime():
    path = SCRIPTS_DIR / "skillforge_mcp_runtime.py"
    spec = importlib.util.spec_from_file_location("skillforge_mcp_runtime", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["skillforge_mcp_runtime"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def registered_tool():
    runtime = _load_runtime()
    meta = runtime.register_tool(runtime.ToolMeta(
        tool_name="tmall_sycm_item_rank_top",
        platform="sycm",
        data_scope="sycm.item_rank",
        endpoint_family="read_metrics",
        warning_group="sycm.report_read",
    ))
    runtime.register_tool(runtime.ToolMeta(
        tool_name="tmall_alimama_item_promotion",
        platform="alimama",
        data_scope="alimama.campaign_report",
        endpoint_family="read_metrics",
        warning_group="alimama.report_read",
    ))
    runtime.register_tool(runtime.ToolMeta(
        tool_name="tmall_sycm_market_rank",
        platform="sycm",
        data_scope="sycm.market_rank",
        endpoint_family="read_metrics",
        warning_group="sycm.market_read",
    ))
    runtime.register_tool(runtime.ToolMeta(
        tool_name="tmall_item_flow_required_metrics",
        platform="sycm",
        data_scope="sycm.item_flow",
        endpoint_family="read_metrics",
        warning_group="sycm.flow_read",
    ))
    return meta


@pytest_asyncio.fixture
async def collection_test_app(monkeypatch, registered_tool):
    import re

    import app.collection.service as collection_service
    import app.datasources.service as datasources_service
    from app.config import settings
    from cryptography.fernet import Fernet

    monkeypatch.setenv("SKILLFORGE_TEST_RUN_TOKEN", "valid-token")
    monkeypatch.delenv("SKILLFORGE_COLLECTION_ALLOW_LEGACY", raising=False)
    monkeypatch.setattr(settings, "COOKIE_ENCRYPT_KEY", Fernet.generate_key().decode())
    datasources_service._cookie_cipher_cache = None
    collection_service._RATE_STATE.clear()
    collection_service._CIRCUIT_STATE.clear()
    collection_service._PENDING_COOKIE_VERIFY_LOCKS.clear()
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$",
        "/skillforge_test",
        str(settings.DATABASE_URL),
    )
    engine = create_async_engine(test_url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import app.browser.models  # noqa: F401
    import app.collection.models  # noqa: F401
    import app.knowledge.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.skills.asset_models  # noqa: F401
    import app.skills.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async def override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def fake_fetch(params):
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {"rows": [{"sku": "A"}]},
            },
            "proof": {
                "status": 200,
                "ok": True,
                "response_hash": "abc123",
                "data_keys": ["rows"],
                "row_count": 1,
            },
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(collection_router, prefix="/api/collection")
    app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    await engine.dispose()


async def _seed_cookie_pool_and_slot(
    session_factory,
    *,
    cookie_details=None,
    user_agent="pytest-agent",
    source_department: str | None = None,
    source_id: str = "platform-sycm",
    platform: str = "sycm",
    shop_id: str = "shop-1",
    owner_user_id: str = "owner-1",
    cookies: str = "_tb_token_=abc; cna=xyz",
    priority: int = 50,
    health_score: int = 100,
    verification_status: str = "active",
    pushed_at=None,
    last_verified_at=None,
    last_error_code: str | None = None,
    slot_id: str = "slot-collection-1",
):
    from app.browser.models import BrowserSlot
    from app.datasources.models import DataSource, PlatformCookiePool
    from app.datasources.service import _credential_alias_for_pool, _get_cookie_cipher

    cipher = _get_cookie_cipher()
    async with session_factory() as session:
        if source_department:
            session.add(DataSource(
                id=source_id,
                name=platform.upper(),
                source_type="platform_cookies",
                department=source_department,
                config={"platform": platform, "shop_id": shop_id},
            ))
        pool = PlatformCookiePool(
            source_id=source_id,
            platform=platform,
            shop_id=shop_id,
            owner_user_id=owner_user_id,
            encrypted_cookies=cipher.encrypt(cookies.encode()).decode(),
            encrypted_cookie_details=cipher.encrypt(
                json.dumps(cookie_details or [
                    {
                        "name": "_tb_token_",
                        "value": "abc",
                        "domain": ".taobao.com",
                        "path": "/",
                        "secure": True,
                    }
                ]).encode()
            ).decode(),
            user_agent=user_agent,
            priority=priority,
            status="active",
            is_active=True,
            health_score=health_score,
            verification_status=verification_status,
            pushed_at=pushed_at or now_bjt(),
            last_verified_at=last_verified_at,
            last_error_code=last_error_code,
        )
        session.add(pool)
        await session.flush()
        alias = _credential_alias_for_pool(pool)
        cdp_suffix = "collection" if slot_id == "slot-collection-1" else slot_id
        session.add(BrowserSlot(
            slot_id=slot_id,
            cdp_url=f"ws://127.0.0.1/devtools/page/{cdp_suffix}",
            node_id="node-1",
            status="idle",
        ))
        await session.commit()
        return pool.id, alias


def _payload(**overrides):
    data = {
        "mcp_tool_name": "tmall_sycm_item_rank_top",
        "skill_id": "skill-1",
        "run_id": "run-1",
        "instance_id": "node-1",
        "platform": "sycm",
        "shop_id": "shop-1",
        "data_scope": "sycm.item_rank",
        "endpoint_family": "read_metrics",
        "warning_group": "sycm.report_read",
        "params": {"url": "https://sycm.taobao.com/cc/item/live/view/top.json"},
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_fetch_via_browser_passes_browser_context(monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service

    captured = {}

    async def fake_fetch_json(**kwargs):
        captured.update(kwargs)
        return {"success": True, "data": {"status": 200}, "proof": {"response_hash": "browserctx"}}

    monkeypatch.setattr(browser_service, "fetch_json", fake_fetch_json)

    result = await collection_service.fetch_via_browser({
        "url": "https://sycm.taobao.com/api.json",
        "method": "POST",
        "headers": {"x-test": "1"},
        "body": {"page": 1},
        "page_url": "https://sycm.taobao.com/dashboard",
        "cdp_url": "ws://slot/page",
        "cookie_details": [{"name": "a", "value": "1", "domain": ".taobao.com"}],
        "cookie_header": "a=1",
        "user_agent": "UA/1",
    })

    assert result["success"] is True
    assert captured == {
        "url": "https://sycm.taobao.com/api.json",
        "method": "POST",
        "headers": {"x-test": "1"},
        "body": {"page": 1},
        "page_url": "https://sycm.taobao.com/dashboard",
        "cdp_url": "ws://slot/page",
        "cookie_details": [{"name": "a", "value": "1", "domain": ".taobao.com"}],
        "cookie_header": "a=1",
        "user_agent": "UA/1",
    }


@pytest.mark.asyncio
async def test_fetch_via_browser_falls_back_to_direct_sycm_cookie(monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service

    async def fake_fetch_json(**kwargs):
        return {
            "success": False,
            "error": "浏览器 fetch_json 未返回响应状态",
            "data": {},
            "proof": {"response_hash": "empty"},
        }

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json;charset=UTF-8"}
        url = "https://sycm.taobao.com/cc/item/live/view/top.json"
        text = '{"code":0,"data":{"data":{"recordCount":1,"data":[{"item":{"itemId":"8001"}}]}}}'

        def json(self):
            return json.loads(self.text)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            assert method == "GET"
            assert url == "https://sycm.taobao.com/cc/item/live/view/top.json"
            assert kwargs["headers"]["cookie"] == "a=1"
            assert "credential" not in kwargs["headers"]
            return FakeResp()

    monkeypatch.setattr(browser_service, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(collection_service.httpx, "AsyncClient", FakeClient)

    result = await collection_service.fetch_via_browser({
        "url": "https://sycm.taobao.com/cc/item/live/view/top.json",
        "method": "GET",
        "cookie_header": "a=1",
        "user_agent": "pytest",
        "_collection_context": {
            "platform": "sycm",
            "shop_id": "shop-1",
            "data_scope": "sycm.item_rank",
        },
    })

    assert result["success"] is True
    assert result["proof"]["transport"] == "direct_with_platform_cookie"
    assert result["data"]["data"]["code"] == 0


@pytest.mark.asyncio
async def test_collection_fetch_requires_run_token(collection_test_app):
    client, session_factory = collection_test_app

    resp = await client.post("/api/collection/fetch", json=_payload())

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "RUN_TOKEN_INVALID"
    async with session_factory() as session:
        rows = (await session.execute(select(CollectionProof))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_collection_fetch_rejects_unregistered_toolmeta(collection_test_app):
    client, session_factory = collection_test_app

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(data_scope="sycm.not_registered"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOOLMETA_NOT_REGISTERED"
    async with session_factory() as session:
        rows = (await session.execute(select(CollectionProof))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_collection_fetch_writes_proof_without_sensitive_ids(collection_test_app):
    client, session_factory = collection_test_app
    _, alias = await _seed_cookie_pool_and_slot(session_factory)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["proof_id"].startswith("proof-")
    assert data["credential_alias"] == alias
    assert "cookie_pool_id" not in data
    assert "owner_user_id" not in data
    async with session_factory() as session:
        proof = (await session.execute(select(CollectionProof))).scalar_one()
        snapshots = (await session.execute(select(PlatformApiSchemaSnapshot))).scalars().all()
    assert proof.run_id == "run-1"
    assert proof.data_scope == "sycm.item_rank"
    assert proof.response_hash == "abc123"
    assert proof.row_count == 1
    assert proof.browser_slot_id == "slot-collection-1"
    assert snapshots and snapshots[0].response_hash == "abc123"


@pytest.mark.asyncio
async def test_collection_fetch_reuses_schema_snapshot_for_same_run_endpoint(collection_test_app):
    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(session_factory)

    payload = _payload()
    for _ in range(2):
        resp = await client.post(
            "/api/collection/fetch",
            headers={"X-Run-Token": "valid-token"},
            json=payload,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async with session_factory() as session:
        proofs = (await session.execute(select(CollectionProof))).scalars().all()
        snapshots = (await session.execute(select(PlatformApiSchemaSnapshot))).scalars().all()
    assert len(proofs) == 2
    assert len(snapshots) == 1
    assert snapshots[0].source_run_id == "run-1"
    assert snapshots[0].response_hash == "abc123"


@pytest.mark.asyncio
async def test_collection_fetch_accepts_signed_run_token(collection_test_app, monkeypatch):
    from app.execution.execution_service import issue_run_token

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(session_factory)
    monkeypatch.delenv("SKILLFORGE_TEST_RUN_TOKEN", raising=False)
    token = issue_run_token(skill_id="skill-1", run_id="run-1", instance_id="node-1")

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": token},
        json=_payload(),
    )

    assert resp.status_code == 200
    assert resp.json()["proof_id"].startswith("proof-")


@pytest.mark.asyncio
async def test_collection_fetch_rejects_cookie_from_other_department(collection_test_app, monkeypatch):
    from app.execution.execution_service import issue_run_token

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(session_factory, source_department="BD")
    monkeypatch.delenv("SKILLFORGE_TEST_RUN_TOKEN", raising=False)
    token = issue_run_token(skill_id="skill-1", run_id="run-1", instance_id="node-1", department="EC")

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": token},
        json=_payload(),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "COOKIE_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_collection_fetch_allows_related_skill_across_department(collection_test_app, monkeypatch):
    from app.datasources.models import DataSource
    from app.execution.execution_service import issue_run_token

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(session_factory, source_department="EC")
    async with session_factory() as session:
        source = await session.get(DataSource, "platform-sycm")
        source.related_skills = ["skill-1"]
        await session.commit()

    monkeypatch.delenv("SKILLFORGE_TEST_RUN_TOKEN", raising=False)
    token = issue_run_token(
        skill_id="skill-1",
        run_id="run-1",
        instance_id="node-1",
        department="示例品牌传统电商运营部",
    )

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": token},
        json=_payload(),
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_collection_fetch_returns_no_cookie_by_default(collection_test_app):
    client, _ = collection_test_app

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "NO_COOKIE"


@pytest.mark.asyncio
async def test_collection_fetch_allows_legacy_only_with_explicit_env(collection_test_app, monkeypatch):
    client, _ = collection_test_app
    monkeypatch.setenv("SKILLFORGE_COLLECTION_ALLOW_LEGACY", "1")

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    assert resp.json()["credential_alias"] == "sycm-shop-1-legacy"


@pytest.mark.asyncio
async def test_collection_fetch_injects_cookie_context(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    _, alias = await _seed_cookie_pool_and_slot(
        session_factory,
        cookie_details=[{
            "name": "sessionid",
            "value": "s-123",
            "domain": ".sycm.taobao.com",
            "path": "/",
            "sameSite": "lax",
        }],
        user_agent="SkillForgeTest/1.0",
    )
    captured = {}

    async def fake_fetch(params):
        captured.update(params)
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "ctx123", "data_keys": ["rows"]},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    assert captured["cdp_url"] == "ws://127.0.0.1/devtools/page/collection"
    assert captured["cookie_header"] == "_tb_token_=abc; cna=xyz"
    assert captured["cookie_details"][0]["name"] == "sessionid"
    assert captured["user_agent"] == "SkillForgeTest/1.0"
    assert captured["credential_alias"] == alias
    assert resp.json()["proof"]["slot_id"] == "slot-collection-1"
    assert resp.json()["proof"]["credential_alias"] == alias


@pytest.mark.asyncio
async def test_collection_fetch_marks_business_not_ok_as_failed(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    _, alias = await _seed_cookie_pool_and_slot(session_factory)

    async def fake_fetch(params):
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {
                    "info": {"ok": False, "message": "登录失效"},
                    "data": {"errorCode": "NO_COOKIE"},
                },
            },
            "proof": {"status": 200, "ok": True, "response_hash": "biz-failed", "data_keys": ["info", "data"]},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["credential_alias"] == alias
    async with session_factory() as session:
        proof = (await session.execute(select(CollectionProof))).scalar_one()
    assert proof.status == "failed"
    assert proof.error_code == "登录失效"


@pytest.mark.asyncio
async def test_direct_alimama_fetch_retries_transient_exception(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        platform="alimama",
        source_id="platform-alimama",
        shop_id="shop-1",
    )
    calls = 0

    async def fake_direct_once(params):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": False,
                "error": "DIRECT_COOKIE_FETCH_EXCEPTION: transient timeout",
                "data": None,
                "proof": {"transport": "direct_with_platform_cookie"},
            }
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {"count": 1, "list": [{"campaignId": 1}]},
            },
            "proof": {"status": 200, "ok": True, "response_hash": "alimama-ok", "data_keys": ["count", "list"]},
        }

    monkeypatch.setattr(collection_service, "_fetch_direct_with_platform_cookie_once", fake_direct_once)
    monkeypatch.setattr(collection_service, "_direct_cookie_retry_delay", lambda attempt: 0)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(
            mcp_tool_name="tmall_alimama_item_promotion",
            platform="alimama",
            data_scope="alimama.campaign_report",
            warning_group="alimama.report_read",
            params={"url": "https://one.alimama.com/campaign/horizontal/findPage.json"},
        ),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert calls == 2
    async with session_factory() as session:
        proof = (await session.execute(select(CollectionProof))).scalar_one()
    assert proof.status == "success"


@pytest.mark.asyncio
async def test_collection_fetch_uses_best_cookie_candidate(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    _, standby_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-standby",
        cookies="standby=1",
        priority=10,
        health_score=100,
        slot_id="slot-standby",
    )
    _, primary_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-primary",
        cookies="primary=1",
        priority=80,
        health_score=70,
        slot_id="slot-primary",
    )
    captured = {}

    async def fake_fetch(params):
        captured.update(params)
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "best123"},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["credential_alias"] == primary_alias
    assert data["credential_alias"] != standby_alias
    assert captured["cookie_header"] == "primary=1"
    assert captured["browser_slot_id"] in {"slot-primary", "slot-standby"}


@pytest.mark.asyncio
async def test_collection_fetch_skips_stale_pending_cookie(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-stale-pending",
        cookies="stale=1",
        priority=10,
        health_score=100,
        verification_status="pending",
        pushed_at=now_bjt() - timedelta(hours=2),
        slot_id="slot-stale-pending",
    )
    _, active_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-active",
        cookies="active=1",
        priority=20,
        health_score=100,
        verification_status="active",
        slot_id="slot-active",
    )
    seen_headers = []

    async def fake_fetch(params):
        seen_headers.append(params["cookie_header"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "active-only"},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["credential_alias"] == active_alias
    assert seen_headers == ["active=1"]


@pytest.mark.asyncio
async def test_collection_fetch_accepts_previously_verified_pending_cookie(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    _, verified_pending_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-verified-pending",
        cookies="verified_pending=1",
        priority=10,
        health_score=100,
        verification_status="pending",
        pushed_at=now_bjt() - timedelta(hours=2),
        last_verified_at=now_bjt() - timedelta(minutes=5),
        slot_id="slot-verified-pending",
    )
    seen_headers = []

    async def fake_fetch(params):
        seen_headers.append(params["cookie_header"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "verified-pending"},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["credential_alias"] == verified_pending_alias
    assert seen_headers == ["verified_pending=1"]


@pytest.mark.asyncio
async def test_collection_fetch_verifies_stale_pending_cookie_before_no_cookie(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-stale-pending",
        cookies="pending=1",
        verification_status="pending",
        last_verified_at=None,
        pushed_at=now_bjt() - timedelta(minutes=20),
        slot_id="slot-stale-pending-only",
    )
    verify_calls = []
    seen_headers = []

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        verify_calls.append((source_id, user_id))
        pool = (await db.execute(
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == source_id)
            .where(PlatformCookiePool.platform == "sycm")
        )).scalar_one()
        pool.verification_status = "active"
        pool.health_score = 100
        pool.last_error_code = None
        pool.last_verified_at = now_bjt()
        await db.commit()
        return {"source_id": source_id, "verified": True, "status": "valid", "detail": "ok"}

    async def fake_fetch(params):
        seen_headers.append(params["cookie_header"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "stale-pending-verified"},
        }

    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["cookie_verify_reason"] == "VERIFIED"
    assert data["cookie_verify_attempts"] == [
        {
            "source_id": "platform-sycm",
            "reason": "VERIFIED",
            "status": "valid",
            "verified": True,
            "detail": "ok",
        }
    ]
    assert verify_calls == [("platform-sycm", "collection_fetch")]
    assert seen_headers == ["pending=1"]


@pytest.mark.asyncio
async def test_collection_fetch_rejects_stale_pending_cookie_when_sync_verify_fails(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-expired-pending-fetch",
        cookies="expired_pending=1",
        verification_status="pending",
        last_verified_at=None,
        pushed_at=now_bjt() - timedelta(minutes=20),
        slot_id="slot-expired-pending-fetch",
    )
    called = False

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        pool = (await db.execute(
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == source_id)
            .where(PlatformCookiePool.platform == "sycm")
        )).scalar_one()
        pool.verification_status = "failed"
        pool.health_score = 0
        pool.last_error_code = "LOGIN_EXPIRED"
        pool.last_verified_at = now_bjt()
        await db.commit()
        return {"source_id": source_id, "verified": False, "status": "expired", "detail": "LOGIN_EXPIRED"}

    async def fake_fetch(params):
        nonlocal called
        called = True
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "should-not-run"},
        }

    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 412
    error = resp.json()["error"]
    assert error["code"] == "NO_COOKIE"
    assert error["detail"]["reason"] == "NO_RUNTIME_COOKIE"
    assert error["detail"]["verify_reason"] == "COOKIE_VERIFY_FAILED"
    assert error["detail"]["verify_attempts"] == [
        {
            "source_id": "platform-sycm",
            "reason": "COOKIE_VERIFY_FAILED",
            "status": "expired",
            "verified": False,
            "detail": "LOGIN_EXPIRED",
        }
    ]
    assert called is False


@pytest.mark.asyncio
async def test_collection_fetch_retries_next_cookie_on_login_failure(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.collection.models import CollectionProof
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    _, first_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-expired",
        cookies="expired=1",
        priority=20,
        health_score=100,
        slot_id="slot-expired",
    )
    _, second_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-fresh",
        cookies="fresh=1",
        priority=10,
        health_score=100,
        slot_id="slot-fresh",
    )
    seen_headers = []

    async def fake_fetch(params):
        seen_headers.append(params["cookie_header"])
        if params["cookie_header"] == "expired=1":
            return {
                "success": True,
                "data": {
                    "url": params["url"],
                    "status": 401,
                    "ok": False,
                    "data": {"errorCode": "FAIL_BIZ_NOT_LOGIN"},
                },
                "proof": {"status": 401, "ok": False, "response_hash": "expired"},
            }
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": [{"sku": "A"}]}},
            "proof": {"status": 200, "ok": True, "response_hash": "fresh", "row_count": 1},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["credential_alias"] == second_alias
    assert data["attempt_count"] == 2
    assert [item["credential_alias"] for item in data["credential_attempts"]] == [first_alias, second_alias]
    assert seen_headers == ["expired=1", "fresh=1"]
    async with session_factory() as session:
        proofs = (await session.execute(select(CollectionProof).order_by(CollectionProof.id))).scalars().all()
        pools = (await session.execute(select(PlatformCookiePool).order_by(PlatformCookiePool.owner_user_id))).scalars().all()
    assert [row.status for row in proofs] == ["failed", "success"]
    assert proofs[0].error_code == "HTTP_401"
    assert {pool.owner_user_id: pool.health_score for pool in pools} == {
        "owner-expired": 0,
        "owner-fresh": 100,
    }
    assert {pool.owner_user_id: pool.verification_status for pool in pools} == {
        "owner-expired": "failed",
        "owner-fresh": "active",
    }


@pytest.mark.asyncio
async def test_collection_fetch_batch_verifies_recent_pending_cookie_before_call(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-unverified",
        cookies="pending=1",
        verification_status="pending",
        last_verified_at=None,
        slot_id="slot-unverified",
    )
    verify_calls = []
    seen_headers = []

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        verify_calls.append((source_id, user_id))
        pool = (await db.execute(
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == source_id)
            .where(PlatformCookiePool.platform == "sycm")
        )).scalar_one()
        pool.verification_status = "active"
        pool.health_score = 100
        pool.last_error_code = None
        pool.last_verified_at = now_bjt()
        await db.commit()
        return {"source_id": source_id, "verified": True, "status": "valid", "detail": "ok"}

    async def fake_fetch(params):
        seen_headers.append(params["cookie_header"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "pending-verified"},
        }

    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch-batch",
        headers={"X-Run-Token": "valid-token"},
        json={
            **_payload(),
            "concurrency": 2,
            "items": [
                {"request_id": "item-1", "params": {"url": "https://sycm.taobao.com/a.json"}},
            ],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["valid_cookie_count"] == 1
    assert data["cookie_verify_attempts"] == [
        {
            "source_id": "platform-sycm",
            "reason": "VERIFIED",
            "status": "valid",
            "verified": True,
            "detail": "ok",
        }
    ]
    assert verify_calls == [("platform-sycm", "collection_batch")]
    assert seen_headers == ["pending=1"]


@pytest.mark.asyncio
async def test_collection_fetch_batch_coalesces_pending_cookie_verify_between_requests(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-coalesced-pending",
        cookies="pending=1",
        verification_status="pending",
        last_verified_at=None,
        slot_id="slot-coalesced-pending",
    )
    verify_started = asyncio.Event()
    verify_can_finish = asyncio.Event()
    verify_calls = 0
    seen_headers = []

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        nonlocal verify_calls
        verify_calls += 1
        verify_started.set()
        await asyncio.wait_for(verify_can_finish.wait(), timeout=2)
        pool = (await db.execute(
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == source_id)
            .where(PlatformCookiePool.platform == "sycm")
        )).scalar_one()
        pool.verification_status = "active"
        pool.health_score = 100
        pool.last_error_code = None
        pool.last_verified_at = now_bjt()
        await db.commit()
        return {"source_id": source_id, "verified": True, "status": "valid", "detail": "ok"}

    async def fake_fetch(params):
        seen_headers.append(params["cookie_header"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "coalesced-pending"},
        }

    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    async def post_batch(name: str):
        return await client.post(
            "/api/collection/fetch-batch",
            headers={"X-Run-Token": "valid-token"},
            json={
                **_payload(run_id=f"run-{name}"),
                "items": [
                    {"request_id": name, "params": {"url": f"https://sycm.taobao.com/{name}.json"}},
                ],
            },
        )

    first = asyncio.create_task(post_batch("first"))
    await asyncio.wait_for(verify_started.wait(), timeout=2)
    second = asyncio.create_task(post_batch("second"))
    await asyncio.sleep(0.05)
    verify_can_finish.set()

    responses = await asyncio.gather(first, second)

    assert [resp.status_code for resp in responses] == [200, 200]
    assert verify_calls == 1
    assert sorted(seen_headers) == ["pending=1", "pending=1"]


@pytest.mark.asyncio
async def test_collection_fetch_batch_rejects_pending_cookie_when_sync_verify_fails(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-expired-pending",
        cookies="expired_pending=1",
        verification_status="pending",
        last_verified_at=None,
        slot_id="slot-expired-pending",
    )
    called = False

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        pool = (await db.execute(
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == source_id)
            .where(PlatformCookiePool.platform == "sycm")
        )).scalar_one()
        pool.verification_status = "failed"
        pool.health_score = 0
        pool.last_error_code = "LOGIN_EXPIRED"
        pool.last_verified_at = now_bjt()
        await db.commit()
        return {"source_id": source_id, "verified": False, "status": "expired", "detail": "LOGIN_EXPIRED"}

    async def fake_fetch(params):
        nonlocal called
        called = True
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "should-not-run"},
        }

    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch-batch",
        headers={"X-Run-Token": "valid-token"},
        json={
            **_payload(),
            "concurrency": 2,
            "items": [
                {"request_id": "item-1", "params": {"url": "https://sycm.taobao.com/a.json"}},
            ],
        },
    )

    assert resp.status_code == 412
    error = resp.json()["error"]
    assert error["code"] == "NO_VALID_COOKIE"
    assert error["detail"]["reason"] == "COOKIE_VERIFY_FAILED"
    assert error["detail"]["verify_attempts"] == [
        {
            "source_id": "platform-sycm",
            "reason": "COOKIE_VERIFY_FAILED",
            "status": "expired",
            "verified": False,
            "detail": "LOGIN_EXPIRED",
        }
    ]
    assert called is False


@pytest.mark.asyncio
async def test_collection_fetch_batch_waits_for_peer_advisory_cookie_verify(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service
    from app.datasources.models import PlatformCookiePool

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-peer-pending",
        cookies="peer_pending=1",
        verification_status="pending",
        last_verified_at=None,
        slot_id="slot-peer-pending",
    )
    fetch_calls = []

    async def fake_try_lock(session_factory_arg, key):
        return None, False

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        raise AssertionError("request without advisory lock must not verify")

    async def fake_sleep(delay):
        async with session_factory() as session:
            pool = (await session.execute(
                select(PlatformCookiePool)
                .where(PlatformCookiePool.source_id == "platform-sycm")
                .where(PlatformCookiePool.platform == "sycm")
            )).scalar_one()
            pool.verification_status = "active"
            pool.health_score = 100
            pool.last_error_code = None
            pool.last_verified_at = now_bjt()
            await session.commit()

    async def fake_fetch(params):
        fetch_calls.append(params["cookie_header"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "peer-verified"},
        }

    monkeypatch.setattr(collection_service, "_try_advisory_cookie_verify_lock", fake_try_lock)
    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch-batch",
        headers={"X-Run-Token": "valid-token"},
        json={
            **_payload(),
            "items": [
                {"request_id": "item-1", "params": {"url": "https://sycm.taobao.com/a.json"}},
            ],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["valid_cookie_count"] == 1
    assert data["cookie_verify_attempts"] == []
    assert fetch_calls == ["peer_pending=1"]


@pytest.mark.asyncio
async def test_collection_fetch_batch_rejects_pending_cookie_when_sync_verify_times_out(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser import service as browser_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-timeout-pending",
        cookies="timeout_pending=1",
        verification_status="pending",
        last_verified_at=None,
        slot_id="slot-timeout-pending",
    )
    called = False

    async def fake_verify_login(db, *, source_id: str, user_id: str = "heartbeat"):
        await asyncio.sleep(1)
        return {"source_id": source_id, "verified": True, "status": "valid", "detail": "late"}

    async def fake_fetch(params):
        nonlocal called
        called = True
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "should-not-run"},
        }

    monkeypatch.setenv("SKILLFORGE_COLLECTION_BATCH_COOKIE_VERIFY_WAIT_SECONDS", "0.01")
    monkeypatch.setattr(browser_service, "verify_login", fake_verify_login)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch-batch",
        headers={"X-Run-Token": "valid-token"},
        json={
            **_payload(),
            "items": [
                {"request_id": "item-1", "params": {"url": "https://sycm.taobao.com/a.json"}},
            ],
        },
    )

    assert resp.status_code == 412
    error = resp.json()["error"]
    assert error["code"] == "NO_VALID_COOKIE"
    assert error["detail"]["reason"] == "COOKIE_VERIFY_PENDING_TIMEOUT"
    assert error["detail"]["verify_attempts"][0]["source_id"] == "platform-sycm"
    assert error["detail"]["verify_attempts"][0]["reason"] == "COOKIE_VERIFY_PENDING_TIMEOUT"
    assert called is False


@pytest.mark.asyncio
async def test_collection_fetch_batch_rejects_active_cookie_without_verification_time(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-active-without-verified-at",
        cookies="active_unverified=1",
        verification_status="active",
        last_verified_at=None,
        slot_id="slot-active-unverified",
    )
    called = False

    async def fake_fetch(params):
        nonlocal called
        called = True
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "should-not-run"},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch-batch",
        headers={"X-Run-Token": "valid-token"},
        json={
            **_payload(),
            "items": [
                {"request_id": "item-1", "params": {"url": "https://sycm.taobao.com/a.json"}},
            ],
        },
    )

    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "NO_VALID_COOKIE"
    assert called is False


@pytest.mark.asyncio
async def test_collection_fetch_batch_distributes_concurrent_items_to_valid_cookies(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    _, first_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-batch-1",
        cookies="batch1=1",
        last_verified_at=now_bjt(),
        slot_id="slot-batch-1",
    )
    _, second_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-batch-2",
        cookies="batch2=1",
        last_verified_at=now_bjt(),
        slot_id="slot-batch-2",
    )
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-batch-bad",
        cookies="bad=1",
        verification_status="failed",
        health_score=0,
        slot_id="slot-batch-bad",
    )
    started = 0
    release = asyncio.Event()
    seen_headers = []

    async def fake_fetch(params):
        nonlocal started
        seen_headers.append(params["cookie_header"])
        started += 1
        if started >= 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=2)
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": [params["cookie_header"]]}},
            "proof": {"status": 200, "ok": True, "response_hash": f"batch-{params['cookie_header']}"},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch-batch",
        headers={"X-Run-Token": "valid-token"},
        json={
            **_payload(),
            "concurrency": 3,
            "items": [
                {"request_id": "item-1", "params": {"url": "https://sycm.taobao.com/a.json"}},
                {"request_id": "item-2", "params": {"url": "https://sycm.taobao.com/b.json"}},
            ],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["concurrency"] == 2
    assert data["valid_cookie_count"] == 2
    aliases = {item["result"]["credential_alias"] for item in data["results"]}
    assert aliases == {first_alias, second_alias}
    assert sorted(seen_headers) == ["batch1=1", "batch2=1"]


@pytest.mark.asyncio
async def test_collection_fetch_retries_http_200_business_not_login(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.collection.models import CollectionProof

    client, session_factory = collection_test_app
    _, first_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-expired-200",
        cookies="expired200=1",
        priority=20,
        slot_id="slot-expired-200",
    )
    _, second_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-fresh-200",
        cookies="fresh200=1",
        priority=10,
        slot_id="slot-fresh-200",
    )

    async def fake_fetch(params):
        if params["cookie_header"] == "expired200=1":
            return {
                "success": True,
                "data": {
                    "url": params["url"],
                    "status": 200,
                    "ok": True,
                    "data": {"errorCode": "FAIL_BIZ_NOT_LOGIN", "message": "未登录"},
                },
                "proof": {"status": 200, "ok": True, "response_hash": "biz-login"},
            }
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": {"rows": []}},
            "proof": {"status": 200, "ok": True, "response_hash": "biz-fresh"},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert [item["credential_alias"] for item in data["credential_attempts"]] == [first_alias, second_alias]
    async with session_factory() as session:
        proofs = (await session.execute(select(CollectionProof).order_by(CollectionProof.id))).scalars().all()
    assert [row.status for row in proofs] == ["failed", "success"]
    assert proofs[0].error_code == "FAIL_BIZ_NOT_LOGIN"


@pytest.mark.asyncio
async def test_collection_fetch_does_not_retry_non_cookie_business_error(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    _, first_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-first",
        cookies="first=1",
        priority=20,
        slot_id="slot-first",
    )
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-second",
        cookies="second=1",
        priority=10,
        slot_id="slot-second",
    )
    calls = []

    async def fake_fetch(params):
        calls.append(params["cookie_header"])
        return {
            "success": False,
            "error": "PARAM_INVALID: missing itemId",
            "data": None,
            "proof": {},
        }

    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_fetch)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["credential_alias"] == first_alias
    assert data["attempt_count"] == 1
    assert calls == ["first=1"]


@pytest.mark.asyncio
async def test_collection_fetch_returns_clear_error_without_browser_slot(collection_test_app):
    from app.datasources.models import PlatformCookiePool
    from app.datasources.service import _get_cookie_cipher

    client, session_factory = collection_test_app
    cipher = _get_cookie_cipher()
    async with session_factory() as session:
        session.add(PlatformCookiePool(
            source_id="platform-sycm",
            platform="sycm",
            shop_id="shop-1",
            owner_user_id="owner-1",
            encrypted_cookies=cipher.encrypt(b"a=1").decode(),
            status="active",
            is_active=True,
            health_score=100,
        ))
        await session.commit()

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 503
    body = resp.json()["error"]
    assert body["code"] == "CREDENTIAL_UNAVAILABLE"
    assert body["detail"]["reason"] == "NO_BROWSER_SLOT"


@pytest.mark.asyncio
async def test_collection_fetch_direct_sycm_does_not_require_browser_slot(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.datasources.models import PlatformCookiePool
    from app.datasources.service import _get_cookie_cipher

    client, session_factory = collection_test_app
    cipher = _get_cookie_cipher()
    async with session_factory() as session:
        session.add(PlatformCookiePool(
            source_id="platform-sycm",
            platform="sycm",
            shop_id="shop-1",
            owner_user_id="owner-1",
            encrypted_cookies=cipher.encrypt(b"a=1").decode(),
            status="active",
            is_active=True,
            health_score=100,
        ))
        await session.commit()

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        url = "https://sycm.taobao.com/cc/item/live/view/top.json"
        text = '{"code":0,"data":{"data":{"recordCount":1,"data":[{"item":{"itemId":"8001"}}]}}}'

        def json(self):
            return json.loads(self.text)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            assert kwargs["headers"]["cookie"] == "a=1"
            return FakeResp()

    async def should_not_use_browser(_params):
        raise AssertionError("direct SYCM collection must not require browser slot")

    monkeypatch.setattr(collection_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(collection_service, "fetch_via_browser", should_not_use_browser)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["proof"]["transport"] == "direct_with_platform_cookie"
    assert body["proof"]["browser_slot_id"] == "legacy-default"


@pytest.mark.asyncio
async def test_collection_fetch_direct_market_rank_skips_browser_on_business_error(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.datasources.models import PlatformCookiePool
    from app.datasources.service import _get_cookie_cipher

    client, session_factory = collection_test_app
    cipher = _get_cookie_cipher()
    async with session_factory() as session:
        session.add(PlatformCookiePool(
            source_id="platform-sycm",
            platform="sycm",
            shop_id="shop-1",
            owner_user_id="owner-market",
            encrypted_cookies=cipher.encrypt(b"market=1").decode(),
            status="active",
            is_active=True,
            health_score=100,
        ))
        await session.commit()

    async def fake_direct_once(params):
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {"code": 600091, "message": "No permission"},
            },
            "proof": {"status": 200, "ok": True, "transport": "direct_with_platform_cookie"},
        }

    async def should_not_use_browser(_params):
        raise AssertionError("market rank direct-primary endpoint must not fall back to CDP/browser")

    monkeypatch.setattr(collection_service, "_fetch_direct_with_platform_cookie_once", fake_direct_once)
    monkeypatch.setattr(collection_service, "fetch_via_browser", should_not_use_browser)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(
            mcp_tool_name="tmall_sycm_market_rank",
            data_scope="sycm.market_rank",
            warning_group="sycm.market_read",
            params={
                "url": "https://sycm.taobao.com/mc/mq/mkt/item/live/rank.json",
                "page_url": "https://sycm.taobao.com/mc/free/market_rank",
            },
        ),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "600091"
    assert body["proof"]["transport"] == "direct_with_platform_cookie"
    assert body["proof"]["browser_fallback_skipped"] == "direct_cookie_primary_endpoint"
    assert body["proof"]["browser_slot_id"] == "legacy-default"
    assert body["credential_attempts"][0]["error"] == "600091"


@pytest.mark.asyncio
async def test_collection_fetch_direct_market_rank_retries_login_cookie(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.collection.models import CollectionProof

    client, session_factory = collection_test_app
    _, first_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-market-expired",
        cookies="expired-market=1",
        priority=20,
        slot_id="slot-market-expired",
    )
    _, second_alias = await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-market-fresh",
        cookies="fresh-market=1",
        priority=10,
        slot_id="slot-market-fresh",
    )
    calls = []

    async def fake_direct_once(params):
        calls.append(params["cookie_header"])
        if params["cookie_header"] == "expired-market=1":
            return {
                "success": True,
                "data": {
                    "url": params["url"],
                    "status": 200,
                    "ok": True,
                    "data": {"code": "FAIL_BIZ_NOT_LOGIN", "message": "未登录"},
                },
                "proof": {"status": 200, "ok": True, "transport": "direct_with_platform_cookie"},
            }
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {"code": 0, "data": {"recordCount": 0, "data": []}},
            },
            "proof": {"status": 200, "ok": True, "transport": "direct_with_platform_cookie"},
        }

    async def should_not_use_browser(_params):
        raise AssertionError("market rank direct-primary endpoint must not use CDP/browser")

    monkeypatch.setattr(collection_service, "_fetch_direct_with_platform_cookie_once", fake_direct_once)
    monkeypatch.setattr(collection_service, "fetch_via_browser", should_not_use_browser)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(
            mcp_tool_name="tmall_sycm_market_rank",
            data_scope="sycm.market_rank",
            warning_group="sycm.market_read",
            params={
                "url": "https://sycm.taobao.com/mc/mq/mkt/item/live/rank.json",
                "page_url": "https://sycm.taobao.com/mc/free/market_rank",
            },
        ),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert calls == ["expired-market=1", "fresh-market=1"]
    assert [item["credential_alias"] for item in body["credential_attempts"]] == [first_alias, second_alias]
    async with session_factory() as session:
        proofs = (await session.execute(select(CollectionProof).order_by(CollectionProof.id))).scalars().all()
    assert [row.status for row in proofs] == ["failed", "success"]
    assert proofs[0].error_code == "FAIL_BIZ_NOT_LOGIN"


@pytest.mark.asyncio
async def test_collection_fetch_oneauth_302_falls_back_to_browser(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        owner_user_id="owner-oneauth",
        cookies="oneauth=1",
        slot_id="slot-oneauth",
    )

    class FakeResp:
        status_code = 302
        headers = {"content-type": "text/html; charset=UTF-8"}
        url = "https://sycm.taobao.com/oneauth/api/commDateByLocation.json"
        text = "<html>redirect</html>"

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            assert kwargs["headers"]["cookie"] == "oneauth=1"
            return FakeResp()

    async def fake_browser(params):
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {"rows": [{"day": "2026-05-26"}]},
            },
            "proof": {"status": 200, "ok": True, "transport": "browser"},
        }

    monkeypatch.setattr(collection_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(collection_service, "fetch_via_browser", fake_browser)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(
            mcp_tool_name="tmall_item_flow_required_metrics",
            data_scope="sycm.item_flow",
            warning_group="sycm.flow_read",
            params={
                "url": "https://sycm.taobao.com/oneauth/api/commDateByLocation.json?targetUrl=http%3A%2F%2Fsycm.taobao.com%2Fcc%2Fitem_archives",
                "page_url": "https://sycm.taobao.com/cc/item_archives",
            },
        ),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["proof"]["transport"] == "browser"
    assert body["proof"]["browser_slot_id"] != "legacy-default"


@pytest.mark.asyncio
async def test_collection_fetch_direct_alimama_does_not_require_browser_slot(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(
        session_factory,
        platform="alimama",
        source_id="platform-alimama",
        cookies="_tb_token_=abc; alimama=1",
        slot_id="slot-alimama",
    )

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json;charset=UTF-8"}
        url = "https://one.alimama.com/member/checkAccess.json?bizCode=universalBP"
        text = '{"info":{"ok":true},"data":{"accessInfo":{"csrfId":"csrf-1"}}}'

        def json(self):
            return json.loads(self.text)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            assert method == "POST"
            assert kwargs["headers"]["cookie"] == "_tb_token_=abc; alimama=1"
            assert kwargs["headers"]["origin"] == "https://one.alimama.com"
            assert kwargs["headers"]["referer"] == "https://one.alimama.com/index.html#!/manage/search"
            return FakeResp()

    async def should_not_use_browser(_params):
        raise AssertionError("direct Alimama collection must not require browser slot")

    monkeypatch.setattr(collection_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(collection_service, "fetch_via_browser", should_not_use_browser)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(
            mcp_tool_name="tmall_alimama_item_promotion",
            platform="alimama",
            source_id="platform-alimama",
            data_scope="alimama.campaign_report",
            warning_group="alimama.report_read",
            params={
                "url": "https://one.alimama.com/member/checkAccess.json?bizCode=universalBP",
                "method": "POST",
                "page_url": "https://one.alimama.com/index.html#!/manage/search",
            },
        ),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["proof"]["transport"] == "direct_with_platform_cookie"
    assert body["proof"]["browser_slot_id"] == "legacy-default"


@pytest.mark.asyncio
async def test_collection_fetch_releases_slot_after_exception(collection_test_app, monkeypatch):
    import app.collection.service as collection_service
    from app.browser.models import BrowserSlot

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(session_factory)

    async def boom(_params):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(collection_service, "fetch_via_browser", boom)

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    async with session_factory() as session:
        slot = await session.get(BrowserSlot, "slot-collection-1")
        assert slot.status == "idle"
        assert slot.current_credential_alias is None


@pytest.mark.asyncio
async def test_collection_fetch_opens_warning_group_circuit(collection_test_app, monkeypatch):
    import app.collection.service as collection_service

    client, session_factory = collection_test_app
    await _seed_cookie_pool_and_slot(session_factory)
    monkeypatch.setenv("SKILLFORGE_COLLECTION_CIRCUIT_FAILURES", "2")
    monkeypatch.setenv("SKILLFORGE_COLLECTION_CIRCUIT_COOLDOWN_SECONDS", "60")

    async def fail(_params):
        return {"success": False, "error": "SYCM_WARNING", "data": None, "proof": {}}

    monkeypatch.setattr(collection_service, "fetch_via_browser", fail)

    for _ in range(2):
        resp = await client.post(
            "/api/collection/fetch",
            headers={"X-Run-Token": "valid-token"},
            json=_payload(),
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    resp = await client.post(
        "/api/collection/fetch",
        headers={"X-Run-Token": "valid-token"},
        json=_payload(),
    )

    assert resp.status_code == 503
    body = resp.json()["error"]
    assert body["code"] == "CIRCUIT_OPEN"
    assert "sycm|shop-1|sycm.report_read" == body["detail"]["circuit_key"]
    assert body["detail"]["trace_id"].startswith("circuit-")
