"""浏览器自动化服务：容器生命周期 + CDP 连接 + 采集调度"""

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import socket
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.collector_base import (
    BrowserRetryableError,
    build_fetch_json_js,
)
from app.browser.models import BrowserSession, CollectionTask, PlatformApiCache
from app.browser.schemas import CollectionValidationError
from app.common.retry import retry_with_backoff
from app.common.exceptions import AppError
from app.config import settings
from app.common.time_utils import isoformat_bjt, now_bjt


# ─── SSRF 防御：discover / capture_apis / fetch-json 入口 URL 校验 ──────────────────
# 业务上 discover 是让 Chrome 去访问"外部平台"站点，禁止访问本地 / 内网 / 元数据服务。
#
# 注意：这只是第一道防线。
#   - 字面量 IP（含 IPv4 / IPv6 / IPv6 mapped IPv4）会被直接挡住。
#   - 对域名做一次性 DNS 解析并校验，但浏览器后续再次解析时可被 DNS rebinding
#     rebind 到 127.0.0.1 / 169.254.169.254 等。根治需要出口网络隔离或 allowlist。


class DiscoverUrlError(ValueError):
    """discover / fetch URL 校验失败（scheme / host / SSRF 风险等）"""


# 内网域名后缀黑名单（简单拦常见命名）
_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".lan", ".corp", ".intranet")
# 显式危险主机名
_BLOCKED_HOSTS = {"localhost", "0.0.0.0", "::", "::1", "ip6-localhost", "ip6-loopback"}


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """IP 是否属于 loopback / private / link-local / multicast / reserved / metadata。"""
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and str(ip) == "169.254.169.254":
        return True
    return False


def _host_is_blocked(host: str) -> bool:
    """host 是否应被拒绝。覆盖显式黑名单、IPv4/IPv6 字面量、IPv6 mapped IPv4、内网后缀。"""
    if not host:
        return True
    h = host.strip().strip("[]").lower()
    if h in _BLOCKED_HOSTS:
        return True
    if any(h.endswith(suf) for suf in _BLOCKED_HOST_SUFFIXES):
        return True
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        return False  # 不是字面量 IP，交给 guard_discover_url 走 DNS 再判
    if _ip_is_blocked(addr):
        return True
    # IPv6 mapped IPv4（`::ffff:127.0.0.1`）
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None and _ip_is_blocked(mapped):
            return True
    return False


