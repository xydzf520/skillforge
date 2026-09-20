"""Retirement must fail closed without disabling independent platform features."""

from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import AppError
from app.coding_agent.runtime_availability import RUNTIME_UNAVAILABLE


@pytest.mark.asyncio
async def test_session_guard_precedes_credentials_and_workspace_resolution(monkeypatch):
    from app.coding_agent.session_service import SessionService

    service = SessionService()
    credentials = AsyncMock(side_effect=AssertionError("credentials must not be loaded"))
    monkeypatch.setattr(service, "_build_subprocess_env_provider_model", credentials)
    with pytest.raises(AppError) as err:
        await service._ensure_session(skill_id="missing", user_id="user")
    assert err.value.code == RUNTIME_UNAVAILABLE
    credentials.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, "true", 1, "false", None])
async def test_admin_cannot_reactivate_removed_runtime(value):
    from app.main import upsert_system_config

    database = AsyncMock()
    with pytest.raises(AppError) as err:
        await upsert_system_config(
            "coding_agent.enabled", {"value": value}, db=database, current_user=object(),
        )
    assert err.value.code == RUNTIME_UNAVAILABLE
    database.execute.assert_not_called()
    database.commit.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_proxy_is_not_registered():
    from app.main import app

    from httpx import ASGITransport, AsyncClient
    # Exercise routing, including routers lazily included by FastAPI.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method in ("POST", "PUT", "DELETE"):
            response = await client.request(method, "/api/coding_agent/upstream/messages")
            assert response.status_code in (404, 405)
