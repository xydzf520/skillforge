import pytest


@pytest.mark.asyncio
async def test_browser_slot_admin_endpoints(client):
    from app.browser.models import BrowserSlot
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(BrowserSlot(
            slot_id="slot-1",
            cdp_url="ws://127.0.0.1/devtools/browser/1",
            node_id="node-1",
            egress_group="eg-a",
            egress_ip_hash="hash-ip",
            status="busy",
            current_pool_id=123,
            current_credential_alias="sycm-shop-owner",
        ))
        await session.commit()

    listed = await client.get("/api/browser/slots")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["slot_id"] == "slot-1"
    assert listed.json()["items"][0]["egress_ip_hash"] == "hash-ip"

    released = await client.post("/api/browser/slots/slot-1/release", json={"reason": "pytest"})
    assert released.status_code == 200
    assert released.json()["status"] == "idle"
    assert released.json()["current_credential_alias"] is None

    maintenance = await client.post("/api/browser/slots/slot-1/maintenance", json={"maintenance": True})
    assert maintenance.status_code == 200
    assert maintenance.json()["status"] == "maintenance"


@pytest.mark.asyncio
async def test_browser_slot_allocation_skips_locked_rows(client):
    from app.browser.models import BrowserSlot
    from app.collection.service import _release_slot, _select_browser_slot
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(BrowserSlot(
            slot_id="slot-lock-1",
            cdp_url="ws://127.0.0.1/devtools/page/lock-1",
            status="idle",
        ))
        await session.commit()

    async with async_session_factory() as session_a:
        slot_a = await _select_browser_slot(
            session_a,
            pool=None,
            credential_alias="sycm-shop-alias",
        )
        assert slot_a is not None
        assert slot_a.slot_id == "slot-lock-1"
        assert slot_a.status == "busy"

        async with async_session_factory() as session_b:
            slot_b = await _select_browser_slot(
                session_b,
                pool=None,
                credential_alias="other-alias",
            )
            assert slot_b is None

        _release_slot(slot_a)
        await session_a.commit()

    async with async_session_factory() as session:
        slot = await session.get(BrowserSlot, "slot-lock-1")
        assert slot.status == "idle"
        assert slot.current_credential_alias is None
