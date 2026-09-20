"""生意参谋数据采集器：API 优先 + DOM 兜底 + Schema 校验。

任务类型：
- check_login：登态检测（URL + 业务 API 双重判断）
- dashboard：经营概览（先 PlatformApiCache 已知 API → 失败再 DOM 兜底）
- discover：跑 capture_apis，把发现的 API 落库到 PlatformApiCache（人工触发一次即可）
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import urlparse

import httpx
from loguru import logger
from sqlalchemy import select

from app.browser.collector_base import BaseCollector, register_collector
from app.browser.schemas import (
    CollectionValidationError,
    validate_collection_result,
)
from app.common.time_utils import now_bjt


SYCM_DOMAIN = "sycm.taobao.com"
DASHBOARD_URL = "https://sycm.taobao.com/portal/home.htm"
DASHBOARD_PATH = "/portal/home.htm"
# 登态页面使用商品排行页，避免 portal 首页在部分账号下跳转到 tmall 页面导致无结论。
LOGIN_CHECK_URL = "https://sycm.taobao.com/cc/item_rank?dateType=today"
# 业务 API 探测点：登录后返回 200 + code=0 + recordCount，未登录返回 401/403 或 NOT_LOGIN。
LOGIN_PROBE_URL = (
    "https://sycm.taobao.com/cc/item/live/view/top.json?"
    "dateType=today&pageSize=1&page=1&order=desc&orderBy=payAmt"
    "&keyword=&follow=false&cateId=&cateLevel=&indexCode=payAmt,itmUv"
)
LOGIN_PROBE_EXCLUDED_COOKIES = {"QNWORKBENCH_SESSION", "EGG_SESS1"}
LOGIN_PROBE_NOT_LOGIN_ERROR_CODES = {"FAIL_BIZ_NOT_LOGIN", "NOT_LOGIN"}
LOGIN_PROBE_NOT_LOGIN_CODES = {"5810"}
DEFAULT_LOGIN_PROBE_USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 Chrome Safari/537.36"


def interpret_login_probe_response(api_resp: dict) -> dict:
    """Convert the SYCM item-rank probe response into valid/expired/unknown.

    The probe endpoint can return HTTP 200 for both usable and unusable cookies,
    so HTTP status alone is not enough. code=0 is the only clear success signal.
    """
    status = api_resp.get("status") if isinstance(api_resp, dict) else None
    if status in (401, 403):
        return {"status": "expired", "logged_in": False, "detail": f"业务 API 返回 {status}"}
    if not (isinstance(status, int) and 200 <= status < 400):
        return {"status": "unknown", "logged_in": False, "detail": f"业务 API 返回非常规状态 {status}"}

    payload = api_resp.get("data") if isinstance(api_resp.get("data"), dict) else {}
    error_code = str(payload.get("errorCode") or "")
    code = str(payload.get("code") if payload.get("code") is not None else "")
    message = str(payload.get("msg") or payload.get("message") or "")
    message_lower = message.lower()
    if error_code in LOGIN_PROBE_NOT_LOGIN_ERROR_CODES:
        return {"status": "expired", "logged_in": False, "detail": f"业务码: {error_code}"}
    if code in LOGIN_PROBE_NOT_LOGIN_CODES or "login" in message_lower or "登录" in message:
        detail = f"业务码: {code}" if code else "业务 API 要求登录"
        if message:
            detail = f"{detail} {message}"[:200]
        return {"status": "expired", "logged_in": False, "detail": detail}
    if code == "0":
        record_count = ""
        data = payload.get("data")
        if isinstance(data, dict):
            if "recordCount" in data:
                record_count = f"，商品数 {data.get('recordCount')}"
            inner = data.get("data")
            if not record_count and isinstance(inner, dict) and "recordCount" in inner:
                record_count = f"，商品数 {inner.get('recordCount')}"
        return {
            "status": "valid",
            "logged_in": True,
            "detail": f"商品排行 API {status} code=0{record_count}",
        }
    return {"status": "unknown", "logged_in": False, "detail": f"业务 API {status} 但业务码无结论"}


async def fetch_login_probe_with_cookie_header(
    cookie_header: str,
    *,
    user_agent: str | None = None,
    timeout: int | float = 8,
) -> dict:
    if not cookie_header:
        return {"status": None, "detail": "未读取到生意参谋 cookies"}
    headers = {
        "accept": "application/json, text/plain, */*",
        "cookie": cookie_header,
        "referer": LOGIN_CHECK_URL,
        "user-agent": user_agent or DEFAULT_LOGIN_PROBE_USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(LOGIN_PROBE_URL, headers=headers)
        try:
            data = resp.json()
        except ValueError:
            data = None
        result = {
            "url": str(resp.url),
            "status": resp.status_code,
            "ok": 200 <= resp.status_code < 400,
            "content_type": resp.headers.get("content-type", ""),
        }
        if isinstance(data, dict):
            result["data"] = data
        else:
            result["text_preview"] = resp.text[:500]
        return result
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "detail": f"直连业务 API 异常: {exc}"}


async def verify_sycm_cookie_header(
    cookie_header: str,
    *,
    user_agent: str | None = None,
    timeout: int | float = 8,
) -> dict:
    probe = await fetch_login_probe_with_cookie_header(cookie_header, user_agent=user_agent, timeout=timeout)
    interpreted = interpret_login_probe_response(probe)
    return {**interpreted, "probe": probe}


@register_collector
class SycmCollector(BaseCollector):
    platform = "sycm"

    async def collect(self, task_type: str, params: dict) -> dict:
        dispatch = {
            "dashboard": self._collect_dashboard,
            "check_login": self._check_login,
            "discover": self._discover_apis,
        }
        fn = dispatch.get(task_type)
        if not fn:
            raise ValueError(
                f"sycm 不支持任务类型: {task_type}，可用: {list(dispatch.keys())}"
            )
        return await fn(params)

    # ─────────────── 登态检测 ───────────────

    async def _check_login(self, params: dict) -> dict:
        await self.enable_resource_blocking()
        await self.navigate(LOGIN_CHECK_URL, wait_ms=3000)

        url_str = (await self.evaluate("window.location.href")) or ""
        url_lower = url_str.lower()
        url_is_login_route = "login" in url_lower or "passport" in url_lower

        api_logged_in: bool | None = None
        api_detail = ""
        try:
            api_resp = await self._fetch_login_probe_with_cookies()
            if api_resp.get("status") is None:
                api_resp = await self.fetch_json(LOGIN_PROBE_URL)
            interpreted = interpret_login_probe_response(api_resp)
            api_logged_in = bool(interpreted.get("logged_in")) if interpreted.get("status") in ("valid", "expired") else None
            api_detail = interpreted.get("detail") or ""
        except Exception as exc:  # noqa: BLE001
            api_detail = f"业务 API 探测异常: {exc}"

        # 三态：valid（业务 API 明确 OK）/ expired（明确进入登录页或 API 明确未登录）/
        # unknown（页面 400、跳转异常、API 无状态等无结论场景）。避免把 Cookie 过大等采集异常误报为失效。
        if api_logged_in is False or url_is_login_route:
            status = "expired"
            logged_in = False
        elif api_logged_in is True:
            status = "valid"
            logged_in = True
        else:
            # URL 像是还在 dashboard，但 API 探测异常/非常规状态 —— 判不清
            status = "unknown"
            logged_in = False

        result = {
            "logged_in": logged_in,
            "status": status,
            "current_url": url_str or "about:blank",
            "detail": api_detail,
        }
        return validate_collection_result(self.platform, "check_login", result)

    async def _fetch_login_probe_with_cookies(self) -> dict:
        """Use CDP cookies for a direct server-side probe.

        SYCM pages can redirect to error.taobao.com even while the business API is usable.
        In that state browser-context fetch becomes cross-origin and returns no structured
        status, so the heartbeat reads Chrome cookies and probes the business API directly.
        """
        cookie_result = await self._send("Network.getAllCookies")
        cookies = cookie_result.get("cookies") if isinstance(cookie_result, dict) else []
        cookie_header = self._build_sycm_cookie_header(cookies or [])
        if not cookie_header:
            return {"status": None, "detail": "未读取到生意参谋 cookies"}

        user_agent = await self.evaluate("navigator.userAgent") or DEFAULT_LOGIN_PROBE_USER_AGENT
        return await fetch_login_probe_with_cookie_header(cookie_header, user_agent=str(user_agent))

    @staticmethod
    def _build_sycm_cookie_header(cookies: list[dict]) -> str:
        pairs: list[str] = []
        seen: set[str] = set()
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            domain = str(cookie.get("domain") or "").lstrip(".").lower()
            if (
                not name
                or name in seen
                or name in LOGIN_PROBE_EXCLUDED_COOKIES
                or not (domain == "taobao.com" or domain.endswith(".taobao.com"))
            ):
                continue
            seen.add(name)
            pairs.append(f"{name}={value}")
        return "; ".join(pairs)

    # ─────────────── Dashboard：API 优先 + DOM 兜底 ───────────────

    async def _collect_dashboard(self, params: dict) -> dict:
        login = await self._check_login({})
        if not login.get("logged_in"):
            raise CollectionValidationError(
                self.platform,
                "dashboard",
                f"未登录: {login.get('detail') or login.get('current_url')}",
            )

        api_result = await self._try_api_first()
        if api_result is not None:
            logger.info("[sycm] dashboard 走 API 路径，metrics={}", len(api_result.get("metrics") or []))
            return validate_collection_result(self.platform, "dashboard", api_result)

        logger.info("[sycm] dashboard 降级到 DOM 抽取")
        dom_result = await self._try_dom_extract()
        return validate_collection_result(self.platform, "dashboard", dom_result)

    async def _try_api_first(self) -> dict | None:
        from app.browser.models import PlatformApiCache
        from app.database import async_session_factory

        async with async_session_factory() as db:
            row = (await db.execute(
                select(PlatformApiCache).where(
                    PlatformApiCache.domain == SYCM_DOMAIN,
                    PlatformApiCache.page_path == DASHBOARD_PATH,
                )
            )).scalar_one_or_none()

        if not row or not row.apis_json:
            return None

        candidates = [
            api for api in (row.apis_json.get("data_apis") or [])
            if api.get("url") and (api.get("method", "GET") or "GET").upper() == "GET"
        ][:5]
        if not candidates:
            return None

        for api in candidates:
            api_url = api["url"]
            try:
                resp = await self.fetch_json(api_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[sycm] API 调用异常 url={}: {}", api_url, exc)
                continue
            if not isinstance(resp, dict):
                continue
            status = resp.get("status")
            if not isinstance(status, int) or not (200 <= status < 400):
                continue
            metrics = self._extract_metrics_from_api(resp.get("data"))
            if metrics:
                return {
                    "metrics": metrics,
                    "source": "api",
                    "page_url": DASHBOARD_URL,
                }
        return None

    async def _try_dom_extract(self) -> dict:
        await self.enable_resource_blocking()
        await self.navigate(DASHBOARD_URL, wait_ms=3000)
        # 给 SPA JS 一点渲染时间；比之前的硬 3s 稍短，靠 enable_resource_blocking 加快网络
        await asyncio.sleep(2)

        data = await self.evaluate("""
        (() => {
            const result = { metrics: [] };
            const cards = document.querySelectorAll(
                '[class*="data"], [class*="card"], [class*="metric"], [class*="summary"]'
            );
            const seen = new Set();
            for (const card of cards) {
                const label = card.querySelector(
                    '[class*="label"], [class*="name"], [class*="title"], dt, .desc'
                );
                const value = card.querySelector(
                    '[class*="value"], [class*="num"], [class*="count"], dd, .number'
                );
                if (label && value) {
                    const k = label.textContent.trim();
                    const v = value.textContent.trim();
                    if (k && v && !seen.has(k) && k.length < 30) {
                        seen.add(k);
                        result.metrics.push({label: k, value: v});
                    }
                }
            }
            return result;
        })()
        """) or {}

        return {
            "metrics": data.get("metrics") or [],
            "source": "dom",
            "page_url": DASHBOARD_URL,
        }

    # ─────────────── 一次性 API 发现：填充 PlatformApiCache ───────────────

    async def _discover_apis(self, params: dict) -> dict:
        from app.browser.collectors.generic import GenericCollector
        from app.browser.models import PlatformApiCache
        from app.browser.service import guard_discover_url
        from app.database import async_session_factory

        page_url = params.get("url") or DASHBOARD_URL
        wait_ms = params.get("wait_ms", 8000)

        # SSRF 防御（兜底）：sycm 专属 discover 强制 hostname = sycm.taobao.com
        page_url = guard_discover_url(page_url, allowed_host=SYCM_DOMAIN)

        # 复用当前 ws 跑 generic._capture_apis（不重连）
        gc = GenericCollector()
        gc._ws = self._ws
        gc._msg_id = self._msg_id
        capture = await gc._capture_apis({"url": page_url, "wait_ms": wait_ms})
        self._msg_id = gc._msg_id

        parsed = urlparse(page_url)
        domain = parsed.hostname or SYCM_DOMAIN
        path = parsed.path or DASHBOARD_PATH
        api_count = capture.get("data_apis_count", 0)

        async with async_session_factory() as db:
            existing = (await db.execute(
                select(PlatformApiCache).where(
                    PlatformApiCache.domain == domain,
                    PlatformApiCache.page_path == path,
                )
            )).scalar_one_or_none()
            if existing:
                existing.apis_json = capture
                existing.api_count = api_count
                existing.discovery_method = "sycm_collector"
                existing.updated_at = now_bjt()
            else:
                db.add(PlatformApiCache(
                    domain=domain,
                    page_path=path,
                    page_title=params.get("page_title") or "生意参谋首页",
                    apis_json=capture,
                    api_count=api_count,
                    discovery_method="sycm_collector",
                    updated_by="sycm_discover",
                ))
            await db.commit()

        return {
            "discovered_apis": api_count,
            "domain": domain,
            "page_path": path,
            "page_url": page_url,
        }

    # ─────────────── 工具：从 API 响应里提取 metrics ───────────────

    @staticmethod
    def _extract_metrics_from_api(data) -> list[dict]:
        """尽力从 API JSON 里抽取 (label, value) 二元组。

        约定：键名包含 amount/count/value/rate/ratio/num/sum 的字段视为指标候选。
        递归遍历 dict/list，最多 30 条。
        """
        if not isinstance(data, (dict, list)):
            return []

        metrics: list[dict] = []
        seen: set[str] = set()
        METRIC_HINTS = ("amount", "count", "value", "rate", "ratio", "num", "sum", "total")

        def add(label: str, value):
            label = (label or "").strip()
            if not label or label in seen:
                return
            sval = "" if value is None else str(value).strip()
            if not sval:
                return
            seen.add(label)
            metrics.append({"label": label[:40], "value": sval[:200]})

        def walk(node, depth=0):
            if depth > 6 or len(metrics) >= 30:
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        walk(v, depth + 1)
                    elif isinstance(v, (str, int, float, bool)) and isinstance(k, str):
                        if any(h in k.lower() for h in METRIC_HINTS):
                            add(k, v)
            elif isinstance(node, list):
                for it in node[:30]:
                    walk(it, depth + 1)

        walk(data)
        return metrics[:30]
