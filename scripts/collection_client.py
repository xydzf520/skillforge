#!/usr/bin/env python3
"""Client for the governed /api/collection/fetch endpoint."""

from __future__ import annotations

import json
import os
from typing import Any
import urllib.error
import urllib.request

from skillforge_mcp_runtime import ToolMeta


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _platform_url() -> str:
    return _env_first("SKILLFORGE_PLATFORM_URL", "SKILLFORGE_BASE_URL", "SKILLFORGE_HTTP_BASE").rstrip("/")


def _run_token() -> str:
    return _env_first("SKILLFORGE_RUN_TOKEN", "RUN_TOKEN")


def _run_id() -> str:
    return _env_first("SKILLFORGE_RUN_ID", "RUN_ID")


def _instance_id() -> str:
    return _env_first("SKILLFORGE_INSTANCE_ID", "INSTANCE_ID")


def _skill_id() -> str:
    return _env_first("SKILLFORGE_SKILL_ID", "SKILL_ID")


def _source_id(meta: ToolMeta, override: str | None = None) -> str:
    return override or meta.source_id or _env_first("SKILLFORGE_SOURCE_ID", "SOURCE_ID") or f"platform-{meta.platform}"


def fetch(
    meta: ToolMeta,
    shop_id: str,
    params: dict[str, Any] | None = None,
    *,
    source_id: str | None = None,
    credential_scope: str | None = None,
    credential_plan_id: str | None = None,
    timeout: int | float | None = None,
) -> dict[str, Any]:
    base = _platform_url()
    if not base:
        raise RuntimeError("mcp_env_missing: SKILLFORGE_PLATFORM_URL")
    body = {
        "mcp_tool_name": meta.tool_name,
        "skill_id": _skill_id(),
        "run_id": _run_id(),
        "instance_id": _instance_id(),
        "source_id": _source_id(meta, source_id),
        "platform": meta.platform,
        "shop_id": shop_id,
        "data_scope": meta.data_scope,
        "endpoint_family": meta.endpoint_family,
        "warning_group": meta.warning_group,
        "params": params or {},
    }
    if credential_scope:
        body["credential_scope"] = credential_scope
    if credential_plan_id:
        body["credential_plan_id"] = credential_plan_id
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = _run_token()
    if token:
        headers["X-Run-Token"] = token
    req = urllib.request.Request(
        f"{base}/api/collection/fetch",
        data=raw,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or 60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"error": {"code": f"HTTP_{exc.code}", "message": str(exc)}}
        code = ((payload.get("error") or {}).get("code") if isinstance(payload, dict) else None) or f"HTTP_{exc.code}"
        message = ((payload.get("error") or {}).get("message") if isinstance(payload, dict) else None) or str(exc)
        return {"success": False, "error": code, "message": message, "data": None, "raw_error": payload}


def fetch_batch(
    meta: ToolMeta,
    shop_id: str,
    items: list[dict[str, Any]],
    *,
    source_id: str | None = None,
    credential_scope: str | None = None,
    credential_plan_id: str | None = None,
    concurrency: int | None = None,
    timeout: int | float | None = None,
) -> dict[str, Any]:
    base = _platform_url()
    if not base:
        raise RuntimeError("mcp_env_missing: SKILLFORGE_PLATFORM_URL")
    body = {
        "mcp_tool_name": meta.tool_name,
        "skill_id": _skill_id(),
        "run_id": _run_id(),
        "instance_id": _instance_id(),
        "source_id": _source_id(meta, source_id),
        "platform": meta.platform,
        "shop_id": shop_id,
        "data_scope": meta.data_scope,
        "endpoint_family": meta.endpoint_family,
        "warning_group": meta.warning_group,
        "items": items or [],
    }
    if credential_scope:
        body["credential_scope"] = credential_scope
    if credential_plan_id:
        body["credential_plan_id"] = credential_plan_id
    if concurrency:
        body["concurrency"] = concurrency
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = _run_token()
    if token:
        headers["X-Run-Token"] = token
    req = urllib.request.Request(
        f"{base}/api/collection/fetch-batch",
        data=raw,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or 120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"error": {"code": f"HTTP_{exc.code}", "message": str(exc)}}
        code = ((payload.get("error") or {}).get("code") if isinstance(payload, dict) else None) or f"HTTP_{exc.code}"
        message = ((payload.get("error") or {}).get("message") if isinstance(payload, dict) else None) or str(exc)
        return {"success": False, "error": code, "message": message, "data": None, "raw_error": payload}
