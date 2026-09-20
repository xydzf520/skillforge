#!/usr/bin/env python3
"""Yuyi Data OpenAPI MCP Server (stdio, 继承 SyncMcpServer 基类 → 自动获得超时保护).

This MCP wraps the documented Yuyi OpenAPI endpoints as stable tools for
SkillForge skills. API credentials are injected by SkillForge from project
backend configuration, with environment variables retained as local fallback:

- YUYIDATA_APP_KEY
- YUYIDATA_APP_SECRET (optional, used for signed query endpoints)
- YUYIDATA_BASE_URL (optional, defaults to https://openapi.yuyidata.com)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 添加 scripts/ 到 sys.path，便于导入 mcp_base
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_base import SyncMcpServer
from skillforge_mcp_runtime import ToolMeta, register_tool


DEFAULT_BASE_URL = "https://openapi.yuyidata.com"
MAX_LIMIT = 100
DEFAULT_DOWNLOAD_MAX_LINES = 500
DEFAULT_DOWNLOAD_MAX_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


API_CATALOG = [
    {"id": "upload_audio_url", "path": "/openapi/v3/session/upload/audioUrl", "method": "POST", "purpose": "Upload audio URL sessions for asynchronous QA/transcription.", "qps": "recommend <= 10 audios/sec", "notes": "Submit previous-day data before 03:00; daily processing starts around 04:00."},
    {"id": "upload_text_session", "path": "/openapi/v3/session/upload/text", "method": "POST", "purpose": "Upload complete two-party text conversations for asynchronous QA.", "qps": "10 DPS", "notes": "Single chat only; msgData may only contain customerId and agentId speakers."},
    {"id": "upload_comment_text", "path": "/openapi/v1/comment/upload/text", "method": "POST", "purpose": "Upload product/SKU comments for QA tagging.", "qps": "10 QPS", "notes": "Up to 10 comments per request."},
    {"id": "retrieve_sessions_create_task", "path": "/openapi/v3/sessions/retrieve", "method": "POST", "purpose": "Create an export task for dialogue content and QA labels.", "qps": "queue accepts 10 requests", "notes": "Recommended T+1 18:00-24:00; start/end span <= 48 hours."},
    {"id": "retrieve_sessions_get_task_info", "path": "/openapi/v3/sessions/getTaskInfo", "method": "POST", "purpose": "Poll export task progress and retrieve temporary file URL.", "qps": "poll every 30 seconds", "notes": "Status: 1 processing, 2 success, 3 failed, 4 no data."},
    {"id": "comment_check_result", "path": "/openapi/v4/comment/check/result", "method": "POST", "purpose": "Pull processed comment QA/tagging results.", "qps": "10 QPS", "notes": "T-30 window; date span <= 3 days; limit 0-100."},
    {"id": "upload_single_sentence", "path": "/openapi/v1/session/dsm-text/{appKey}", "method": "POST", "purpose": "Upload one dialogue sentence, e.g. for WeCom streaming data.", "qps": "not specified", "notes": "appKey is in path, not request body."},
    {"id": "upload_batch_sentences", "path": "/openapi/v1/session/batch-dsm-text/{appKey}", "method": "POST", "purpose": "Upload up to 100 dialogue sentences.", "qps": "3 QPS", "notes": "appKey is in path, not request body."},
    {"id": "upload_custom_text_data", "path": "/openapi/v1/textdata/upload", "method": "POST", "purpose": "Upload custom text data.", "qps": "not specified", "notes": "Custom field schema must match the Yuyi platform configuration."},
    {"id": "sso_create_user", "path": "/openapi/v3/user/account", "method": "POST", "purpose": "Create a Yuyi user account for SSO.", "qps": "not specified", "notes": "Permissions are managed inside Yuyi."},
    {"id": "training_check_result", "path": "/openapi/v4/training/check/result", "method": "POST", "purpose": "Pull training result data.", "qps": "not specified", "notes": "Recommended every 3 hours."},
    {"id": "customized_data_result", "path": "/openapi/v1/customizedData/result", "method": "POST", "purpose": "Pull custom data type results.", "qps": "not specified", "notes": "Recommended every 3 hours."},
    {"id": "order_result", "path": "/openapi/v1/order/result", "method": "POST", "purpose": "Pull order/refund/label result data.", "qps": "not specified", "notes": "Recommended every 1 hour; limit 0-100."},
]


YUYIDATA_WRITE_TOOLS = {
    "yuyidata_upload_audio_url",
    "yuyidata_upload_text_session",
    "yuyidata_upload_comments",
    "yuyidata_create_sessions_retrieve_task",
    "yuyidata_upload_sentence",
    "yuyidata_upload_batch_sentences",
    "yuyidata_upload_custom_text_data",
    "yuyidata_create_sso_user",
}


for _tool_name in (
    "yuyidata_api_catalog",
    "yuyidata_upload_audio_url",
    "yuyidata_upload_text_session",
    "yuyidata_upload_comments",
    "yuyidata_create_sessions_retrieve_task",
    "yuyidata_get_task_info",
    "yuyidata_download_task_file",
    "yuyidata_check_comments",
    "yuyidata_upload_sentence",
    "yuyidata_upload_batch_sentences",
    "yuyidata_upload_custom_text_data",
    "yuyidata_create_sso_user",
    "yuyidata_build_sso_url",
    "yuyidata_check_training",
    "yuyidata_check_customized_data",
    "yuyidata_check_orders",
):
    register_tool(ToolMeta(
        tool_name=_tool_name,
        platform="yuyidata",
        data_scope="yuyidata.customer_service",
        endpoint_family="openapi",
        warning_group="yuyidata.customer_service",
        requires_shop_id=False,
        source_id="platform-yuyidata",
        write=_tool_name in YUYIDATA_WRITE_TOOLS,
    ))


# ── 工具函数 ──

def _base_url(args: dict | None = None) -> str:
    args = args or {}
    return (args.get("baseUrl") or os.environ.get("YUYIDATA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _app_key(args: dict | None = None) -> str:
    args = args or {}
    key = str(args.get("appKey") or os.environ.get("YUYIDATA_APP_KEY") or "").strip()
    if not key:
        raise ValueError("缺少 YUYIDATA_APP_KEY；请在服务环境变量或 MCP env 中配置")
    return key


def _app_secret(args: dict | None = None) -> str:
    args = args or {}
    return str(args.get("appSecret") or os.environ.get("YUYIDATA_APP_SECRET") or "").strip()


def _url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return f"{_base_url()}{path}"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key.lower() in {"appkey", "appsecret", "sign"}:
                out[key] = "********" if item else ""
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _hmac_sha256_hex(data: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


def _post_json(path: str, body: dict, *, timeout: int | None = None) -> dict:
    url = _url(path)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/plain, */*", "User-Agent": "SkillForge-YuyiData-MCP/1.0"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout or int(os.environ.get("YUYIDATA_TIMEOUT", "30"))) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError:
                data = None
            return {"ok": 200 <= resp.status < 300, "url": url, "status": resp.status, "durationMs": int((time.monotonic() - started) * 1000), "data": data, "textPreview": None if data is not None else text[:1000]}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = None
        return {"ok": False, "url": url, "status": exc.code, "durationMs": int((time.monotonic() - started) * 1000), "data": data, "textPreview": None if data is not None else text[:1000], "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "url": url, "status": None, "durationMs": int((time.monotonic() - started) * 1000), "error": str(exc)}


def _maybe_post(path: str, body: dict, args: dict) -> dict:
    if bool(args.get("dryRun", False)):
        return {"dryRun": True, "url": _url(path), "method": "POST", "body": _redact(body)}
    result = _post_json(path, body)
    result["request"] = {"body": _redact(body)}
    return result


def _limit(args: dict) -> int:
    return max(1, min(int(args.get("limit") or MAX_LIMIT), MAX_LIMIT))


def _offset(args: dict) -> int:
    return max(0, int(args.get("offset") or 0))


def _paged_body(args: dict) -> dict:
    body = {"appKey": _app_key(args), "startDate": str(args["startDate"]), "endDate": str(args["endDate"]), "offset": _offset(args), "limit": _limit(args)}
    if "timeType" in args and args.get("timeType") is not None:
        body["timeType"] = int(args.get("timeType") or 0)
    return body


def _comment_sign(body: dict, secret: str) -> str:
    return _hmac_sha256_hex(f"{body['appKey']}{body['startDate']}{body['endDate']}{body['offset']}{body['limit']}", secret)


def _retrieve_sign(body: dict, secret: str) -> str:
    return _hmac_sha256_hex(f"{body['appKey']}{body['startDate']}{body['endDate']}{body['dialogueOnly']}", secret)


def _download_limits(args: dict) -> tuple[int | None, int]:
    raw_lines = args.get("maxLines")
    if raw_lines is None:
        max_lines: int | None = DEFAULT_DOWNLOAD_MAX_LINES
    else:
        parsed_lines = int(raw_lines)
        max_lines = None if parsed_lines <= 0 else parsed_lines

    raw_bytes = args.get("maxBytes")
    if raw_bytes is None:
        max_bytes = DEFAULT_DOWNLOAD_MAX_BYTES
    else:
        parsed_bytes = int(raw_bytes)
        max_bytes = MAX_DOWNLOAD_BYTES if parsed_bytes <= 0 else parsed_bytes
    max_bytes = max(1024, min(max_bytes, MAX_DOWNLOAD_BYTES))
    return max_lines, max_bytes


# ── 业务 handler ──

def yuyidata_upload_audio_url(args: dict) -> dict:
    audio_info = args["audioInfo"]
    if isinstance(audio_info, dict):
        audio_info = [audio_info]
    if not isinstance(audio_info, list) or not audio_info:
        raise ValueError("audioInfo 必须是对象或非空数组")
    body = {"appKey": _app_key(args), "audioInfo": audio_info}
    return _maybe_post("/openapi/v3/session/upload/audioUrl", body, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_upload_text_session(args: dict) -> dict:
    data = args["data"]
    if not isinstance(data, dict):
        raise ValueError("data 必须是对象")
    body = {"appKey": _app_key(args), "data": data}
    return _maybe_post("/openapi/v3/session/upload/text", body, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_upload_comments(args: dict) -> dict:
    data = args["data"]
    if not isinstance(data, list) or not data:
        raise ValueError("data 必须是非空数组")
    if len(data) > 10:
        raise ValueError("评论上传一次最多支持 10 条")
    body = {"appKey": _app_key(args), "data": data}
    return _maybe_post("/openapi/v1/comment/upload/text", body, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_create_sessions_retrieve_task(args: dict) -> dict:
    body = {"appKey": _app_key(args), "startDate": str(args["startDate"]), "endDate": str(args["endDate"]), "dialogueOnly": int(args.get("dialogueOnly", 0))}
    for key in ("dialogRound", "filter", "fileExpireTime", "callBackData"):
        if args.get(key) is not None:
            body[key] = args[key]
    secret = _app_secret(args)
    if secret and not args.get("sign"):
        body["sign"] = _retrieve_sign(body, secret)
    elif args.get("sign"):
        body["sign"] = args["sign"]
    return _maybe_post("/openapi/v3/sessions/retrieve", body, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_get_task_info(args: dict) -> dict:
    body = {"appKey": _app_key(args), "taskId": str(args["taskId"])}
    return _post_json("/openapi/v3/sessions/getTaskInfo", body)


def yuyidata_download_task_file(args: dict) -> dict:
    file_path = str(args["filePath"])
    parsed = urlparse(file_path)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("filePath 只支持 http/https")
    max_lines, max_bytes = _download_limits(args)
    req = urllib.request.Request(file_path, headers={"User-Agent": "SkillForge-YuyiData-MCP/1.0"})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=int(os.environ.get("YUYIDATA_TIMEOUT", "30"))) as resp:
        raw = resp.read(max_bytes + 1)
        bytes_truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        text = raw.decode("utf-8", errors="replace")
    records: list[Any] = []
    parse_errors: list[dict] = []
    lines = text.splitlines()
    selected_lines = lines if max_lines is None else lines[:max_lines]
    lines_truncated = max_lines is not None and len(lines) > max_lines
    for idx, line in enumerate(selected_lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            parse_errors.append({"line": idx, "error": str(exc), "preview": line[:200]})
    return {
        "ok": True,
        "url": file_path,
        "durationMs": int((time.monotonic() - started) * 1000),
        "bytesRead": len(raw),
        "maxBytes": max_bytes,
        "maxLines": 0 if max_lines is None else max_lines,
        "truncated": bytes_truncated or lines_truncated,
        "bytesTruncated": bytes_truncated,
        "linesTruncated": lines_truncated,
        "recordsReturned": len(records),
        "parseErrors": parse_errors[:5],
        "records": records,
    }


def yuyidata_check_comments(args: dict) -> dict:
    body = _paged_body(args)
    secret = _app_secret(args)
    if secret and not args.get("sign"):
        body["sign"] = _comment_sign(body, secret)
    elif args.get("sign"):
        body["sign"] = args["sign"]
    return _maybe_post("/openapi/v4/comment/check/result", body, args)


def yuyidata_upload_sentence(args: dict) -> dict:
    payload = args["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    path = f"/openapi/v1/session/dsm-text/{_app_key(args)}"
    return _maybe_post(path, payload, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_upload_batch_sentences(args: dict) -> dict:
    data = args["data"]
    if not isinstance(data, list) or not data:
        raise ValueError("data 必须是非空数组")
    if len(data) > 100:
        raise ValueError("批量单句上传一次最多支持 100 个 element")
    path = f"/openapi/v1/session/batch-dsm-text/{_app_key(args)}"
    return _maybe_post(path, {"data": data}, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_upload_custom_text_data(args: dict) -> dict:
    data = args["data"]
    if not isinstance(data, list) or not data:
        raise ValueError("data 必须是非空数组")
    body = {"appKey": _app_key(args), "data": data}
    return _maybe_post("/openapi/v1/textdata/upload", body, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_create_sso_user(args: dict) -> dict:
    body = {"appKey": _app_key(args), "name": str(args["name"]), "openId": str(args["openId"])}
    if args.get("role"):
        body["role"] = str(args["role"])
    return _maybe_post("/openapi/v3/user/account", body, {**args, "dryRun": args.get("dryRun", True)})


def yuyidata_build_sso_url(args: dict) -> dict:
    app_key = _app_key(args)
    user_id = str(args["userId"])
    ts = int(args.get("time") or time.time())
    sign = hashlib.md5(f"{user_id}{ts}{app_key}".encode("utf-8")).hexdigest()  # noqa: S324 - required by vendor protocol
    return {"url": f"https://neosight.yuyidata.com/fast-login?userId={user_id}&time={ts}&sign={sign}", "userId": user_id, "time": ts, "expiresInSeconds": 600}


def yuyidata_check_training(args: dict) -> dict:
    body = _paged_body(args)
    return _maybe_post("/openapi/v4/training/check/result", body, args)


def yuyidata_check_customized_data(args: dict) -> dict:
    body = _paged_body(args)
    return _maybe_post("/openapi/v1/customizedData/result", body, args)


def yuyidata_check_orders(args: dict) -> dict:
    body = _paged_body(args)
    return _maybe_post("/openapi/v1/order/result", body, args)


_HANDLERS = {
    "yuyidata_upload_audio_url": yuyidata_upload_audio_url,
    "yuyidata_upload_text_session": yuyidata_upload_text_session,
    "yuyidata_upload_comments": yuyidata_upload_comments,
    "yuyidata_create_sessions_retrieve_task": yuyidata_create_sessions_retrieve_task,
    "yuyidata_get_task_info": yuyidata_get_task_info,
    "yuyidata_download_task_file": yuyidata_download_task_file,
    "yuyidata_check_comments": yuyidata_check_comments,
    "yuyidata_upload_sentence": yuyidata_upload_sentence,
    "yuyidata_upload_batch_sentences": yuyidata_upload_batch_sentences,
    "yuyidata_upload_custom_text_data": yuyidata_upload_custom_text_data,
    "yuyidata_create_sso_user": yuyidata_create_sso_user,
    "yuyidata_build_sso_url": yuyidata_build_sso_url,
    "yuyidata_check_training": yuyidata_check_training,
    "yuyidata_check_customized_data": yuyidata_check_customized_data,
    "yuyidata_check_orders": yuyidata_check_orders,
}


# ── MCP Server ──

class YuyiDataMcpServer(SyncMcpServer):
    """YuyiData MCP Server — 纯同步后端，由基类 SyncMcpServer 提供 asyncio 超时保护。"""

    server_name = "yuyidata"
    DEFAULT_TIMEOUT = 60

    TOOLS = [
        {"name": "yuyidata_api_catalog", "description": "List the Yuyi OpenAPI endpoints wrapped by this MCP, including paths, purpose and rate limits.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "yuyidata_upload_audio_url", "description": "Upload audio URL sessions to Yuyi. Set dryRun=false to perform the real upload.", "inputSchema": {"type": "object", "properties": {"audioInfo": {"description": "One audioInfo object or an array of audioInfo objects."}, "dryRun": {"type": "boolean", "default": True}}, "required": ["audioInfo"]}},
        {"name": "yuyidata_upload_text_session", "description": "Upload one complete text conversation to Yuyi. Set dryRun=false to perform the real upload.", "inputSchema": {"type": "object", "properties": {"data": {"description": "Conversation payload with date/customerId/agentId/msgData."}, "dryRun": {"type": "boolean", "default": True}}, "required": ["data"]}},
        {"name": "yuyidata_upload_comments", "description": "Upload product/SKU comments to Yuyi. Set dryRun=false to perform the real upload.", "inputSchema": {"type": "object", "properties": {"data": {"type": "array", "description": "Comment items; max 10 per request."}, "dryRun": {"type": "boolean", "default": True}}, "required": ["data"]}},
        {"name": "yuyidata_create_sessions_retrieve_task", "description": "Create a dialogue export task. Defaults to dry-run; set dryRun=false to perform the real call. Adds HMAC sign automatically when YUYIDATA_APP_SECRET is configured.", "inputSchema": {"type": "object", "properties": {"startDate": {"type": "string"}, "endDate": {"type": "string"}, "dialogueOnly": {"type": "integer", "default": 0}, "dialogRound": {"type": "integer"}, "filter": {"type": "string"}, "fileExpireTime": {"type": "integer", "default": 3600}, "callBackData": {"type": "object"}, "dryRun": {"type": "boolean", "default": True}}, "required": ["startDate", "endDate"]}},
        {"name": "yuyidata_get_task_info", "description": "Poll a Yuyi dialogue export task by taskId.", "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}}, "required": ["taskId"]}},
        {"name": "yuyidata_download_task_file", "description": "Download and parse a Yuyi task filePath. NDJSON lines are parsed into records. Set maxLines=0 to parse all downloaded lines.", "inputSchema": {"type": "object", "properties": {"filePath": {"type": "string"}, "maxLines": {"type": "integer", "default": 500, "minimum": 0, "description": "0 means no line limit"}, "maxBytes": {"type": "integer", "default": 2097152, "maximum": MAX_DOWNLOAD_BYTES, "description": "0 means maximum allowed download budget"}}, "required": ["filePath"]}},
        {"name": "yuyidata_check_comments", "description": "Pull processed comment QA results. Adds HMAC sign automatically when secret is configured.", "inputSchema": {"type": "object", "properties": {"startDate": {"type": "string"}, "endDate": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 100}, "timeType": {"type": "integer", "default": 0}, "dryRun": {"type": "boolean", "default": False}}, "required": ["startDate", "endDate"]}},
        {"name": "yuyidata_upload_sentence", "description": "Upload one single-sentence dialogue event. Set dryRun=false to perform the real upload.", "inputSchema": {"type": "object", "properties": {"payload": {"type": "object"}, "dryRun": {"type": "boolean", "default": True}}, "required": ["payload"]}},
        {"name": "yuyidata_upload_batch_sentences", "description": "Upload up to 100 single-sentence dialogue events. Set dryRun=false to perform the real upload.", "inputSchema": {"type": "object", "properties": {"data": {"type": "array"}, "dryRun": {"type": "boolean", "default": True}}, "required": ["data"]}},
        {"name": "yuyidata_upload_custom_text_data", "description": "Upload custom text data. Set dryRun=false to perform the real upload.", "inputSchema": {"type": "object", "properties": {"data": {"type": "array"}, "dryRun": {"type": "boolean", "default": True}}, "required": ["data"]}},
        {"name": "yuyidata_create_sso_user", "description": "Create a Yuyi SSO user account. Set dryRun=false to perform the real call.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "openId": {"type": "string"}, "role": {"type": "string"}, "dryRun": {"type": "boolean", "default": True}}, "required": ["name", "openId"]}},
        {"name": "yuyidata_build_sso_url", "description": "Build a signed neosight.yuyidata.com fast-login URL from userId.", "inputSchema": {"type": "object", "properties": {"userId": {"type": "string"}, "time": {"type": "integer", "description": "Unix seconds; defaults to now."}}, "required": ["userId"]}},
        {"name": "yuyidata_check_training", "description": "Pull training result data.", "inputSchema": {"type": "object", "properties": {"startDate": {"type": "string"}, "endDate": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 100}, "dryRun": {"type": "boolean", "default": False}}, "required": ["startDate", "endDate"]}},
        {"name": "yuyidata_check_customized_data", "description": "Pull custom data type results.", "inputSchema": {"type": "object", "properties": {"startDate": {"type": "string"}, "endDate": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 100}, "dryRun": {"type": "boolean", "default": False}}, "required": ["startDate", "endDate"]}},
        {"name": "yuyidata_check_orders", "description": "Pull order result data.", "inputSchema": {"type": "object", "properties": {"startDate": {"type": "string"}, "endDate": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 100}, "timeType": {"type": "integer", "default": 0}, "dryRun": {"type": "boolean", "default": False}}, "required": ["startDate", "endDate"]}},
    ]

    def handle_tool_call(self, name: str, arguments: dict) -> str:
        if name == "yuyidata_api_catalog":
            return _json_dumps({
                "baseUrl": _base_url(arguments or {}),
                "credential": {
                    "appKeyConfigured": bool((arguments or {}).get("appKey") or os.environ.get("YUYIDATA_APP_KEY")),
                    "appSecretConfigured": bool((arguments or {}).get("appSecret") or os.environ.get("YUYIDATA_APP_SECRET")),
                },
                "endpoints": API_CATALOG,
            })
        handler = _HANDLERS.get(name)
        if not handler:
            return f"未知工具: {name}"
        return _json_dumps(handler(arguments or {}))


# 向后兼容：模块级 handle_tool_call / TOOLS（供测试直接调用）
_server = YuyiDataMcpServer()
handle_tool_call = _server.handle_tool_call
TOOLS = _server.TOOLS

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(_server.main())
    except KeyboardInterrupt:
        pass