def guard_discover_url(
    url: str, *, field: str = "url", allowed_host: str | None = None,
) -> str:
    """校验一个由外部输入拼出来的 URL 是否可以交给浏览器去请求。

    规则：
      1. scheme 必须是 http/https
      2. 必须有 host；若 allowed_host 指定，host 必须完全等于
      3. host 不得落入内网/保留/链路本地/回环（含 IPv6 mapped IPv4）
      4. 若 host 是域名，一次性 DNS 解析所有 A/AAAA，任一落入内网都拒掉
      5. URL 长度上限 2048

    失败抛 DiscoverUrlError，调用方翻译为 AppError("PARAM_INVALID", 400)。
    """
    if not isinstance(url, str) or not url:
        raise DiscoverUrlError(f"{field} 不能为空")
    if len(url) > 2048:
        raise DiscoverUrlError(f"{field} 超出 2048 字符限制")
    try:
        parsed = urlparse(url)
    except Exception as e:  # noqa: BLE001
        raise DiscoverUrlError(f"{field} 解析失败: {e}") from e
    if parsed.scheme.lower() not in ("http", "https"):
        raise DiscoverUrlError(f"{field} 仅允许 http/https，收到: {parsed.scheme!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise DiscoverUrlError(f"{field} 缺少 host")
    if allowed_host is not None and host != allowed_host.lower():
        raise DiscoverUrlError(
            f"{field} hostname 必须为 {allowed_host!r}，收到 {host!r}"
        )
    if _host_is_blocked(host):
        raise DiscoverUrlError(f"{field} 指向内网/保留地址: {host}")
    # 域名 → DNS 解析所有 A/AAAA
    try:
        ipaddress.ip_address(host)
        return url  # 字面量 IP 已判过
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise DiscoverUrlError(f"{field} DNS 解析失败: {e}")
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _ip_is_blocked(addr):
            raise DiscoverUrlError(
                f"{field} 域名 {host} 解析到内网 IP: {ip_str}"
            )
        if isinstance(addr, ipaddress.IPv6Address):
            mapped = addr.ipv4_mapped
            if mapped is not None and _ip_is_blocked(mapped):
                raise DiscoverUrlError(
                    f"{field} 域名 {host} 解析到 IPv6-mapped 内网 IP: {ip_str}"
                )
    return url


# ─── 并发与缓存（"页面池 light"） ────────────────────────────────────
# Semaphore 限制同时跑的采集任务数，避免单 Chrome 实例被打爆
#
# 为何默认 BROWSER_COLLECT_CONCURRENCY = 1？
# 当前 _get_cdp_ws_url() 永远拿 Chrome 的"第一个 page"，并通过 _WS_URL_CACHE 按 cdp_url
# 全局缓存。多个 collect/heartbeat/discover 并发时会串行操作同一个 tab —— navigate /
# evaluate / fetch_json / capture_apis 互相踩 DOM 和网络状态，导致登录心跳假阳、抓包
# 串线、DOM 结果混杂。cookie_heartbeat_loop 常驻后该 race 更易触发。
# 真正的并发能力需要"每个任务 Target.createTarget 新 page，结束后 closeTarget"，
# 在 page pool 就位前先把并发降回 1。线上调优时可通过 settings.BROWSER_COLLECT_CONCURRENCY 覆盖。
_COLLECT_SEMAPHORE: asyncio.Semaphore | None = None

# ws_url 短期缓存：避免每次 collect 都 HTTP poll /json 找 page target
_WS_URL_CACHE: dict[str, tuple[str, float]] = {}
_WS_URL_TTL = 5.0  # 秒
_VERIFY_BROWSER_USE_LOCK = asyncio.Lock()
_VERIFY_LOCKS: dict[str, asyncio.Lock] = {}
_VERIFY_LOCKS_GUARD = asyncio.Lock()


def _runtime_cdp_url() -> str:
    return f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"


def _configured_verify_cdp_url() -> str:
    host = (settings.BROWSER_VERIFY_CDP_HOST or settings.BROWSER_CDP_HOST).strip()
    port = int(settings.BROWSER_VERIFY_CDP_PORT or settings.BROWSER_CDP_PORT)
    return f"http://{host}:{port}"


async def select_verify_cdp_url() -> tuple[str, str]:
    """Return (cdp_url, role) for login verification.

    The dedicated verify browser avoids sharing one page target with manual
    browsing and runtime collections. If it is not running, verification falls
    back to the runtime browser and the caller serializes access with
    _VERIFY_BROWSER_USE_LOCK.
    """
    runtime_url = _runtime_cdp_url()
    verify_url = _configured_verify_cdp_url()
    if verify_url != runtime_url and await _check_cdp(verify_url):
        return verify_url, "verify"
    return runtime_url, "runtime"


async def get_cookie_verify_lock(
    *,
    source_id: str,
    platform: str,
    shop_id: str,
    credential_id: str | int | None = None,
) -> asyncio.Lock:
    key = ":".join([
        str(source_id or "").lower(),
        str(platform or "").lower(),
        str(shop_id or ""),
        str(credential_id or ""),
    ])
    async with _VERIFY_LOCKS_GUARD:
        lock = _VERIFY_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _VERIFY_LOCKS[key] = lock
        return lock


def _get_collect_semaphore() -> asyncio.Semaphore:
    global _COLLECT_SEMAPHORE
    if _COLLECT_SEMAPHORE is None:
        _COLLECT_SEMAPHORE = asyncio.Semaphore(settings.BROWSER_COLLECT_CONCURRENCY)
    return _COLLECT_SEMAPHORE


def _bjt_iso() -> str:
    return isoformat_bjt(now_bjt()) or ""


def _stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_json(value) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _data_shape(value) -> tuple[list[str], int | None]:
    """返回可审计 proof 需要的 data_keys / row_count。"""
    data = value.get("data") if isinstance(value, dict) and "data" in value else value
    if isinstance(data, dict):
        return list(data.keys())[:50], None
    if isinstance(data, list):
        keys: list[str] = []
        if data and isinstance(data[0], dict):
            keys = list(data[0].keys())[:50]
        return keys, len(data)
    return [], None


def build_response_proof(fetch_payload, *, page_url: str | None = None, requested_url: str | None = None) -> dict:
    """为 replay/fetch 结果生成证据摘要，不存完整响应体。"""
    data_keys, row_count = _data_shape(fetch_payload)
    proof = {
        "verified_at": _bjt_iso(),
        "page_url": page_url,
        "api_url": requested_url or (fetch_payload.get("url") if isinstance(fetch_payload, dict) else None),
        "response_hash": _sha256_json(fetch_payload),
        "data_keys": data_keys,
        "row_count": row_count,
    }
    if isinstance(fetch_payload, dict):
        proof.update({
            "status": fetch_payload.get("status"),
            "ok": fetch_payload.get("ok"),
            "content_type": fetch_payload.get("content_type"),
        })
    return proof


def attach_capture_proof(
    apis_json: dict,
    *,
    page_url: str | None = None,
    capture_task_id: int | None = None,
    browser_session_id: int | None = None,
    login_source_id: str | None = None,
) -> dict:
    """把 capture proof 和注册表元信息写入 apis_json 顶层，兼容旧 DB schema。"""
    if not isinstance(apis_json, dict):
        return apis_json

    result = dict(apis_json)
    resolved_page_url = page_url or result.get("url")
    response_hashes = [
        api.get("response_hash")
        for api in (result.get("data_apis") or [])
        if isinstance(api, dict) and api.get("response_hash")
    ]
    api_proofs = []
    for index, api in enumerate(result.get("data_apis") or []):
        if not isinstance(api, dict):
            continue
        api_proofs.append({
            "api_index": index,
            "url": api.get("url"),
            "method": api.get("method"),
            "status": api.get("status"),
            "response_hash": api.get("response_hash"),
            "data_keys": api.get("data_keys") or api.get("data_sample_keys") or [],
            "row_count": api.get("row_count") or api.get("data_count"),
        })

    captured_at = result.get("_proof", {}).get("captured_at") if isinstance(result.get("_proof"), dict) else None
    proof = {
        **(result.get("_proof") if isinstance(result.get("_proof"), dict) else {}),
        "captured_at": captured_at or _bjt_iso(),
        "page_url": resolved_page_url,
        "capture_task_id": capture_task_id,
        "browser_session_id": browser_session_id,
        "login_source_id": login_source_id,
        "response_hashes": response_hashes,
        "api_proofs": api_proofs,
    }
    registry_meta = {
        **(result.get("_registry_meta") if isinstance(result.get("_registry_meta"), dict) else {}),
        "page_url": resolved_page_url,
        "capture_task_id": capture_task_id,
        "browser_session_id": browser_session_id,
        "login_source_id": login_source_id,
        "response_hashes": response_hashes,
        "last_verified_at": (result.get("_registry_meta") or {}).get("last_verified_at")
        if isinstance(result.get("_registry_meta"), dict) else None,
        "verification_status": (result.get("_registry_meta") or {}).get("verification_status")
        if isinstance(result.get("_registry_meta"), dict) else "captured",
    }
    result["_proof"] = proof
    result["_registry_meta"] = registry_meta
    return result


# ═══════════════════════════════════════════════
# 容器管理
# ═══════════════════════════════════════════════

def _compose_file() -> str:
    """返回 docker-compose 文件绝对路径"""
    p = Path(settings.BROWSER_COMPOSE_FILE)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent.parent / p
    return str(p)


async def start_browser(db: AsyncSession, user_id: str = "") -> dict:
    """启动浏览器容器"""
    # 检查是否已在运行
    status = await get_browser_status()
    if status["status"] == "running":
        return status

    compose = _compose_file()
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose, "up", "-d", "browser",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("[browser] 容器启动失败: {}", stderr.decode())
        raise RuntimeError(f"容器启动失败: {stderr.decode()[:200]}")

    proc_verify = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose, "up", "-d", "browser-verify",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, verify_stderr = await proc_verify.communicate()
    if proc_verify.returncode != 0:
        logger.warning("[browser] 验证浏览器未启动，登态验证将回退运行浏览器: {}", verify_stderr.decode()[:200])

    # 等待 CDP 就绪
    cdp_url = f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"
    ok = await _wait_for_cdp(cdp_url, timeout=settings.BROWSER_HEALTH_TIMEOUT)
    if not ok:
        raise RuntimeError("Chrome CDP 启动超时")

    # 获取容器 ID
    proc2 = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose, "ps", "-q", "browser",
        stdout=asyncio.subprocess.PIPE,
    )
    out2, _ = await proc2.communicate()
    container_id = out2.decode().strip().split("\n")[0]

    # 写入/更新数据库
    row = (await db.execute(
        select(BrowserSession).where(BrowserSession.name == "default")
    )).scalar_one_or_none()
    if not row:
        row = BrowserSession(name="default", created_by=user_id)
        db.add(row)
    row.container_id = container_id[:12] if container_id else ""
    row.status = "running"
    row.cdp_url = cdp_url
    row.novnc_url = f"http://{settings.BROWSER_NOVNC_HOST}:{settings.BROWSER_NOVNC_PORT}"
    row.updated_at = now_bjt()
    await db.commit()

    logger.info("[browser] 容器已启动 container={}", row.container_id)

    # 自动注入已存储的平台 cookies
    try:
        injected = await inject_stored_cookies(db, cdp_url)
        logger.info("[browser] cookies 注入完成: {}", injected)
    except Exception as e:
        logger.warning("[browser] cookies 注入失败（非致命）: {}", e)

    return await get_browser_status()


