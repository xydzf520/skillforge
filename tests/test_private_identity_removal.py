"""Retired identity endpoints must stay unavailable in a built public deployment."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.router import router
from app.config import Settings
from app.main import SPAStaticFiles


@pytest.fixture
def public_app(tmp_path):
    (tmp_path / "index.html").write_text("<html>public application</html>")
    app = FastAPI()
    app.include_router(router, prefix="/api/auth")
    app.mount("/", SPAStaticFiles(directory=tmp_path, html=True))
    return app


@pytest.mark.parametrize("session_cookie", [None, "retired-session"])
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/auth/edge/me"),
    ("GET", "/api/auth/aiproject/start"),
    ("GET", "/api/auth/aiproject/verify?ticket=retired-ticket"),
    ("GET", "/api/auth/aiproject/oidc/.well-known/openid-configuration"),
    ("GET", "/api/auth/aiproject/oidc/jwks"),
    ("GET", "/api/auth/aiproject/oidc/authorize"),
    ("POST", "/api/auth/aiproject/oidc/token"),
    ("GET", "/api/auth/aiproject/oidc/userinfo"),
])
async def test_retired_endpoints_return_404_with_static_frontend(public_app, method, path, session_cookie):
    from app.auth.dependencies import SESSION_COOKIE

    cookies = {SESSION_COOKIE: session_cookie} if session_cookie else {}
    async with AsyncClient(transport=ASGITransport(app=public_app), base_url="http://test", cookies=cookies) as client:
        response = await client.request(method, path)
    assert response.status_code == 404
    assert "location" not in response.headers
    assert "set-cookie" not in response.headers
    assert not any(key.startswith(("x-intofun-", "x-open-webui-")) for key in response.headers)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def test_unknown_api_does_not_fall_back_to_spa(public_app, method):
    async with AsyncClient(transport=ASGITransport(app=public_app), base_url="http://test") as client:
        response = await client.request(method, "/api/unknown")
        page = await client.get("/skills/example")
    assert response.status_code == 404
    assert page.status_code == 200
    assert "public application" in page.text


def test_retired_configuration_cannot_reenable_identity_provider(monkeypatch):
    monkeypatch.setenv("AI_PROJECT_CALLBACK_ORIGINS", "https://project.example.com")
    monkeypatch.setenv("AI_PROJECT_OIDC_REDIRECT_URIS", "https://project.example.com/callback")
    settings = Settings(_env_file=None)
    assert not any(name.startswith("AI_PROJECT_") for name in settings.model_dump())


async def test_public_auth_entry_points_remain_available(public_app, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DINGTALK_APP_KEY", "")
    monkeypatch.setattr(settings, "DINGTALK_APP_SECRET", "")
    async with AsyncClient(transport=ASGITransport(app=public_app), base_url="http://test") as client:
        providers = await client.get("/api/auth/providers")
        logout = await client.post("/api/auth/logout")
        missing_credentials = await client.post("/api/auth/login", json={})
    assert providers.status_code == 200
    assert providers.json() == {"dingtalk": False}
    assert logout.status_code == 200
    assert missing_credentials.status_code == 422
