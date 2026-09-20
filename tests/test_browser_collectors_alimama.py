from __future__ import annotations

import pytest

from app.browser.collectors.alimama import (
    ALIMAMA_CHECK_ACCESS_URL,
    AlimamaCollector,
)


class _FakeAlimama(AlimamaCollector):
    def __init__(self, *, page: dict | None = None, fetch_responses: dict | None = None):
        super().__init__()
        self.page = page or {
            "url": "https://one.alimama.com/index.html#!/report/account?rptType=account",
            "title": "one",
            "bodyText": "",
        }
        self.fetch_responses = fetch_responses or {}
        self.navigations: list[str] = []
        self.cdp_calls: list[tuple[str, dict]] = []
        self._ws = object()
        self._msg_id = 0

    async def _send(self, method, params=None):
        self.cdp_calls.append((method, params or {}))
        return {}

    async def navigate(self, url, wait_ms=4000):
        self.navigations.append(url)

    async def evaluate(self, expression):
        if "fetch(" in expression:
            for url, resp in self.fetch_responses.items():
                if url in expression:
                    return resp
            return {"status": 0, "ok": False, "url": "?"}
        if "document.title" in expression:
            return self.page
        return None


@pytest.mark.asyncio
async def test_check_login_valid_with_universal_bp_access():
    coll = _FakeAlimama(
        fetch_responses={
            ALIMAMA_CHECK_ACCESS_URL: {
                "status": 200,
                "ok": True,
                "data": {
                    "info": {"ok": True, "message": "OK"},
                    "meta": {"nickName": "demo"},
                },
            }
        },
    )

    result = await coll._check_login({})

    assert result["logged_in"] is True
    assert result["status"] == "valid"
    assert "demo" in result["detail"]
    assert "bizCode=universalBP" in result["check_url"]


@pytest.mark.asyncio
async def test_check_login_expired_when_redirected_to_login():
    coll = _FakeAlimama(
        page={"url": "https://one.alimama.com/index.html#!/login/index", "title": "login", "bodyText": ""},
        fetch_responses={
            ALIMAMA_CHECK_ACCESS_URL: {
                "status": 200,
                "ok": True,
                "data": {"info": {"ok": True, "message": "OK"}},
            }
        },
    )

    result = await coll._check_login({})

    assert result["logged_in"] is False
    assert result["status"] == "expired"


@pytest.mark.asyncio
async def test_check_login_unknown_when_access_data_is_null():
    coll = _FakeAlimama(
        fetch_responses={
            ALIMAMA_CHECK_ACCESS_URL: {
                "status": 200,
                "ok": True,
                "data": None,
            }
        },
    )

    result = await coll._check_login({})

    assert result["logged_in"] is False
    assert result["status"] == "unknown"
    assert "未返回登录态信息" in result["detail"]