async def stop_browser(db: AsyncSession) -> dict:
    """停止浏览器容器（保留数据卷）"""
    compose = _compose_file()
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose, "stop", "browser",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    proc_verify = await asyncio.create_subprocess_exec(
        "docker", "compose", "-f", compose, "stop", "browser-verify",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc_verify.communicate()

    row = (await db.execute(
        select(BrowserSession).where(BrowserSession.name == "default")
    )).scalar_one_or_none()
    if row:
        row.status = "stopped"
        row.updated_at = now_bjt()
        await db.commit()

    logger.info("[browser] 容器已停止")
    return {"status": "stopped"}


async def get_browser_status() -> dict:
    """获取浏览器容器状态"""
    cdp_url = f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"
    cdp_ok = await _check_cdp(cdp_url)
    novnc_ok = await _check_novnc()

    if cdp_ok:
        # 获取 Chrome 版本信息
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{cdp_url}/json/version")
            version_info = resp.json()
        verify_cdp_url = _configured_verify_cdp_url()
        verify_cdp_role = (
            "verify"
            if verify_cdp_url != cdp_url and await _check_cdp(verify_cdp_url)
            else "runtime"
        )
        return {
            "status": "running",
            "cdp_url": cdp_url,
            "verify_cdp_url": verify_cdp_url,
            "verify_cdp_role": verify_cdp_role,
            "novnc_available": novnc_ok,
            "browser": version_info.get("Browser", ""),
            "pages": await _get_page_count(cdp_url),
        }
    verify_cdp_url = _configured_verify_cdp_url()
    verify_cdp_role = (
        "verify"
        if verify_cdp_url != cdp_url and await _check_cdp(verify_cdp_url)
        else "runtime"
    )
    return {
        "status": "stopped",
        "cdp_url": None,
        "verify_cdp_url": verify_cdp_url,
        "verify_cdp_role": verify_cdp_role,
        "novnc_available": False,
    }


# ═══════════════════════════════════════════════
# Cookies 注入
# ═══════════════════════════════════════════════

# 平台 source_id → cookie domain 映射
PLATFORM_DOMAINS = {
    "platform-taobao": [".taobao.com"],
    "platform-sycm": [".taobao.com", ".sycm.taobao.com"],
    "platform-alimama": [".alimama.com", ".taobao.com"],
    "platform-douyin": [".douyin.com", ".jinritemai.example.test"],
    "platform-jd": [".jd.com"],
    "platform-pdd": [".pinduoduo.com"],
}


def _cookie_pool_runtime_order():
    from app.datasources.models import PlatformCookiePool

    return (
        PlatformCookiePool.priority.desc(),
        PlatformCookiePool.health_score.desc(),
        PlatformCookiePool.last_used_at.asc().nullsfirst(),
        PlatformCookiePool.pushed_at.desc(),
    )


async def inject_stored_cookies(db: AsyncSession, cdp_url: str | None = None) -> dict:
    """从数据库读取所有平台 cookies，注入到 Docker Chrome"""
    from app.datasources.service import get_cookie_details as get_stored_cookie_details
    from app.datasources.service import get_cookies as get_stored_cookies
    from app.datasources.models import PlatformCookiePool

    if not cdp_url:
        cdp_url = f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"

    if not await _check_cdp(cdp_url):
        return {"error": "浏览器未运行"}

    # Network.setCookies 需要 browser-level WebSocket
    ws_url = await _get_browser_ws_url(cdp_url)
    results = {}

    for source_id, domains in PLATFORM_DOMAINS.items():
        try:
            source_platform = source_id.removeprefix("platform-")
            pool_row = (await db.execute(
                select(PlatformCookiePool)
                .where(PlatformCookiePool.source_id == source_id)
                .where(PlatformCookiePool.platform == source_platform)
                .where(PlatformCookiePool.is_active == True)  # noqa: E712
                .where(PlatformCookiePool.status == "active")
                .order_by(*_cookie_pool_runtime_order())
                .limit(1)
            )).scalar_one_or_none()
            if pool_row:
                cookie_details = await get_stored_cookie_details(
                    db,
                    source_id,
                    platform=pool_row.platform,
                    shop_id=pool_row.shop_id,
                    owner_user_id=pool_row.owner_user_id,
                    connector_key_id=pool_row.connector_key_id,
                )
                cookie_str = await get_stored_cookies(
                    db,
                    source_id,
                    platform=pool_row.platform,
                    shop_id=pool_row.shop_id,
                    owner_user_id=pool_row.owner_user_id,
                    connector_key_id=pool_row.connector_key_id,
                )
            else:
                cookie_details = await get_stored_cookie_details(db, source_id)
                cookie_str = await get_stored_cookies(db, source_id)
            if not cookie_details and not cookie_str:
                results[source_id] = "无 cookies"
                continue

            cdp_cookies = []
            seen = set()

            def same_site(value: str | None) -> str | None:
                mapping = {
                    "no_restriction": "None",
                    "lax": "Lax",
                    "strict": "Strict",
                }
                return mapping.get((value or "").lower())

            if cookie_details:
                for item in cookie_details:
                    name = str(item.get("name") or "").strip()
                    value = str(item.get("value") or "")
                    domain = str(item.get("domain") or "").strip()
                    if not name or not domain:
                        continue
                    path = str(item.get("path") or "/")
                    key = (domain, path, name)
                    if key in seen:
                        continue
                    seen.add(key)
                    cookie = {
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                        "secure": bool(item.get("secure")),
                        "httpOnly": bool(item.get("httpOnly")),
                    }
                    ss = same_site(item.get("sameSite"))
                    if ss:
                        cookie["sameSite"] = ss
                    expires = item.get("expirationDate")
                    if isinstance(expires, (int, float)) and expires > 0:
                        cookie["expires"] = float(expires)
                    cdp_cookies.append(cookie)
            else:
                # 兼容旧版本扩展：只有 name=value 字符串时仍注入到平台默认域。
                pairs = [p.strip() for p in (cookie_str or "").split(";") if "=" in p]
                for pair in pairs:
                    name, val = pair.split("=", 1)
                    name = name.strip()
                    if name in seen:
                        continue
                    seen.add(name)
                    for domain in domains:
                        cdp_cookies.append({
                            "name": name,
                            "value": val.strip(),
                            "domain": domain,
                            "path": "/",
                        })

            if not cdp_cookies:
                results[source_id] = "无有效 cookies"
                continue

            # 通过 CDP 注入（page-level，因为 Network 域在 page target 上可用）
            import websockets, json
            page_ws_url = await _get_cdp_ws_url(cdp_url)
            async with websockets.connect(page_ws_url, max_size=10 * 1024 * 1024) as ws:
                # 先启用 Network 域
                await ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
                await ws.recv()
                # 设置 cookies
                await ws.send(json.dumps({
                    "id": 2,
                    "method": "Network.setCookies",
                    "params": {"cookies": cdp_cookies},
                }))
                resp = json.loads(await ws.recv())
                # 跳过事件消息，找 id=2 的响应
                while resp.get("id") != 2:
                    resp = json.loads(await ws.recv())
                if "error" in resp:
                    results[source_id] = f"注入失败: {resp['error']}"
                else:
                    suffix = f"（Cookie 池 {pool_row.platform}/{pool_row.shop_id}）" if pool_row else ""
                    results[source_id] = f"注入 {len(seen)} 个 cookies{suffix}"

        except Exception as e:
            results[source_id] = f"异常: {e}"

    return results


async def clear_browser_cookies(cdp_url: str) -> None:
    """Clear browser-level cookies for an isolated verification browser."""
    if not await _check_cdp(cdp_url):
        raise AppError("BROWSER_NOT_RUNNING", 503, {"detail": "浏览器未运行"})

    import websockets
    page_ws_url = await _get_cdp_ws_url(cdp_url, force_refresh=True)
    async with websockets.connect(page_ws_url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
        await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Network.clearBrowserCookies", "params": {}}))
        resp = json.loads(await ws.recv())
        while resp.get("id") != 2:
            resp = json.loads(await ws.recv())
        if "error" in resp:
            raise AppError("COOKIE_CLEAR_FAILED", 502, {"detail": resp["error"]})


def _cookie_details_for_cdp(cookie_details: list[dict], *, fallback_domains: list[str]) -> list[dict]:
    cdp_cookies: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in cookie_details:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        raw_domain = str(item.get("domain") or "").strip()
        domains = [raw_domain] if raw_domain else fallback_domains
        if not name:
            continue
        path = str(item.get("path") or "/")
        for domain in domains:
            if not domain:
                continue
            key = (domain, path, name)
            if key in seen:
                continue
            seen.add(key)
            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": bool(item.get("secure")),
                "httpOnly": bool(item.get("httpOnly")),
            }
            same_site = _same_site_for_cdp(item.get("sameSite"))
            if same_site:
                cookie["sameSite"] = same_site
            expires = item.get("expirationDate")
            if isinstance(expires, (int, float)) and expires > 0:
                cookie["expires"] = float(expires)
            cdp_cookies.append(cookie)
    return cdp_cookies


def _cookie_header_for_cdp(cookie_header: str, *, domains: list[str]) -> list[dict]:
    cdp_cookies: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for pair in [p.strip() for p in (cookie_header or "").split(";") if "=" in p]:
        name, value = pair.split("=", 1)
        name = name.strip()
        if not name:
            continue
        for domain in domains:
            key = (domain, name)
            if key in seen:
                continue
            seen.add(key)
            cdp_cookies.append({
                "name": name,
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            })
    return cdp_cookies


async def inject_cookie_pool_to_browser(
    db: AsyncSession,
    pool,
    *,
    cdp_url: str | None = None,
    clear_existing: bool = False,
) -> dict:
    """Inject one selected cookie credential into Docker Chrome."""
    from app.datasources.service import get_cookie_details as get_stored_cookie_details
    from app.datasources.service import get_cookies as get_stored_cookies

    if not cdp_url:
        cdp_url = f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"
    if not await _check_cdp(cdp_url):
        raise AppError("BROWSER_NOT_RUNNING", 503, {"detail": "浏览器未运行"})

    source_id = str(getattr(pool, "source_id", "") or "")
    domains = PLATFORM_DOMAINS.get(source_id) or [str(getattr(pool, "domain", "") or "")]
    domains = [domain for domain in domains if domain]
    cookie_details = await get_stored_cookie_details(
        db,
        source_id,
        platform=pool.platform,
        shop_id=pool.shop_id,
        owner_user_id=pool.owner_user_id,
        connector_key_id=pool.connector_key_id,
    )
    cookie_header = await get_stored_cookies(
        db,
        source_id,
        platform=pool.platform,
        shop_id=pool.shop_id,
        owner_user_id=pool.owner_user_id,
        connector_key_id=pool.connector_key_id,
    )
    cdp_cookies = _cookie_details_for_cdp(cookie_details or [], fallback_domains=domains)
    if not cdp_cookies:
        cdp_cookies = _cookie_header_for_cdp(cookie_header or "", domains=domains)
    if not cdp_cookies:
        raise AppError("NO_COOKIE", 412, {"detail": "该 credential 没有可注入 cookie"})

    import websockets
    page_ws_url = await _get_cdp_ws_url(cdp_url, force_refresh=True)
    async with websockets.connect(page_ws_url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
        await ws.recv()
        if clear_existing:
            await ws.send(json.dumps({"id": 2, "method": "Network.clearBrowserCookies", "params": {}}))
            resp = json.loads(await ws.recv())
            while resp.get("id") != 2:
                resp = json.loads(await ws.recv())
            if "error" in resp:
                raise AppError("COOKIE_CLEAR_FAILED", 502, {"detail": resp["error"]})
        await ws.send(json.dumps({
            "id": 3,
            "method": "Network.setCookies",
            "params": {"cookies": cdp_cookies},
        }))
        resp = json.loads(await ws.recv())
        while resp.get("id") != 3:
            resp = json.loads(await ws.recv())
        if "error" in resp:
            raise AppError("COOKIE_INJECT_FAILED", 502, {"detail": resp["error"]})
    return {"injected": len(cdp_cookies), "source_id": source_id, "platform": pool.platform, "shop_id": pool.shop_id}


async def sync_cookies_to_browser(db: AsyncSession) -> dict:
    """手动触发 cookies 同步到 Docker Chrome（API 调用）"""
    cdp_url = f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"
    return await inject_stored_cookies(db, cdp_url)


async def navigate_to_url(url: str) -> dict:
    """在 Docker Chrome 中打开指定 URL（通过 CDP Page.navigate）"""
    import json, websockets
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅允许 http/https URL，不允许 {parsed.scheme}:")

    cdp_url = f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"
    if not await _check_cdp(cdp_url):
        raise RuntimeError("浏览器未运行，请先启动 Docker 浏览器")

    page_ws_url = await _get_cdp_ws_url(cdp_url)
    async with websockets.connect(page_ws_url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Page.navigate",
            "params": {"url": url},
        }))
        resp = json.loads(await ws.recv())
        while resp.get("id") != 1:
            resp = json.loads(await ws.recv())
        if "error" in resp:
            return {"ok": False, "error": resp["error"]}
        return {"ok": True, "url": url, "frameId": resp.get("result", {}).get("frameId")}


