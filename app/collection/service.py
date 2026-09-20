"""Governed collection fetch service."""

from __future__ import annotations

import hashlib
import hmac
import asyncio
import contextlib
import json
import os
import random
import sys
import time
from pathlib import Path
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import NoSuchTableError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collection.models import CollectionProof, PlatformApiSchemaSnapshot
from app.collection.schemas import CollectionFetchBatchRequest, CollectionFetchRequest
from app.browser.models import BrowserSlot
from app.common.exceptions import AppError
from app.common.time_utils import now_bjt
from app.datasources.models import DataSource, PlatformCookieAudit, PlatformCookiePool
from app.datasources.service import _credential_alias_for_pool, _get_cookie_cipher
from app.execution.execution_service import verify_run_token


_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_RATE_STATE: dict[tuple[str, str, str, str, str], list[float]] = {}
_CIRCUIT_STATE: dict[tuple[str, str, str], dict[str, Any]] = {}
_PENDING_COOKIE_VERIFY_LOCKS: dict[tuple[str, str, str], asyncio.Lock] = {}
_COOKIE_VERIFY_LOCK_FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ProgrammingError,
    NoSuchTableError,
    OperationalError,
    NotImplementedError,
)
PENDING_COOKIE_GRACE_SECONDS = 10 * 60
DEFAULT_BATCH_CONCURRENCY = 4
MAX_BATCH_CONCURRENCY = 20
DEFAULT_BATCH_COOKIE_WAIT_SECONDS = 15.0
DEFAULT_BATCH_COOKIE_VERIFY_WAIT_SECONDS = 20.0
DEFAULT_FETCH_COOKIE_WAIT_SECONDS = 3.0
DEFAULT_COOKIE_VERIFIED_MAX_AGE_SECONDS = 24 * 60 * 60
DIRECT_COOKIE_FETCH_ATTEMPTS = 3
DIRECT_COOKIE_FETCH_TIMEOUT_SECONDS = 20.0
DIRECT_FIRST_SYCM_ENDPOINT_MARKERS = (
    "/mc/common/free/getCateInfo.json",
    "/mc/mq/mkt/priceSeg/list.json",
    "/mc/mq/mkt/item/live/rank.json",
    "/mc/mq/mkt/item/offline/rank.json",
)


def _allow_legacy_collection_fetch() -> bool:
    return os.environ.get("SKILLFORGE_COLLECTION_ALLOW_LEGACY", "").lower() in {"1", "true", "yes"}


def _collection_rate_limit() -> tuple[int, float]:
    max_calls = int(os.environ.get("SKILLFORGE_COLLECTION_RATE_MAX_CALLS", "120") or "120")
    window = float(os.environ.get("SKILLFORGE_COLLECTION_RATE_WINDOW_SECONDS", "60") or "60")
    return max_calls, window


def _collection_circuit_config() -> tuple[int, float, float]:
    failures = int(os.environ.get("SKILLFORGE_COLLECTION_CIRCUIT_FAILURES", "3") or "3")
    window = float(os.environ.get("SKILLFORGE_COLLECTION_CIRCUIT_WINDOW_SECONDS", "60") or "60")
    cooldown = float(os.environ.get("SKILLFORGE_COLLECTION_CIRCUIT_COOLDOWN_SECONDS", "300") or "300")
    return failures, window, cooldown


def _cookie_verified_max_age_seconds() -> float:
    return float(
        os.environ.get(
            "SKILLFORGE_COLLECTION_COOKIE_VERIFIED_MAX_AGE_SECONDS",
            str(DEFAULT_COOKIE_VERIFIED_MAX_AGE_SECONDS),
        ) or DEFAULT_COOKIE_VERIFIED_MAX_AGE_SECONDS
    )


def _batch_cookie_verify_wait_seconds() -> float:
    return float(
        os.environ.get(
            "SKILLFORGE_COLLECTION_BATCH_COOKIE_VERIFY_WAIT_SECONDS",
            str(DEFAULT_BATCH_COOKIE_VERIFY_WAIT_SECONDS),
        ) or DEFAULT_BATCH_COOKIE_VERIFY_WAIT_SECONDS
    )


def _cookie_runtime_ready(pool: PlatformCookiePool) -> bool:
    """Only hand Skill execution credentials that are current enough to trust.

    Freshly pushed cookies enter as pending because browser verification runs
    asynchronously. A previously verified cookie can also become pending after
    the extension refreshes it, so keep it eligible unless it carries an error.
    """
    status = str(getattr(pool, "verification_status", "") or "").lower()
    if status == "active":
        return True
    if status == "warning":
        return int(getattr(pool, "health_score", 0) or 0) > 0
    if status in {"failed", "disabled", "expired"}:
        return False
    if status in {"", "unknown", "pending"} and getattr(pool, "last_verified_at", None) and not getattr(pool, "last_error_code", None):
        return True
    pushed_at = getattr(pool, "pushed_at", None)
    if status in {"", "unknown", "pending"} and pushed_at:
        return now_bjt() - pushed_at <= timedelta(seconds=PENDING_COOKIE_GRACE_SECONDS)
    return False


def _cookie_verified_ready(pool: PlatformCookiePool) -> bool:
    status = str(getattr(pool, "verification_status", "") or "").lower()
    last_verified_at = getattr(pool, "last_verified_at", None)
    if not last_verified_at:
        return False
    max_age = _cookie_verified_max_age_seconds()
    if max_age > 0 and now_bjt() - last_verified_at > timedelta(seconds=max_age):
        return False
    return (
        status == "active"
        and int(getattr(pool, "health_score", 0) or 0) > 0
        and not getattr(pool, "last_error_code", None)
    )


def _cookie_pending_verify_candidate(pool: PlatformCookiePool) -> bool:
    status = str(getattr(pool, "verification_status", "") or "").lower()
    if status not in {"", "unknown", "pending"}:
        return False
    if int(getattr(pool, "health_score", 0) or 0) <= 0:
        return False
    if getattr(pool, "last_error_code", None):
        return False
    return not _cookie_verified_ready(pool)


def _cookie_verify_lock_key(pool: PlatformCookiePool) -> str:
    return ":".join((
        "collection_cookie_verify",
        str(getattr(pool, "source_id", "") or ""),
        str(getattr(pool, "platform", "") or ""),
        str(getattr(pool, "shop_id", "") or ""),
    ))


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _shape(value: Any) -> tuple[list[str], int | None]:
    data = value.get("data") if isinstance(value, dict) and "data" in value else value
    if isinstance(data, dict):
        return [str(k) for k in list(data.keys())[:50]], None
    if isinstance(data, list):
        keys: list[str] = []
        if data and isinstance(data[0], dict):
            keys = [str(k) for k in list(data[0].keys())[:50]]
        return keys, len(data)
    return [], None


def _extract_fetch_payload(fetch_result: dict[str, Any]) -> Any:
    outer = fetch_result.get("data") if isinstance(fetch_result, dict) else None
    if isinstance(outer, dict) and "data" in outer:
        return outer.get("data")
    return outer


