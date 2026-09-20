"""Cloud video upstream client exposed through SkillForge MCP builtins."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from app.common.exceptions import AppError
from app.common.ai import call_llm_multimodal, redact_secret_text
from app.config import settings


CLOUD_VIDEO_LOGIN_PATH = "/login/login"
CLOUD_VIDEO_VIDEO_TYPE = 0
CLOUD_VIDEO_DEFAULT_PAGE = 1
CLOUD_VIDEO_DEFAULT_PAGE_SIZE = 24
CLOUD_VIDEO_MAX_PAGE_SIZE = 60
CLOUD_VIDEO_MAX_CATEGORY_ITEMS = 500
CLOUD_VIDEO_MAX_TAG_ITEMS = 500
CLOUD_VIDEO_REPORT_DEFAULT_START_DATE = "2026-05-01"
CLOUD_VIDEO_REPORT_DEFAULT_END_DATE = "2026-06-09"
CLOUD_VIDEO_REPORT_MAX_DAYS = 93
CLOUD_VIDEO_REPORT_PAGE_SIZE = 500
CLOUD_VIDEO_REPORT_MAX_VIDEO_ROWS = 200_000
CLOUD_VIDEO_REPORT_MAX_PERSON_ROWS = 10_000
CLOUD_VIDEO_REPORT_DEFAULT_VIDEO_ROWS = 200_000
CLOUD_VIDEO_REPORT_DEFAULT_RETURNED_VIDEO_ROWS = 50_000
CLOUD_VIDEO_REPORT_CONCURRENCY = 8
CLOUD_VIDEO_SORT_OPTIONS = {1, 2, 3, 4, 5, 22}
CLOUD_VIDEO_MATERIAL_REPORT_SEARCH_TYPES = {0, 1, 2, 3, 4}
CLOUD_VIDEO_VIDEO_REPORT_DATA_TYPES = {1, 2, 3}
CLOUD_VIDEO_VIDEO_REPORT_TOP_TYPES = {0, 1, 2}
CLOUD_VIDEO_SIGN_INDICES = (2, 4, 5, 7, 11, 14, 15, 18, 22, 23, 26, 28, 31, 33, 35, 36)
CLOUD_VIDEO_REFRESH_BUFFER_SECONDS = 24 * 60 * 60
CLOUD_VIDEO_LOGIN_DEBOUNCE_SECONDS = 60
AUTH_FAILURE_PATTERNS = ("登录状态已失效", "请重新登录", "token", "失效")
CLOUD_VIDEO_VISUAL_MAX_VIDEOS = 30
CLOUD_VIDEO_VISUAL_DEFAULT_MAX_VIDEOS = 20
CLOUD_VIDEO_VISUAL_VIDEO_FPS = 1
CLOUD_VIDEO_VISUAL_VIDEO_MAX_FRAMES = 48
CLOUD_VIDEO_VISUAL_QUERY_PAGE_SIZE = 10
URL_TEXT_RE = re.compile(r"https?://[^\s\"')<>]+", re.I)


@dataclass(frozen=True)
class CloudVideoRuntimeConfig:
    enabled: bool
    base_url: str
    login_account: str
    password: str
    timeout_seconds: int


@dataclass(frozen=True)
class CloudVideoSession:
    token: str
    expires_at: int
    obtained_at: int
    account: str


_cached_session: CloudVideoSession | None = None
_session_lock = asyncio.Lock()


def _runtime_config() -> CloudVideoRuntimeConfig:
    timeout_seconds = int(getattr(settings, "CLOUD_VIDEO_REQUEST_TIMEOUT_SECONDS", 30) or 30)
    return CloudVideoRuntimeConfig(
        enabled=bool(getattr(settings, "CLOUD_VIDEO_ENABLED", False)),
        base_url=str(getattr(settings, "CLOUD_VIDEO_API_BASE_URL", "") or "").rstrip("/"),
        login_account=str(getattr(settings, "CLOUD_VIDEO_LOGIN_ACCOUNT", "") or "").strip(),
        password=str(getattr(settings, "CLOUD_VIDEO_PASSWORD", "") or "").strip(),
        timeout_seconds=min(max(timeout_seconds, 3), 120),
    )


def _require_runtime_config() -> CloudVideoRuntimeConfig:
    config = _runtime_config()
    if not config.enabled or not config.base_url or not config.login_account or not config.password:
        raise AppError(
            "MCP_ENV_MISSING",
            503,
            {
                "detail": "云视频 MCP 未配置服务端账号或未启用",
                "credential_location": "platform_only",
            },
        )
    return config


def _now_seconds() -> int:
    return int(time.time())


def _b64url_json(payload_part: str) -> dict[str, Any]:
    padding = "=" * ((4 - len(payload_part) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload_part + padding).encode("ascii"))
        parsed = json.loads(decoded.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppError("MCP_CALL_FAILED", 502, {"detail": "云视频 token payload 无法解析"}) from exc
    if not isinstance(parsed, dict):
        raise AppError("MCP_CALL_FAILED", 502, {"detail": "云视频 token payload 格式异常"})
    return parsed


def _decode_token_exp(token: str) -> int:
    parts = token.split(".")
    if len(parts) < 2 or not parts[1]:
        raise AppError("MCP_CALL_FAILED", 502, {"detail": "云视频 token 缺少 payload"})
    exp = _b64url_json(parts[1]).get("exp")
    if not isinstance(exp, (int, float)):
        raise AppError("MCP_CALL_FAILED", 502, {"detail": "云视频 token 缺少过期时间"})
    return int(exp)


def _token_secret(token: str) -> str:
    parts = token.split(".")
    if len(parts) < 2:
        raise AppError("MCP_CALL_FAILED", 502, {"detail": "云视频 token 缺少 payload"})
    payload = parts[1]
    return "".join(payload[index] if 0 <= index < len(payload) else "" for index in CLOUD_VIDEO_SIGN_INDICES)


def _request_signature(*, token: str, request_id: str, timestamp: str) -> str:
    secret = _token_secret(token)
    text = f"requestId={request_id}&timestamp={timestamp}&{secret}"
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _form_body(body: dict[str, Any]) -> bytes:
    values: dict[str, str] = {}
    for key, value in body.items():
        if value is None:
            continue
        values[str(key)] = str(value)
    return urllib.parse.urlencode(values).encode("utf-8")


def _post_form_sync(
    config: CloudVideoRuntimeConfig,
    path: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    request = urllib.request.Request(
        f"{config.base_url}{path}",
        data=_form_body(body),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "SkillForge-CloudVideo-MCP/1.0",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8", "replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = int(exc.code)
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "MCP_CALL_FAILED",
            502,
            {"detail": f"云视频上游请求失败：{type(exc).__name__}", "path": path},
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return status, None
    return status, parsed if isinstance(parsed, dict) else None


def _is_auth_failure(status: int, payload: dict[str, Any] | None) -> bool:
    if status == 401:
        return True
    if not payload or payload.get("code") == 1:
        return False
    info = str(payload.get("info") or "").strip().lower()
    return any(pattern.lower() in info for pattern in AUTH_FAILURE_PATTERNS)


def _upstream_error(status: int, payload: dict[str, Any] | None, path: str) -> AppError:
    info = str((payload or {}).get("info") or "").strip()
    code = (payload or {}).get("code")
    return AppError(
        "MCP_CALL_FAILED",
        502,
        {
            "detail": info or f"云视频上游请求失败：HTTP {status}",
            "upstream_status": status,
            "upstream_code": code,
            "path": path,
            "credential_location": "platform_only",
        },
    )


async def _login(force_refresh: bool = False) -> CloudVideoSession:
    global _cached_session

    config = _require_runtime_config()
    if not force_refresh and _cached_session:
        if _cached_session.expires_at - _now_seconds() > CLOUD_VIDEO_REFRESH_BUFFER_SECONDS:
            return _cached_session

    async with _session_lock:
        if not force_refresh and _cached_session:
            if _cached_session.expires_at - _now_seconds() > CLOUD_VIDEO_REFRESH_BUFFER_SECONDS:
                return _cached_session
        if force_refresh and _cached_session:
            fresh_enough = _cached_session.expires_at - _now_seconds() > CLOUD_VIDEO_REFRESH_BUFFER_SECONDS
            just_obtained = _now_seconds() - _cached_session.obtained_at < CLOUD_VIDEO_LOGIN_DEBOUNCE_SECONDS
            if fresh_enough and just_obtained:
                return _cached_session
        status, payload = await asyncio.to_thread(
            _post_form_sync,
            config,
            CLOUD_VIDEO_LOGIN_PATH,
            {
                "loginAccount": config.login_account,
                "password": config.password,
                "authCode": "",
                "type": "",
                "onlyId": "",
                "appId": "",
            },
            {"isInner": "1"},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        token = data.get("token") if isinstance(data, dict) else None
        if status != 200 or not payload or payload.get("code") != 1 or not isinstance(token, str) or not token:
            raise _upstream_error(status, payload, CLOUD_VIDEO_LOGIN_PATH)
        _cached_session = CloudVideoSession(
            token=token,
            expires_at=_decode_token_exp(token),
            obtained_at=_now_seconds(),
            account=config.login_account,
        )
        return _cached_session


def invalidate_cloud_video_session_cache() -> None:
    global _cached_session
    _cached_session = None


async def _request_upstream(
    path: str,
    body: dict[str, Any],
    *,
    retry_on_auth_failure: bool = True,
    is_inner: str = "0",
) -> Any:
    global _cached_session

    config = _require_runtime_config()
    session = await _login(False)
    request_id = str(uuid.uuid4())
    timestamp = str(_now_seconds())
    headers = {
        "isInner": is_inner,
        "token": session.token,
        "requestId": request_id,
        "timestamp": timestamp,
        "sign": _request_signature(token=session.token, request_id=request_id, timestamp=timestamp),
        "api-version": "2.0.0",
        "Access-Control-Allow-Private-Network": "true",
        "Origin": "https://sucaiwang.zhishangsoft.com",
        "Referer": "https://sucaiwang.zhishangsoft.com/",
    }
    status, payload = await asyncio.to_thread(_post_form_sync, config, path, body, headers)
    if _is_auth_failure(status, payload):
        invalidate_cloud_video_session_cache()
        if retry_on_auth_failure:
            await _login(True)
            return await _request_upstream(path, body, retry_on_auth_failure=False, is_inner=is_inner)
    if status != 200 or not payload or payload.get("code") != 1:
        raise _upstream_error(status, payload, path)
    return payload.get("data")


def _iso_from_seconds(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _csv_arg(args: dict[str, Any], *names: str) -> list[str]:
    raw = None
    for name in names:
        if name in args:
            raw = args.get(name)
            break
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    return [str(item).strip() for item in values if str(item).strip()]


def _text_arg(args: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in args and args.get(name) is not None:
            return str(args.get(name)).strip()
    return ""


def _bool_arg(args: dict[str, Any], *names: str, default: bool = False) -> bool:
    for name in names:
        if name in args:
            value = args.get(name)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "on"}
            return bool(value)
    return default


def _int_arg(args: dict[str, Any], *names: str, default: int, minimum: int, maximum: int) -> int:
    raw = None
    for name in names:
        if name in args:
            raw = args.get(name)
            break
    try:
        parsed = int(raw if raw is not None and raw != "" else default)
    except (TypeError, ValueError):
        raise AppError("PARAM_INVALID", 400, {"field": names[0], "reason": "must be integer"}) from None
    return min(max(parsed, minimum), maximum)


def _sort_order(args: dict[str, Any]) -> int:
    order = _int_arg(args, "sort_order", "sortOrder", default=1, minimum=1, maximum=99)
    return order if order in CLOUD_VIDEO_SORT_OPTIONS else 1


def _date_mode(args: dict[str, Any]) -> str:
    mode = _text_arg(args, "date_mode", "dateMode").lower()
    return mode if mode in {"upload", "shoot", "cost"} else "upload"


def _search_type(args: dict[str, Any], *, default: int = 1, maximum: int = 4) -> int:
    return _int_arg(args, "search_type", "searchType", default=default, minimum=0, maximum=maximum)


def _format_date_range(args: dict[str, Any]) -> dict[str, str]:
    mode = _date_mode(args)
    start = _text_arg(args, "start_date", "startDate")
    end = _text_arg(args, "end_date", "endDate")
    if mode == "shoot":
        return {
            "startDate": "",
            "endDate": "",
            "shootStartTime": start,
            "shootEndTime": end,
            "dyStatCostStartTime": "",
            "dyStatCostEndTime": "",
        }
    if mode == "cost":
        return {
            "startDate": "",
            "endDate": "",
            "shootStartTime": "",
            "shootEndTime": "",
            "dyStatCostStartTime": start,
            "dyStatCostEndTime": end,
        }
    return {
        "startDate": start,
        "endDate": end,
        "shootStartTime": "",
        "shootEndTime": "",
        "dyStatCostStartTime": "",
        "dyStatCostEndTime": "",
    }


def _parse_iso_date(value: str, *, field: str) -> date:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise AppError("PARAM_INVALID", 400, {"field": field, "reason": "must be YYYY-MM-DD"}) from None


def _report_date_range(args: dict[str, Any]) -> tuple[date, date]:
    start = _parse_iso_date(
        _text_arg(args, "start_date", "startDate") or CLOUD_VIDEO_REPORT_DEFAULT_START_DATE,
        field="start_date",
    )
    end = _parse_iso_date(
        _text_arg(args, "end_date", "endDate") or CLOUD_VIDEO_REPORT_DEFAULT_END_DATE,
        field="end_date",
    )
    if end < start:
        raise AppError("PARAM_INVALID", 400, {"field": "end_date", "reason": "must be >= start_date"})
    days = (end - start).days + 1
    if days > CLOUD_VIDEO_REPORT_MAX_DAYS:
        raise AppError("PARAM_INVALID", 400, {"field": "end_date", "reason": "date range cannot exceed 93 days"})
    return start, end


def _iter_days(start: date, end: date) -> list[date]:
    days = (end - start).days + 1
    return [start + timedelta(days=offset) for offset in range(days)]


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value or "").replace(",", "").strip()
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def _rounded(value: Any, digits: int = 4) -> float:
    return round(_number(value), digits)


def _report_metric_fields() -> tuple[str, ...]:
    return (
        "statCost",
        "roi",
        "payOrderAmountAndPayOrderCouponAmount",
        "payOrderAmount",
        "convertCnt",
        "convertRate",
        "convertCost",
        "showCnt",
        "clickCnt",
        "clickRate",
        "averageClickPrice",
        "totalPlay",
        "playDuration3s",
        "playDuration3sRate",
        "playOverRate",
    )


def _metric_summary(row: dict[str, Any]) -> dict[str, float]:
    return {field: _rounded(row.get(field)) for field in _report_metric_fields() if row.get(field) not in (None, "")}


def _report_payload(
    *,
    tab: str,
    start_date: str,
    end_date: str,
    account_ids: list[str],
    platform_type: int,
    platform_data_type: int,
    length: int,
    start: int,
    sort_key: str = "statCost",
    sort_type: str = "desc",
    search_video_ids: str = "",
) -> dict[str, Any]:
    data_type = 1
    row_type = 1 if tab == "detail" else 0
    return {
        "dataType": data_type,
        "type": row_type,
        "videoType": 0,
        "startDate": start_date,
        "endDate": end_date,
        "idStr": ",".join(account_ids),
        "typeIdStr": "",
        "sortKey": sort_key,
        "sortType": sort_type,
        "platformType": platform_type,
        "platformDataType": platform_data_type,
        "videoStartDate": "",
        "videoEndDate": "",
        "latType": 0,
        "topicType": "",
        "searchVideoIdsStr": search_video_ids,
        "isUseCache": True,
        "isStorewide": False,
        "version": "",
        "currency": "CNY",
        "isZyb": "",
        "length": length,
        "start": start,
    }


def _material_report_payload(
    *,
    search_type: int,
    search_ids: list[str],
    start_date: str,
    end_date: str,
    type_ids: list[str],
    platform_type: int,
    advertiser_id: str,
    advertiser_name: str,
    dimension_type: int | None,
) -> dict[str, Any]:
    # Mirrors the cloud-video front-end route /video-report/ad-report-new.
    if dimension_type is None:
        dimension_type = 2 if search_type == 4 else 1 if search_type in {1, 2, 3} else 0
    upstream_search_type: int | str = 2 if search_type == 4 else search_type if search_type in {1, 2, 3} else ""
    return {
        "type": dimension_type,
        "searchType": upstream_search_type,
        "searchIds": ",".join(search_ids),
        "statCostStartDate": start_date,
        "statCostEndDate": end_date,
        "typeIds": ",".join(type_ids),
        "platformType": platform_type,
        "advertiserId": advertiser_id,
        "advCompanyName": advertiser_name,
    }


def _video_usage_report_payload(
    *,
    data_type: int,
    top_type: int,
    metric_type: int,
    video_type: int | str,
    start_date: str,
    end_date: str,
    ids: list[str],
    type_ids: list[str],
    label_ids: list[str],
    query_type: int,
) -> dict[str, Any]:
    return {
        "dataType": data_type,
        "topType": top_type,
        "type": metric_type,
        "videoType": video_type,
        "startDate": start_date,
        "endDate": end_date,
        "idStr": ",".join(ids),
        "typeIdStr": ",".join(type_ids),
        "labelIdsStr": ",".join(label_ids),
        "queryType": query_type,
    }


def _map_labels(labels: Any) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for label in _safe_list(labels):
        data = _safe_dict(label)
        label_id = data.get("labelId", data.get("id"))
        if label_id is None:
            continue
        mapped.append(
            {
                "id": str(label_id),
                "name": _text(data.get("name"), limit=120) or "",
                "parent_id": str(data.get("referId")) if data.get("referId") not in (None, "") else None,
                "parent_name": _text(data.get("referName"), limit=120),
                "level": data.get("level") if isinstance(data.get("level"), int) else None,
            }
        )
    return mapped


def _map_categories(categories: Any, *, parent_id: str | None = None, include_children: bool = True) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for category in _safe_list(categories):
        data = _safe_dict(category)
        category_id = data.get("id")
        if category_id is None:
            continue
        item = {
            "id": str(category_id),
            "name": _text(data.get("name"), limit=120) or "",
            "level": data.get("level") if isinstance(data.get("level"), int) else None,
            "parent_id": parent_id,
            "auth_type": data.get("authType") if isinstance(data.get("authType"), int) else None,
            "video_type": data.get("videoType") if isinstance(data.get("videoType"), int) else None,
        }
        if include_children:
            item["children"] = _map_categories(
                data.get("childrens") or data.get("children") or [],
                parent_id=str(category_id),
                include_children=True,
            )
        mapped.append(item)
    return mapped


def _flatten_categories(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            clone = dict(node)
            children = clone.pop("children", [])
            flattened.append(clone)
            walk(children if isinstance(children, list) else [])

    walk(categories)
    return flattened


def _root_category_ids(categories: list[dict[str, Any]]) -> list[str]:
    return [str(category.get("id")) for category in categories if category.get("id") is not None]


def _resolve_parent_category_ids(categories: list[dict[str, Any]], selected_ids: list[str]) -> list[str]:
    if not selected_ids:
        return _root_category_ids(categories)
    flat = {str(item.get("id")): item for item in _flatten_categories(categories) if item.get("id") is not None}
    parent_ids: set[str] = set()
    for category_id in selected_ids:
        current = flat.get(str(category_id))
        while current:
            parent_id = current.get("parent_id")
            if not parent_id:
                if current.get("id") is not None:
                    parent_ids.add(str(current.get("id")))
                break
            current = flat.get(str(parent_id))
    return sorted(parent_ids)


def _map_tags(tags: Any, *, parent_id: str | None = None, include_system: bool = False) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for tag in _safe_list(tags):
        data = _safe_dict(tag)
        tag_id = data.get("id")
        if tag_id is None:
            continue
        is_system = data.get("isSys") == 1
        children = _map_tags(data.get("childrens") or data.get("children") or [], parent_id=str(tag_id), include_system=include_system)
        if is_system and not include_system:
            mapped.extend(children)
            continue
        mapped.append(
            {
                "id": str(tag_id),
                "name": _text(data.get("name"), limit=120) or "",
                "parent_id": parent_id,
                "checked_type": data.get("checkedType") if isinstance(data.get("checkedType"), int) else 0,
                "is_system": is_system,
                "state": data.get("state") if isinstance(data.get("state"), int) else 0,
                "refer_video_types": data.get("referVideoTypes") if isinstance(data.get("referVideoTypes"), list) else [],
                "children": children,
            }
        )
    return mapped


def _redacted_raw_video(video: dict[str, Any]) -> dict[str, Any]:
    raw = dict(video)
    if raw.get("coverUrl"):
        raw["coverUrl"] = "<redacted>"
    if raw.get("videoUrl"):
        raw["videoUrl"] = "<redacted>"
    return raw


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_cache_part(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:80].strip("._")
    return text or fallback


def _visual_cache_root() -> Path:
    root = Path(getattr(settings, "CLOUD_VIDEO_VISUAL_CACHE_ROOT", "data/cloud-video-visual-cache")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _visual_cache_path(cache_key: str) -> Path:
    safe_key = _safe_cache_part(cache_key, fallback="visual")
    rel = PurePosixPath(safe_key[:2], f"{safe_key}.json")
    root = _visual_cache_root()
    path = (root / rel.as_posix()).resolve()
    if root not in path.parents and path != root:
        raise AppError("PARAM_INVALID", 400, {"detail": "invalid visual cache key"})
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_visual_cache(cache_key: str, fingerprint: dict[str, Any], *, ttl_seconds: int) -> dict[str, Any] | None:
    path = _visual_cache_path(cache_key)
    if not path.is_file():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(cached, dict):
        return None
    cached_at = int(cached.get("cached_at") or 0)
    if ttl_seconds > 0 and _now_seconds() - cached_at > ttl_seconds:
        return None
    if cached.get("fingerprint_hash") != _json_hash(fingerprint):
        return None
    result = cached.get("result")
    return result if isinstance(result, dict) else None


def _write_visual_cache(cache_key: str, fingerprint: dict[str, Any], result: dict[str, Any]) -> None:
    path = _visual_cache_path(cache_key)
    safe_result = _strip_urls(result)
    payload = {
        "cached_at": _now_seconds(),
        "fingerprint_hash": _json_hash(fingerprint),
        "fingerprint": fingerprint,
        "result": safe_result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _strip_urls(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if "url" in lowered or lowered in {"token", "sign", "cookie", "authorization"}:
                cleaned[key] = "<redacted>" if item else item
            else:
                cleaned[key] = _strip_urls(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_urls(item) for item in value]
    if isinstance(value, str):
        return URL_TEXT_RE.sub("<redacted-url>", value)
    return value


def _raw_video_media(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_url": _text(raw.get("videoUrl"), limit=4000),
        "cover_url": _text(raw.get("coverUrl"), limit=4000),
    }


def _download_media_data_url(url: str, *, default_mime: str, max_bytes: int) -> tuple[str, int, str]:
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("media url is empty")
    parsed = urllib.parse.urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("media url must be http(s)")
    request = urllib.request.Request(clean_url, headers={"User-Agent": "SkillForge-CloudVideo-Visual/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(int(getattr(settings, "CLOUD_VIDEO_REQUEST_TIMEOUT_SECONDS", 30) or 30), 3), 120)) as response:
        content_type = str(response.headers.get("Content-Type") or default_mime).split(";", 1)[0].strip() or default_mime
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("media_too_large")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}", len(data), content_type


def _visual_video_fingerprint(video: dict[str, Any], topic_key: str) -> dict[str, Any]:
    raw = _safe_dict(video.get("raw"))
    lifecycle_hint = _safe_dict(video.get("lifecycle_hint"))
    return {
        "video_id": str(video.get("id") or raw.get("videoId") or ""),
        "title": video.get("title") or _text(raw.get("name"), limit=300),
        "topic_key": topic_key,
        "duration_seconds": video.get("duration_seconds") or raw.get("duration"),
        "size_bytes": video.get("size_bytes") or raw.get("fileSize"),
        "created_at": video.get("created_at") or raw.get("createTime"),
        "state_label": video.get("state_label") or raw.get("videoState"),
        "has_video": bool(raw.get("videoUrl")),
        "has_cover": bool(raw.get("coverUrl")),
        "lifecycle_hint": {
            key: lifecycle_hint.get(key)
            for key in ("source", "click_total", "click_peak", "zero_second_click", "phases", "script_opening", "creative_gap_summary")
            if lifecycle_hint.get(key) not in (None, "", [], {})
        },
    }


def _visual_model_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _strip_urls(value)
    text = str(value or "").strip()
    if not text:
        return {}
    cleaned = text
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1:] if first_nl >= 0 else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return _strip_urls(parsed)
    except json.JSONDecodeError:
        pass
    return {"summary": URL_TEXT_RE.sub("<redacted-url>", text), "format": "text"}


async def _cloud_video_raw_by_id(video_id: str) -> dict[str, Any] | None:
    clean_id = str(video_id or "").strip()
    if not clean_id:
        return None
    data = await _request_upstream(
        "/api/video/query",
        {
            "length": CLOUD_VIDEO_VISUAL_QUERY_PAGE_SIZE,
            "start": 0,
            "order": 1,
            "labelIds": "",
            "unionLabelIds": "",
            "eliminateLabelIds": "",
            "name": clean_id,
            "searchDate": "",
            "typeIdStr": "",
            "videoType": CLOUD_VIDEO_VIDEO_TYPE,
            "searchType": 1,
            "searchIds": "",
            "startDate": "",
            "endDate": "",
            "shootStartTime": "",
            "shootEndTime": "",
            "dyStatCostStartTime": "",
            "dyStatCostEndTime": "",
            "systemAutoLabelType": "",
            "videoState": "",
            "videoCollectionId": "",
        },
    )
    result = _safe_dict(data)
    for item in _safe_list(result.get("list")):
        raw = _safe_dict(item)
        if str(raw.get("videoId", raw.get("id")) or "").strip() == clean_id:
            return raw
    return None


def _merge_visual_video_input(item: dict[str, Any], raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    mapped = _map_video(raw, include_labels=True, include_raw=True) if raw else {}
    video_id = str(item.get("video_id") or item.get("videoId") or item.get("id") or mapped.get("id") or "").strip()
    title = _text(item.get("video_name") or item.get("title") or mapped.get("title"), limit=300) or video_id
    topic_key = _text(item.get("topic_key") or item.get("topicKey") or mapped.get("category_name") or "", limit=300) or "未分类"
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else mapped.get("labels")
    lifecycle_hint = item.get("lifecycle_hint") if isinstance(item.get("lifecycle_hint"), dict) else {}
    return {
        **mapped,
        "id": video_id,
        "title": title,
        "topic_key": topic_key,
        "category_name": item.get("category_name") or item.get("categoryName") or mapped.get("category_name"),
        "labels": labels,
        "lifecycle_hint": lifecycle_hint,
        "raw": raw,
    }


def _visual_content_parts(
    video: dict[str, Any],
    media: dict[str, Any],
    *,
    role: str,
    topic_key: str,
    visual_detail: str = "high",
) -> list[dict[str, Any]]:
    lifecycle_hint = _safe_dict(video.get("lifecycle_hint"))
    summary = {
        "role": role,
        "topic_key": topic_key,
        "video_id": video.get("id"),
        "title": video.get("title"),
        "duration_seconds": video.get("duration_seconds"),
        "size_bytes": video.get("size_bytes"),
        "category_name": video.get("category_name"),
        "labels": video.get("labels"),
    }
    if lifecycle_hint:
        summary["lifecycle_hint"] = lifecycle_hint
    lifecycle_prompt = ""
    if role == "benchmark" and lifecycle_hint:
        lifecycle_prompt = (
            "该 benchmark 附带千川点击生命周期摘要，请重点观察 click_peak 秒点及相邻 2 秒内的可见画面、"
            "口播/字幕/商品/价格/优惠/CTA/信任证据，判断用户为什么在这些点位产生点击；"
            "revision_actions 需要说明低消耗视频可以复用的触发因素，不要跨主题照搬完整脚本。"
        )
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请基于下面完整短视频素材做视觉诊断，不要只看封面、首帧或尾帧；"
                "不要使用未提供的投放数据，不要编造画面外信息。"
                "按普通消费者看到视频的顺序判断首屏钩子、商品露出、节奏密度、卖点证据、行动引导。"
                f"{lifecycle_prompt}"
                "返回 JSON，字段包含 summary、diagnosis、evidence、revision_actions、risks、confidence。"
                f"素材元数据：{json.dumps(summary, ensure_ascii=False, default=str)}"
            ),
        }
    ]
    if media.get("video_model_url"):
        parts.append({
            "type": "video_url",
            "video_url": {
                "url": media["video_model_url"],
                "detail": visual_detail,
                "fps": CLOUD_VIDEO_VISUAL_VIDEO_FPS,
                "max_frames": CLOUD_VIDEO_VISUAL_VIDEO_MAX_FRAMES,
            },
        })
    return parts


async def _analyze_cloud_video_visual(
    video: dict[str, Any],
    *,
    role: str,
    topic_key: str,
    cache_scope: str,
    force_refresh: bool,
    ttl_seconds: int,
) -> dict[str, Any]:
    raw = _safe_dict(video.get("raw"))
    video_id = str(video.get("id") or raw.get("videoId") or "").strip()
    if not video_id:
        return {"status": "skipped", "reason": "missing_video_id"}
    fingerprint = _visual_video_fingerprint(video, topic_key)
    cache_key = _json_hash({"scope": cache_scope, "topic_key": topic_key, "video_id": video_id})
    if not force_refresh:
        cached = _read_visual_cache(cache_key, fingerprint, ttl_seconds=ttl_seconds)
        if cached:
            return {**cached, "cache_hit": True}

    media_refs = _raw_video_media(raw)
    if not media_refs.get("video_url"):
        result = {
            "status": "skipped",
            "reason": "no_video_media",
            "video": {k: fingerprint.get(k) for k in ("video_id", "title", "topic_key", "duration_seconds", "size_bytes")},
            "media": {"has_video": False, "has_cover": bool(media_refs.get("cover_url")), "raw_urls_returned": False},
            "cache_hit": False,
        }
        _write_visual_cache(cache_key, fingerprint, result)
        return result

    system = (
        "你是 SkillForge 内容电商短视频视觉诊断 Agent。"
        "你必须基于完整视频可见信息给出可验证结论，不要只看封面、首帧或尾帧。"
        "不要编造画面、价格、销量、功效、优惠或投放数据。"
    )

    async def call_visual(media: dict[str, Any], *, delivery: str, visual_detail: str = "high") -> dict[str, Any]:
        raw_result = await call_llm_multimodal(
            system,
            _visual_content_parts(video, media, role=role, topic_key=topic_key, visual_detail=visual_detail),
            json_mode=False,
            max_tokens=1800,
            temperature=0.1,
            timeout=120,
            call_source="cloud_video_visual_analysis",
            cost_context={
                "skill_id": "cloud_video_visual_analysis",
                "conversation_id": f"cloud-video:{video_id}",
                "department": "示例品牌内容电商运营部",
                "video_id": video_id,
                "topic_key": topic_key,
                "role": role,
                "delivery": delivery,
                "visual_detail": visual_detail,
            },
            model_profile="vision",
            require_system_config=True,
        )
        parsed = _visual_model_result(raw_result)
        if not parsed:
            raise RuntimeError("vision_model_empty_response")
        return parsed

    delivery = "direct_url"
    media: dict[str, Any] = {}
    if media_refs.get("video_url"):
        media["video_model_url"] = media_refs["video_url"]

    try:
        try:
            parsed = await call_visual(media, delivery=delivery, visual_detail="high")
            visual_detail = "high"
        except Exception:
            parsed = await call_visual(media, delivery=delivery, visual_detail="low")
            visual_detail = "low"
    except Exception as direct_exc:  # noqa: BLE001
        max_bytes = int(getattr(settings, "CLOUD_VIDEO_VISUAL_MAX_MEDIA_BYTES", 30 * 1024 * 1024) or 30 * 1024 * 1024)
        media = {}
        download_error = ""
        if media_refs.get("video_url"):
            try:
                data_url, byte_size, mime_type = await asyncio.to_thread(
                    _download_media_data_url,
                    str(media_refs["video_url"]),
                    default_mime="video/mp4",
                    max_bytes=max_bytes,
                )
                media.update({"video_model_url": data_url, "downloaded_kind": "video", "downloaded_bytes": byte_size, "downloaded_mime": mime_type})
            except Exception as exc:  # noqa: BLE001
                download_error = redact_secret_text(exc, limit=160) or type(exc).__name__
        if not media.get("video_model_url"):
            result = {
                "status": "failed",
                "reason": "direct_url_and_video_download_failed",
                "video": {k: fingerprint.get(k) for k in ("video_id", "title", "topic_key", "duration_seconds", "size_bytes")},
                "media": {"has_video": bool(media_refs.get("video_url")), "has_cover": bool(media_refs.get("cover_url")), "raw_urls_returned": False},
                "error": redact_secret_text(direct_exc, limit=140) or download_error,
                "download_error": download_error,
                "cache_hit": False,
            }
            _write_visual_cache(cache_key, fingerprint, result)
            return result
        delivery = "downloaded_data_url"
        try:
            try:
                parsed = await call_visual(media, delivery=delivery, visual_detail="high")
                visual_detail = "high"
            except Exception:
                parsed = await call_visual(media, delivery=delivery, visual_detail="low")
                visual_detail = "low"
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "role": role,
                "topic_key": topic_key,
                "video": {k: fingerprint.get(k) for k in ("video_id", "title", "topic_key", "duration_seconds", "size_bytes")},
                "media": {
                    "has_video": bool(media_refs.get("video_url")),
                    "has_cover": bool(media_refs.get("cover_url")),
                    "delivery": delivery,
                    "downloaded_kind": media.get("downloaded_kind"),
                    "downloaded_bytes": media.get("downloaded_bytes"),
                    "raw_urls_returned": False,
                },
                "error": redact_secret_text(exc, limit=300) or type(exc).__name__,
                "fallback_from": "direct_url",
                "cache_hit": False,
            }

    try:
        result = {
            "status": "ok",
            "role": role,
            "topic_key": topic_key,
            "video": {k: fingerprint.get(k) for k in ("video_id", "title", "topic_key", "duration_seconds", "size_bytes")},
            "media": {
                "has_video": bool(media_refs.get("video_url")),
                "has_cover": bool(media_refs.get("cover_url")),
                "delivery": delivery,
                "downloaded_kind": media.get("downloaded_kind"),
                "downloaded_bytes": media.get("downloaded_bytes"),
                "raw_urls_returned": False,
            },
            "model_profile": "vision",
            "visual_detail": visual_detail,
            "result": parsed,
            "cache_hit": False,
        }
        _write_visual_cache(cache_key, fingerprint, result)
        return result
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "role": role,
            "topic_key": topic_key,
            "video": {k: fingerprint.get(k) for k in ("video_id", "title", "topic_key", "duration_seconds", "size_bytes")},
            "media": {
                "has_video": bool(media_refs.get("video_url")),
                "has_cover": bool(media_refs.get("cover_url")),
                "downloaded_kind": media.get("downloaded_kind"),
                "downloaded_bytes": media.get("downloaded_bytes"),
                "raw_urls_returned": False,
            },
            "error": redact_secret_text(exc, limit=300) or type(exc).__name__,
            "cache_hit": False,
        }


def _map_video(video: Any, *, include_labels: bool = True, include_raw: bool = False) -> dict[str, Any]:
    data = _safe_dict(video)
    video_id = data.get("videoId", data.get("id"))
    labels = _map_labels(data.get("labels")) if include_labels else []
    item: dict[str, Any] = {
        "id": str(video_id) if video_id is not None else "",
        "title": _text(data.get("name"), limit=300) or (f"video-{video_id}" if video_id is not None else "video"),
        "description": _text(data.get("desc"), limit=1000),
        "account_id": str(data.get("accountId")) if data.get("accountId") not in (None, "") else None,
        "account_name": _text(data.get("accountName"), limit=120),
        "category_id": str(data.get("typeId")) if data.get("typeId") not in (None, "") else None,
        "category_name": _text(data.get("typeName"), limit=120),
        "parent_category_id": str(data.get("parentTypeId")) if data.get("parentTypeId") not in (None, "") else None,
        "parent_category_name": _text(data.get("parentTypeName"), limit=120),
        "duration_seconds": data.get("duration") if isinstance(data.get("duration"), (int, float)) else None,
        "size_bytes": data.get("fileSize") if isinstance(data.get("fileSize"), (int, float)) else None,
        "created_at": _text(data.get("createTime"), limit=80),
        "state_label": _text(data.get("videoState"), limit=80),
        "video_type": data.get("videoType") if isinstance(data.get("videoType"), int) else None,
        "can_play": data.get("canPlay") == 1,
        "labels": labels,
        "media": {
            "has_cover": bool(_text(data.get("coverUrl"), limit=20)),
            "has_video": bool(_text(data.get("videoUrl"), limit=20)),
            "raw_url_redacted": True,
        },
        "metrics": {
            "views": data.get("viewCount") if isinstance(data.get("viewCount"), int) else 0,
            "downloads": data.get("downloadCount") if isinstance(data.get("downloadCount"), int) else 0,
            "collects": data.get("collectCount") if isinstance(data.get("collectCount"), int) else 0,
            "pushes": data.get("pushCount") if isinstance(data.get("pushCount"), int) else 0,
            "copies": data.get("copyNum") if isinstance(data.get("copyNum"), int) else 0,
            "jianying_copies": data.get("jianyingCount") if isinstance(data.get("jianyingCount"), int) else 0,
        },
    }
    if include_raw:
        item["raw"] = _redacted_raw_video(data)
    return item


def _map_account_tree(nodes: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    roots: list[dict[str, Any]] = []
    people: list[dict[str, Any]] = []

    def walk(node: Any, *, depth: int, team: dict[str, Any] | None, group: dict[str, Any] | None) -> dict[str, Any] | None:
        data = _safe_dict(node)
        node_id = data.get("id")
        if node_id is None:
            return None
        name = _text(
            data.get("name") or data.get("accountName") or data.get("teamName") or data.get("groupName"),
            limit=120,
        ) or str(node_id)
        children = _safe_list(data.get("childrens") or data.get("children"))
        item: dict[str, Any] = {"id": str(node_id), "name": name, "children": []}
        if depth == 0:
            team = {"id": str(node_id), "name": name}
        elif depth == 1:
            group = {"id": str(node_id), "name": name}
            item["team_id"] = (team or {}).get("id")
            item["team_name"] = (team or {}).get("name")
        if children:
            for child in children:
                mapped_child = walk(child, depth=depth + 1, team=team, group=group)
                if mapped_child:
                    item["children"].append(mapped_child)
        else:
            person = {
                "account_id": str(node_id),
                "account_name": name,
                "team_id": (team or {}).get("id"),
                "team_name": (team or {}).get("name"),
                "group_id": (group or {}).get("id"),
                "group_name": (group or {}).get("name"),
            }
            people.append(person)
            item.update(person)
        return item

    for root in _safe_list(nodes):
        mapped = walk(root, depth=0, team=None, group=None)
        if mapped:
            roots.append(mapped)
    return roots, people


def _map_person_report_row(row: Any, *, day: str | None = None) -> dict[str, Any]:
    data = _safe_dict(row)
    return {
        "date": day,
        "team_id": str(data.get("teamId")) if data.get("teamId") not in (None, "") else None,
        "team_name": _text(data.get("teamName"), limit=120),
        "group_id": str(data.get("groupId")) if data.get("groupId") not in (None, "") else None,
        "group_name": _text(data.get("groupName"), limit=120),
        "account_id": str(data.get("accountId")) if data.get("accountId") not in (None, "") else None,
        "account_name": _text(data.get("accountName"), limit=120),
        "primary_video_id": str(data.get("videoId")) if data.get("videoId") not in (None, "") else None,
        "metrics": _metric_summary(data),
    }


def _map_video_report_row(row: Any, *, day: str | None = None) -> dict[str, Any]:
    data = _safe_dict(row)
    return {
        "date": day,
        "team_id": str(data.get("teamId")) if data.get("teamId") not in (None, "") else None,
        "team_name": _text(data.get("teamName"), limit=120),
        "group_id": str(data.get("groupId")) if data.get("groupId") not in (None, "") else None,
        "group_name": _text(data.get("groupName"), limit=120),
        "account_id": str(data.get("accountId")) if data.get("accountId") not in (None, "") else None,
        "account_name": _text(data.get("accountName"), limit=120),
        "video_id": str(data.get("videoId")) if data.get("videoId") not in (None, "") else None,
        "video_name": _text(data.get("videoName"), limit=500),
        "parent_category_name": _text(data.get("parentTypeName"), limit=120),
        "category_name": _text(data.get("typeName"), limit=120),
        "metrics": _metric_summary(data),
    }


def _map_material_report_row(row: Any) -> dict[str, Any]:
    data = _safe_dict(row)
    items = []
    for item in _safe_list(data.get("items")):
        item_data = _safe_dict(item)
        items.append(
            {
                "label_name": _text(item_data.get("labelName"), limit=120),
                "material_count": _rounded(item_data.get("materialCount"), 0),
                "material_count_ratio": _rounded(item_data.get("materialCountRatio")),
                "stat_cost": _rounded(item_data.get("statCost")),
                "stat_cost_ratio": _rounded(item_data.get("statCostRatio")),
                "material_ids": item_data.get("materialIdSet") if isinstance(item_data.get("materialIdSet"), list) else [],
            }
        )
    return {
        "team_id": str(data.get("teamId")) if data.get("teamId") not in (None, "") else None,
        "team_name": _text(data.get("teamName"), limit=120),
        "group_id": str(data.get("groupId")) if data.get("groupId") not in (None, "") else None,
        "group_name": _text(data.get("groupName"), limit=120),
        "account_id": str(data.get("accountId")) if data.get("accountId") not in (None, "") else None,
        "account_name": _text(data.get("accountName"), limit=120),
        "parent_category_id": str(data.get("parentTypeId")) if data.get("parentTypeId") not in (None, "") else None,
        "parent_category_name": _text(data.get("parentTypeName"), limit=120),
        "category_id": str(data.get("typeId")) if data.get("typeId") not in (None, "") else None,
        "category_name": _text(data.get("typeName"), limit=120),
        "material_count": _rounded(data.get("materialCount"), 0),
        "material_count_ratio": _rounded(data.get("materialCountRatio")),
        "stat_cost": _rounded(data.get("statCost")),
        "stat_cost_ratio": _rounded(data.get("statCostRatio")),
        "items": items,
        "raw": {key: value for key, value in data.items() if key not in {"items"}},
    }


def _map_video_usage_report_detail(row: Any) -> dict[str, Any]:
    data = _safe_dict(row)
    return {
        "team_id": str(data.get("tId") or data.get("teamId")) if data.get("tId") or data.get("teamId") else None,
        "team_name": _text(data.get("teamName") or data.get("tName"), limit=120),
        "group_id": str(data.get("gId") or data.get("groupId")) if data.get("gId") or data.get("groupId") else None,
        "group_name": _text(data.get("groupName") or data.get("gName"), limit=120),
        "account_id": str(data.get("aId") or data.get("accountId")) if data.get("aId") or data.get("accountId") else None,
        "account_name": _text(data.get("accountName") or data.get("aName") or data.get("name"), limit=120),
        "name": _text(data.get("name"), limit=200),
        "upload_user_count": _rounded(data.get("uploadUserCount"), 0),
        "upload_count": _rounded(data.get("uploadCount"), 0),
        "download_count": _rounded(data.get("downloadCount"), 0),
        "download_user_count": _rounded(data.get("numberOfDownloads"), 0),
        "push_user_count": _rounded(data.get("numberOfPush"), 0),
        "jianying_user_count": _rounded(data.get("numberOfJianying"), 0),
        "utilization_rate": _rounded(data.get("utilizationRate")),
        "burst_video_count": _rounded(data.get("burstNumber"), 0),
        "raw": data,
    }


def _map_video_usage_report_date(row: Any) -> dict[str, Any]:
    data = _safe_dict(row)
    calendars = []
    for item in _safe_list(data.get("videoReportDateCalendarVos")):
        item_data = _safe_dict(item)
        calendars.append(
            {
                "date": _text(item_data.get("date"), limit=32),
                "count": _rounded(item_data.get("count"), 0),
            }
        )
    return {
        "name": _text(data.get("name"), limit=120),
        "items": calendars,
        "raw": {key: value for key, value in data.items() if key != "videoReportDateCalendarVos"},
    }


def _map_video_usage_report_proportion(row: Any) -> dict[str, Any]:
    data = _safe_dict(row)
    return {
        "name": _text(data.get("name"), limit=120),
        "value": _rounded(data.get("value"), 0),
        "raw": data,
    }


def _map_video_usage_report(data: Any) -> dict[str, Any]:
    payload = _safe_dict(data)
    detail_rows = _safe_list(payload.get("videoReportDetailVos"))
    date_rows = _safe_list(payload.get("videoReportDateVos"))
    proportion_rows = _safe_list(payload.get("videoProportionVos"))
    mapped_details = [_map_video_usage_report_detail(item) for item in detail_rows]
    total_upload_count = round(sum(_number(item.get("upload_count")) for item in mapped_details), 4)
    total_burst_count = round(sum(_number(item.get("burst_video_count")) for item in mapped_details), 4)
    return {
        "detail_count": len(mapped_details),
        "details": mapped_details,
        "date_series": [_map_video_usage_report_date(item) for item in date_rows],
        "proportions": [_map_video_usage_report_proportion(item) for item in proportion_rows],
        "summary": {
            "upload_count": total_upload_count,
            "burst_video_count": total_burst_count,
        },
        "raw_keys": sorted(str(key) for key in payload.keys()),
    }


def _strip_html_text(value: Any, *, limit: int = 2000) -> str | None:
    text = _text(value, limit=limit)
    if text is None:
        return None
    text = text.replace("<br/>", "\n").replace("<br>", "\n").replace("</p>", "\n")
    stripped: list[str] = []
    in_tag = False
    for char in text:
        if char == "<":
            in_tag = True
            continue
        if char == ">":
            in_tag = False
            continue
        if not in_tag:
            stripped.append(char)
    cleaned = " ".join("".join(stripped).split())
    return cleaned[:limit] if cleaned else None


def _html_text_list(value: Any, *, limit: int = 2000) -> list[dict[str, Any]]:
    items = []
    for item in _safe_list(value):
        raw = _text(item, limit=limit)
        if raw is None:
            continue
        items.append({"text": _strip_html_text(raw, limit=limit), "html": raw})
    return items


def _map_audit_reject(row: Any) -> dict[str, Any]:
    data = _safe_dict(row)
    reject_reasons = _html_text_list(data.get("rejectReasons"))
    suggestions = _html_text_list(data.get("suggestions"))
    return {
        "id": str(data.get("id")) if data.get("id") not in (None, "") else None,
        "video_id": str(data.get("videoId")) if data.get("videoId") not in (None, "") else None,
        "transform_id": str(data.get("transformId")) if data.get("transformId") not in (None, "") else None,
        "transform_name": _text(data.get("transformName"), limit=300),
        "platform_type": data.get("type") if isinstance(data.get("type"), int) else None,
        "platform_name": {
            0: "巨量广告",
            2: "巨量千川",
            3: "抖音首页",
            4: "磁力智投",
            5: "磁力金牛",
            6: "腾讯ADQ",
            7: "快手首页",
            9: "TikTok",
            10: "百度营销",
        }.get(data.get("type")),
        "ad_type": data.get("adType") if isinstance(data.get("adType"), int) else None,
        "advertiser_id": str(data.get("advertiserId")) if data.get("advertiserId") not in (None, "") else None,
        "advertiser_name": _text(data.get("advertiserName"), limit=200),
        "material_id": str(data.get("materialId")) if data.get("materialId") not in (None, "") else None,
        "ad_id": str(data.get("adId")) if data.get("adId") not in (None, "") else None,
        "dynamic_creative_id": str(data.get("dynamicCreativeId")) if data.get("dynamicCreativeId") not in (None, "") else None,
        "state": data.get("state") if isinstance(data.get("state"), int) else None,
        "state_label": "历史卡审" if data.get("state") == 1 else "卡审中" if data.get("state") == 0 else None,
        "reject_reasons": reject_reasons,
        "suggestions": suggestions,
        "reject_reason_text": [item["text"] for item in reject_reasons if item.get("text")],
        "suggestion_text": [item["text"] for item in suggestions if item.get("text")],
        "refresh_time": _text(data.get("refreshTime"), limit=80),
        "raw": {key: value for key, value in data.items() if key not in {"rejectReasons", "suggestions"}},
    }


def _report_page_rows(data: Any) -> tuple[list[Any], int]:
    result = _safe_dict(data)
    page = _safe_dict(result.get("data")) if isinstance(result.get("data"), dict) else result
    rows = _safe_list(page.get("list"))
    total = page.get("total")
    return rows, int(total) if isinstance(total, int) else len(rows)


async def _cloud_video_account_tree() -> dict[str, Any]:
    data = await _request_upstream("/api/team/query", {"isCancel": ""}, is_inner="0")
    tree, people = _map_account_tree(data)
    return {
        "tree": tree,
        "people": people,
        "account_ids": [str(item["account_id"]) for item in people if item.get("account_id")],
    }


async def _query_report_rows(
    *,
    tab: str,
    start_date: str,
    end_date: str,
    account_ids: list[str],
    platform_type: int,
    platform_data_type: int,
    limit: int,
    sort_key: str = "statCost",
    sort_type: str = "desc",
    search_video_ids: str = "",
) -> tuple[list[Any], int]:
    rows: list[Any] = []
    total = 0
    start = 0
    page_size = min(CLOUD_VIDEO_REPORT_PAGE_SIZE, max(1, limit))
    while len(rows) < limit:
        data = await _request_upstream(
            "/api/report/query-video-report",
            _report_payload(
                tab=tab,
                start_date=start_date,
                end_date=end_date,
                account_ids=account_ids,
                platform_type=platform_type,
                platform_data_type=platform_data_type,
                length=page_size,
                start=start,
                sort_key=sort_key,
                sort_type=sort_type,
                search_video_ids=search_video_ids,
            ),
            is_inner="0",
        )
        page_rows, total = _report_page_rows(data)
        if not page_rows:
            break
        remaining = limit - len(rows)
        rows.extend(page_rows[:remaining])
        if len(rows) >= total or len(page_rows) < page_size:
            break
        start += page_size
    return rows, total


async def _query_daily_report_rows(
    *,
    days: list[date],
    account_ids: list[str],
    platform_type: int,
    platform_data_type: int,
    max_person_rows: int,
    max_video_rows: int,
    max_returned_video_rows: int,
    include_daily_rows: bool,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(CLOUD_VIDEO_REPORT_CONCURRENCY)

    async def query_day_tab(day_text: str, tab: str, limit: int) -> tuple[list[Any], int]:
        async with semaphore:
            return await _query_report_rows(
                tab=tab,
                start_date=day_text,
                end_date=day_text,
                account_ids=account_ids,
                platform_type=platform_type,
                platform_data_type=platform_data_type,
                limit=limit,
            )

    async def query_day(day: date) -> dict[str, Any]:
        day_text = day.isoformat()
        person_task = asyncio.create_task(query_day_tab(day_text, "personal", max_person_rows))
        video_task = asyncio.create_task(query_day_tab(day_text, "detail", max_video_rows))
        person_result, video_result = await asyncio.gather(person_task, video_task)
        person_rows, person_total = person_result
        video_rows, video_total = video_result
        return {
            "day": day_text,
            "person_rows": person_rows,
            "person_total": person_total,
            "video_rows": video_rows,
            "video_total": video_total,
        }

    day_results = await asyncio.gather(*(query_day(day) for day in days))
    day_results.sort(key=lambda item: str(item.get("day") or ""))

    daily_person_rows: list[dict[str, Any]] = []
    daily_video_rows: list[dict[str, Any]] = []
    summary_video_rows: list[dict[str, Any]] = []
    person_total_rows = 0
    video_total_rows = 0
    analysis_truncated = False
    returned_rows_truncated = False
    for result in day_results:
        day_text = str(result["day"])
        person_rows = _safe_list(result.get("person_rows"))
        video_rows = _safe_list(result.get("video_rows"))
        person_total = int(result.get("person_total") or 0)
        video_total = int(result.get("video_total") or 0)
        person_total_rows += person_total
        video_total_rows += video_total
        analysis_truncated = analysis_truncated or len(person_rows) < person_total or len(video_rows) < video_total

        remaining_person_rows = max_person_rows - len(daily_person_rows)
        mapped_person_rows = [_map_person_report_row(row, day=day_text) for row in person_rows]
        if remaining_person_rows > 0:
            daily_person_rows.extend(mapped_person_rows[:remaining_person_rows])
            returned_rows_truncated = returned_rows_truncated or len(mapped_person_rows) > remaining_person_rows
        elif person_total:
            returned_rows_truncated = True

        mapped_video_rows = [_map_video_report_row(row, day=day_text) for row in video_rows]
        summary_video_rows.extend(mapped_video_rows)
        if include_daily_rows:
            remaining_video_rows = max_returned_video_rows - len(daily_video_rows)
            if remaining_video_rows > 0:
                daily_video_rows.extend(mapped_video_rows[:remaining_video_rows])
                returned_rows_truncated = returned_rows_truncated or len(mapped_video_rows) > remaining_video_rows
            elif video_total:
                returned_rows_truncated = True

    return {
        "daily_person_rows": daily_person_rows,
        "daily_video_rows": daily_video_rows,
        "summary_video_rows": summary_video_rows,
        "person_total_rows": person_total_rows,
        "video_total_rows": video_total_rows,
        "analysis_truncated": analysis_truncated,
        "returned_rows_truncated": returned_rows_truncated,
    }


def _sum_into(target: dict[str, float], metrics: dict[str, Any]) -> None:
    summed_fields = {
        "statCost",
        "payOrderAmountAndPayOrderCouponAmount",
        "payOrderAmount",
        "convertCnt",
        "showCnt",
        "clickCnt",
        "totalPlay",
        "playDuration3s",
    }
    for field in summed_fields:
        target[field] = round(target.get(field, 0.0) + _number(metrics.get(field)), 4)
    play_weight = _number(metrics.get("totalPlay")) or _number(metrics.get("showCnt"))
    if play_weight > 0:
        for rate_field in ("playOverRate", "playDuration3sRate"):
            if metrics.get(rate_field) not in (None, ""):
                target[f"_{rate_field}_weighted"] = round(
                    target.get(f"_{rate_field}_weighted", 0.0) + _number(metrics.get(rate_field)) * play_weight,
                    4,
                )
                target[f"_{rate_field}_weight"] = round(target.get(f"_{rate_field}_weight", 0.0) + play_weight, 4)


def _derive_rates(metrics: dict[str, float]) -> dict[str, float]:
    stat_cost = _number(metrics.get("statCost"))
    pay_amount = _number(metrics.get("payOrderAmountAndPayOrderCouponAmount"))
    convert_cnt = _number(metrics.get("convertCnt"))
    click_cnt = _number(metrics.get("clickCnt"))
    show_cnt = _number(metrics.get("showCnt"))
    total_play = _number(metrics.get("totalPlay"))
    play_3s = _number(metrics.get("playDuration3s"))
    derived = dict(metrics)
    if stat_cost:
        derived["roi"] = round(pay_amount / stat_cost, 4)
        derived["convertCost"] = round(stat_cost / convert_cnt, 4) if convert_cnt else 0.0
        derived["averageClickPrice"] = round(stat_cost / click_cnt, 4) if click_cnt else 0.0
    if show_cnt:
        derived["clickRate"] = round(click_cnt / show_cnt * 100, 4)
    if click_cnt:
        derived["convertRate"] = round(convert_cnt / click_cnt * 100, 4)
    if total_play and play_3s:
        derived["playDuration3sRate"] = round(play_3s / total_play * 100, 4)
    for rate_field in ("playOverRate", "playDuration3sRate"):
        weight = _number(metrics.get(f"_{rate_field}_weight"))
        weighted = _number(metrics.get(f"_{rate_field}_weighted"))
        if weight > 0:
            derived[rate_field] = round(weighted / weight, 4)
        derived.pop(f"_{rate_field}_weight", None)
        derived.pop(f"_{rate_field}_weighted", None)
    return derived


def _video_signal(video: dict[str, Any], *, person_metrics: dict[str, float]) -> dict[str, Any]:
    metrics = video.get("metrics") if isinstance(video.get("metrics"), dict) else {}
    roi = _number(metrics.get("roi"))
    click_rate = _number(metrics.get("clickRate"))
    convert_rate = _number(metrics.get("convertRate"))
    stat_cost = _number(metrics.get("statCost"))
    person_roi = _number(person_metrics.get("roi"))
    cost_share = stat_cost / _number(person_metrics.get("statCost")) if _number(person_metrics.get("statCost")) else 0
    issues: list[str] = []
    strengths: list[str] = []
    if cost_share >= 0.2:
        strengths.append("high_spend_weight")
    if person_roi and roi and roi < person_roi * 0.9:
        issues.append("roi_below_person_average")
    if click_rate and click_rate < 1.0:
        issues.append("low_ctr")
    if convert_rate and convert_rate < 5.0:
        issues.append("low_convert_rate")
    if _number(metrics.get("playOverRate")) < 2.0:
        issues.append("low_completion_rate")
    if roi and person_roi and roi > person_roi * 1.1:
        strengths.append("roi_above_person_average")
    if click_rate >= 1.5:
        strengths.append("strong_ctr")
    return {
        "cost_share": round(cost_share, 4),
        "issues": issues,
        "strengths": strengths,
        "analysis_hint": "需要结合视频首屏钩子、商品露出、节奏密度、卖点证据和行动引导做视觉诊断。",
    }


def _build_person_video_summary(video_rows: list[dict[str, Any]], *, top_n: int, low_n: int) -> list[dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for row in video_rows:
        account_id = str(row.get("account_id") or "")
        if not account_id:
            continue
        person = people.setdefault(
            account_id,
            {
                "account_id": account_id,
                "account_name": row.get("account_name"),
                "team_name": row.get("team_name"),
                "group_name": row.get("group_name"),
                "metrics": {},
                "videos_by_id": {},
            },
        )
        row_metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        _sum_into(person["metrics"], row_metrics)
        video_id = str(row.get("video_id") or "")
        if not video_id:
            continue
        video = person["videos_by_id"].setdefault(
            video_id,
            {
                "video_id": video_id,
                "video_name": row.get("video_name"),
                "parent_category_name": row.get("parent_category_name"),
                "category_name": row.get("category_name"),
                "account_id": account_id,
                "account_name": row.get("account_name"),
                "team_name": row.get("team_name"),
                "group_name": row.get("group_name"),
                "metrics": {},
                "daily_breakdown": [],
            },
        )
        _sum_into(video["metrics"], row_metrics)
        video["daily_breakdown"].append(
            {
                "date": row.get("date"),
                "metrics": row_metrics,
            }
        )
    result: list[dict[str, Any]] = []
    for person in people.values():
        person["metrics"] = _derive_rates(person["metrics"])
        videos = list(person["videos_by_id"].values())
        for video in videos:
            video["metrics"] = _derive_rates(video["metrics"])
        videos = sorted(
            videos,
            key=lambda item: _number((item.get("metrics") or {}).get("statCost")),
            reverse=True,
        )
        for video in videos:
            video["signal"] = _video_signal(video, person_metrics=person["metrics"])
        low_videos = sorted(
            videos,
            key=lambda item: (
                _number((item.get("metrics") or {}).get("roi")),
                -_number((item.get("metrics") or {}).get("statCost")),
            ),
        )
        person_cost = _number((person.get("metrics") or {}).get("statCost"))
        min_low_cost = max(1000.0, person_cost * 0.005)
        low_candidates = [
            item
            for item in low_videos
            if _number((item.get("metrics") or {}).get("statCost")) >= min_low_cost
        ]
        if not low_candidates:
            low_candidates = [item for item in low_videos if _number((item.get("metrics") or {}).get("statCost")) > 0]
        person["top_cost_videos"] = videos[:top_n]
        person["low_roi_videos"] = low_candidates[:low_n]
        person.pop("videos_by_id", None)
        result.append(person)
    return sorted(result, key=lambda item: _number((item.get("metrics") or {}).get("statCost")), reverse=True)


async def builtin_cloud_video_session(args: dict[str, Any]) -> dict[str, Any]:
    force_refresh = _bool_arg(args, "force_refresh", "forceRefresh", default=False)
    config = _runtime_config()
    if not config.enabled:
        return {
            "ok": True,
            "enabled": False,
            "account": None,
            "expires_at": None,
            "expires_in_seconds": None,
            "credential_location": "platform_only",
        }
    session = await _login(force_refresh)
    expires_in_seconds = max(0, session.expires_at - _now_seconds())
    return {
        "ok": True,
        "enabled": True,
        "account": session.account,
        "expires_at": _iso_from_seconds(session.expires_at),
        "expires_in_seconds": expires_in_seconds,
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_categories(args: dict[str, Any]) -> dict[str, Any]:
    include_children = _bool_arg(args, "include_children", "includeChildren", default=True)
    max_items = _int_arg(args, "max_items", "maxItems", default=200, minimum=1, maximum=CLOUD_VIDEO_MAX_CATEGORY_ITEMS)
    category_id = _text_arg(args, "category_id", "categoryId")
    data = await _request_upstream(
        "/api/type/query",
        {"videoType": CLOUD_VIDEO_VIDEO_TYPE, "categoryId": category_id},
    )
    tree = _map_categories(data, include_children=True)
    items = tree if include_children else _flatten_categories(tree)
    items = items[:max_items]
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "query": {"category_id": category_id or "", "include_children": include_children, "max_items": max_items},
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_accounts(args: dict[str, Any]) -> dict[str, Any]:
    include_tree = _bool_arg(args, "include_tree", "includeTree", default=False)
    max_people = _int_arg(args, "max_people", "maxPeople", default=200, minimum=1, maximum=1000)
    data = await _cloud_video_account_tree()
    people = data["people"][:max_people]
    result: dict[str, Any] = {
        "ok": True,
        "count": len(people),
        "total": len(data["people"]),
        "people": people,
        "query": {"include_tree": include_tree, "max_people": max_people},
        "credential_location": "platform_only",
    }
    if include_tree:
        result["tree"] = data["tree"]
    return result


async def builtin_cloud_video_tags(args: dict[str, Any]) -> dict[str, Any]:
    category_ids = _csv_arg(args, "category_ids", "categoryIds", "type_ids", "typeIds")
    parent_type_ids = _csv_arg(args, "parent_type_ids", "parentTypeIds")
    include_system = _bool_arg(args, "include_system", "includeSystem", default=False)
    max_items = _int_arg(args, "max_items", "maxItems", default=200, minimum=1, maximum=CLOUD_VIDEO_MAX_TAG_ITEMS)
    if not parent_type_ids:
        categories_data = await _request_upstream(
            "/api/type/query",
            {"videoType": CLOUD_VIDEO_VIDEO_TYPE, "categoryId": ""},
        )
        parent_type_ids = _resolve_parent_category_ids(_map_categories(categories_data, include_children=True), category_ids)
    data = await _request_upstream(
        "/api/type-label-relation/query-index",
        {
            "parentTypeIds": ",".join(parent_type_ids),
            "typeIdsStr": ",".join(category_ids),
            "videoType": CLOUD_VIDEO_VIDEO_TYPE,
        },
    )
    tree = _map_tags(data, include_system=include_system)
    items = tree[:max_items]
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "query": {
            "category_ids": category_ids,
            "parent_type_ids": parent_type_ids,
            "include_system": include_system,
            "max_items": max_items,
        },
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_videos(args: dict[str, Any]) -> dict[str, Any]:
    page = _int_arg(args, "page", default=CLOUD_VIDEO_DEFAULT_PAGE, minimum=1, maximum=10_000)
    page_size = _int_arg(
        args,
        "page_size",
        "pageSize",
        default=CLOUD_VIDEO_DEFAULT_PAGE_SIZE,
        minimum=1,
        maximum=CLOUD_VIDEO_MAX_PAGE_SIZE,
    )
    category_ids = _csv_arg(args, "category_ids", "categoryIds", "type_ids", "typeIds")
    tag_ids = _csv_arg(args, "tag_ids", "tagIds", "label_ids", "labelIds")
    exclude_tag_ids = _csv_arg(args, "exclude_tag_ids", "excludeTagIds", "eliminate_label_ids", "eliminateLabelIds")
    search_type = _search_type(args, default=1)
    search_ids = _csv_arg(args, "search_ids", "searchIds", "account_ids", "accountIds")
    system_auto_label_type = _text_arg(args, "system_auto_label_type", "systemAutoLabelType")
    video_state = _text_arg(args, "video_state", "videoState")
    video_type = _int_arg(args, "video_type", "videoType", default=CLOUD_VIDEO_VIDEO_TYPE, minimum=0, maximum=30)
    search = _text_arg(args, "search", "name")
    date_mode = _date_mode(args)
    dates = _format_date_range(args)
    include_labels = _bool_arg(args, "include_labels", "includeLabels", default=True)
    include_raw = _bool_arg(args, "include_raw", "includeRaw", default=False)
    auto_page = _bool_arg(args, "auto_page", "autoPage", default=False)
    max_pages = _int_arg(args, "max_pages", "maxPages", default=5, minimum=1, maximum=20)
    max_items = _int_arg(args, "max_items", "maxItems", default=300, minimum=1, maximum=1000)
    order = 9 if date_mode == "cost" else _sort_order(args)

    def page_body(page_number: int) -> dict[str, Any]:
        return {
            "length": page_size,
            "start": (page_number - 1) * page_size,
            "order": order,
            "labelIds": ",".join(tag_ids),
            "unionLabelIds": "",
            "eliminateLabelIds": ",".join(exclude_tag_ids),
            "name": search,
            "searchDate": "",
            "typeIdStr": ",".join(category_ids),
            "videoType": video_type,
            "searchType": search_type,
            "searchIds": ",".join(search_ids),
            "startDate": dates["startDate"],
            "endDate": dates["endDate"],
            "shootStartTime": dates["shootStartTime"],
            "shootEndTime": dates["shootEndTime"],
            "dyStatCostStartTime": dates["dyStatCostStartTime"],
            "dyStatCostEndTime": dates["dyStatCostEndTime"],
            "systemAutoLabelType": system_auto_label_type,
            "videoState": video_state,
            "videoCollectionId": "",
        }

    pages_read = 0
    total: int | None = None
    total_pages: int | None = None
    last_page = page
    raw_items: list[Any] = []
    if auto_page:
        for page_number in range(page, page + max_pages):
            data = await _request_upstream("/api/video/query", page_body(page_number))
            result = _safe_dict(data)
            batch = _safe_list(result.get("list"))
            pages_read += 1
            last_page = result.get("pageNo") if isinstance(result.get("pageNo"), int) else page_number
            if isinstance(result.get("total"), int):
                total = result.get("total")
            if isinstance(result.get("totalPage"), int):
                total_pages = result.get("totalPage")
            if not batch:
                break
            remaining = max_items - len(raw_items)
            raw_items.extend(batch[:remaining])
            if len(raw_items) >= max_items:
                break
            if total_pages is not None and page_number >= total_pages:
                break
            if total is not None and len(raw_items) >= total:
                break
    else:
        data = await _request_upstream("/api/video/query", page_body(page))
        result = _safe_dict(data)
        raw_items = _safe_list(result.get("list"))
        pages_read = 1
        last_page = result.get("pageNo") if isinstance(result.get("pageNo"), int) else page
        total = result.get("total") if isinstance(result.get("total"), int) else len(raw_items)
        total_pages = result.get("totalPage") if isinstance(result.get("totalPage"), int) else None

    items = [_map_video(item, include_labels=include_labels, include_raw=include_raw) for item in raw_items]
    truncated = bool(
        auto_page
        and (
            len(items) >= max_items
            or (total_pages is not None and pages_read >= max_pages and last_page < total_pages)
            or (total is not None and len(items) < total and pages_read >= max_pages)
        )
    )
    return {
        "ok": True,
        "page": last_page,
        "page_size": page_size,
        "total": total if total is not None else len(items),
        "total_pages": total_pages,
        "count": len(items),
        "pagination": {
            "auto_page": auto_page,
            "start_page": page,
            "pages_read": pages_read,
            "max_pages": max_pages if auto_page else 1,
            "max_items": max_items if auto_page else page_size,
            "truncated": truncated,
        },
        "query": {
            "search": search,
            "category_ids": category_ids,
            "tag_ids": tag_ids,
            "exclude_tag_ids": exclude_tag_ids,
            "search_type": search_type,
            "search_ids": search_ids,
            "system_auto_label_type": system_auto_label_type or None,
            "video_state": video_state or None,
            "video_type": video_type,
            "sort_order": order,
            "date_mode": date_mode,
            "start_date": _text_arg(args, "start_date", "startDate") or None,
            "end_date": _text_arg(args, "end_date", "endDate") or None,
            "include_labels": include_labels,
            "include_raw": include_raw,
            "auto_page": auto_page,
            "max_pages": max_pages if auto_page else None,
            "max_items": max_items if auto_page else None,
        },
        "items": items,
        "credential_location": "platform_only",
        "media_access": {
            "raw_urls_returned": False,
            "note": "MCP 默认只返回素材元数据；视频/封面原始地址由平台保管。",
        },
    }


def _visual_request_items(args: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = args.get(key)
        if isinstance(value, list):
            items: list[dict[str, Any]] = []
            for item in value:
                if isinstance(item, dict):
                    video_id = str(item.get("video_id") or item.get("videoId") or item.get("id") or "").strip()
                    if video_id:
                        items.append(item)
                elif str(item or "").strip():
                    items.append({"video_id": str(item).strip()})
            return items
    ids = _csv_arg(args, "video_ids", "videoIds")
    return [{"video_id": item} for item in ids]


async def builtin_cloud_video_visual_analysis(args: dict[str, Any]) -> dict[str, Any]:
    low_items = _visual_request_items(args, "low_videos", "videos")
    benchmark_items = _visual_request_items(args, "benchmark_videos", "benchmarks")
    max_videos = _int_arg(
        args,
        "max_videos",
        "maxVideos",
        default=CLOUD_VIDEO_VISUAL_DEFAULT_MAX_VIDEOS,
        minimum=1,
        maximum=CLOUD_VIDEO_VISUAL_MAX_VIDEOS,
    )
    force_refresh = _bool_arg(args, "force_refresh", "forceRefresh", default=False)
    ttl_seconds = _int_arg(
        args,
        "cache_ttl_seconds",
        "cacheTtlSeconds",
        default=int(getattr(settings, "CLOUD_VIDEO_VISUAL_CACHE_TTL_SECONDS", 14 * 24 * 60 * 60) or 0),
        minimum=0,
        maximum=90 * 24 * 60 * 60,
    )
    requested: list[tuple[str, dict[str, Any]]] = []
    for item in low_items:
        requested.append(("low", item))
    for item in benchmark_items:
        requested.append(("benchmark", item))
    if not requested:
        raise AppError("PARAM_INVALID", 400, {"detail": "low_videos/videos or benchmark_videos is required"})
    if len(requested) > max_videos:
        requested = requested[:max_videos]

    resolved: list[tuple[str, dict[str, Any]]] = []
    missing: list[dict[str, Any]] = []
    errors: list[str] = []
    for role, item in requested:
        video_id = str(item.get("video_id") or item.get("videoId") or item.get("id") or "").strip()
        try:
            raw = await _cloud_video_raw_by_id(video_id)
        except AppError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            message = str(detail.get("detail") or exc.message or exc.code or "video_query_failed")
            errors.append(message[:300])
            missing.append({
                "role": role,
                "video_id": video_id,
                "topic_key": item.get("topic_key") or item.get("topicKey"),
                "status": "skipped",
                "reason": "video_query_failed",
                "error_code": exc.code,
                "error": message[:300],
            })
            continue
        if raw is None:
            missing.append({
                "role": role,
                "video_id": video_id,
                "topic_key": item.get("topic_key") or item.get("topicKey"),
                "status": "skipped",
                "reason": "media_not_found",
            })
            continue
        resolved.append((role, _merge_visual_video_input(item, raw)))

    unique: dict[str, tuple[str, dict[str, Any], str]] = {}
    for role, video in resolved:
        topic_key = str(video.get("topic_key") or "未分类")
        video_id = str(video.get("id") or "")
        cache_scope = "topic_benchmark" if role == "benchmark" else "low_video"
        dedupe_key = _json_hash({"role": role, "topic_key": topic_key, "video_id": video_id})
        unique.setdefault(dedupe_key, (role, video, cache_scope))

    async def analyze_one(entry: tuple[str, dict[str, Any], str]) -> tuple[str, dict[str, Any]]:
        role, video, cache_scope = entry
        topic_key = str(video.get("topic_key") or "未分类")
        result = await _analyze_cloud_video_visual(
            video,
            role=role,
            topic_key=topic_key,
            cache_scope=cache_scope,
            force_refresh=force_refresh,
            ttl_seconds=ttl_seconds,
        )
        key = _json_hash({"role": role, "topic_key": topic_key, "video_id": video.get("id")})
        return key, result

    analyzed_pairs = await asyncio.gather(*(analyze_one(entry) for entry in unique.values())) if unique else []
    analyzed = {key: value for key, value in analyzed_pairs}

    low_results: dict[str, Any] = {}
    benchmark_results: dict[str, Any] = {}
    for role, video in resolved:
        topic_key = str(video.get("topic_key") or "未分类")
        video_id = str(video.get("id") or "")
        key = _json_hash({"role": role, "topic_key": topic_key, "video_id": video_id})
        result = analyzed.get(key) or {"status": "skipped", "reason": "analysis_not_available"}
        if role == "benchmark":
            benchmark_results[f"{topic_key}::{video_id}"] = result
        else:
            low_results[video_id] = result

    ok_count = sum(1 for item in [*low_results.values(), *benchmark_results.values()] if isinstance(item, dict) and item.get("status") == "ok")
    skipped_count = len(missing) + sum(
        1 for item in [*low_results.values(), *benchmark_results.values()]
        if isinstance(item, dict) and item.get("status") != "ok"
    )
    return {
        "ok": True,
        "analysis_schema": "cloud_video_visual_analysis_v1",
        "status": "ok" if ok_count and not skipped_count else "partial" if ok_count else "no_usable_visual_media",
        "counts": {
            "requested": len(requested),
            "resolved": len(resolved),
            "ok": ok_count,
            "skipped_or_failed": skipped_count,
            "cache_hits": sum(
                1 for item in [*low_results.values(), *benchmark_results.values()]
                if isinstance(item, dict) and item.get("cache_hit") is True
            ),
        },
        "low_videos": low_results,
        "benchmark_videos": benchmark_results,
        "missing": missing,
        "errors": errors[:20],
        "media_access": {"raw_urls_returned": False, "credential_location": "platform_only"},
        "cache": {"scope": "topic_key + video_id", "ttl_seconds": ttl_seconds},
    }


async def builtin_cloud_video_material_report(args: dict[str, Any]) -> dict[str, Any]:
    start_date, end_date = _report_date_range(args)
    search_type = _search_type(args, default=0)
    if search_type not in CLOUD_VIDEO_MATERIAL_REPORT_SEARCH_TYPES:
        raise AppError("PARAM_INVALID", 400, {"field": "search_type", "reason": "must be 0, 1, 2, 3 or 4"})
    search_ids = _csv_arg(args, "search_ids", "searchIds", "account_ids", "accountIds")
    type_ids = _csv_arg(args, "type_ids", "typeIds", "category_ids", "categoryIds")
    platform_type = _int_arg(args, "platform_type", "platformType", default=2, minimum=0, maximum=230)
    dimension_type_arg = _text_arg(args, "dimension_type", "dimensionType", "type")
    dimension_type = None if dimension_type_arg == "" else _int_arg(args, "dimension_type", "dimensionType", "type", default=0, minimum=0, maximum=20)
    data = await _request_upstream(
        "/api/dy/video-material-relation/query-video-relation-material-report",
        _material_report_payload(
            search_type=search_type,
            search_ids=search_ids,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            type_ids=type_ids,
            platform_type=platform_type,
            advertiser_id=_text_arg(args, "advertiser_id", "advertiserId"),
            advertiser_name=_text_arg(args, "advertiser_name", "advCompanyName"),
            dimension_type=dimension_type,
        ),
        is_inner="0",
    )
    rows = [_map_material_report_row(item) for item in _safe_list(data)]
    return {
        "ok": True,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "platform": {"platform_type": platform_type},
        "query": {
            "search_type": search_type,
            "search_ids": search_ids,
            "type_ids": type_ids,
            "dimension_type": dimension_type,
        },
        "count": len(rows),
        "items": rows,
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_video_usage_report(args: dict[str, Any]) -> dict[str, Any]:
    start_date, end_date = _report_date_range(args)
    data_type = _int_arg(args, "data_type", "dataType", default=3, minimum=1, maximum=3)
    if data_type not in CLOUD_VIDEO_VIDEO_REPORT_DATA_TYPES:
        raise AppError("PARAM_INVALID", 400, {"field": "data_type", "reason": "must be 1, 2 or 3"})
    top_type = _int_arg(args, "top_type", "topType", default=2, minimum=0, maximum=2)
    if top_type not in CLOUD_VIDEO_VIDEO_REPORT_TOP_TYPES:
        raise AppError("PARAM_INVALID", 400, {"field": "top_type", "reason": "must be 0, 1 or 2"})
    metric_type = _int_arg(args, "metric_type", "metricType", "type", default=0, minimum=0, maximum=20)
    video_type_arg = _text_arg(args, "video_type", "videoType")
    video_type: int | str
    if video_type_arg == "":
        video_type = CLOUD_VIDEO_VIDEO_TYPE
    else:
        video_type = _int_arg(args, "video_type", "videoType", default=CLOUD_VIDEO_VIDEO_TYPE, minimum=0, maximum=30)
    ids = _csv_arg(args, "ids", "idStr", "account_ids", "accountIds", "search_ids", "searchIds")
    type_ids = _csv_arg(args, "type_ids", "typeIds", "typeIdStr", "category_ids", "categoryIds")
    label_ids = _csv_arg(args, "label_ids", "labelIds", "labelIdsStr", "tag_ids", "tagIds")
    query_type = _int_arg(args, "query_type", "queryType", default=0, minimum=0, maximum=20)
    data = await _request_upstream(
        "/api/video/query-video-report",
        _video_usage_report_payload(
            data_type=data_type,
            top_type=top_type,
            metric_type=metric_type,
            video_type=video_type,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            ids=ids,
            type_ids=type_ids,
            label_ids=label_ids,
            query_type=query_type,
        ),
        is_inner="0",
    )
    mapped = _map_video_usage_report(data)
    return {
        "ok": True,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "query": {
            "data_type": data_type,
            "top_type": top_type,
            "metric_type": metric_type,
            "video_type": video_type,
            "ids": ids,
            "type_ids": type_ids,
            "label_ids": label_ids,
            "query_type": query_type,
        },
        **mapped,
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_audit_rejects(args: dict[str, Any]) -> dict[str, Any]:
    video_id = _text_arg(args, "video_id", "videoId")
    if not video_id:
        raise AppError("PARAM_INVALID", 400, {"field": "video_id", "reason": "is required"})
    page = _int_arg(args, "page", default=CLOUD_VIDEO_DEFAULT_PAGE, minimum=1, maximum=10_000)
    page_size = _int_arg(args, "page_size", "pageSize", default=50, minimum=1, maximum=500)
    platform_type = _int_arg(args, "platform_type", "platformType", "type", default=2, minimum=0, maximum=30)
    state_arg = _text_arg(args, "state")
    state: int | str = ""
    if state_arg != "":
        state = _int_arg(args, "state", default=0, minimum=0, maximum=1)
    path = "/api/video/get-tx-audit-reject" if platform_type == 6 else "/api/video/get-audit-reject"
    data = await _request_upstream(
        path,
        {
            "length": page_size,
            "start": (page - 1) * page_size,
            "videoId": video_id,
            "type": platform_type,
            "state": state,
        },
        is_inner="0",
    )
    result = _safe_dict(data)
    raw_items = _safe_list(result.get("list"))
    items = [_map_audit_reject(item) for item in raw_items]
    return {
        "ok": True,
        "page": result.get("pageNo") if isinstance(result.get("pageNo"), int) else page,
        "page_size": result.get("pageSize") if isinstance(result.get("pageSize"), int) else page_size,
        "total": result.get("total") if isinstance(result.get("total"), int) else len(items),
        "count": len(items),
        "query": {
            "video_id": video_id,
            "platform_type": platform_type,
            "state": state if state != "" else None,
        },
        "items": items,
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_ad_report(args: dict[str, Any]) -> dict[str, Any]:
    start_date, end_date = _report_date_range(args)
    tab_text = _text_arg(args, "tab", "report_tab", "reportTab").lower()
    tab = "detail" if tab_text in {"detail", "明细", "video", "videos"} else "personal"
    platform_type = _int_arg(args, "platform_type", "platformType", default=2, minimum=0, maximum=230)
    platform_data_type = _int_arg(args, "platform_data_type", "platformDataType", default=0, minimum=0, maximum=10)
    limit = _int_arg(args, "limit", default=100, minimum=1, maximum=1000)
    account_ids = _csv_arg(args, "account_ids", "accountIds", "idStr")
    if not account_ids:
        accounts = await _cloud_video_account_tree()
        account_ids = accounts["account_ids"]
    rows, total = await _query_report_rows(
        tab=tab,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        account_ids=account_ids,
        platform_type=platform_type,
        platform_data_type=platform_data_type,
        limit=limit,
    )
    mapper = _map_video_report_row if tab == "detail" else _map_person_report_row
    items = [mapper(row) for row in rows]
    return {
        "ok": True,
        "tab": "明细" if tab == "detail" else "个人",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "platform": {"platform_type": platform_type, "platform_data_type": platform_data_type},
        "total": total,
        "count": len(items),
        "items": items,
        "query": {"account_ids_count": len(account_ids), "limit": limit},
        "credential_location": "platform_only",
    }


async def builtin_cloud_video_daily_person_video_report(args: dict[str, Any]) -> dict[str, Any]:
    start_date, end_date = _report_date_range(args)
    platform_type = _int_arg(args, "platform_type", "platformType", default=2, minimum=0, maximum=230)
    platform_data_type = _int_arg(args, "platform_data_type", "platformDataType", default=0, minimum=0, maximum=10)
    max_video_rows = _int_arg(
        args,
        "max_video_rows",
        "maxVideoRows",
        default=CLOUD_VIDEO_REPORT_DEFAULT_VIDEO_ROWS,
        minimum=1,
        maximum=CLOUD_VIDEO_REPORT_MAX_VIDEO_ROWS,
    )
    max_returned_video_rows = _int_arg(
        args,
        "max_returned_video_rows",
        "maxReturnedVideoRows",
        default=CLOUD_VIDEO_REPORT_DEFAULT_RETURNED_VIDEO_ROWS,
        minimum=0,
        maximum=CLOUD_VIDEO_REPORT_MAX_VIDEO_ROWS,
    )
    max_person_rows = _int_arg(
        args,
        "max_person_rows",
        "maxPersonRows",
        default=CLOUD_VIDEO_REPORT_MAX_PERSON_ROWS,
        minimum=1,
        maximum=CLOUD_VIDEO_REPORT_MAX_PERSON_ROWS,
    )
    top_n = _int_arg(args, "top_videos_per_person", "topVideosPerPerson", default=5, minimum=1, maximum=20)
    low_n = _int_arg(args, "low_videos_per_person", "lowVideosPerPerson", default=5, minimum=1, maximum=20)
    include_daily_rows = _bool_arg(args, "include_daily_rows", "includeDailyRows", default=False)
    account_ids = _csv_arg(args, "account_ids", "accountIds", "idStr")
    accounts = await _cloud_video_account_tree()
    if not account_ids:
        account_ids = accounts["account_ids"]
    account_id_set = set(account_ids)
    people_scope = [person for person in accounts["people"] if str(person.get("account_id")) in account_id_set]

    daily_result = await _query_daily_report_rows(
        days=_iter_days(start_date, end_date),
        account_ids=account_ids,
        platform_type=platform_type,
        platform_data_type=platform_data_type,
        max_person_rows=max_person_rows,
        max_video_rows=max_video_rows,
        max_returned_video_rows=max_returned_video_rows,
        include_daily_rows=include_daily_rows,
    )
    daily_person_rows = daily_result["daily_person_rows"]
    daily_video_rows = daily_result["daily_video_rows"]
    summary_video_rows = daily_result["summary_video_rows"]
    person_total_rows = int(daily_result["person_total_rows"])
    video_total_rows = int(daily_result["video_total_rows"])
    analysis_truncated = bool(daily_result["analysis_truncated"])
    returned_rows_truncated = bool(daily_result["returned_rows_truncated"])

    people = _build_person_video_summary(summary_video_rows, top_n=top_n, low_n=low_n)
    return {
        "ok": True,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "platform": {
            "platform_type": platform_type,
            "platform_data_type": platform_data_type,
            "name": "巨量千川",
            "subtype": "千川汇总",
        },
        "scope": {
            "account_count": len(account_ids),
            "people_count": len(people_scope),
            "people": people_scope,
        },
        "daily": {
            "person_total_rows": person_total_rows,
            "video_total_rows": video_total_rows,
            "person_returned_rows": len(daily_person_rows),
            "video_returned_rows": len(daily_video_rows),
            "analysis_rows": len(summary_video_rows),
            "analysis_truncated": analysis_truncated,
            "returned_rows_truncated": returned_rows_truncated,
            "truncated": analysis_truncated or returned_rows_truncated,
        },
        "people": people,
        "daily_person_rows": daily_person_rows if include_daily_rows else [],
        "daily_video_rows": daily_video_rows if include_daily_rows else [],
        "analysis_contract": {
            "join_key": "video_id",
            "visual_analysis_input": "将 people[].top_cost_videos 和 people[].low_roi_videos 的 video_id/video_name 交给项目视频视觉分析能力，再按人生成改进方案。",
            "diagnosis_dimensions": ["首屏钩子", "商品露出", "节奏密度", "卖点证据", "行动引导"],
        },
        "credential_location": "platform_only",
    }