# ═══════════════════════════════════════════════
# 采集任务
# ═══════════════════════════════════════════════

async def run_collection(
    db: AsyncSession,
    *,
    platform: str,
    task_type: str,
    params: dict | None = None,
    user_id: str = "",
    cdp_url: str | None = None,
    force_new_page: bool = False,
) -> dict:
    """执行数据采集任务（带并发限流 + 自动重试 + schema 校验）。"""
    from app.browser.collector_base import get_collector

    # 创建任务记录
    task = CollectionTask(
        platform=platform,
        task_type=task_type,
        params=params or {},
        status="running",
        created_by=user_id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    cdp_url = cdp_url or _runtime_cdp_url()
    if not await _check_cdp(cdp_url):
        task.status = "failed"
        task.error_message = "浏览器未运行"
        task.completed_at = now_bjt()
        await db.commit()
        return {"task_id": task.id, "status": "failed", "error": "浏览器未运行"}

    async def _do_collect() -> dict:
        # 每次重试都重新解析 ws_url（旧 page target 可能已失效）
        target_id: str | None = None
        if force_new_page:
            ws_url, target_id = await _create_page_target(cdp_url)
        else:
            ws_url = await _get_cdp_ws_url(cdp_url, force_refresh=False)
        collector = get_collector(platform)
        try:
            return await asyncio.wait_for(collector.run(ws_url, task_type, params or {}), timeout=35)
        except BrowserRetryableError:
            # 强制下次重试拿新 ws_url
            _WS_URL_CACHE.pop(cdp_url, None)
            raise
        finally:
            if target_id:
                await _close_target(cdp_url, target_id)

    try:
        async with _get_collect_semaphore():
            result = await retry_with_backoff(
                _do_collect,
                max_retries=2,
                base_delay=2.0,
                max_delay=10.0,
                retryable_exceptions=(BrowserRetryableError,),
                description=f"browser.collect[{platform}.{task_type}]",
            )
        if task_type == "capture_apis" and isinstance(result, dict):
            task_params = params or {}
            result = attach_capture_proof(
                result,
                page_url=task_params.get("url") or result.get("url"),
                capture_task_id=task.id,
                browser_session_id=task.browser_session_id,
                login_source_id=task_params.get("login_source_id"),
            )

        task.status = "success"
        task.result = result
        task.completed_at = now_bjt()
        await db.commit()
        logger.info(
            "[browser] 采集完成 platform={} type={} task_id={}",
            platform, task_type, task.id,
        )
        return {"task_id": task.id, "status": "success", "result": result}

    except CollectionValidationError as exc:
        task.status = "failed"
        task.error_message = f"schema 校验失败: {exc.detail}"[:500]
        task.completed_at = now_bjt()
        await db.commit()
        logger.warning(
            "[browser] 采集结果 schema 不通过 platform={} type={}: {}",
            platform, task_type, exc.detail,
        )
        return {
            "task_id": task.id,
            "status": "failed",
            "error": f"采集结果校验失败: {exc.detail}",
            "error_kind": "validation",
        }
    except Exception as e:  # noqa: BLE001
        task.status = "failed"
        task.error_message = str(e)[:500]
        task.completed_at = now_bjt()
        await db.commit()
        logger.error(
            "[browser] 采集失败 platform={} type={}: {}",
            platform, task_type, e,
        )
        return {
            "task_id": task.id,
            "status": "failed",
            "error": str(e)[:200],
            "error_kind": "runtime",
        }


# ═══════════════════════════════════════════════
# Discover：一键给某个平台抓 API 表
# ═══════════════════════════════════════════════

# platform → 是否有专属 discover 任务（其它平台一律走 generic.capture_apis）
_PLATFORM_HAS_OWN_DISCOVER = {"sycm"}


async def run_discover(
    db: AsyncSession,
    *,
    url: str,
    platform: str = "generic",
    page_title: str = "",
    wait_ms: int = 8000,
    user_id: str = "",
) -> dict:
    """对某个 URL 跑一次 capture_apis 并把结果落到 PlatformApiCache。

    管理员一键刷新某平台 API 表用。
    返回 {discovered_apis, domain, page_path, page_url, task_id}
    """
    # SSRF 防御：所有 discover URL 必须 https 且不指向内网 / 元数据服务
    allowed_host = "sycm.taobao.com" if platform == "sycm" else None
    url = guard_discover_url(url, allowed_host=allowed_host)

    if platform in _PLATFORM_HAS_OWN_DISCOVER:
        # sycm 等有自定义 discover 的走专属路径（更稳，会用平台默认 URL）
        result = await run_collection(
            db,
            platform=platform,
            task_type="discover",
            params={"url": url, "page_title": page_title, "wait_ms": wait_ms},
            user_id=user_id,
        )
        if result.get("status") != "success":
            return {
                "discovered_apis": 0,
                "error": result.get("error"),
                "task_id": result.get("task_id"),
            }
        payload = result.get("result") or {}
        return {
            "discovered_apis": payload.get("discovered_apis", 0),
            "domain": payload.get("domain"),
            "page_path": payload.get("page_path"),
            "page_url": payload.get("page_url"),
            "task_id": result.get("task_id"),
        }

    # 通用路径：generic.capture_apis → 手动 upsert PlatformApiCache
    cap_result = await run_collection(
        db,
        platform="generic",
        task_type="capture_apis",
        params={"url": url, "wait_ms": wait_ms},
        user_id=user_id,
    )
    if cap_result.get("status") != "success":
        return {
            "discovered_apis": 0,
            "error": cap_result.get("error"),
            "task_id": cap_result.get("task_id"),
        }

    apis_json = attach_capture_proof(
        cap_result.get("result") or {},
        page_url=url,
        capture_task_id=cap_result.get("task_id"),
        login_source_id=None,
    )
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    page_path = parsed.path or "/"
    api_count = apis_json.get("data_apis_count", 0)

    existing = (await db.execute(
        select(PlatformApiCache).where(
            PlatformApiCache.domain == domain,
            PlatformApiCache.page_path == page_path,
        )
    )).scalar_one_or_none()
    if existing:
        existing.apis_json = apis_json
        existing.api_count = api_count
        existing.page_title = page_title or existing.page_title
        existing.discovery_method = "browser_discover_endpoint"
        existing.updated_by = user_id or "discover"
        existing.updated_at = now_bjt()
    else:
        db.add(PlatformApiCache(
            domain=domain,
            page_path=page_path,
            page_title=page_title or url,
            apis_json=apis_json,
            api_count=api_count,
            discovery_method="browser_discover_endpoint",
            updated_by=user_id or "discover",
        ))
    await db.commit()

    return {
        "discovered_apis": api_count,
        "domain": domain,
        "page_path": page_path,
        "page_url": url,
        "task_id": cap_result.get("task_id"),
    }


# ═══════════════════════════════════════════════
# 登态心跳：主动验证平台 cookie 是否仍有效
# ═══════════════════════════════════════════════

# source_id → (platform, check_login task_type)
PLATFORM_LOGIN_PROBE = {
    "platform-sycm": ("sycm", "check_login"),
    "platform-alimama": ("alimama", "check_login"),
}


async def _direct_verify_source_cookie(db: AsyncSession, source_id: str) -> dict | None:
    """Verify source login with the stored credential before using Chrome.

    SYCM has a stable item-rank probe that can validate the exact cookie stored
    in the credential pool. This avoids false unknown results caused by runtime
    Chrome carrying stale cookies with the same names.
    """
    if source_id != "platform-sycm":
        return None
    try:
        from app.browser.collectors import sycm
        from app.datasources.models import PlatformCookiePool
        from app.datasources.service import _get_cookie_cipher

        row = (await db.execute(
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == source_id)
            .where(PlatformCookiePool.platform == "sycm")
            .where(PlatformCookiePool.is_active == True)  # noqa: E712
            .where(PlatformCookiePool.status == "active")
            .order_by(*_cookie_pool_runtime_order())
            .limit(1)
        )).scalar_one_or_none()
        if not row:
            return None
        cipher = _get_cookie_cipher()
        cookie_header = cipher.decrypt(row.encrypted_cookies.encode("utf-8")).decode("utf-8")
        result = await sycm.verify_sycm_cookie_header(cookie_header, user_agent=row.user_agent)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[browser] direct verify skipped source={}: {}", source_id, exc)
        return None

    status = result.get("status")
    if status not in ("valid", "expired"):
        return None
    detail = str(result.get("detail") or "")
    logged_in = bool(result.get("logged_in"))
    await _write_verify_status(db, source_id, status=str(status), detail=detail)
    with contextlib.suppress(Exception):
        await _write_cookie_pool_verify_status(
            db,
            source_id=source_id,
            platform="sycm",
            status=str(status),
            detail=detail,
        )
    return {
        "source_id": source_id,
        "verified": logged_in,
        "status": str(status),
        "detail": detail,
        "cdp_role": "direct_cookie",
    }