def _test_tokens() -> list[str]:
    raw = os.environ.get("SKILLFORGE_TEST_RUN_TOKEN") or os.environ.get("SKILLFORGE_RUN_TOKEN_TEST") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def verify_collection_run_token(
    token: str | None,
    *,
    skill_id: str | None,
    run_id: str | None,
    instance_id: str | None,
) -> dict[str, str]:
    """Verify the platform-issued run token.

    Unit tests may opt into env-provided test tokens, but production no longer
    accepts a baked-in default token.
    """

    token = (token or "").strip()
    if not token:
        raise AppError("RUN_TOKEN_INVALID", 401, {"reason": "missing"})
    test_tokens = _test_tokens()
    if test_tokens and any(hmac.compare_digest(token, expected) for expected in test_tokens):
        return {
            "skill_id": skill_id or "",
            "run_id": run_id or "",
            "instance_id": instance_id or "",
            "verifier": "env_test_token",
        }
    if token.startswith("test:") and os.environ.get("SKILLFORGE_ALLOW_STRUCTURAL_TEST_TOKEN") == "1":
        parts = token.split(":", 3)
        if len(parts) == 4:
            t_skill_id, t_run_id, t_instance_id = parts[1:]
            if (
                (not skill_id or skill_id == t_skill_id)
                and (not run_id or run_id == t_run_id)
                and (not instance_id or instance_id == t_instance_id)
            ):
                return {
                    "skill_id": t_skill_id,
                    "run_id": t_run_id,
                    "instance_id": t_instance_id,
                    "verifier": "structural_test_token",
                }
    claims = verify_run_token(
        token,
        skill_id=skill_id,
        run_id=run_id,
        instance_id=instance_id,
    )
    return {
        "skill_id": str(claims.get("skill_id") or ""),
        "run_id": str(claims.get("run_id") or ""),
        "instance_id": str(claims.get("instance_id") or ""),
        "department": str(claims.get("department") or ""),
        "skill_department": str(claims.get("skill_department") or claims.get("department") or ""),
        "verifier": "run_token",
    }


def _load_tool_runtime():
    try:
        import skillforge_mcp_runtime as runtime

        runtime.ensure_default_tools_registered()
        return runtime
    except Exception as exc:  # noqa: BLE001
        raise AppError("TOOLMETA_NOT_REGISTERED", 400, {"reason": f"manifest_load_failed: {exc}"}) from exc


def _validate_tool_meta(body: CollectionFetchRequest) -> dict[str, Any]:
    runtime = _load_tool_runtime()
    meta = runtime.find_tool_meta(
        tool_name=body.mcp_tool_name,
        platform=body.platform,
        data_scope=body.data_scope,
        endpoint_family=body.endpoint_family,
        warning_group=body.warning_group,
    )
    if not meta:
        raise AppError(
            "TOOLMETA_NOT_REGISTERED",
            400,
            {
                "mcp_tool_name": body.mcp_tool_name,
                "platform": body.platform,
                "data_scope": body.data_scope,
                "endpoint_family": body.endpoint_family,
                "warning_group": body.warning_group,
            },
        )
    return meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)


def _direct_cookie_fallback_enabled() -> bool:
    return os.environ.get("SKILLFORGE_COLLECTION_DIRECT_COOKIE_FALLBACK", "1").lower() not in {"0", "false", "no"}


def _direct_cookie_host_allowed(platform: str, host: str) -> bool:
    if platform == "sycm":
        return host == "sycm.taobao.com"
    if platform == "alimama":
        return host == "one.alimama.com" or host.endswith(".alimama.com")
    return False


def _direct_cookie_primary(params: dict[str, Any]) -> bool:
    context = params.get("_collection_context") if isinstance(params.get("_collection_context"), dict) else {}
    platform = str(context.get("platform") or "").lower()
    url = str(params.get("url") or params.get("api_url") or "")
    if platform != "sycm":
        return False
    return any(marker in url for marker in DIRECT_FIRST_SYCM_ENDPOINT_MARKERS)


def _setdefault_header(headers: dict[str, str], name: str, value: str) -> None:
    if not any(str(key).lower() == name.lower() for key in headers):
        headers[name] = value


def _should_direct_cookie_fallback(params: dict[str, Any], fetch_result: dict[str, Any] | None = None) -> bool:
    if not _direct_cookie_fallback_enabled():
        return False
    if "mock_data" in params or "fetch_result" in params:
        return False
    context = params.get("_collection_context") if isinstance(params.get("_collection_context"), dict) else {}
    platform = str(context.get("platform") or "").lower()
    url = str(params.get("url") or params.get("api_url") or "")
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not _direct_cookie_host_allowed(platform, host):
        return False
    method = str(params.get("method") or "GET").upper()
    if method not in {"GET", "POST"}:
        return False
    if not params.get("cookie_header"):
        return False
    if fetch_result is None:
        return True
    return not _fetch_succeeded(fetch_result)


def _direct_cookie_fetch_required(params: dict[str, Any]) -> bool:
    context = params.get("_collection_context") if isinstance(params.get("_collection_context"), dict) else {}
    return str(context.get("platform") or "").lower() == "alimama" or _direct_cookie_primary(params)


def _direct_cookie_retry_delay(attempt: int) -> float:
    return (0.35 * max(1, attempt)) + random.uniform(0.0, 0.15)


def _direct_cookie_retryable_error(result: dict[str, Any]) -> bool:
    if _fetch_succeeded(result):
        return False
    error = str(result.get("error") or "")
    if error.startswith("DIRECT_COOKIE_FETCH_EXCEPTION"):
        return True
    status = _fetch_http_status(result)
    return isinstance(status, int) and status in {408, 425, 429, 500, 502, 503, 504}


async def _fetch_direct_with_platform_cookie_once(params: dict[str, Any]) -> dict[str, Any]:
    url = str(params.get("url") or params.get("api_url") or "")
    method = str(params.get("method") or "GET").upper()
    headers = {str(k): str(v) for k, v in (params.get("headers") or {}).items() if str(k).lower() != "cookie"}
    _setdefault_header(headers, "accept", "application/json, text/plain, */*")
    headers["cookie"] = str(params.get("cookie_header") or "")
    if params.get("page_url"):
        _setdefault_header(headers, "referer", str(params["page_url"]))
    if params.get("user_agent"):
        _setdefault_header(headers, "user-agent", str(params["user_agent"]))
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if host == "one.alimama.com" or host.endswith(".alimama.com"):
        _setdefault_header(headers, "origin", "https://one.alimama.com")
        _setdefault_header(headers, "referer", "https://one.alimama.com/")

    request_kwargs: dict[str, Any] = {"headers": headers}
    body = params.get("body")
    if body is not None:
        content_type = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
        if isinstance(body, (bytes, str)):
            request_kwargs["content"] = body
        elif "application/json" in content_type or not content_type:
            request_kwargs["json"] = body
        else:
            request_kwargs["content"] = str(body)

    try:
        timeout = httpx.Timeout(DIRECT_COOKIE_FETCH_TIMEOUT_SECONDS, connect=8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.request(method, url, **request_kwargs)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"DIRECT_COOKIE_FETCH_EXCEPTION: {exc}",
            "data": None,
            "proof": {"transport": "direct_with_platform_cookie"},
        }

    try:
        data = resp.json()
    except ValueError:
        data = None
    proof = {
        "status": resp.status_code,
        "ok": 200 <= resp.status_code < 400,
        "content_type": resp.headers.get("content-type", ""),
        "transport": "direct_with_platform_cookie",
    }
    payload = {
        "url": str(resp.url),
        "status": resp.status_code,
        "ok": 200 <= resp.status_code < 400,
        "content_type": resp.headers.get("content-type", ""),
    }
    if isinstance(data, (dict, list)):
        payload["data"] = data
        return {"success": True, "data": payload, "proof": proof}
    payload["text_preview"] = resp.text[:1000]
    return {
        "success": False,
        "error": f"DIRECT_COOKIE_FETCH_NON_JSON: HTTP_{resp.status_code}",
        "data": payload,
        "proof": proof,
    }


