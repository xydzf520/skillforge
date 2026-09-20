from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.database as db_mod
from app.auth.models import User
from app.codex import service as codex_service
from app.codex.models import CodexCliSession, CodexMcpCallAudit, SfDataRecord
from app.common.exceptions import AppError
from app.common.time_utils import now_bjt
from app.sf.data_service import get_sf_data_record, list_sf_data_records


async def _seed_cli_session(raw_token: str = "sf-data-token") -> str:
    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        session.add(
            User(
                id="admin",
                username="admin",
                name="Admin",
                role="admin",
                state="active",
                can_view_all=True,
                is_active=True,
                must_change_password=False,
            )
        )
        session.add(
            CodexCliSession(
                id="sf-data-session",
                user_id="admin",
                token_hash=codex_service.token_hash(raw_token),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                expires_at=now + timedelta(days=1),
                created_at=now,
            )
        )
        await session.commit()
    return raw_token


def test_mcp_catalog_includes_sf_data_store_tools():
    catalog = codex_service.mcp_catalog_for_user(SimpleNamespace(id="admin", role="admin", can_view_all=True))
    tools = {tool["name"]: tool for tool in (catalog.get("servers") or [{}])[0].get("tools") or []}

    assert tools["skillforge_sf_data_write"]["meta"]["write"] is True
    assert tools["skillforge_sf_data_write"]["meta"]["data_scope"] == "sf.data_store"
    assert "namespace" in tools["skillforge_sf_data_write"]["inputSchema"]["properties"]
    assert tools["skillforge_sf_data_list"]["meta"]["write"] is False
    assert tools["skillforge_sf_data_get"]["meta"]["write"] is False


@pytest.mark.asyncio
async def test_sf_data_api_dry_run_real_write_and_overview(client):
    dry = await client.post(
        "/api/sf/data",
        json={
            "namespace": "demo.metrics",
            "title": "Demo Data",
            "data": {"token": "secret-value", "count": 3},
            "metadata": {"source": "unit"},
            "dry_run": True,
        },
    )
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True

    created = await client.post(
        "/api/sf/data",
        json={
            "namespace": "demo.metrics",
            "title": "Demo Data",
            "data": {"token": "secret-value", "count": 3},
            "metadata": {"source": "unit"},
            "skill_id": "skill-a",
            "dry_run": False,
            "idempotency_key": "demo-metrics-1",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["created"] is True
    record = body["record"]
    assert record["namespace"] == "demo.metrics"
    assert record["data"]["token"] == "***"
    assert record["data"]["count"] == 3

    again = await client.post(
        "/api/sf/data",
        json={
            "namespace": "demo.metrics",
            "data": {"count": 99},
            "dry_run": False,
            "idempotency_key": "demo-metrics-1",
        },
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["record"]["id"] == record["id"]

    listing = await client.get("/api/sf/data", params={"namespace": "demo.metrics"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == record["id"]

    detail = await client.get(f"/api/sf/data/{record['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["count"] == 3

    overview = await client.get("/api/sf/overview", params={"days": 30, "skill_id": "skill-a"})
    assert overview.status_code == 200
    assert overview.json()["data_records"]["total"] == 1


@pytest.mark.asyncio
async def test_sf_data_mcp_write_requires_idempotency_and_is_audited(client, monkeypatch):
    raw_token = await _seed_cli_session()
    monkeypatch.setattr(codex_service.audit, "log", AsyncMock())

    missing_key = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_sf_data_write",
            "arguments": {"namespace": "mcp.demo", "data": {"value": 1}},
            "run_mode": "mcp_cli",
            "dry_run": False,
        },
    )
    assert missing_key.status_code == 400

    dry = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_sf_data_write",
            "arguments": {"namespace": "mcp.demo", "data": {"value": 1}},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert dry.status_code == 200
    assert dry.json()["data"]["dry_run"] is True

    real = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_sf_data_write",
            "arguments": {"namespace": "mcp.demo", "data": {"value": 1}},
            "run_mode": "mcp_cli",
            "dry_run": False,
            "idempotency_key": "mcp-demo-1",
        },
    )
    assert real.status_code == 200
    created = real.json()["data"]["record"]
    assert created["namespace"] == "mcp.demo"

    get_resp = await client.post(
        "/api/codex/mcp/call",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "tool": "skillforge_sf_data_get",
            "arguments": {"id": created["id"]},
            "run_mode": "mcp_cli",
            "dry_run": True,
        },
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["data"]["value"] == 1

    async with db_mod.async_session_factory() as session:
        count = (await session.execute(select(SfDataRecord))).scalars().all()
        audits = (await session.execute(select(CodexMcpCallAudit).where(CodexMcpCallAudit.tool == "skillforge_sf_data_write"))).scalars().all()
    assert len(count) == 1
    assert len(audits) >= 2
    assert any(row.dry_run is False and row.ok is True for row in audits)


@pytest.mark.asyncio
async def test_sf_data_visibility_private_is_owner_only(client):
    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        owner = User(
            id="owner",
            username="owner",
            name="Owner",
            role="aibp",
            department="AI",
            state="active",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
        )
        peer = User(
            id="peer",
            username="peer",
            name="Peer",
            role="aibp",
            department="AI",
            state="active",
            can_view_all=False,
            is_active=True,
            must_change_password=False,
        )
        session.add_all([owner, peer])
        session.add(
            SfDataRecord(
                id="sfdata_private",
                namespace="private.demo",
                title="Private",
                content_type="json",
                data_json={"value": 1},
                metadata_json={},
                sha256="a" * 64,
                size_bytes=12,
                source="test",
                user_id="owner",
                department="AI",
                visibility="private",
                idempotency_key="private-demo",
                created_at=now,
            )
        )
        await session.commit()

    async with db_mod.async_session_factory() as session:
        owner = await session.get(User, "owner")
        peer = await session.get(User, "peer")
        owner_list = await list_sf_data_records(session, owner, namespace="private.demo")
        peer_list = await list_sf_data_records(session, peer, namespace="private.demo")

        assert owner_list["total"] == 1
        assert peer_list["total"] == 0
        with pytest.raises(AppError) as exc:
            await get_sf_data_record(session, peer, "sfdata_private")
        assert exc.value.code == "AUTH_PERMISSION_DENIED"