async def verify_login(
    db: AsyncSession,
    *,
    source_id: str,
    user_id: str = "heartbeat",
) -> dict:
    """主动跑一次 check_login 并把结果写回 DataSource.config，供前端展示登态。

    返回 {source_id, verified, status, detail, last_verified_at}
    """
    probe = PLATFORM_LOGIN_PROBE.get(source_id)
    if not probe:
        return {
            "source_id": source_id,
            "verified": False,
            "status": "unsupported",
            "detail": f"未配置 source_id={source_id} 的登态探测",
        }
    platform, task_type = probe

    direct_result = await _direct_verify_source_cookie(db, source_id)
    if direct_result is not None:
        return direct_result

    cdp_url, cdp_role = await select_verify_cdp_url()
    if not await _check_cdp(cdp_url):
        await _write_verify_status(
            db, source_id,
            status="unknown",
            detail="浏览器未运行，跳过本次心跳",
        )
        return {
            "source_id": source_id,
            "verified": False,
            "status": "unknown",
            "detail": "浏览器未运行",
        }

    if user_id == "heartbeat" and _VERIFY_BROWSER_USE_LOCK.locked():
        logger.info("[heartbeat] {} 跳过：验证浏览器忙，避免共享 target 冲突", source_id)
        return {
            "source_id": source_id,
            "verified": False,
            "status": "unknown",
            "detail": "SKIPPED_BROWSER_BUSY",
            "skipped": True,
        }

    async def _run_probe() -> dict:
        if cdp_role == "verify" or user_id != "heartbeat":
            with contextlib.suppress(Exception):
                await clear_browser_cookies(cdp_url)
        with contextlib.suppress(Exception):
            await inject_stored_cookies(db, cdp_url)
        return await run_collection(
            db,
            platform=platform,
            task_type=task_type,
            params={},
            user_id=user_id,
            cdp_url=cdp_url,
            force_new_page=cdp_role == "verify",
        )

    try:
        async with _VERIFY_BROWSER_USE_LOCK:
            result = await _run_probe()
    except Exception as exc:  # noqa: BLE001
        await _write_verify_status(
            db, source_id,
            status="unknown",
            detail=f"心跳执行异常: {exc}",
        )
        return {
            "source_id": source_id,
            "verified": False,
            "status": "unknown",
            "detail": str(exc)[:200],
        }

    if result.get("status") != "success":
        await _write_verify_status(
            db, source_id,
            status="unknown",
            detail=result.get("error") or "采集失败",
        )
        return {
            "source_id": source_id,
            "verified": False,
            "status": "unknown",
            "detail": result.get("error"),
        }

    payload = result.get("result") or {}
    logged_in = bool(payload.get("logged_in"))
    detail = payload.get("detail") or payload.get("current_url") or ""
    # 优先使用采集器返回的三态 status；没有（非 sycm 或旧版）则降级到 bool 判定
    payload_status = payload.get("status")
    if payload_status in ("valid", "expired", "unknown"):
        new_status = payload_status
    else:
        new_status = "valid" if logged_in else "expired"
    await _write_verify_status(db, source_id, status=new_status, detail=detail)
    with contextlib.suppress(Exception):
        await _write_cookie_pool_verify_status(
            db,
            source_id=source_id,
            platform=platform,
            status=new_status,
            detail=detail,
        )

    return {
        "source_id": source_id,
        "verified": logged_in,
        "status": new_status,
        "detail": detail,
        "current_url": payload.get("current_url"),
        "cdp_role": cdp_role,
    }


