"""SycmCollector 单元测试：API 优先 / DOM 兜底 / Schema 校验 / 资源拦截。

不连真实 Chrome —— 通过替换 BaseCollector._send / evaluate / navigate 来 stub CDP。
"""
from __future__ import annotations

import pytest

from app.browser.collectors.sycm import (
    DASHBOARD_URL,
    LOGIN_CHECK_URL,
    LOGIN_PROBE_URL,
    SycmCollector,
)
from app.browser.schemas import CollectionValidationError


class _FakeSycm(SycmCollector):
    """替换 CDP 调用为可观察的 stub。"""

    def __init__(self, *, eval_responses: dict | None = None, fetch_responses: dict | None = None):
        super().__init__()
        self.eval_responses = eval_responses or {}
        self.fetch_responses = fetch_responses or {}
        self.cdp_calls: list[tuple[str, dict]] = []
        self.navigations: list[str] = []
        # 让 enable_resource_blocking 内的 _send 不报错
        self._ws = object()
        self._msg_id = 0

    async def _send(self, method, params=None):
        self.cdp_calls.append((method, params or {}))
        return {}

    async def navigate(self, url, wait_ms=3000):
        self.navigations.append(url)

    async def evaluate(self, expression):
        # 1) location.href
        if expression == "window.location.href":
            return self.eval_responses.get("location.href")
        # 2) fetch_json 包裹的 JS：识别成 url 找匹配的 fetch_responses
        if "fetch(" in expression:
            for url, resp in self.fetch_responses.items():
                if url in expression:
                    return resp
            return {"status": 0, "ok": False, "url": "?"}
        # 3) DOM 抽取：返回预设的 metrics 列表
        if "result.metrics.push" in expression:
            return self.eval_responses.get("dom_extract")
        return None


# ─────────────── _check_login ───────────────


@pytest.mark.asyncio
async def test_check_login_success_when_url_and_api_both_ok():
    coll = _FakeSycm(
        eval_responses={"location.href": "https://sycm.taobao.com/portal/home.htm"},
        fetch_responses={
            LOGIN_PROBE_URL: {"status": 200, "ok": True, "data": {"code": 0, "data": {"recordCount": 1}}},
        },
    )
    result = await coll._check_login({})
    assert result["logged_in"] is True
    assert "code=0" in (result["detail"] or "")
    assert coll.navigations == [LOGIN_CHECK_URL]


@pytest.mark.asyncio
async def test_check_login_fails_on_401_even_if_url_looks_ok():
    """业务 API 401 → 即使 URL 看起来是登录后的也判未登录。"""
    coll = _FakeSycm(
        eval_responses={"location.href": "https://sycm.taobao.com/portal/home.htm"},
        fetch_responses={
            LOGIN_PROBE_URL: {"status": 401, "ok": False},
        },
    )
    result = await coll._check_login({})
    assert result["logged_in"] is False
    assert "401" in (result["detail"] or "")


@pytest.mark.asyncio
async def test_check_login_fails_on_login_url():
    coll = _FakeSycm(
        eval_responses={"location.href": "https://login.taobao.com/passport/login"},
        fetch_responses={LOGIN_PROBE_URL: {"status": 200, "ok": True, "data": {"code": 0}}},
    )
    result = await coll._check_login({})
    assert result["logged_in"] is False


@pytest.mark.asyncio
async def test_check_login_fails_on_business_error_code():
    """业务 API 200 但 errorCode = NOT_LOGIN → 也算未登录。"""
    coll = _FakeSycm(
        eval_responses={"location.href": "https://sycm.taobao.com/portal/home.htm"},
        fetch_responses={
            LOGIN_PROBE_URL: {
                "status": 200, "ok": True,
                "data": {"errorCode": "FAIL_BIZ_NOT_LOGIN"},
            },
        },
    )
    result = await coll._check_login({})
    assert result["logged_in"] is False
    assert "FAIL_BIZ_NOT_LOGIN" in (result["detail"] or "")


@pytest.mark.asyncio
async def test_check_login_fails_on_business_login_code_5810():
    """业务 API 200 但 code=5810/login message → 明确判未登录。"""
    coll = _FakeSycm(
        eval_responses={"location.href": "https://sycm.taobao.com/portal/home.htm"},
        fetch_responses={
            LOGIN_PROBE_URL: {
                "status": 200,
                "ok": True,
                "data": {"code": 5810, "message": "You must login system first."},
            },
        },
    )
    result = await coll._check_login({})
    assert result["logged_in"] is False
    assert result["status"] == "expired"
    assert "5810" in (result["detail"] or "")


@pytest.mark.asyncio
async def test_check_login_unknown_on_page_probe_without_login_route():
    """页面探测无结论且没有明确进入登录页时，不能误报 cookies 已失效。"""
    coll = _FakeSycm(
        eval_responses={"location.href": "chrome-error://chromewebdata/"},
        fetch_responses={
            LOGIN_PROBE_URL: {"status": None, "ok": False},
        },
    )
    result = await coll._check_login({})
    assert result["logged_in"] is False
    assert result["status"] == "unknown"


# ─────────────── _collect_dashboard：API 优先 ───────────────


