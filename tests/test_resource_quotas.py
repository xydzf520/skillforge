"""M2 资源配额测试。"""

from datetime import datetime

import pytest


@pytest.mark.asyncio
async def test_create_list_and_update_quota(client):
    create_resp = await client.post("/api/approval/quotas", json={
        "org_unit_id": "总部",
        "resource_type": "llm_tokens",
        "period": "monthly",
        "quota_limit": 10000,
        "burst_limit": 12000,
        "enabled": True,
    })
    assert create_resp.status_code == 200
    quota_id = create_resp.json()["id"]

    list_resp = await client.get("/api/approval/quotas?org_unit_id=总部")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    update_resp = await client.put(f"/api/approval/quotas/{quota_id}", json={
        "quota_limit": 15000,
        "enabled": False,
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["quota_limit"] == 15000
    assert update_resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_quota_usage_for_llm_tokens(client):
    from app.auth.models import User
    from app.common.models import UsageLog
    from app.database import async_session_factory

    await client.post("/api/approval/quotas", json={
        "org_unit_id": "总部",
        "resource_type": "llm_tokens",
        "period": "monthly",
        "quota_limit": 1000,
        "burst_limit": 1200,
        "enabled": True,
    })

    async with async_session_factory() as session:
        session.add(
            User(
                id="quota-user",
                username="quota-user",
                name="配额用户",
                role="operator",
                department="总部",
                is_active=True,
                must_change_password=False,
            )
        )
        session.add(
            UsageLog(
                user_id="quota-user",
                department="总部",
                model="gpt-test",
                input_tokens=300,
                output_tokens=200,
                ts=datetime.utcnow(),
            )
        )
        await session.commit()

    usage_resp = await client.get("/api/approval/quotas/usage?org_unit_id=总部")
    assert usage_resp.status_code == 200
    item = usage_resp.json()["items"][0]
    assert item["used"] == 500
    assert item["usage_pct"] == 50.0
    assert item["alert_level"] == "normal"


@pytest.mark.asyncio
async def test_approval_quota_list_contract(client):
    create_resp = await client.post("/api/approval/quotas", json={
        "org_unit_id": "华东区",
        "resource_type": "browser_minutes",
        "period": "daily",
        "quota_limit": 240,
        "burst_limit": 300,
        "enabled": True,
    })
    assert create_resp.status_code == 200

    resp = await client.get("/api/approval/quotas?org_unit_id=华东区")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 1
    assert data["items"][0]["org_unit_id"] == "华东区"
    assert data["items"][0]["resource_type"] == "browser_minutes"
    assert data["items"][0]["period"] == "daily"
    assert data["items"][0]["quota_limit"] == 240
    assert data["items"][0]["burst_limit"] == 300
    assert data["items"][0]["enabled"] is True


@pytest.mark.asyncio
async def test_legacy_dashboard_quota_route_removed(client):
    resp = await client.get("/api/dashboard/quotas")
    assert resp.status_code == 404