async def _write_cookie_pool_verify_status(
    db: AsyncSession,
    *,
    source_id: str,
    platform: str,
    status: str,
    detail: str = "",
) -> None:
    from app.datasources.models import PlatformCookiePool

    row = (await db.execute(
        select(PlatformCookiePool)
        .where(PlatformCookiePool.source_id == source_id)
        .where(PlatformCookiePool.platform == platform)
        .where(PlatformCookiePool.is_active == True)  # noqa: E712
        .where(PlatformCookiePool.status == "active")
        .order_by(*_cookie_pool_runtime_order())
        .limit(1)
    )).scalar_one_or_none()
    if not row:
        return
    row.last_verified_at = now_bjt()
    if status == "valid":
        row.verification_status = "active"
        row.health_score = 100
        row.last_error_code = None
    elif status == "expired":
        row.verification_status = "failed"
        row.health_score = 0
        row.last_error_code = "LOGIN_EXPIRED"
    else:
        capability = dict(getattr(row, "capability_json", None) or {})
        capability["last_verify_unknown_detail"] = (detail or "VERIFY_UNKNOWN")[:200]
        row.capability_json = capability
        if row.verification_status != "active" or int(row.health_score or 0) <= 0:
            row.verification_status = "unknown"
            row.health_score = max(int(row.health_score or 0), 50)
            row.last_error_code = (detail or "VERIFY_UNKNOWN")[:80]
    row.updated_at = now_bjt()
    await db.commit()