@pytest.mark.asyncio
async def test_dashboard_uses_api_when_cache_hit(monkeypatch):
    api_url = "https://sycm.taobao.com/portal/api/getOverview.json"

    coll = _FakeSycm(
        eval_responses={"location.href": "https://sycm.taobao.com/portal/home.htm"},
        fetch_responses={
            LOGIN_PROBE_URL: {"status": 200, "ok": True, "data": {"code": 0, "data": {"recordCount": 1}}},
            api_url: {
                "status": 200, "ok": True,
                "data": {
                    "overview": {
                        "salesAmount": 3540000,
                        "orderCount": 1234,
                        "uvRate": "5.2%",
                    }
                },
            },
        },
    )

    async def fake_try_api_first(self):
        # 模拟 PlatformApiCache 命中：直接调 fetch_json + extract_metrics
        resp = await self.fetch_json(api_url)
        metrics = self._extract_metrics_from_api(resp.get("data"))
        if not metrics:
            return None
        return {
            "metrics": metrics,
            "source": "api",
            "page_url": DASHBOARD_URL,
        }

    monkeypatch.setattr(SycmCollector, "_try_api_first", fake_try_api_first)

    result = await coll._collect_dashboard({})
    assert result["source"] == "api"
    assert len(result["metrics"]) >= 2
    labels = {m["label"] for m in result["metrics"]}
    assert "salesAmount" in labels


@pytest.mark.asyncio
async def test_dashboard_falls_back_to_dom_when_api_misses(monkeypatch):
    coll = _FakeSycm(
        eval_responses={
            "location.href": "https://sycm.taobao.com/portal/home.htm",
            "dom_extract": {
                "metrics": [
                    {"label": "销售额", "value": "¥3,540,000"},
                    {"label": "访客数", "value": "10000"},
                ]
            },
        },
        fetch_responses={
            LOGIN_PROBE_URL: {"status": 200, "ok": True, "data": {"code": 0, "data": {"recordCount": 1}}},
        },
    )

    async def no_api(self):
        return None  # API 缓存未命中

    monkeypatch.setattr(SycmCollector, "_try_api_first", no_api)

    result = await coll._collect_dashboard({})
    assert result["source"] == "dom"
    assert len(result["metrics"]) == 2


@pytest.mark.asyncio
async def test_dashboard_raises_validation_when_dom_empty(monkeypatch):
    coll = _FakeSycm(
        eval_responses={
            "location.href": "https://sycm.taobao.com/portal/home.htm",
            "dom_extract": {"metrics": []},
        },
        fetch_responses={
            LOGIN_PROBE_URL: {"status": 200, "ok": True, "data": {"code": 0, "data": {"recordCount": 1}}},
        },
    )

    async def no_api(self):
        return None

    monkeypatch.setattr(SycmCollector, "_try_api_first", no_api)

    with pytest.raises(CollectionValidationError) as exc:
        await coll._collect_dashboard({})
    assert "metrics" in exc.value.detail


@pytest.mark.asyncio
async def test_dashboard_raises_when_not_logged_in():
    coll = _FakeSycm(
        eval_responses={"location.href": "https://login.taobao.com/passport/login"},
        fetch_responses={LOGIN_PROBE_URL: {"status": 401, "ok": False}},
    )
    with pytest.raises(CollectionValidationError) as exc:
        await coll._collect_dashboard({})
    assert "未登录" in exc.value.detail


# ─────────────── 资源拦截 ───────────────


@pytest.mark.asyncio
async def test_resource_blocking_called_on_login():
    coll = _FakeSycm(
        eval_responses={"location.href": "https://sycm.taobao.com/portal/home.htm"},
        fetch_responses={LOGIN_PROBE_URL: {"status": 200, "ok": True, "data": {"code": 0}}},
    )
    await coll._check_login({})
    methods = [c[0] for c in coll.cdp_calls]
    assert "Network.enable" in methods
    assert "Network.setBlockedURLs" in methods
    # 校验默认列表里有 png/woff 之类
    block_call = next(c for c in coll.cdp_calls if c[0] == "Network.setBlockedURLs")
    urls = block_call[1].get("urls") or []
    assert any("png" in u for u in urls)
    assert any("woff" in u for u in urls)


# ─────────────── _extract_metrics_from_api ───────────────


def test_extract_metrics_picks_amount_count_rate_keys():
    data = {
        "overview": {
            "salesAmount": 3540000,
            "orderCount": 1234,
            "conversionRate": "5.2%",
            "title": "should be ignored",  # 不含 metric hint，不入选
        }
    }
    metrics = SycmCollector._extract_metrics_from_api(data)
    labels = {m["label"] for m in metrics}
    assert labels == {"salesAmount", "orderCount", "conversionRate"}


def test_extract_metrics_handles_lists_and_depth_cap():
    data = {
        "items": [
            {"itemValue": 1, "name": "skip"},
            {"itemValue": 2, "name": "skip"},
        ]
    }
    metrics = SycmCollector._extract_metrics_from_api(data)
    # itemValue 字段被收集；同 label 去重 → 只 1 条
    assert len(metrics) == 1
    assert metrics[0]["label"] == "itemValue"


def test_extract_metrics_skips_empty_values():
    metrics = SycmCollector._extract_metrics_from_api({"saleAmount": ""})
    assert metrics == []