async def _fetch_direct_with_platform_cookie(params: dict[str, Any]) -> dict[str, Any]:
    last_result: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    for attempt in range(1, max(1, DIRECT_COOKIE_FETCH_ATTEMPTS) + 1):
        result = await _fetch_direct_with_platform_cookie_once(params)
        proof = result.setdefault("proof", {})
        if isinstance(proof, dict):
            proof["direct_attempt"] = attempt
        history.append({
            "attempt": attempt,
            "success": bool(_fetch_succeeded(result)),
            "error": _fetch_error_code(result) if not _fetch_succeeded(result) else "",
            "http_status": _fetch_http_status(result),
        })
        last_result = result
        if _fetch_succeeded(result) or not _direct_cookie_retryable_error(result) or attempt >= DIRECT_COOKIE_FETCH_ATTEMPTS:
            break
        await asyncio.sleep(_direct_cookie_retry_delay(attempt))

    assert last_result is not None
    if len(history) > 1:
        proof = last_result.setdefault("proof", {})
        if isinstance(proof, dict):
            proof["direct_attempt_count"] = len(history)
            proof["direct_attempts"] = history
    return last_result


def _source_related_to_skill(source_department: str | None, token_claims: dict[str, str], source_related_skills: list | None) -> bool:
    token_department = str(token_claims.get("department") or "").strip()
    if token_department and source_department == token_department:
        return True
    skill_id = str(token_claims.get("skill_id") or "").strip()
    return bool(skill_id and isinstance(source_related_skills, list) and skill_id in source_related_skills)


def _filter_cookie_pool_scope(
    rows: list[tuple[PlatformCookiePool, str | None, list | None]],
    token_claims: dict[str, str],
    *,
    body: CollectionFetchRequest,
) -> list[PlatformCookiePool]:
    if not rows:
        return []

    token_department = str(token_claims.get("department") or "").strip()
    if not token_department:
        return [pool for pool, _source_department, _source_related_skills in rows]

    matches: list[PlatformCookiePool] = []
    for pool, source_department, source_related_skills in rows:
        if _source_related_to_skill(source_department, token_claims, source_related_skills):
            matches.append(pool)
    if matches:
        return matches
    raise AppError(
        "COOKIE_PERMISSION_DENIED",
        403,
        {
            "platform": body.platform,
            "shop_id": body.shop_id,
            "department": token_department,
        },
    )


async def fetch_via_browser(params: dict[str, Any]) -> dict[str, Any]:
    """Default fetch adapter, deliberately small and easy to monkeypatch."""

    if isinstance(params.get("fetch_result"), dict):
        return dict(params["fetch_result"])
    if "mock_data" in params:
        data = params["mock_data"]
        keys, row_count = _shape(data)
        return {
            "success": True,
            "data": {
                "url": params.get("url") or params.get("api_url") or "mock://collection",
                "status": 200,
                "ok": True,
                "data": data,
            },
            "proof": {
                "status": 200,
                "ok": True,
                "response_hash": _sha256_json(data),
                "data_keys": keys,
                "row_count": row_count,
            },
        }
    url = params.get("url") or params.get("api_url")
    if not url:
        return {"success": False, "error": "PARAM_INVALID: missing url", "data": None}

    from app.browser import service as browser_service

    result = await browser_service.fetch_json(
        url=str(url),
        method=str(params.get("method") or "GET"),
        headers=params.get("headers") or {},
        body=params.get("body"),
        page_url=params.get("page_url"),
        cdp_url=params.get("cdp_url"),
        cookie_details=params.get("cookie_details"),
        cookie_header=params.get("cookie_header"),
        user_agent=params.get("user_agent"),
    )
    if _should_direct_cookie_fallback(params, result):
        direct = await _fetch_direct_with_platform_cookie(params)
        if _fetch_succeeded(direct):
            return direct
        result = dict(result)
        proof = dict(result.get("proof") or {})
        proof["direct_cookie_fallback"] = {
            "status": _fetch_http_status(direct),
            "error": _fetch_error_code(direct),
        }
        result["proof"] = proof
    return result


def _legacy_credential_alias(body: CollectionFetchRequest) -> str:
    alias = body.params.get("credential_alias") if isinstance(body.params, dict) else None
    return str(alias or f"{body.platform}-{body.shop_id}-legacy")


async def _select_cookie_pool_rows(
    db: AsyncSession,
    body: CollectionFetchRequest,
) -> list[tuple[PlatformCookiePool, str | None, list | None]]:
    stmt = (
        select(PlatformCookiePool, DataSource.department, DataSource.related_skills)
        .outerjoin(DataSource, PlatformCookiePool.source_id == DataSource.id)
        .where(
            PlatformCookiePool.platform == body.platform.strip().lower(),
            PlatformCookiePool.shop_id == body.shop_id.strip(),
            PlatformCookiePool.is_active == True,  # noqa: E712
            PlatformCookiePool.status == "active",
            PlatformCookiePool.health_score > 0,
        )
    )
    if body.source_id:
        stmt = stmt.where(PlatformCookiePool.source_id == body.source_id)
    stmt = stmt.order_by(
        PlatformCookiePool.priority.desc(),
        PlatformCookiePool.health_score.desc(),
        PlatformCookiePool.last_used_at.asc().nullsfirst(),
        PlatformCookiePool.pushed_at.desc(),
    )
    return list((await db.execute(stmt.limit(20))).all())


async def _select_cookie_pools(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
    verified_only: bool = False,
) -> list[PlatformCookiePool]:
    rows = await _select_cookie_pool_rows(db, body)
    ready = _cookie_verified_ready if verified_only else _cookie_runtime_ready
    rows = [(pool, source_department, source_related_skills) for pool, source_department, source_related_skills in rows if ready(pool)]
    return _filter_cookie_pool_scope(rows, token_claims, body=body)


async def _select_pending_cookie_pools(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
) -> list[PlatformCookiePool]:
    rows = await _select_cookie_pool_rows(db, body)
    rows = [
        (pool, source_department, source_related_skills)
        for pool, source_department, source_related_skills in rows
        if _cookie_pending_verify_candidate(pool)
    ]
    return _filter_cookie_pool_scope(rows, token_claims, body=body)


async def _select_cookie_pool(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
) -> PlatformCookiePool | None:
    pools = await _select_cookie_pools(db, body, token_claims=token_claims)
    return pools[0] if pools else None


