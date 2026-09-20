#!/usr/bin/env python3
"""
Browser MCP Server (stdio 模式, 继承 SyncMcpServer 基类 → 自动获得超时保护).

让 AI 编程助手直接调用浏览器能力，无需 Bash+curl。
协议: MCP (JSON-RPC over stdio)
内部通过 HTTP 调 /api/browser/collect-local 端点。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
import urllib.request

# 添加仓库根目录和 scripts/ 到 sys.path，便于导入 mcp_base 与业务解析 helper
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_base import SyncMcpServer

from app.qianchuan.video_content_analysis import (
    collect_qianchuan_video_content_analysis,
    qianchuan_video_content_analysis_input_schema,
)

BROWSER_API = "http://127.0.0.1:8000/api/browser"


# ── HTTP 调用 ──

def _call_browser(task_type: str, params: dict | None = None) -> dict:
    body = json.dumps({"platform": "generic", "task_type": task_type, "params": params or {}}).encode()
    req = urllib.request.Request(
        f"{BROWSER_API}/collect-local", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _get_status() -> dict:
    try:
        with urllib.request.urlopen(f"{BROWSER_API}/status-local", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"status": "stopped", "error": str(e)}


def _get_platform_apis(domain: str = None) -> dict:
    url = f"{BROWSER_API}/platform-apis"
    if domain:
        url += f"?domain={domain}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"total": 0, "platforms": [], "error": str(e)}


def _get_platform_api_detail(item_id: int) -> dict:
    try:
        with urllib.request.urlopen(f"{BROWSER_API}/platform-apis/{item_id}", timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _save_platform_api(url: str, apis_json: dict, page_title: str = "") -> dict:
    body = json.dumps({
        "url": url, "page_title": page_title, "apis_json": apis_json,
        "discovery_method": "browser_capture_apis",
    }).encode()
    req = urllib.request.Request(
        f"{BROWSER_API}/platform-apis", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _fetch_json(
    url: str | None = None, method: str = "GET", headers: dict | None = None,
    body=None, page_url: str | None = None, registry_id: int | None = None, api_index: int = 0,
) -> dict:
    req_body = json.dumps({
        "url": url, "registry_id": registry_id, "api_index": api_index,
        "method": method, "headers": headers or {}, "body": body, "page_url": page_url,
    }).encode()
    req = urllib.request.Request(
        f"{BROWSER_API}/fetch-json-local", data=req_body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"success": False, "error": str(e), "data": None}


_NOISY_API_MARKERS = (
    "oneauth/", "/custom/menu/", "qn-autolog", "umdcv", "everyhelp.",
    "helpcenter.", "/api/monitor", "/ucc/charge/", "/ipoll/activity/getCurrentTime",
    "/portal/common/alias/", "/oneauth/alias/",
)

_BUSINESS_API_MARKERS = (
    "/cc/", "/mc/", "/flow/", "/portal/gmv/", "/s_portal/", "/alimama",
    "rank", "item", "crowd", "source", "trade", "pay", "price", "keyword",
    "diagnose", "market", "qzt",
)


def _truncate_text(value: Any, limit: int = 420) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _parse_json_preview(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _shape_of(value: Any, *, depth: int = 0, max_keys: int = 6, max_items: int = 1) -> Any:
    if depth >= 2:
        if isinstance(value, dict):
            return {"type": "object", "keys": list(value.keys())[:max_keys]}
        if isinstance(value, list):
            return {"type": "array", "length": len(value)}
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): _shape_of(v, depth=depth + 1, max_keys=max_keys, max_items=max_items)
                for k, v in list(value.items())[:max_keys]}
    if isinstance(value, list):
        return {"type": "array", "length": len(value),
                "items": [_shape_of(v, depth=depth + 1, max_keys=max_keys, max_items=max_items)
                          for v in value[:max_items]]}
    return type(value).__name__


def _api_score(api: dict) -> int:
    url = str(api.get("url") or "").lower()
    score = 0
    if api.get("status") in (200, "200", None):
        score += 2
    if str(api.get("method") or "").upper() == "GET":
        score += 1
    if any(marker in url for marker in _BUSINESS_API_MARKERS):
        score += 6
    if any(marker in url for marker in _NOISY_API_MARKERS):
        score -= 12
    if "json" in url:
        score += 1
    return score


def _compact_api(api: dict, index: int) -> dict:
    preview = _parse_json_preview(api.get("body_preview"))
    item = {"index": index, "method": api.get("method"), "status": api.get("status"), "url": api.get("url")}
    if api.get("content_type"):
        item["content_type"] = api.get("content_type")
    if api.get("source"):
        item["source"] = api.get("source")
    if isinstance(preview, (dict, list)):
        item["response_shape"] = _shape_of(preview)
        item["preview"] = _truncate_text(preview, 160)
    elif preview is not None:
        item["preview"] = _truncate_text(preview, 160)
    return item


def _compact_platform_api_detail(result: dict, *, max_apis: int = 10) -> dict:
    apis_json = result.get("apis_json") if isinstance(result.get("apis_json"), dict) else {}
    data_apis = apis_json.get("data_apis") if isinstance(apis_json.get("data_apis"), list) else []
    scored = [(index, api, _api_score(api)) for index, api in enumerate(data_apis)]
    candidates = [(index, api) for index, api, score in scored if score >= 0] or \
                 [(index, api) for index, api, _score in scored]
    ranked = sorted(candidates, key=lambda pair: (_api_score(pair[1]), -pair[0]), reverse=True)
    selected = sorted(ranked[:max_apis], key=lambda pair: pair[0])
    return {
        "id": result.get("id"), "domain": result.get("domain"),
        "page_path": result.get("page_path"), "page_title": result.get("page_title"),
        "page_url": result.get("page_url") or apis_json.get("url"),
        "api_count": result.get("api_count") or len(data_apis),
        "verification_status": result.get("verification_status"),
        "last_verified_at": result.get("last_verified_at"),
        "data_apis": [_compact_api(api, index) for index, api in selected],
        "omitted_api_count": max(0, len(data_apis) - len(selected)),
        "usage_hint": (
            "这是压缩摘要。写 Skill 时优先使用 data_apis[*].url；"
            "需要真实返回样本时用 browser_fetch_json(registry_id=id, api_index=index) 验证单个 API。"
        ),
    }


# ── MCP Server ──

class BrowserMcpServer(SyncMcpServer):
    """Browser MCP Server — 纯同步后端，由基类 SyncMcpServer 提供 asyncio 超时保护。"""

    server_name = "browser"
    DEFAULT_TIMEOUT = 60

    TOOLS = [
        {
            "name": "browser_capture_apis",
            "description": "导航到指定 URL 并抓包该页面发出的所有 XHR/fetch API 调用。返回每个 API 的 URL、请求方法、响应 JSON 结构和数据预览。用于发现页面的内部数据 API，然后用 browser_extract fetch 取数据。首次采集新页面时优先使用此工具。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓包的页面 URL"},
                    "wait_ms": {"type": "integer", "description": "等待页面 API 调用完成的毫秒数，默认 12000", "default": 12000},
                },
                "required": ["url"],
            },
        },
        {
            "name": "browser_extract",
            "description": "在浏览器页面上下文中执行 JavaScript 表达式并返回结果。页面已登录（cookies 已注入），fetch() 自动带认证。典型用法: 传入 fetch('API_URL').then(r=>r.json()) 直接调页面内部 API。也可传 CSS selector 提取 DOM 元素。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "js": {"type": "string", "description": "要执行的 JavaScript 表达式（支持 async/await）"},
                    "selector": {"type": "string", "description": "CSS 选择器（与 js 二选一）"},
                    "url": {"type": "string", "description": "可选，先导航到此 URL 再提取"},
                },
            },
        },
        {
            "name": "browser_explore",
            "description": "导航到 URL，返回页面结构化摘要：表格、数据卡片、导航链接、表单。用于快速了解一个新页面有什么数据，但返回数据较粗糙。精确取数请用 browser_capture_apis 发现 API 后用 browser_extract fetch。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要探索的页面 URL"},
                },
                "required": ["url"],
            },
        },
        {
            "name": "browser_screenshot",
            "description": "截取当前页面截图（PNG base64），用于调试页面状态。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "可选，先导航到此 URL 再截图"},
                },
            },
        },
        {
            "name": "browser_status",
            "description": "检查浏览器是否在运行。如果未运行，提示用户去 数据源 > 浏览器连接 启动。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "browser_fetch_json",
            "description": "在已登录的浏览器上下文里直接 fetch 指定 API URL，或用 registry_id+api_index replay 注册表 API。返回 JSON 结果和 proof（响应 hash、data_keys、row_count、验证状态）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要请求的 API URL；与 registry_id 二选一"},
                    "registry_id": {"type": "integer", "description": "平台注册表记录 ID；与 api_index 配合 replay 已抓包 API"},
                    "api_index": {"type": "integer", "description": "data_apis 下标，默认 0", "default": 0},
                    "method": {"type": "string", "description": "HTTP 方法，默认 GET", "default": "GET"},
                    "headers": {"type": "object", "description": "可选请求头", "default": {}},
                    "body": {"description": "可选请求体；对象会自动 JSON.stringify"},
                    "page_url": {"type": "string", "description": "可选，先导航到该页面再 fetch"},
                },
            },
        },
        {
            "name": "browser_qianchuan_video_content_analysis",
            "description": "在已登录千川浏览器上下文中读取单个视频的内容分析数据：互动时序分析（整体点击次数/流失/点赞/评论/转发/关注）、脚本文本、素材标签和 benchmark 标签缺口。用于素材分析-视频素材-推商品-点击-内容分析场景。",
            "inputSchema": qianchuan_video_content_analysis_input_schema(),
        },
        {
            "name": "browser_replay_platform_api",
            "description": "按平台注册表 id 和 api_index replay 已发现 API，更新 last_verified_at/verification_status，并返回 replay proof。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "registry_id": {"type": "integer", "description": "平台注册表记录 ID"},
                    "api_index": {"type": "integer", "description": "data_apis 下标，默认 0", "default": 0},
                    "page_url": {"type": "string", "description": "可选，覆盖注册表中的页面 URL"},
                },
                "required": ["registry_id"],
            },
        },
        {
            "name": "browser_list_platforms",
            "description": "列出平台 API 注册表中已发现的所有平台和页面。创建新 Skill 时，先调此工具查看目标平台是否已有 API 缓存。如果已有，用 browser_get_platform_apis 读取详情，不需要重新抓包。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "可选，按域名筛选（如 sycm.taobao.com）"},
                },
            },
        },
        {
            "name": "browser_get_platform_apis",
            "description": "读取平台 API 注册表中某个页面的 API 发现结果。默认返回压缩摘要。只有用户明确要求排查某个完整抓包时才传 full=true。创建新 Skill 时用此工具获取已知 API，直接写脚本，不需要重新抓包。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "平台 API 记录 ID（从 browser_list_platforms 获取）"},
                    "full": {"type": "boolean", "description": "是否返回完整抓包 JSON；默认 false。创建 Skill 时不要开启。", "default": False},
                },
                "required": ["id"],
            },
        },
        {
            "name": "browser_get_cached_apis",
            "description": "按 id 或 domain/page_path 从平台 API 注册表读取已缓存的 API 发现结果。优先复用历史抓包结果，而不是重新 capture_apis。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "可选，平台注册表记录 ID"},
                    "domain": {"type": "string", "description": "可选，按域名筛选，如 sycm.taobao.com"},
                    "page_path": {"type": "string", "description": "可选，按页面路径精确匹配，如 /cc/item_rank"},
                    "full": {"type": "boolean", "description": "是否返回完整抓包 JSON；默认 false。创建 Skill 时不要开启。", "default": False},
                },
            },
        },
    ]

    def effective_timeout(self, tool_name: str, tool_args: dict) -> int:
        if tool_name == "browser_capture_apis":
            wait_ms = int(tool_args.get("wait_ms", 12000))
            return max(self.DEFAULT_TIMEOUT, int(wait_ms / 1000) + 15)
        return self.DEFAULT_TIMEOUT

    def handle_tool_call(self, name: str, arguments: dict) -> str:
        if name == "browser_status":
            result = _get_status()
            if result.get("status") == "running":
                return f"浏览器在线: Chrome {result.get('browser', '?')}, {result.get('pages', '?')} 个标签页"
            return f"浏览器未运行: {result.get('error', '未知错误')}。请让用户去 数据源 > 浏览器连接 启动。"

        if name == "browser_capture_apis":
            resp = _call_browser("capture_apis", {"url": arguments["url"], "wait_ms": arguments.get("wait_ms", 12000)})
            if resp.get("status") == "success":
                r = resp["result"]
                save_result = _save_platform_api(url=arguments["url"], apis_json=r, page_title=r.get("url", ""))
                saved_msg = ""
                if save_result.get("id"):
                    saved_msg = f"\n\n[已自动保存到平台 API 注册表: id={save_result['id']} domain={save_result.get('domain')} api_count={save_result.get('api_count')}]"
                return json.dumps(r, ensure_ascii=False, indent=2) + saved_msg
            return f"抓包失败: {resp.get('error', '未知错误')}"

        if name == "browser_fetch_json":
            if not arguments.get("url") and arguments.get("registry_id") is None:
                return "错误: 缺少 url 或 registry_id 参数"
            result = _fetch_json(
                url=arguments.get("url"), method=arguments.get("method", "GET"),
                headers=arguments.get("headers") or {}, body=arguments.get("body"),
                page_url=arguments.get("page_url"), registry_id=arguments.get("registry_id"),
                api_index=arguments.get("api_index", 0),
            )
            if arguments.get("registry_id") is not None:
                return json.dumps(result, ensure_ascii=False, indent=2)
            if result.get("success"):
                return json.dumps(result, ensure_ascii=False, indent=2)
            return f"fetch 失败: {result.get('error', '未知错误')}"

        if name == "browser_qianchuan_video_content_analysis":
            result = collect_qianchuan_video_content_analysis(
                arguments,
                lambda spec: _fetch_json(
                    url=spec.get("url"),
                    method=spec.get("method", "GET"),
                    headers=spec.get("headers") or {},
                    body=spec.get("body"),
                    page_url=spec.get("page_url"),
                ),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        if name == "browser_replay_platform_api":
            if arguments.get("registry_id") is None:
                return "错误: 缺少 registry_id 参数"
            result = _fetch_json(registry_id=int(arguments["registry_id"]), api_index=arguments.get("api_index", 0), page_url=arguments.get("page_url"))
            return json.dumps(result, ensure_ascii=False, indent=2)

        if name == "browser_list_platforms":
            result = _get_platform_apis(arguments.get("domain"))
            if not result.get("platforms"):
                return "平台 API 注册表为空。需要先用 browser_capture_apis 抓包一个平台页面。"
            lines = [f"已发现 {result['total']} 个平台页面:\n"]
            for p in result["platforms"]:
                verify = p.get("verification_status") or "unknown"
                lines.append(f"  [{p['id']}] {p['domain']}{p['page_path']} — {p.get('page_title') or ''} ({p['api_count']} 个 API, verification={verify}, 更新于 {p.get('updated_at', '?')[:10]})")
            return "\n".join(lines)

        if name == "browser_get_platform_apis":
            result = _get_platform_api_detail(arguments["id"])
            if result.get("error"):
                return f"读取失败: {result['error']}"
            if arguments.get("full") is True:
                return json.dumps(result, ensure_ascii=False, indent=2)
            return json.dumps(_compact_platform_api_detail(result), ensure_ascii=False, indent=2)

        if name == "browser_get_cached_apis":
            full = arguments.get("full") is True
            item_id = arguments.get("id")
            if item_id:
                result = _get_platform_api_detail(int(item_id))
                if result.get("error"):
                    return f"读取失败: {result['error']}"
                if full:
                    return json.dumps(result, ensure_ascii=False, indent=2)
                return json.dumps(_compact_platform_api_detail(result), ensure_ascii=False, indent=2)
            result = _get_platform_apis(arguments.get("domain"))
            platforms = result.get("platforms") or []
            if arguments.get("page_path"):
                platforms = [p for p in platforms if p.get("page_path") == arguments["page_path"]]
            if not platforms:
                return "未找到匹配的缓存 API 记录。"
            if len(platforms) == 1:
                result = _get_platform_api_detail(platforms[0]["id"])
                if result.get("error"):
                    return f"读取失败: {result['error']}"
                if full:
                    return json.dumps(result, ensure_ascii=False, indent=2)
                return json.dumps(_compact_platform_api_detail(result), ensure_ascii=False, indent=2)
            lines = [f"匹配到 {len(platforms)} 条缓存 API 记录:"]
            for p in platforms[:20]:
                lines.append(f"  [{p['id']}] {p['domain']}{p['page_path']} — {p.get('page_title') or ''} ({p.get('api_count', 0)} 个 API)")
            return "\n".join(lines)

        if name == "browser_extract":
            params = {}
            if arguments.get("url"):
                params["url"] = arguments["url"]
            if arguments.get("js"):
                params["js"] = arguments["js"]
            elif arguments.get("selector"):
                params["selector"] = arguments["selector"]
            else:
                return "错误: 必须提供 js 或 selector 参数"
            resp = _call_browser("extract", params)
            if resp.get("status") == "success":
                return json.dumps(resp["result"], ensure_ascii=False, indent=2)
            return f"提取失败: {resp.get('error', '未知错误')}"

        if name == "browser_explore":
            resp = _call_browser("explore", {"url": arguments["url"]})
            if resp.get("status") == "success":
                return json.dumps(resp["result"], ensure_ascii=False, indent=2)
            return f"探索失败: {resp.get('error', '未知错误')}"

        if name == "browser_screenshot":
            params = {}
            if arguments.get("url"):
                params["url"] = arguments["url"]
            resp = _call_browser("screenshot", params)
            if resp.get("status") == "success":
                return "截图已获取（PNG base64 已截断预览）"
            return f"截图失败: {resp.get('error', '未知错误')}"

        return f"未知工具: {name}"


# 向后兼容：模块级 handle_tool_call / TOOLS（供测试直接调用）
_server = BrowserMcpServer()
handle_tool_call = _server.handle_tool_call
TOOLS = _server.TOOLS

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(_server.main())
    except KeyboardInterrupt:
        pass