async def _write_verify_status(
    db: AsyncSession,
    source_id: str,
    *,
    status: str,
    detail: str = "",
) -> None:
    """更新 DataSource.config.{verify_status, verify_detail, last_verified_at}。"""
    from app.datasources.models import DataSource

    ds = (await db.execute(
        select(DataSource).where(DataSource.id == source_id)
    )).scalar_one_or_none()
    if not ds:
        return
    config = dict(ds.config or {})
    config["verify_status"] = status
    config["verify_detail"] = (detail or "")[:300]
    config["last_verified_at"] = isoformat_bjt(now_bjt())
    ds.config = config
    ds.updated_at = now_bjt()
    await db.commit()


async def list_tasks(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """查询采集任务列表"""
    from sqlalchemy import func
    total = (await db.execute(
        select(func.count()).select_from(CollectionTask)
    )).scalar() or 0
    rows = (await db.execute(
        select(CollectionTask)
        .order_by(CollectionTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    return {
        "total": total,
        "page": page,
        "items": [
            {
                "id": r.id,
                "platform": r.platform,
                "task_type": r.task_type,
                "status": r.status,
                "error_message": r.error_message,
                "created_by": r.created_by,
                "created_at": isoformat_bjt(r.created_at),
                "completed_at": isoformat_bjt(r.completed_at),
                "result_preview": _preview(r.result),
            }
            for r in rows
        ],
    }


def _preview(result: dict | None, max_len: int = 200) -> str | None:
    """结果摘要预览"""
    if not result:
        return None
    import json
    s = json.dumps(result, ensure_ascii=False)
    return s[:max_len] + "..." if len(s) > max_len else s


# 旧名保留为别名，外部模块（如 collectors/sycm 历史代码）可能 import
_build_fetch_json_js = build_fetch_json_js


async def fetch_json(
    *,
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body=None,
    page_url: str | None = None,
    cdp_url: str | None = None,
    cookie_details: list[dict] | None = None,
    cookie_header: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """在浏览器上下文中直接 fetch 某个 API URL。"""
    from app.browser.collector_base import get_collector

    target_cdp_url = cdp_url or f"http://{settings.BROWSER_CDP_HOST}:{settings.BROWSER_CDP_PORT}"
    if target_cdp_url.startswith(("ws://", "wss://")):
        ws_url = target_cdp_url
    else:
        if not await _check_cdp(target_cdp_url):
            return {"success": False, "error": "浏览器未运行", "data": None}
        ws_url = await _get_cdp_ws_url(target_cdp_url)

    if cookie_details or cookie_header or user_agent:
        try:
            await _prepare_fetch_context(
                ws_url,
                url=url,
                cookie_details=cookie_details,
                cookie_header=cookie_header,
                user_agent=user_agent,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"浏览器上下文注入失败: {exc}", "data": None}

    collector = get_collector("generic")
    params = {
        "js": build_fetch_json_js(url, method=method, headers=headers, body=body),
    }
    if page_url:
        params["url"] = page_url
    try:
        result = await asyncio.wait_for(collector.run(ws_url, "extract", params), timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"浏览器 fetch_json 超时或失败: {exc}", "data": None}
    data = result.get("data") if isinstance(result, dict) else result
    proof = build_response_proof(data, page_url=page_url, requested_url=url)
    if not isinstance(data, dict):
        return {
            "success": False,
            "error": "浏览器 fetch_json 未返回结构化结果",
            "data": data,
            "proof": proof,
        }
    has_fetch_shape = any(key in data for key in ("status", "ok", "data", "parse_error", "text_preview", "content_type"))
    if not has_fetch_shape:
        return {
            "success": False,
            "error": "浏览器 fetch_json 未返回响应状态",
            "data": data,
            "proof": proof,
        }
    return {
        "success": True,
        "data": data,
        "proof": proof,
    }


def _same_site_for_cdp(value: str | None) -> str | None:
    mapping = {
        "no_restriction": "None",
        "none": "None",
        "lax": "Lax",
        "strict": "Strict",
    }
    return mapping.get((value or "").lower())


def _cookies_from_header(cookie_header: str | None, *, url: str) -> list[dict]:
    if not cookie_header:
        return []
    parsed = urlparse(url)
    domain = parsed.hostname
    if not domain:
        return []
    cookies = []
    for pair in [p.strip() for p in cookie_header.split(";") if "=" in p]:
        name, value = pair.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies.append({
            "name": name,
            "value": value.strip(),
            "domain": domain,
            "path": "/",
        })
    return cookies


def _cookies_for_cdp(cookie_details: list[dict] | None, *, cookie_header: str | None, url: str) -> list[dict]:
    cookies: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in cookie_details or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip()
        if not name or not domain:
            continue
        path = str(item.get("path") or "/")
        key = (domain, path, name)
        if key in seen:
            continue
        seen.add(key)
        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": bool(item.get("secure")),
            "httpOnly": bool(item.get("httpOnly")),
        }
        same_site = _same_site_for_cdp(item.get("sameSite"))
        if same_site:
            cookie["sameSite"] = same_site
        expires = item.get("expirationDate")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = float(expires)
        cookies.append(cookie)

    for item in _cookies_from_header(cookie_header, url=url):
        key = (item["domain"], item["path"], item["name"])
        if key not in seen:
            seen.add(key)
            cookies.append(item)
    return cookies


async def _prepare_fetch_context(
    ws_url: str,
    *,
    url: str,
    cookie_details: list[dict] | None,
    cookie_header: str | None,
    user_agent: str | None,
) -> None:
    """Inject the selected credential into the exact page target used for fetch."""
    import websockets

    cdp_cookies = _cookies_for_cdp(cookie_details, cookie_header=cookie_header, url=url)
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        msg_id = 0

        async def send(method: str, params: dict | None = None) -> dict:
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == msg_id:
                    if "error" in resp:
                        raise RuntimeError(f"{method}: {resp['error']}")
                    return resp.get("result", {})

        await send("Network.enable")
        if user_agent:
            await send("Network.setUserAgentOverride", {"userAgent": user_agent})
        if cdp_cookies:
            await send("Network.setCookies", {"cookies": cdp_cookies})


def _registry_page_url(row: PlatformApiCache, apis_json: dict) -> str | None:
    meta = apis_json.get("_registry_meta") if isinstance(apis_json.get("_registry_meta"), dict) else {}
    proof = apis_json.get("_proof") if isinstance(apis_json.get("_proof"), dict) else {}
    return meta.get("page_url") or proof.get("page_url") or row.page_url


def _verification_status(fetch_result: dict) -> str:
    if not fetch_result.get("success"):
        return "failed"
    proof = fetch_result.get("proof") or {}
    status = proof.get("status")
    ok = proof.get("ok")
    if ok is False:
        return "failed"
    if isinstance(status, int) and status >= 400:
        return "failed"
    return "verified"


async def replay_platform_api(
    db: AsyncSession,
    *,
    registry_id: int,
    api_index: int = 0,
    method: str | None = None,
    headers: dict | None = None,
    body=None,
    page_url: str | None = None,
) -> dict:
    """按注册表 id + api_index replay 已抓包 API，并回写 verification proof。"""
    row = await db.get(PlatformApiCache, registry_id)
    if not row or not isinstance(row.apis_json, dict):
        return {"success": False, "error": "平台注册表记录不存在", "data": None}

    apis_json = json.loads(json.dumps(row.apis_json, ensure_ascii=False))
    data_apis = apis_json.get("data_apis") or []
    if api_index < 0 or api_index >= len(data_apis):
        return {"success": False, "error": f"api_index 越界: {api_index}", "data": None}

    api = data_apis[api_index] or {}
    api_url = api.get("url")
    if not api_url:
        return {"success": False, "error": f"api_index={api_index} 缺少 url", "data": None}

    replay_page_url = page_url or _registry_page_url(row, apis_json)
    try:
        guard_discover_url(api_url, field="url")
        if replay_page_url:
            guard_discover_url(replay_page_url, field="page_url")
        fetch_result = await fetch_json(
            url=api_url,
            method=method or api.get("method") or "GET",
            headers=headers,
            body=body,
            page_url=replay_page_url,
        )
    except Exception as exc:  # noqa: BLE001
        now = _bjt_iso()
        meta = apis_json.get("_registry_meta") if isinstance(apis_json.get("_registry_meta"), dict) else {}
        meta.update({
            "last_verified_at": now,
            "verification_status": "failed",
            "last_error": str(exc)[:500],
        })
        apis_json["_registry_meta"] = meta
        row.apis_json = apis_json
        row.updated_at = now_bjt()
        await db.commit()
        return {"success": False, "error": str(exc)[:200], "data": None, "proof": {
            "verified_at": now,
            "registry_id": registry_id,
            "api_index": api_index,
            "api_url": api_url,
            "page_url": replay_page_url,
            "verification_status": "failed",
        }}

    proof = dict(fetch_result.get("proof") or {})
    status = _verification_status(fetch_result)
    now = proof.get("verified_at") or _bjt_iso()
    proof.update({
        "registry_id": registry_id,
        "api_index": api_index,
        "verification_status": status,
    })
    fetch_result["proof"] = proof

    meta = apis_json.get("_registry_meta") if isinstance(apis_json.get("_registry_meta"), dict) else {}
    response_hashes = list(meta.get("response_hashes") or [])
    if proof.get("response_hash") and proof["response_hash"] not in response_hashes:
        response_hashes.append(proof["response_hash"])
    meta.update({
        "page_url": replay_page_url,
        "last_verified_at": now,
        "verification_status": status,
        "response_hashes": response_hashes,
        "last_replay_api_index": api_index,
    })
    meta.pop("last_error", None)
    apis_json["_registry_meta"] = meta
    data_apis[api_index] = {**api, "last_replay_proof": proof}
    apis_json["data_apis"] = data_apis
    apis_json.setdefault("_replay_proofs", []).append(proof)
    apis_json["_replay_proofs"] = apis_json["_replay_proofs"][-20:]
    row.apis_json = apis_json
    row.updated_at = now_bjt()
    await db.commit()
    return fetch_result


# ═══════════════════════════════════════════════
# CDP 工具函数
# ═══════════════════════════════════════════════

async def _check_cdp(cdp_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{cdp_url}/json/version")
            return resp.status_code == 200
    except Exception:
        return False


async def _check_novnc() -> bool:
    try:
        url = f"http://{settings.BROWSER_NOVNC_HOST}:{settings.BROWSER_NOVNC_PORT}/"
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False


async def _wait_for_cdp(cdp_url: str, timeout: int = 30) -> bool:
    """等待 CDP 就绪"""
    for _ in range(timeout):
        if await _check_cdp(cdp_url):
            return True
        await asyncio.sleep(1)
    return False


async def _get_browser_ws_url(cdp_url: str) -> str:
    """获取 browser-level CDP WebSocket URL"""
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{cdp_url}/json/version")
        return resp.json()["webSocketDebuggerUrl"]


async def _create_page_target(cdp_url: str) -> tuple[str, str]:
    """Create an isolated page target and return (page_ws_url, target_id)."""
    import websockets

    browser_ws_url = await _get_browser_ws_url(cdp_url)
    async with websockets.connect(browser_ws_url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Target.createTarget",
            "params": {"url": "about:blank"},
        }))
        resp = json.loads(await ws.recv())
        while resp.get("id") != 1:
            resp = json.loads(await ws.recv())
        if "error" in resp:
            raise RuntimeError(f"CDP createTarget failed: {resp['error']}")
        target_id = str(resp.get("result", {}).get("targetId") or "")
        if not target_id:
            raise RuntimeError("CDP createTarget did not return targetId")

    async with httpx.AsyncClient(timeout=5) as client:
        for _ in range(10):
            resp = await client.get(f"{cdp_url}/json")
            for page in resp.json():
                if page.get("id") == target_id and page.get("webSocketDebuggerUrl"):
                    return page["webSocketDebuggerUrl"], target_id
            await asyncio.sleep(0.1)
    raise RuntimeError("CDP created target but page websocket was not available")


async def _close_target(cdp_url: str, target_id: str) -> None:
    """Best-effort close for a temporary page target."""
    import websockets

    try:
        browser_ws_url = await _get_browser_ws_url(cdp_url)
        async with websockets.connect(browser_ws_url, max_size=10 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "id": 1,
                "method": "Target.closeTarget",
                "params": {"targetId": target_id},
            }))
            await ws.recv()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[browser] close temporary target failed: {}", exc)


async def _get_cdp_ws_url(cdp_url: str, force_refresh: bool = False) -> str:
    """获取第一个 page 的 CDP WebSocket URL（带 5s 缓存）。

    强制重连场景（如 BrowserRetryableError）会清空缓存并 force_refresh=True。
    """
    if not force_refresh:
        cached = _WS_URL_CACHE.get(cdp_url)
        if cached and (time.monotonic() - cached[1]) < _WS_URL_TTL:
            return cached[0]

    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{cdp_url}/json")
        pages = resp.json()
        for p in pages:
            if p.get("type") == "page":
                ws_url = p["webSocketDebuggerUrl"]
                _WS_URL_CACHE[cdp_url] = (ws_url, time.monotonic())
                return ws_url
        resp2 = await client.get(f"{cdp_url}/json/version")
        ws_url = resp2.json()["webSocketDebuggerUrl"]
        _WS_URL_CACHE[cdp_url] = (ws_url, time.monotonic())
        return ws_url


async def _get_page_count(cdp_url: str) -> int:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{cdp_url}/json")
            return len(resp.json())
    except Exception:
        return 0