async def _select_browser_slot(
    db: AsyncSession,
    *,
    pool: PlatformCookiePool | None,
    credential_alias: str,
) -> BrowserSlot | None:
    stmt = (
        select(BrowserSlot)
        .where(BrowserSlot.status == "idle")
        .order_by(BrowserSlot.last_used_at.asc().nullsfirst(), BrowserSlot.slot_id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    slot = (await db.execute(stmt)).scalar_one_or_none()
    if not slot:
        return None
    slot.status = "busy"
    slot.current_pool_id = pool.id if pool else None
    slot.current_credential_alias = credential_alias
    slot.last_used_at = now_bjt()
    slot.updated_at = now_bjt()
    await db.flush()
    return slot


async def _claim_cookie_pools(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
    limit: int = 1,
    verified_only: bool = False,
    exclude_pool_ids: set[int] | None = None,
) -> list[PlatformCookiePool]:
    candidates = await _select_cookie_pools(
        db,
        body,
        token_claims=token_claims,
        verified_only=verified_only,
    )
    if not candidates:
        return []

    claimed: list[PlatformCookiePool] = []
    now = now_bjt()
    exclude_pool_ids = exclude_pool_ids or set()
    for candidate in candidates:
        if int(candidate.id) in exclude_pool_ids:
            continue
        stmt = (
            select(PlatformCookiePool)
            .where(PlatformCookiePool.id == candidate.id)
            .where(PlatformCookiePool.is_active == True)  # noqa: E712
            .where(PlatformCookiePool.status == "active")
            .where(PlatformCookiePool.health_score > 0)
            .with_for_update(skip_locked=True)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if not row:
            continue
        if verified_only and not _cookie_verified_ready(row):
            continue
        if not verified_only and not _cookie_runtime_ready(row):
            continue
        row.last_used_at = now
        row.updated_at = now
        claimed.append(row)
        if len(claimed) >= max(1, limit):
            break
    await db.flush()
    return claimed


async def _claim_cookie_pool(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
    verified_only: bool = False,
    exclude_pool_ids: set[int] | None = None,
    wait_seconds: float = 0.0,
) -> PlatformCookiePool | None:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        claimed = await _claim_cookie_pools(
            db,
            body,
            token_claims=token_claims,
            limit=1,
            verified_only=verified_only,
            exclude_pool_ids=exclude_pool_ids,
        )
        if claimed:
            return claimed[0]
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.1)


def _decrypt_cookie_pool(pool: PlatformCookiePool) -> tuple[str, list[dict]]:
    cipher = _get_cookie_cipher()
    cookie_header = cipher.decrypt(pool.encrypted_cookies.encode("utf-8")).decode("utf-8")
    cookie_details: list[dict] = []
    if pool.encrypted_cookie_details:
        raw_details = cipher.decrypt(pool.encrypted_cookie_details.encode("utf-8")).decode("utf-8")
        parsed = json.loads(raw_details)
        if isinstance(parsed, list):
            cookie_details = [item for item in parsed if isinstance(item, dict)]
    return cookie_header, cookie_details


def _fetch_http_status(fetch_result: dict[str, Any]) -> int | None:
    proof = fetch_result.get("proof") if isinstance(fetch_result.get("proof"), dict) else {}
    status = proof.get("status")
    if isinstance(status, int):
        return status
    outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    status = outer.get("status") if isinstance(outer, dict) else None
    return status if isinstance(status, int) else None


def _fetch_ok_flag(fetch_result: dict[str, Any]) -> bool | None:
    proof = fetch_result.get("proof") if isinstance(fetch_result.get("proof"), dict) else {}
    ok = proof.get("ok")
    if isinstance(ok, bool):
        return ok
    outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    ok = outer.get("ok") if isinstance(outer, dict) else None
    return ok if isinstance(ok, bool) else None


def _payload_business_ok_flag(value: Any) -> bool | None:
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if code not in (None, "", 0, "0", "0.0"):
        return False
    info = value.get("info")
    if isinstance(info, dict) and isinstance(info.get("ok"), bool):
        return info.get("ok")
    ok = value.get("ok")
    if isinstance(ok, bool):
        return ok
    success = value.get("success")
    if isinstance(success, bool):
        return success
    data = value.get("data")
    if isinstance(data, dict):
        return _payload_business_ok_flag(data)
    return None


def _business_error_code(fetch_result: dict[str, Any]) -> str:
    outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    candidates: list[Any] = []
    if isinstance(outer, dict):
        candidates.extend(outer.get(key) for key in ("error", "errorCode", "error_code", "code"))
        payload = outer.get("data")
        if _payload_business_ok_flag(payload) is False:
            info = payload.get("info") if isinstance(payload, dict) and isinstance(payload.get("info"), dict) else {}
            candidates.extend(info.get(key) for key in ("errorCode", "error_code", "code", "message", "msg"))
            if isinstance(payload, dict):
                candidates.extend(payload.get(key) for key in ("error", "errorCode", "error_code", "code", "message", "msg"))
        nested = outer.get("data")
        if isinstance(nested, dict):
            candidates.extend(nested.get(key) for key in ("error", "errorCode", "error_code", "code"))
    for value in candidates:
        if value not in (None, "", 0, "0", "0.0"):
            return str(value)[:80]
    return ""


def _fetch_succeeded(fetch_result: dict[str, Any]) -> bool:
    if not fetch_result.get("success"):
        return False
    if _fetch_ok_flag(fetch_result) is False:
        return False
    status = _fetch_http_status(fetch_result)
    if isinstance(status, int) and status >= 400:
        return False
    outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    if _payload_business_ok_flag(outer.get("data")) is False:
        return False
    return not _is_cookie_failure_marker(fetch_result)


def _fetch_error_code(fetch_result: dict[str, Any]) -> str:
    raw_error = str(fetch_result.get("error") or "").strip()
    if raw_error:
        return raw_error[:80]
    status = _fetch_http_status(fetch_result)
    if isinstance(status, int) and status >= 400:
        return f"HTTP_{status}"
    if _fetch_ok_flag(fetch_result) is False:
        return "FETCH_NOT_OK"
    outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    if _payload_business_ok_flag(outer.get("data")) is False:
        return _business_error_code(fetch_result) or "BUSINESS_NOT_OK"
    return _business_error_code(fetch_result) or "FETCH_FAILED"


def _failure_text(fetch_result: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("error", "message"):
        value = fetch_result.get(key)
        if value:
            parts.append(str(value))
    proof = fetch_result.get("proof") if isinstance(fetch_result.get("proof"), dict) else {}
    for key in ("warning", "warning_signal", "content_type"):
        value = proof.get(key)
        if value:
            parts.append(_stable_json(value) if isinstance(value, (dict, list)) else str(value))
    outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    for key in ("error", "errorCode", "code", "message", "msg", "text_preview", "content_type"):
        value = outer.get(key) if isinstance(outer, dict) else None
        if value:
            parts.append(_stable_json(value) if isinstance(value, (dict, list)) else str(value))
    nested = outer.get("data") if isinstance(outer, dict) and isinstance(outer.get("data"), dict) else {}
    for key in ("error", "errorCode", "code", "message", "msg"):
        value = nested.get(key) if isinstance(nested, dict) else None
        if value:
            parts.append(str(value))
    return "\n".join(parts).lower()


def _is_cookie_failure_marker(fetch_result: dict[str, Any]) -> bool:
    haystack = _failure_text(fetch_result)
    retry_markers = (
        "cookie",
        "not_login",
        "notlogin",
        "login",
        "auth",
        "unauthorized",
        "forbidden",
        "http_401",
        "http_403",
        "401",
        "403",
        "x5sec",
        "punish",
        "验证码",
        "安全校验",
        "安全验证",
        "登录",
        "未登录",
        "鉴权",
        "认证",
        "授权",
        "失效",
        "过期",
    )
    return any(marker in haystack for marker in retry_markers)


def _is_cookie_retriable_failure(fetch_result: dict[str, Any]) -> bool:
    if _fetch_succeeded(fetch_result):
        return False
    status = _fetch_http_status(fetch_result)
    if status in {401, 403, 419, 440}:
        return True
    return _is_cookie_failure_marker(fetch_result)


def _rate_key(body: CollectionFetchRequest, credential_alias: str) -> tuple[str, str, str, str, str]:
    return (
        body.platform.strip().lower(),
        body.shop_id.strip(),
        body.endpoint_family,
        body.warning_group,
        credential_alias,
    )


def _circuit_key(body: CollectionFetchRequest) -> tuple[str, str, str]:
    return (body.platform.strip().lower(), body.shop_id.strip(), body.warning_group)


def _check_rate_limit(body: CollectionFetchRequest, credential_alias: str) -> None:
    max_calls, window = _collection_rate_limit()
    if max_calls <= 0 or window <= 0:
        return
    now = time.monotonic()
    key = _rate_key(body, credential_alias)
    recent = [ts for ts in _RATE_STATE.get(key, []) if now - ts < window]
    if len(recent) >= max_calls:
        retry_after = max(0.0, window - (now - recent[0]))
        raise AppError("RATE_LIMIT_HOLD", 429, {
            "rate_key": "|".join(key),
            "retry_after_seconds": round(retry_after, 3),
        })
    recent.append(now)
    _RATE_STATE[key] = recent


def _check_circuit(body: CollectionFetchRequest) -> None:
    now = time.monotonic()
    key = _circuit_key(body)
    state = _CIRCUIT_STATE.get(key)
    if not state:
        return
    opened_until = float(state.get("opened_until") or 0)
    if opened_until > now:
        raise AppError("CIRCUIT_OPEN", 503, {
            "circuit_key": "|".join(key),
            "opened_until_monotonic": round(opened_until, 3),
            "last_error": state.get("last_error"),
            "trace_id": state.get("trace_id"),
        })
    if opened_until:
        _CIRCUIT_STATE.pop(key, None)


def _record_circuit_outcome(body: CollectionFetchRequest, fetch_result: dict[str, Any]) -> None:
    key = _circuit_key(body)
    if _fetch_succeeded(fetch_result):
        _CIRCUIT_STATE.pop(key, None)
        return
    failures, window, cooldown = _collection_circuit_config()
    if failures <= 0:
        return
    now = time.monotonic()
    state = _CIRCUIT_STATE.get(key) or {}
    recent = [ts for ts in state.get("failures", []) if now - ts < window]
    recent.append(now)
    trace_id = f"circuit-{uuid4().hex[:12]}"
    next_state: dict[str, Any] = {
        "failures": recent,
        "last_error": str(fetch_result.get("error") or "FETCH_FAILED")[:160],
        "trace_id": trace_id,
    }
    if len(recent) >= failures:
        next_state["opened_until"] = now + cooldown
    _CIRCUIT_STATE[key] = next_state


def _audit_cookie_use(
    db: AsyncSession,
    pool: PlatformCookiePool | None,
    *,
    action: str,
    body: CollectionFetchRequest,
    detail: dict[str, Any] | None = None,
) -> None:
    if not pool:
        return
    db.add(PlatformCookieAudit(
        cookie_pool_id=pool.id,
        source_id=pool.source_id,
        platform=pool.platform,
        shop_id=pool.shop_id,
        owner_user_id=pool.owner_user_id,
        connector_key_id=pool.connector_key_id,
        action=action,
        auth_source=pool.auth_source,
        actor_user_id=body.skill_id or "collection_service",
        detail={
            "run_id": body.run_id,
            "skill_id": body.skill_id,
            "instance_id": body.instance_id,
            "data_scope": body.data_scope,
            "mcp_tool_name": body.mcp_tool_name,
            **(detail or {}),
        },
    ))


def _update_pool_health(pool: PlatformCookiePool | None, fetch_result: dict[str, Any]) -> None:
    if not pool:
        return
    pool.last_used_at = now_bjt()
    success = _fetch_succeeded(fetch_result)
    if success:
        pool.health_score = min(int(pool.health_score or 0) + 5, 100)
        pool.verification_status = "active"
        pool.last_error_code = None
    elif _is_cookie_retriable_failure(fetch_result):
        pool.health_score = 0
        pool.verification_status = "failed"
        pool.last_error_code = _fetch_error_code(fetch_result)
    else:
        pool.health_score = max(int(pool.health_score or 0) - 20, 0)
        pool.verification_status = "warning" if pool.health_score > 0 else "failed"
        pool.last_error_code = _fetch_error_code(fetch_result)
    pool.updated_at = now_bjt()


def _release_slot(slot: BrowserSlot | None) -> None:
    if not slot:
        return
    slot.status = "idle"
    slot.current_pool_id = None
    slot.current_credential_alias = None
    slot.updated_at = now_bjt()


def _build_proof_row(
    body: CollectionFetchRequest,
    fetch_result: dict[str, Any],
    *,
    credential_alias: str,
    pool: PlatformCookiePool | None = None,
    slot: BrowserSlot | None = None,
) -> CollectionProof:
    proof = fetch_result.get("proof") if isinstance(fetch_result.get("proof"), dict) else {}
    payload = _extract_fetch_payload(fetch_result)
    data_keys, row_count = _shape(payload)
    response_hash = proof.get("response_hash") or _sha256_json(payload)
    http_status = proof.get("status")
    if not isinstance(http_status, int):
        outer = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
        http_status = outer.get("status") if isinstance(outer.get("status"), int) else None
    success = _fetch_succeeded(fetch_result)
    return CollectionProof(
        proof_id=f"proof-{uuid4().hex[:20]}",
        run_id=body.run_id,
        skill_id=body.skill_id,
        mcp_tool_name=body.mcp_tool_name,
        platform=body.platform,
        shop_id=body.shop_id,
        data_scope=body.data_scope,
        endpoint_family=body.endpoint_family,
        warning_group=body.warning_group,
        credential_scope=body.credential_scope,
        credential_plan_id=body.credential_plan_id,
        cookie_pool_id=pool.id if pool else None,
        credential_alias=credential_alias,
        browser_slot_id=str(slot.slot_id if slot else body.params.get("browser_slot_id") or "legacy-default"),
        retry_of_proof_id=body.retry_of_proof_id,
        status="success" if success else "failed",
        http_status=http_status,
        error_code=None if success else _fetch_error_code(fetch_result),
        response_hash=response_hash,
        data_keys=proof.get("data_keys") or data_keys,
        row_count=proof.get("row_count") if proof.get("row_count") is not None else row_count,
        warning_signal=proof.get("warning_signal") if isinstance(proof.get("warning_signal"), dict) else {},
        created_at=now_bjt(),
    )


def _endpoint_from_params(params: dict[str, Any]) -> str:
    return str(params.get("url") or params.get("api_url") or params.get("endpoint") or "")


def _snapshot_from_fetch(
    body: CollectionFetchRequest,
    fetch_result: dict[str, Any],
    proof_row: CollectionProof,
) -> PlatformApiSchemaSnapshot | None:
    if not _fetch_succeeded(fetch_result):
        return None
    endpoint = _endpoint_from_params(body.params)
    if not endpoint:
        return None
    return PlatformApiSchemaSnapshot(
        platform=body.platform,
        shop_id=body.shop_id,
        endpoint_hash=hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
        endpoint=endpoint,
        response_keys=proof_row.data_keys or [],
        row_count=proof_row.row_count,
        response_hash=proof_row.response_hash,
        source_run_id=body.run_id,
        captured_at=now_bjt(),
    )


async def _upsert_schema_snapshot(
    db: AsyncSession,
    body: CollectionFetchRequest,
    fetch_result: dict[str, Any],
    proof_row: CollectionProof,
) -> None:
    snapshot = _snapshot_from_fetch(body, fetch_result, proof_row)
    if snapshot is None:
        return
    existing = (await db.execute(
        select(PlatformApiSchemaSnapshot).where(
            PlatformApiSchemaSnapshot.platform == snapshot.platform,
            PlatformApiSchemaSnapshot.shop_id == snapshot.shop_id,
            PlatformApiSchemaSnapshot.endpoint_hash == snapshot.endpoint_hash,
            PlatformApiSchemaSnapshot.source_run_id == snapshot.source_run_id,
        )
    )).scalar_one_or_none()
    if existing is None:
        db.add(snapshot)
        return
    existing.endpoint = snapshot.endpoint
    existing.response_keys = snapshot.response_keys
    existing.row_count = snapshot.row_count
    existing.response_hash = snapshot.response_hash
    existing.captured_at = snapshot.captured_at


def _decorate_collection_result(
    body: CollectionFetchRequest,
    fetch_result: dict[str, Any],
    proof_row: CollectionProof,
    *,
    credential_alias: str,
) -> dict[str, Any]:
    proof = dict(fetch_result.get("proof") or {})
    proof.update({
        "proof_id": proof_row.proof_id,
        "tool_name": body.mcp_tool_name,
        "platform": body.platform,
        "shop_id": body.shop_id,
        "data_scope": body.data_scope,
        "endpoint_family": body.endpoint_family,
        "warning_group": body.warning_group,
        "credential_alias": credential_alias,
        "browser_slot_id": proof_row.browser_slot_id,
        "slot_id": proof_row.browser_slot_id,
        "response_hash": proof_row.response_hash,
        "row_count": proof_row.row_count,
        "data_keys": proof_row.data_keys or [],
    })
    result = dict(fetch_result)
    result["success"] = proof_row.status == "success"
    result["proof"] = proof
    result["proof_id"] = proof_row.proof_id
    result["credential_alias"] = credential_alias
    result["status"] = proof_row.status
    return result


def _redact_attempts(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "credential_alias": str(item.get("credential_alias") or ""),
            "status": str(item.get("status") or "failed"),
            "error": str(item.get("error") or "")[:80] or None,
            "proof_id": item.get("proof_id"),
            "browser_slot_id": item.get("browser_slot_id"),
        }
        for item in attempts
    ]


async def _fetch_with_cookie_pool(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    base_params: dict[str, Any],
    pool: PlatformCookiePool | None,
    credential_alias: str,
) -> tuple[dict[str, Any], CollectionProof, BrowserSlot | None]:
    params = dict(base_params)
    slot: BrowserSlot | None = None
    cookie_header: str | None = None
    cookie_details: list[dict] = []
    if pool:
        cookie_header, cookie_details = _decrypt_cookie_pool(pool)

    params["_collection_context"] = {
        "platform": body.platform,
        "shop_id": body.shop_id,
        "data_scope": body.data_scope,
        "endpoint_family": body.endpoint_family,
        "warning_group": body.warning_group,
        "credential_alias": credential_alias,
        "browser_slot_id": None,
    }
    if pool:
        params["cookie_header"] = cookie_header
        params["cookie_details"] = cookie_details
        params["user_agent"] = pool.user_agent
        params["credential_alias"] = credential_alias

    fetch_result: dict[str, Any] | None = None
    if _should_direct_cookie_fallback(params):
        direct_result = await _fetch_direct_with_platform_cookie(params)
        if _fetch_succeeded(direct_result) or _direct_cookie_fetch_required(params):
            fetch_result = direct_result

    if fetch_result is None:
        needs_browser_slot = bool(pool) and "mock_data" not in params and "fetch_result" not in params
        if needs_browser_slot:
            slot = await _select_browser_slot(db, pool=pool, credential_alias=credential_alias)
            if not slot:
                raise AppError("CREDENTIAL_UNAVAILABLE", 503, {
                    "reason": "NO_BROWSER_SLOT",
                    "platform": body.platform,
                    "shop_id": body.shop_id,
                    "credential_alias": credential_alias,
                })
            params["_collection_context"]["browser_slot_id"] = slot.slot_id
            params["cdp_url"] = slot.cdp_url
            params["browser_slot_id"] = slot.slot_id

        try:
            fetch_result = await fetch_via_browser(params)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            fetch_result = {
                "success": False,
                "error": f"FETCH_EXCEPTION: {exc}",
                "data": None,
                "proof": {},
            }
        finally:
            _release_slot(slot)
    elif _fetch_succeeded(fetch_result) is False and _direct_cookie_primary(params):
        proof = dict(fetch_result.get("proof") or {})
        proof["browser_fallback_skipped"] = "direct_cookie_primary_endpoint"
        fetch_result = dict(fetch_result)
        fetch_result["proof"] = proof
    if _fetch_succeeded(fetch_result) is False and not fetch_result.get("error"):
        fetch_result = dict(fetch_result)
        fetch_result["error"] = _fetch_error_code(fetch_result) or "FETCH_FAILED"

    _update_pool_health(pool, fetch_result)
    _audit_cookie_use(
        db,
        pool,
        action="use" if _fetch_succeeded(fetch_result) else "fail",
        body=body,
        detail={
            "credential_alias": credential_alias,
            "browser_slot_id": slot.slot_id if slot else None,
            "error": None if _fetch_succeeded(fetch_result) else _fetch_error_code(fetch_result),
        },
    )
    proof_row = _build_proof_row(
        body,
        fetch_result,
        credential_alias=credential_alias,
        pool=pool,
        slot=slot,
    )
    db.add(proof_row)
    await _upsert_schema_snapshot(db, body, fetch_result, proof_row)
    await db.flush()
    return fetch_result, proof_row, slot


async def fetch_collection(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    run_token: str | None,
    verified_cookies_only: bool = False,
    cookie_wait_seconds: float = 0.0,
) -> dict[str, Any]:
    token_claims = verify_collection_run_token(
        run_token,
        skill_id=body.skill_id,
        run_id=body.run_id,
        instance_id=body.instance_id,
    )
    _validate_tool_meta(body)

    initial_pool = await _claim_cookie_pool(
        db,
        body,
        token_claims=token_claims,
        verified_only=verified_cookies_only,
        wait_seconds=cookie_wait_seconds,
    )
    cookie_verify_attempts: list[dict[str, Any]] = []
    cookie_verify_reason = ""
    if not initial_pool:
        verify_result = await _verify_pending_cookie(
            db,
            body,
            token_claims=token_claims,
            user_id="collection_fetch",
            inflight_key="fetch_verify_inflight",
            result_key="last_fetch_verify",
        )
        cookie_verify_attempts = list(verify_result.get("attempts") or [])
        cookie_verify_reason = str(verify_result.get("reason") or "")
        if int(verify_result.get("valid_cookie_count") or 0) > 0:
            initial_pool = await _claim_cookie_pool(
                db,
                body,
                token_claims=token_claims,
                verified_only=verified_cookies_only,
                wait_seconds=cookie_wait_seconds,
            )
    if not initial_pool and not _allow_legacy_collection_fetch():
        detail = {
            "platform": body.platform,
            "shop_id": body.shop_id,
            "data_scope": body.data_scope,
            "reason": "NO_VALID_COOKIE" if verified_cookies_only else "NO_RUNTIME_COOKIE",
        }
        if cookie_verify_attempts:
            detail["verify_reason"] = cookie_verify_reason
            detail["verify_attempts"] = cookie_verify_attempts
        raise AppError("NO_COOKIE", 412, detail)
    _check_circuit(body)

    base_params = dict(body.params or {})
    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None
    last_proof_row: CollectionProof | None = None
    last_alias = ""
    attempted_pool_ids: set[int] = set()
    index = 0
    pool: PlatformCookiePool | None = initial_pool

    while pool is not None or (index == 0 and _allow_legacy_collection_fetch()):
        index += 1
        credential_alias = _credential_alias_for_pool(pool) if pool else _legacy_credential_alias(body)
        last_alias = credential_alias
        if pool:
            attempted_pool_ids.add(int(pool.id))
        _check_rate_limit(body, credential_alias)
        fetch_result, proof_row, slot = await _fetch_with_cookie_pool(
            db,
            body,
            base_params=base_params,
            pool=pool,
            credential_alias=credential_alias,
        )
        last_result = fetch_result
        last_proof_row = proof_row
        attempt = {
            "credential_alias": credential_alias,
            "status": proof_row.status,
            "error": None if proof_row.status == "success" else proof_row.error_code,
            "proof_id": proof_row.proof_id,
            "browser_slot_id": proof_row.browser_slot_id,
        }
        attempts.append(attempt)
        if _fetch_succeeded(fetch_result):
            _record_circuit_outcome(body, fetch_result)
            result = _decorate_collection_result(body, fetch_result, proof_row, credential_alias=credential_alias)
            if cookie_verify_attempts:
                result["cookie_verify_attempts"] = cookie_verify_attempts
                result["cookie_verify_reason"] = cookie_verify_reason
            if len(attempts) > 1:
                result["attempt_count"] = len(attempts)
                result["credential_attempts"] = _redact_attempts(attempts)
            return result
        if not pool or not _is_cookie_retriable_failure(fetch_result):
            break
        pool = await _claim_cookie_pool(
            db,
            body,
            token_claims=token_claims,
            verified_only=verified_cookies_only,
            exclude_pool_ids=attempted_pool_ids,
            wait_seconds=cookie_wait_seconds,
        )
        if not pool:
            break

    assert last_result is not None and last_proof_row is not None
    _record_circuit_outcome(body, last_result)
    result = _decorate_collection_result(body, last_result, last_proof_row, credential_alias=last_alias)
    result["attempt_count"] = len(attempts)
    result["credential_attempts"] = _redact_attempts(attempts)
    if cookie_verify_attempts:
        result["cookie_verify_attempts"] = cookie_verify_attempts
        result["cookie_verify_reason"] = cookie_verify_reason
    return result


def _batch_item_body(
    batch: CollectionFetchBatchRequest,
    index: int,
) -> CollectionFetchRequest:
    item = batch.items[index]
    request_id = item.request_id or str(index)
    return CollectionFetchRequest(
        mcp_tool_name=batch.mcp_tool_name,
        skill_id=batch.skill_id,
        run_id=batch.run_id,
        instance_id=batch.instance_id,
        source_id=batch.source_id,
        platform=batch.platform,
        shop_id=batch.shop_id,
        data_scope=batch.data_scope,
        endpoint_family=batch.endpoint_family,
        warning_group=batch.warning_group,
        credential_scope=batch.credential_scope,
        credential_plan_id=batch.credential_plan_id,
        retry_of_proof_id=item.retry_of_proof_id,
        params={
            **(item.params or {}),
            "_batch_index": index,
            "_batch_request_id": request_id,
        },
    )


async def _count_verified_cookie_pools(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
) -> int:
    pools = await _select_cookie_pools(
        db,
        body,
        token_claims=token_claims,
        verified_only=True,
    )
    return len(pools)


def _pending_cookie_verify_lock(pool: PlatformCookiePool) -> asyncio.Lock:
    key = (
        str(getattr(pool, "source_id", "") or ""),
        str(getattr(pool, "platform", "") or ""),
        str(getattr(pool, "shop_id", "") or ""),
    )
    lock = _PENDING_COOKIE_VERIFY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PENDING_COOKIE_VERIFY_LOCKS[key] = lock
    return lock


async def _try_advisory_cookie_verify_lock(
    session_factory: async_sessionmaker[AsyncSession],
    key: str,
) -> tuple[AsyncSession | None, bool]:
    session = session_factory()
    try:
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": key},
        )
        return session, bool(result.scalar())
    except _COOKIE_VERIFY_LOCK_FALLBACK_EXCEPTIONS:
        with contextlib.suppress(Exception):
            await session.close()
        return None, True


async def _release_advisory_cookie_verify_lock(
    session: AsyncSession | None,
    key: str,
) -> None:
    if session is None:
        return
    try:
        await session.execute(
            text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": key},
        )
    finally:
        await session.close()


def _cookie_verify_failure_reason(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "NO_VERIFIED_COOKIE"
    reasons = {str(item.get("reason") or "") for item in attempts}
    if "COOKIE_VERIFY_PENDING_TIMEOUT" in reasons:
        return "COOKIE_VERIFY_PENDING_TIMEOUT"
    if "COOKIE_VERIFY_UNSUPPORTED" in reasons and len(reasons) == 1:
        return "COOKIE_VERIFY_UNSUPPORTED"
    return "COOKIE_VERIFY_FAILED"


async def _verify_pending_cookie(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
    user_id: str = "collection",
    inflight_key: str = "verify_inflight",
    result_key: str = "last_verify",
) -> dict[str, Any]:
    """Give freshly pushed pending cookies one sync verification chance.

    Collection still prefers already verified cookies. This closes the race where
    a cookie was pushed but the background verifier has not written
    last_verified_at yet, or where the push has exceeded the short pending grace
    window before the scheduled Skill starts.
    """
    candidates = await _select_pending_cookie_pools(db, body, token_claims=token_claims)
    attempts: list[dict[str, Any]] = []
    if not candidates:
        return {
            "valid_cookie_count": 0,
            "attempts": attempts,
            "reason": "NO_VERIFIED_COOKIE",
        }

    from app.browser import service as browser_service

    session_factory = _session_factory_from_session(db)
    seen_sources: set[str] = set()
    wait_seconds = max(0.1, _batch_cookie_verify_wait_seconds())
    for pool in candidates:
        source_id = str(pool.source_id or "")
        if not source_id or source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        local_lock = _pending_cookie_verify_lock(pool)
        lock_key = _cookie_verify_lock_key(pool)
        async with local_lock:
            advisory_session, lock_acquired = await _try_advisory_cookie_verify_lock(session_factory, lock_key)
            try:
                # A waiting request may have loaded the pending row before another
                # request committed verification, so force the next query to see DB state.
                db.expire_all()
                valid_count = await _count_verified_cookie_pools(db, body, token_claims=token_claims)
                if valid_count > 0:
                    return {
                        "valid_cookie_count": valid_count,
                        "attempts": attempts,
                        "reason": "VERIFIED_BY_PEER",
                    }
                if not lock_acquired:
                    peer_deadline = time.monotonic() + wait_seconds
                    while time.monotonic() < peer_deadline:
                        await asyncio.sleep(0.2)
                        db.expire_all()
                        valid_count = await _count_verified_cookie_pools(db, body, token_claims=token_claims)
                        if valid_count > 0:
                            return {
                                "valid_cookie_count": valid_count,
                                "attempts": attempts,
                                "reason": "VERIFIED_BY_PEER",
                            }
                    attempts.append({
                        "source_id": source_id,
                        "reason": "COOKIE_VERIFY_PENDING_TIMEOUT",
                        "detail": "another worker is still verifying this cookie",
                    })
                    continue
                verify_started_at = now_bjt()
                with contextlib.suppress(Exception):
                    row = await db.get(PlatformCookiePool, pool.id)
                    if row is not None:
                        capability = dict(getattr(row, "capability_json", None) or {})
                        capability[inflight_key] = {
                            "started_at": verify_started_at.isoformat(),
                            "lock_key": lock_key,
                        }
                        row.capability_json = capability
                        row.updated_at = verify_started_at
                        await db.commit()
                try:
                    result = await asyncio.wait_for(
                        browser_service.verify_login(db, source_id=source_id, user_id=user_id),
                        timeout=wait_seconds,
                    )
                except asyncio.TimeoutError:
                    with contextlib.suppress(Exception):
                        await db.rollback()
                    with contextlib.suppress(Exception):
                        row = await db.get(PlatformCookiePool, pool.id)
                        if row is not None:
                            capability = dict(getattr(row, "capability_json", None) or {})
                            capability.pop(inflight_key, None)
                            capability[result_key] = {
                                "finished_at": now_bjt().isoformat(),
                                "source_id": source_id,
                                "status": "timeout",
                                "verified": False,
                            }
                            row.capability_json = capability
                            row.updated_at = now_bjt()
                            await db.commit()
                    attempts.append({
                        "source_id": source_id,
                        "reason": "COOKIE_VERIFY_PENDING_TIMEOUT",
                        "detail": f"login verification exceeded {wait_seconds:.1f}s",
                    })
                    continue
                except Exception as exc:  # noqa: BLE001
                    with contextlib.suppress(Exception):
                        await db.rollback()
                    with contextlib.suppress(Exception):
                        row = await db.get(PlatformCookiePool, pool.id)
                        if row is not None:
                            capability = dict(getattr(row, "capability_json", None) or {})
                            capability.pop(inflight_key, None)
                            capability[result_key] = {
                                "finished_at": now_bjt().isoformat(),
                                "source_id": source_id,
                                "status": "exception",
                                "verified": False,
                            }
                            row.capability_json = capability
                            row.updated_at = now_bjt()
                            await db.commit()
                    attempts.append({
                        "source_id": source_id,
                        "reason": "COOKIE_VERIFY_FAILED",
                        "detail": str(exc)[:200],
                    })
                    continue

                with contextlib.suppress(Exception):
                    row = await db.get(PlatformCookiePool, pool.id)
                    if row is not None:
                        capability = dict(getattr(row, "capability_json", None) or {})
                        capability.pop(inflight_key, None)
                        capability[result_key] = {
                            "finished_at": now_bjt().isoformat(),
                            "source_id": source_id,
                            "status": result.get("status"),
                            "verified": bool(result.get("verified")),
                        }
                        row.capability_json = capability
                        row.updated_at = now_bjt()
                        await db.commit()

                valid_count = await _count_verified_cookie_pools(db, body, token_claims=token_claims)
                verified = bool(result.get("verified")) and valid_count > 0
                attempt_reason = "VERIFIED" if verified else "COOKIE_VERIFY_FAILED"
                if str(result.get("status") or "") == "unsupported":
                    attempt_reason = "COOKIE_VERIFY_UNSUPPORTED"
                attempts.append({
                    "source_id": source_id,
                    "reason": attempt_reason,
                    "status": result.get("status"),
                    "verified": bool(result.get("verified")),
                    "detail": str(result.get("detail") or "")[:200],
                })
                if verified:
                    return {
                        "valid_cookie_count": valid_count,
                        "attempts": attempts,
                        "reason": "VERIFIED",
                    }
            finally:
                await _release_advisory_cookie_verify_lock(advisory_session, lock_key)

    return {
        "valid_cookie_count": 0,
        "attempts": attempts,
        "reason": _cookie_verify_failure_reason(attempts),
    }


async def _verify_pending_cookie_for_batch(
    db: AsyncSession,
    body: CollectionFetchRequest,
    *,
    token_claims: dict[str, str],
) -> dict[str, Any]:
    return await _verify_pending_cookie(
        db,
        body,
        token_claims=token_claims,
        user_id="collection_batch",
        inflight_key="batch_verify_inflight",
        result_key="last_batch_verify",
    )


def _session_factory_from_session(db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    bind = getattr(db, "bind", None)
    if bind is None:
        raise AppError("DB_SESSION_UNAVAILABLE", 500, {"reason": "missing async bind"})
    return async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=False)


async def fetch_collection_batch(
    db: AsyncSession,
    body: CollectionFetchBatchRequest,
    *,
    run_token: str | None,
) -> dict[str, Any]:
    token_claims = verify_collection_run_token(
        run_token,
        skill_id=body.skill_id,
        run_id=body.run_id,
        instance_id=body.instance_id,
    )
    probe_body = _batch_item_body(body, 0)
    _validate_tool_meta(probe_body)
    valid_cookie_count = await _count_verified_cookie_pools(db, probe_body, token_claims=token_claims)
    verify_attempts: list[dict[str, Any]] = []
    verify_reason = "VERIFIED"
    if valid_cookie_count <= 0:
        verify_result = await _verify_pending_cookie_for_batch(db, probe_body, token_claims=token_claims)
        valid_cookie_count = int(verify_result.get("valid_cookie_count") or 0)
        verify_attempts = list(verify_result.get("attempts") or [])
        verify_reason = str(verify_result.get("reason") or "NO_VERIFIED_COOKIE")
    if valid_cookie_count <= 0:
        raise AppError("NO_VALID_COOKIE", 412, {
            "platform": body.platform,
            "shop_id": body.shop_id,
            "data_scope": body.data_scope,
            "reason": verify_reason,
            "detail": "批量并发采集只使用已验证有效的 Cookie；pending Cookie 已触发同步验证但仍未通过",
            "verify_attempts": verify_attempts,
        })

    desired = body.concurrency or DEFAULT_BATCH_CONCURRENCY
    concurrency = max(1, min(int(desired), MAX_BATCH_CONCURRENCY, valid_cookie_count, len(body.items)))
    session_factory = _session_factory_from_session(db)
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any] | None] = [None] * len(body.items)

    async def run_one(index: int) -> None:
        request_id = body.items[index].request_id or str(index)
        async with semaphore:
            async with session_factory() as item_db:
                try:
                    item_body = _batch_item_body(body, index)
                    result = await fetch_collection(
                        item_db,
                        item_body,
                        run_token=run_token,
                        verified_cookies_only=True,
                        cookie_wait_seconds=DEFAULT_BATCH_COOKIE_WAIT_SECONDS,
                    )
                    await item_db.commit()
                    results[index] = {
                        "request_id": request_id,
                        "index": index,
                        "success": bool(result.get("success")),
                        "result": result,
                    }
                except AppError as exc:
                    await item_db.rollback()
                    results[index] = {
                        "request_id": request_id,
                        "index": index,
                        "success": False,
                        "error": exc.code,
                        "detail": exc.detail,
                    }
                except Exception as exc:  # noqa: BLE001
                    await item_db.rollback()
                    results[index] = {
                        "request_id": request_id,
                        "index": index,
                        "success": False,
                        "error": "FETCH_EXCEPTION",
                        "detail": str(exc)[:300],
                    }

    await asyncio.gather(*(run_one(index) for index in range(len(body.items))))
    materialized = [item for item in results if item is not None]
    success_count = sum(1 for item in materialized if item.get("success"))
    return {
        "success": success_count == len(body.items),
        "status": "success" if success_count == len(body.items) else "partial_failed",
        "requested_count": len(body.items),
        "success_count": success_count,
        "failed_count": len(body.items) - success_count,
        "concurrency": concurrency,
        "valid_cookie_count": valid_cookie_count,
        "cookie_verify_attempts": verify_attempts,
        "results": materialized,
    }
