#!/usr/bin/env python3
"""Tmall/SYCM/Alimama MCP Server (stdio).

这个 server 是业务层 MCP：不直接维护浏览器、Cookie 或 CDP 连接，而是复用
`browser_mcp_server.py` 已有的本地浏览器 HTTP 封装：

- `_fetch_json` → `/api/browser/fetch-json-local`

这样 MCP 只负责把已验证接口包装成稳定业务工具，登录态、SSRF 防护、抓包和
响应 proof 继续走现有 Browser/Platform API 底座。
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import random
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
import urllib.request

import browser_mcp_server as browser_mcp
import collection_client
# This optional deployment adapter is intentionally not bundled with the public
# edition. Its absence must not prevent unrelated MCP tools/catalogs from loading.
try:
    from tmall_item_reviews_probe import CdpClient, _risk_level, collect_reviews, get_page_ws_url
except ModuleNotFoundError as exc:
    if exc.name != "tmall_item_reviews_probe":
        raise

    def _review_adapter_unavailable(*args, **kwargs):
        raise RuntimeError("optional_review_adapter_unavailable: configure an authorized review adapter for this deployment")

    CdpClient = _review_adapter_unavailable
    _risk_level = _review_adapter_unavailable
    collect_reviews = _review_adapter_unavailable
    get_page_ws_url = _review_adapter_unavailable

# 添加 scripts/ 到 sys.path，便于导入 mcp_base
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_base import AsyncMcpServer, SyncMcpServer
from skillforge_mcp_runtime import ToolMeta, register_tool


SYCM_RANK_API = "https://sycm.taobao.com/cc/item/live/view/top.json"
SYCM_RANK_PAGE = "https://sycm.taobao.com/cc/item_rank?dateRange={date_range_q}&dateType={date_type}"
SYCM_DETAIL_PAGE = (
    "https://sycm.taobao.com/cc/item_archives?"
    "activeKey=flow&itemId={item_id}&dateType={date_type}&dateRange={date_range_q}"
)
SYCM_MARKET_RANK_PAGE = (
    "https://sycm.taobao.com/mc/free/market_rank?"
    "activeKey=item&dateRange={date_range_q}&dateType={date_type}"
    "&parentCateId={parent_cate_id}&cateId={cate_id}&cateFlag={cate_flag}"
)
SYCM_MARKET_CATE_API = "https://sycm.taobao.com/mc/common/free/getCateInfo.json"
SYCM_MARKET_PRICE_SEG_API = "https://sycm.taobao.com/mc/mq/mkt/priceSeg/list.json"
SYCM_MARKET_ITEM_RANK_API = "https://sycm.taobao.com/mc/mq/mkt/item/offline/rank.json"
SYCM_MARKET_ITEM_LIVE_RANK_API = "https://sycm.taobao.com/mc/mq/mkt/item/live/rank.json"
SYCM_ACTIVITY_LIST_API = "https://sycm.taobao.com/datawar/activityConfig/getActivityListBy.json"
ALIMAMA_CHECK_ACCESS_URL = "https://one.alimama.com/member/checkAccess.json?bizCode=universalBP"
CDP_JSON_URL = "http://127.0.0.1:9222/json/list"
SELLER_PRICE_HOME_PAGE = "https://myseller.taobao.com/home.htm/starb/price-home"
SELLER_PRICE_RISK_PAGE = "https://myseller.taobao.com/home.htm/PriceManagement/?source=qianniulist&TabCode=Risk"
SELLER_MARKETING_TOOLS_PAGE = "https://myseller.taobao.com/home.htm/starb/nebula/mkt-tools/mkt-tools-home/home"
SELLER_PRICE_CONTROL_API = "https://mystarseller.taobao.com/star/pricecontrol/queryItemList.do"
SELLER_STAR_ITEMS_API = "https://mystarseller.taobao.com/staritem/items.do"
SELLER_CONTROL_PLANS_API = "https://mystarseller.taobao.com/staritem/queryControlPlans.do"
SELLER_CORE_DATA_API = "https://mystarseller.taobao.com/star/statistic/coreData.do"
SELLER_STRATEGY_SUGGESTIONS_API = "https://mystarseller.taobao.com/star/strategy/queryStrategySuggestions.do"
SELLER_MIX_ACTIVITY_API = "https://shell.mkt.taobao.com/portal/getMixActivityList"
SELLER_PRICE_ANNOUNCEMENT_API = "https://shell.mkt.taobao.com/unified/price_management/getAnnouncement"
SELLER_PRICE_PAGE_MODULE_API = "https://shell.mkt.taobao.com/unified/price_management/getPageModule?pageCode=index"
SYCM_COOKIE_SKIP = {"QNWORKBENCH_SESSION", "EGG_SESS1"}
MCP_DIRECT_COOKIE_BLOCKED = "MCP_DIRECT_COOKIE_BLOCKED"
PRODUCTION_ENVS = {"prod", "production", "staging", "stage"}
LOCAL_ENVS = {"local", "dev", "development", "test", "testing"}
_COOKIE_CACHE: tuple[float, str, str] | None = None
_COOKIE_CACHE_LOCK = threading.Lock()
_ALIMAMA_ACCESS_CACHE: dict[str, tuple[float, dict]] = {}
_FETCH_THROTTLE_LOCK = threading.Lock()
_FETCH_THROTTLE_LAST: dict[str, float] = {}
_ALIMAMA_ACCOUNT_SIGNATURE_CACHE: dict[tuple[str, str, str, str], dict] = {}
ALIMAMA_SEARCH_ADZONE_PKGS = ["114790550288", "114786650498"]
ALIMAMA_DISPLAY_ADZONE_PKGS = [
    "111287850200", "111287850195", "111287850198", "115025700386",
    "666666666666", "115031250425", "9999999999999", "111888100453",
    "111589650086", "111287850196", "111287850037", "111805200132",
    "111287850197", "111953000076", "111882000121",
]
MARKET_CATEGORY_PRESETS = {
    "contraceptive_condom": {
        "label": "计生品类/避孕套",
        "parentCateId": "50024153",
        "cateId": "50024154",
        "cateFlag": 2,
    },
    "计生品类/避孕套": {
        "label": "计生品类/避孕套",
        "parentCateId": "50024153",
        "cateId": "50024154",
        "cateFlag": 2,
    },
    "避孕套": {
        "label": "计生品类/避孕套",
        "parentCateId": "50024153",
        "cateId": "50024154",
        "cateFlag": 2,
    },
}

_CURRENT_TOOL_NAME: contextvars.ContextVar[str] = contextvars.ContextVar("tmall_tool_name", default="")
_CURRENT_TOOL_ARGS: contextvars.ContextVar[dict] = contextvars.ContextVar("tmall_tool_args", default={})
_CURRENT_PROOFS: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("tmall_proofs", default=None)


def _env_name() -> str:
    return str(
        os.environ.get("SKILLFORGE_ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("ENV")
        or os.environ.get("RUNTIME_ENV")
        or "local"
    ).lower()


def _is_production_env() -> bool:
    return _env_name() in PRODUCTION_ENVS


def _direct_browser_access_allowed() -> bool:
    if _is_production_env():
        return False
    return _env_name() in LOCAL_ENVS


def _direct_cookie_blocked_result(operation: str, url: str | None = None) -> dict:
    return {
        "success": False,
        "error": MCP_DIRECT_COOKIE_BLOCKED,
        "message": f"{operation} blocked; use /api/collection/fetch",
        "data": None,
        "proof": {
            "warning": MCP_DIRECT_COOKIE_BLOCKED,
            "transport": "blocked_direct_browser_access",
            "api_url": url,
        },
    }


def _direct_cookie_blocked_payload(operation: str) -> dict:
    return {
        "ok": False,
        "error": MCP_DIRECT_COOKIE_BLOCKED,
        "message": f"{operation} blocked; use /api/collection/fetch",
        "api_coverage": "blocked_direct_browser_access",
    }


def _register_tmall_tool_metas() -> dict[str, ToolMeta]:
    specs = {
        "tmall_sycm_item_rank_summary": ("sycm", "sycm.item_rank", "read_metrics", "sycm.report_read"),
        "tmall_sycm_item_rank_top": ("sycm", "sycm.item_rank", "read_metrics", "sycm.report_read"),
        "tmall_sycm_market_rank": ("sycm", "sycm.market_rank", "read_metrics", "sycm.market_read"),
        "tmall_item_flow_required_metrics": ("sycm", "sycm.item_flow", "read_metrics", "sycm.report_read"),
        "tmall_item_flow_required_metrics_batch": ("sycm", "sycm.item_flow", "read_metrics", "sycm.report_read"),
        "tmall_sycm_item_360_metrics": ("sycm", "sycm.item_360", "read_metrics", "sycm.report_read"),
        "tmall_sycm_item_detail": ("sycm", "sycm.item_detail", "read_metrics", "sycm.report_read"),
        "tmall_sycm_item_flow_sources": ("sycm", "sycm.flow_sources", "read_metrics", "sycm.report_read"),
        "tmall_sycm_activity_price": ("sycm", "sycm.activity_price", "read_metrics", "sycm.report_read"),
        "tmall_item_activity_price_check": ("sycm", "sycm.activity_price", "read_metrics", "sycm.report_read"),
        "tmall_seller_price_competitiveness_check": ("tmall", "tmall.seller_price", "read_pages", "tmall.seller_read"),
        "tmall_seller_marketing_activity_list": ("tmall", "tmall.seller_activity", "read_pages", "tmall.seller_read"),
        "tmall_seller_price_risk_check": ("tmall", "tmall.seller_price_risk", "read_pages", "tmall.seller_read"),
        "tmall_alimama_item_promotion_compare": ("alimama", "alimama.campaign_report", "read_metrics", "alimama.report_read"),
        "tmall_alimama_item_promotion": ("alimama", "alimama.campaign_report", "read_metrics", "alimama.report_read"),
        "tmall_item_promotion_required_metrics": ("alimama", "alimama.campaign_report", "read_metrics", "alimama.report_read"),
        "tmall_item_review_qa_check": ("tmall", "tmall.review_signals", "read_pages", "tmall.review_read"),
        "tmall_item_reviews": ("tmall", "tmall.review_signals", "read_pages", "tmall.review_read"),
        "tmall_item_reviews_api": ("tmall", "tmall.review_signals", "read_pages", "tmall.review_read"),
        "tmall_link_decline_required_context": ("composite", "tmall.link_decline_context", "composite_read", "composite.tmall_link_decline"),
        "tmall_store_weekly_snapshot": ("composite", "tmall.store_weekly_snapshot", "composite_read", "composite.tmall_store_weekly_snapshot"),
    }
    metas: dict[str, ToolMeta] = {}
    for tool_name, (platform, data_scope, endpoint_family, warning_group) in specs.items():
        metas[tool_name] = register_tool(ToolMeta(
            tool_name=tool_name,
            platform=platform,
            data_scope=data_scope,
            endpoint_family=endpoint_family,
            warning_group=warning_group,
            source_id=f"platform-{platform}" if platform != "composite" else "platform-composite",
        ))
    return metas


TMALL_TOOL_META = _register_tmall_tool_metas()


def _with_tool_meta(tools: list[dict]) -> list[dict]:
    result = []
    for tool in tools:
        item = dict(tool)
        meta = TMALL_TOOL_META.get(str(item.get("name") or ""))
        if meta:
            item["toolMeta"] = meta.to_dict()
        result.append(item)
    return result


def _sync_tmall_tool_meta_catalog(tools: list[dict]) -> None:
    for tool in tools:
        tool_name = str(tool.get("name") or "")
        meta = TMALL_TOOL_META.get(tool_name)
        if not meta:
            continue
        input_schema = tool.get("inputSchema")
        updated = register_tool(ToolMeta(
            tool_name=meta.tool_name,
            platform=meta.platform,
            data_scope=meta.data_scope,
            endpoint_family=meta.endpoint_family,
            warning_group=meta.warning_group,
            requires_shop_id=meta.requires_shop_id,
            write=meta.write,
            source_id=meta.source_id,
            description=str(tool.get("description") or meta.description or ""),
            input_schema=input_schema if isinstance(input_schema, dict) else meta.input_schema,
        ))
        TMALL_TOOL_META[tool_name] = updated


def _with_catalog_schema(tools: list[dict]) -> list[dict]:
    result = []
    for tool in tools:
        item = dict(tool)
        catalog_item = TMALL_TOOL_CATALOG.get(str(item.get("name") or ""))
        if isinstance(catalog_item, dict):
            if catalog_item.get("description"):
                item["description"] = catalog_item["description"]
            input_schema = catalog_item.get("inputSchema")
            if isinstance(input_schema, dict):
                item["inputSchema"] = input_schema
        result.append(item)
    return result


TOOLS = [
    {
        "name": "tmall_sycm_item_rank_summary",
        "description": "获取生意参谋商品排行总商品数、更新时间和首屏采集状态。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dateRange": {"type": "string", "description": "如 2026-04-22|2026-04-22，默认今天"},
                "dateType": {"type": "string", "default": "today"},
                "orderBy": {"type": "string", "default": "payAmt"},
                "granularity": {"type": "string", "description": "当前仅支持 live_snapshot/day，不支持 minute"},
            },
        },
    },
    {
        "name": "tmall_sycm_item_rank_top",
        "description": "获取生意参谋商品排行 TopN，包含商品名称、ID、支付金额、访客、转化率和环比。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 500},
                "dateRange": {"type": "string", "description": "如 2026-04-22|2026-04-22，默认今天"},
                "dateType": {"type": "string", "default": "today"},
                "orderBy": {"type": "string", "default": "payAmt"},
                "order": {"type": "string", "default": "desc"},
                "granularity": {"type": "string", "description": "当前仅支持 live_snapshot/day，不支持 minute"},
            },
        },
    },
    {
        "name": "tmall_sycm_item_detail",
        "description": "获取商品360基础信息、属性诊断、人群包、新品、SKU 和价格风险提醒。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string", "description": "商品ID；也可直接传 itemArchivesUrl 自动解析"},
                "itemArchivesUrl": {"type": "string", "description": "商品360流量来源页URL，自动读取 itemId/dateRange/dateType"},
                "dateRange": {"type": "string", "description": "默认最近完整日"},
                "dateType": {"type": "string", "default": "day"},
                "timeRange": {"type": "string", "description": "分钟级口径探测字段；当前返回不支持"},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_sycm_item_flow_sources",
        "description": "获取商品360流量来源树，并返回根渠道和扁平化来源路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string", "description": "商品ID；也可直接传 itemArchivesUrl 自动解析"},
                "itemArchivesUrl": {"type": "string", "description": "商品360流量来源页URL，自动读取 itemId/dateRange/dateType"},
                "dateRange": {"type": "string", "description": "默认最近完整日"},
                "dateType": {"type": "string", "default": "day"},
            },
        },
    },
    {
        "name": "tmall_sycm_item_360_metrics",
        "description": "获取商品360可直接决策的流量来源指标：经营优势-搜索免费访客/转化/成交、付费推广-无界-关键词推广、免费汇总、付费汇总和其他来源环比。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string", "description": "商品ID；也可直接传 itemArchivesUrl 自动解析"},
                "itemArchivesUrl": {"type": "string", "description": "商品360流量来源页URL，自动读取 itemId/dateRange/dateType"},
                "dateRange": {"type": "string", "description": "默认最近完整日"},
                "dateType": {"type": "string", "default": "day"},
                "compareDateRange": {"type": "string", "description": "默认取上一等长周期"},
                "compareDateType": {"type": "string", "default": "day"},
                "timeRange": {"type": "string", "description": "分钟级口径探测字段；当前返回不支持"},
                "compareTimeRange": {"type": "string", "description": "分钟级对比口径探测字段；当前返回不支持"},
                "includeDetail": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "tmall_item_flow_required_metrics",
        "description": "按新运营待办表获取商品360流量来源：经营优势-搜索访客/转化/成交与付费推广-无界-关键词推广访客/转化环比，支持 item_archives 的 day/today 口径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string", "description": "商品ID；也可直接传 itemArchivesUrl 自动解析"},
                "itemArchivesUrl": {"type": "string", "description": "商品360流量来源页URL，自动读取 itemId/dateRange/dateType"},
                "dateRange": {"type": "string", "description": "默认最近完整日"},
                "dateType": {"type": "string", "default": "day"},
                "compareDateRange": {"type": "string", "description": "默认取上一等长周期"},
                "compareDateType": {"type": "string", "default": "day"},
                "timeRange": {"type": "string", "description": "分钟级口径探测字段；当前返回不支持"},
                "compareTimeRange": {"type": "string", "description": "分钟级对比口径探测字段；当前返回不支持"},
            },
        },
    },
    {
        "name": "tmall_item_flow_required_metrics_batch",
        "description": "批量获取商品360流量来源搜索节点日数据，逐 itemId 返回 tmall_item_flow_required_metrics 结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemIds": {"type": "array", "items": {"type": "string"}, "description": "商品ID列表"},
                "dateRange": {"type": "string", "description": "默认最近完整日"},
                "dateType": {"type": "string", "default": "day"},
                "compareDateRange": {"type": "string", "description": "默认取上一等长周期"},
                "compareDateType": {"type": "string", "default": "day"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["itemIds"],
        },
    },
    {
        "name": "tmall_sycm_market_rank",
        "description": "获取生意参谋市场排行竞品 TopN，封装 /mc/mq/mkt/item/offline/rank.json，并返回类目、价格带和更新时间。支持 dateType=today + dateRange=当天|当天 采集实时榜；08:55持久化和跨日对比由 skill 完成。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 300},
                "dateRange": {"type": "string", "description": "如 2026-04-23|2026-04-23；dateType=today 时用于实时榜，默认最近 7 个完整日"},
                "dateType": {"type": "string", "default": "recent7", "description": "支持 today/recent7/day 等生意参谋口径；today 可用于 08:55 实时快照"},
                "cateId": {"type": "string", "description": "市场类目 ID；不传则取当前店铺默认叶子类目"},
                "parentCateId": {"type": "string", "description": "父类目 ID；计生品类/避孕套为 50024153"},
                "cateFlag": {"type": "integer", "default": 0, "description": "生意参谋类目层级标识；计生品类/避孕套为 2"},
                "categoryPreset": {"type": "string", "description": "预置类目；contraceptive_condom=计生品类/避孕套"},
                "rankType": {"type": "string", "default": "gmv"},
                "sellerType": {"type": "integer", "default": -1},
                "keyword": {"type": "string", "default": ""},
                "priceSeg": {"type": "string", "default": ""},
                "minPrice": {"type": "number", "description": "最低价格；用于按商品金额下浮 10% 筛选同价位竞品"},
                "maxPrice": {"type": "number", "description": "最高价格；用于按商品金额上浮 10% 筛选同价位竞品"},
                "indexCode": {"type": "string", "default": "payByrCnt,uv"},
                "includeDetailRows": {"type": "boolean", "default": False, "description": "默认返回压缩行，避免 Top300 输出过大；需要原始 metrics/detailUrl 时再开启"},
            },
        },
    },
    {
        "name": "tmall_sycm_activity_price",
        "description": "获取生意参谋活动与价格检查数据，封装活动列表、活动榜单、价格风险和商品价格提醒接口。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemIds": {
                    "description": "商品 ID 字符串或数组；传入后会查询商品级价格风险提醒",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                "activityStartType": {"type": "string", "default": "recentHalfYear"},
                "activityStatus": {"type": "integer", "default": -1},
                "activityType": {"type": "integer", "default": -1},
            },
        },
    },
    {
        "name": "tmall_item_reviews",
        "description": "通过天猫详情页 MTOP API 采集 TopN 评价、问大家和风险词；失败时降级 DOM/CDP。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
                "waitSeconds": {"type": "number", "default": 4},
                "includeAskAll": {"type": "boolean", "default": True},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_item_reviews_api",
        "description": "通过天猫详情页前端 MTOP API 直接采集评价列表和问大家，不做 DOM 文本解析。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
                "waitSeconds": {"type": "number", "default": 2},
                "timeoutSeconds": {"type": "number", "default": 20},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_item_review_qa_check",
        "description": "按新运营待办表检查链接是否有问大家、差评置顶和负向评价风险。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "waitSeconds": {"type": "number", "default": 2},
                "timeoutSeconds": {"type": "number", "default": 15},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_seller_price_competitiveness_check",
        "description": "通过天猫商家中心价格竞争力/五星价格力接口检查商品价格力星级、建议价、高价限流和流量诊断。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 300},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_seller_marketing_activity_list",
        "description": "通过天猫商家中心营销工具接口获取活动列表和生效/暂停/结束状态；活动级口径，不等同商品级参与明细。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string", "description": "可选，仅用于返回商品级口径缺口说明"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            },
        },
    },
    {
        "name": "tmall_seller_price_risk_check",
        "description": "通过天猫商家中心营销风险 MTOP API 检查 0 元订单、超低价订单、活动范围不一致、优惠力度过大等价格风险。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "waitSeconds": {"type": "number", "default": 3},
                "timeoutSeconds": {"type": "number", "default": 20},
            },
        },
    },
    {
        "name": "tmall_item_activity_price_check",
        "description": "按新运营待办表检查活动状态、价格风险和五星价格力；组合商家中心 API 与生意参谋补充口径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                "waitSeconds": {"type": "number", "default": 3},
                "timeoutSeconds": {"type": "number", "default": 20},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_alimama_item_promotion",
        "description": (
            "按商品 ID 获取阿里妈妈关键词推广或人群推广计划列表。"
            "复用 checkAccess 获取 csrfId，再 replay findPage 接口。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "promotionType": {"type": "string", "enum": ["search", "display"], "default": "search"},
                "dateRange": {"type": "string", "description": "默认今天"},
                "offset": {"type": "integer", "default": 0},
                "pageSize": {"type": "integer", "default": 40},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_alimama_item_promotion_compare",
        "description": "按商品 ID 采集阿里妈妈关键词/人群推广当前期和上一期计划级数据，并汇总 ROI、PPC、点击、转化环比。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "promotionTypes": {
                    "oneOf": [
                        {"type": "string", "enum": ["search", "display", "all"]},
                        {"type": "array", "items": {"type": "string", "enum": ["search", "display"]}},
                    ],
                    "default": "all",
                },
                "dateRange": {"type": "string", "description": "默认今天"},
                "compareDateRange": {"type": "string", "description": "默认取上一等长周期"},
                "timeRange": {"type": "string", "description": "分钟级口径探测字段；当前返回不支持"},
                "compareTimeRange": {"type": "string", "description": "分钟级对比口径探测字段；当前返回不支持"},
                "pageSize": {"type": "integer", "default": 40},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_item_promotion_required_metrics",
        "description": "按新运营待办表获取阿里妈妈关键词推广：manage/search itemId筛选底部合计（花费、直接成交金额、投入产出比、点击转化率、平均点击花费、点击率）及环比。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "dateRange": {"type": "string", "description": "默认今天"},
                "compareDateRange": {"type": "string", "description": "默认取上一等长周期"},
                "pageSize": {"type": "integer", "default": 40},
            },
            "required": ["itemId"],
        },
    },
    {
        "name": "tmall_link_decline_required_context",
        "description": "一次性返回新运营待办表所需的免费流依据、付费流依据、运营检查项和当前没有稳定 API 的清单。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "dateRange": {"type": "string", "description": "商品360默认最近完整日；关键词推广默认今天"},
                "compareDateRange": {"type": "string", "description": "默认取上一等长周期"},
                "reviewLimit": {"type": "integer", "default": 10},
            },
            "required": ["itemId"],
        },
    },
]

_sync_tmall_tool_meta_catalog(TOOLS)
TOOLS = _with_tool_meta(TOOLS)
TMALL_TOOL_CATALOG = {str(tool.get("name") or ""): dict(tool) for tool in TOOLS}


def _today_range() -> str:
    today = date.today().isoformat()
    return f"{today}|{today}"


def _last_complete_day_range() -> str:
    d = date.today() - timedelta(days=1)
    day = d.isoformat()
    return f"{day}|{day}"


def _recent_complete_days_range(days: int = 7) -> str:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=max(1, days) - 1)
    return f"{start.isoformat()}|{end.isoformat()}"


def _date_bounds(date_range: str) -> tuple[str, str]:
    parts = (date_range or "").split("|", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    day = parts[0] if parts and parts[0] else date.today().isoformat()
    return day, day


def _default_alimama_date_type(date_range: Any) -> str:
    end_day = _date_range_end_day(date_range)
    return "day" if end_day and end_day < date.today().isoformat() else "today"


def _alimama_realtime_requested(args: dict, date_range: str) -> bool:
    date_type = str(args.get("dateType") or _default_alimama_date_type(date_range)).strip().lower()
    if date_type == "day":
        return False
    if date_type in {"today", "realtime", "real_time", "live_snapshot"}:
        return True
    end_day = _date_range_end_day(date_range)
    return bool(end_day and end_day >= date.today().isoformat())


def _alimama_granularity(use_realtime: bool) -> str:
    return "live_snapshot" if use_realtime else "day"


def _range_has_time(date_range: Any) -> bool:
    text = unquote(str(date_range or ""))
    if not text:
        return False
    parts = text.split("|", 1)
    return any(":" in part or "T" in part for part in parts)


def _sycm_granularity(date_type: Any) -> str:
    text = str(date_type or "").strip().lower()
    if text == "today":
        return "live_snapshot"
    return text or "day"


def _minute_compare_requested(args: dict) -> bool:
    granularity = str(args.get("granularity") or args.get("timeGranularity") or "").lower()
    if granularity in {"minute", "minutely", "hour", "hourly", "realtime", "real_time"}:
        return True
    if args.get("timeRange") or args.get("compareTimeRange"):
        return True
    return _range_has_time(args.get("dateRange")) or _range_has_time(args.get("compareDateRange"))


def _unsupported_minute_response(source: str, args: dict, *, item_id: str | None = None) -> dict:
    message = "当前已验证 API 不支持分钟级/小时级同口径对比；请使用日期级 dateRange/compareDateRange，或先在固定时间运行并持久化快照。"
    return {
        "source": source,
        "itemId": item_id or str(args.get("itemId") or ""),
        "dateRange": args.get("dateRange"),
        "compareDateRange": args.get("compareDateRange"),
        "timeRange": args.get("timeRange"),
        "compareTimeRange": args.get("compareTimeRange"),
        "dateType": args.get("dateType"),
        "compareDateType": args.get("compareDateType"),
        "granularity": "unsupported_minute",
        "supported": False,
        "supportsMinuteCompare": False,
        "dataQuality": [message],
        "error": message,
    }


def _previous_equal_date_range(date_range: str) -> str:
    start_raw, end_raw = _date_bounds(date_range)
    try:
        start = date.fromisoformat(start_raw[:10])
        end = date.fromisoformat(end_raw[:10])
    except ValueError:
        return _last_complete_day_range()
    span = max(1, (end - start).days + 1)
    previous_start = start - timedelta(days=span)
    previous_end = end - timedelta(days=span)
    return f"{previous_start.isoformat()}|{previous_end.isoformat()}"


def _q_date_range(date_range: str) -> str:
    return quote(date_range, safe="")


def _first_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[0]).strip() if values else ""


def _parse_item_archives_url(value: Any) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    return {
        "inputUrl": text,
        "itemId": _first_query_value(query, "itemId"),
        "dateRange": _first_query_value(query, "dateRange"),
        "dateType": _first_query_value(query, "dateType"),
        "activeKey": _first_query_value(query, "activeKey"),
    }


def _resolve_item_archives_args(args: dict) -> dict:
    resolved = dict(args or {})
    url_info = _parse_item_archives_url(
        resolved.get("itemArchivesUrl") or resolved.get("pageUrl") or resolved.get("url")
    )
    warnings: list[str] = []
    explicit_item_id = str(resolved.get("itemId") or "").strip()
    url_item_id = str(url_info.get("itemId") or "").strip()
    if url_item_id:
        if explicit_item_id and explicit_item_id != url_item_id:
            warnings.append(f"传入itemId={explicit_item_id}与item_archives链接itemId={url_item_id}不一致，已按链接itemId采集")
        resolved["itemId"] = url_item_id
    elif explicit_item_id:
        resolved["itemId"] = explicit_item_id
    else:
        raise ValueError("缺少 itemId；可直接传 itemId，或传 item_archives 链接中的 itemId")
    for key in ("dateRange", "dateType"):
        if not resolved.get(key) and url_info.get(key):
            resolved[key] = url_info[key]
    if url_info.get("inputUrl"):
        resolved["_itemArchivesInputUrl"] = url_info["inputUrl"]
        resolved["_itemArchivesActiveKey"] = url_info.get("activeKey") or ""
    resolved["_itemArchivesWarnings"] = warnings
    return resolved


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _pct(curr: Any, prev: Any) -> float | None:
    prev_num = _num(prev)
    if prev_num <= 0:
        return None
    return round((_num(curr) - prev_num) / prev_num * 100, 2)


def _val(obj: Any, key: str, default: Any = None) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        return default
    value = obj.get(key)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _cycle(obj: Any, key: str) -> Any:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value.get("cycleCrc") if isinstance(value, dict) else None


def _cycle_change_pct(obj: Any, key: str) -> float | None:
    cycle = _cycle(obj, key)
    if cycle is None or str(cycle).strip() == "":
        return None
    return round(_num(cycle) * 100, 2)


def _needs_sycm_fallback(result: dict, url: str, method: str) -> bool:
    if method.upper() != "GET" or "sycm.taobao.com" not in url:
        return False
    data = result.get("data") if isinstance(result, dict) else None
    proof = result.get("proof") if isinstance(result, dict) else None
    return data == {} and isinstance(proof, dict) and proof.get("status") is None


async def _read_chrome_cookie_header() -> tuple[str, str]:
    if not _direct_browser_access_allowed():
        raise RuntimeError(MCP_DIRECT_COOKIE_BLOCKED)
    ws_url = get_page_ws_url(CDP_JSON_URL)
    async with CdpClient(ws_url) as cdp:
        await cdp.send("Network.enable")
        cookie_result = await cdp.send("Network.getAllCookies")
        ua = await cdp.evaluate("navigator.userAgent")
    cookies = cookie_result.get("cookies") if isinstance(cookie_result, dict) else []
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in cookies or []:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if (
            not name
            or name in seen
            or name in SYCM_COOKIE_SKIP
            or not (domain == "taobao.com" or domain.endswith(".taobao.com"))
        ):
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs), str(ua or "Mozilla/5.0")


def _cached_taobao_cookie_header() -> tuple[str, str]:
    global _COOKIE_CACHE
    if _is_production_env():
        return "", "Mozilla/5.0"
    now = time.monotonic()
    with _COOKIE_CACHE_LOCK:
        if _COOKIE_CACHE and now - _COOKIE_CACHE[0] < 60:
            return _COOKIE_CACHE[1], _COOKIE_CACHE[2]
        try:
            cookie_header, user_agent = asyncio.run(_read_chrome_cookie_header())
        except Exception:  # noqa: BLE001
            cookie_header, user_agent = "", "Mozilla/5.0"
        _COOKIE_CACHE = (time.monotonic(), cookie_header, user_agent)
        return cookie_header, user_agent


def _direct_fetch_timeout(default: float = 30.0) -> float:
    raw = os.environ.get("TMALL_DIRECT_FETCH_TIMEOUT")
    try:
        return max(3.0, min(float(raw), 60.0)) if raw else default
    except (TypeError, ValueError):
        return default


def _direct_flow_fetch_timeout() -> float:
    raw = os.environ.get("TMALL_FLOW_DIRECT_FETCH_TIMEOUT")
    try:
        return max(3.0, min(float(raw), 30.0)) if raw else 12.0
    except (TypeError, ValueError):
        return 12.0


def _direct_cookie_fetch_allowed() -> bool:
    return os.environ.get("TMALL_MCP_DIRECT_FETCH_ALLOWED") == "1" and _env_name() in LOCAL_ENVS


DIRECT_COOKIE_FETCH_HOSTS = (
    "sycm.taobao.com",
    "mystarseller.taobao.com",
    "shell.mkt.taobao.com",
)
FAST_FAIL_DIRECT_HTML_PATHS = (
    "sycm.taobao.com/mc/mq/mkt/item/offline/rank.json",
)
ANTI_BOT_HTML_MARKERS = (
    "_____tmd_____",
    "/punish",
    "x5sec",
    "验证码",
    "安全验证",
)


def _should_direct_cookie_fetch(url: str) -> bool:
    return _direct_cookie_fetch_allowed() and any(host in url for host in DIRECT_COOKIE_FETCH_HOSTS)


def _should_fast_fail_direct_html(url: str) -> bool:
    if os.environ.get("TMALL_DIRECT_HTML_BROWSER_FALLBACK") == "1":
        return False
    return any(path in url for path in FAST_FAIL_DIRECT_HTML_PATHS)


def _direct_html_block_reason(fetch_result: dict) -> str:
    outer = fetch_result.get("data") if isinstance(fetch_result, dict) else {}
    if not isinstance(outer, dict) or outer.get("data") is not None:
        return ""
    content_type = str(outer.get("content_type") or "").lower()
    preview = str(outer.get("text_preview") or "")
    haystack = f"{content_type}\n{preview}".lower()
    if any(marker.lower() in haystack for marker in ANTI_BOT_HTML_MARKERS):
        return "淘宝安全校验拦截：接口返回 HTML challenge，已跳过浏览器兜底以避免长时间超时"
    if "text/html" in content_type and "sycm.taobao.com" in str(outer.get("url") or ""):
        return "生意参谋接口返回 HTML，未返回 JSON，已跳过浏览器兜底以避免长时间超时"
    return ""


def _direct_taobao_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: Any = None,
    page_url: str | None = None,
    timeout: float | None = None,
) -> dict:
    if not _direct_cookie_fetch_allowed():
        return _direct_cookie_blocked_result("direct Chrome cookie fetch", url)
    cookie_header, user_agent = _cached_taobao_cookie_header()
    if not cookie_header:
        return {"success": False, "error": "未读取到 Chrome 中的淘宝 Cookie", "data": None}
    # 根据 API host 自动选择 Referer，避免阿里妈妈等接口的 referer 白名单拦截
    if page_url:
        default_referer = page_url
    elif "alimama.com" in url:
        default_referer = "https://one.alimama.com/"
    elif "sycm.taobao.com" in url:
        default_referer = "https://sycm.taobao.com/"
    elif "myseller.taobao.com" in url:
        default_referer = "https://myseller.taobao.com/"
    else:
        default_referer = "https://myseller.taobao.com/"
    req_headers = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_header,
        "Referer": default_referer,
        "User-Agent": user_agent,
    }
    req_headers.update(headers or {})
    body_bytes = None
    if body is not None:
        content_type = str(req_headers.get("content-type") or req_headers.get("Content-Type") or "").lower()
        if isinstance(body, bytes):
            body_bytes = body
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif "application/json" in content_type or not content_type:
            req_headers.setdefault("content-type", "application/json;charset=UTF-8")
            body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            body_bytes = str(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers=req_headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or _direct_fetch_timeout()) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            return {
                "success": True,
                "data": {
                    "url": url,
                    "status": resp.status,
                    "ok": 200 <= resp.status < 400,
                    "content_type": resp.headers.get("content-type", ""),
                    "data": data,
                    "text_preview": None if data is not None else text[:1000],
                },
                "proof": {
                    "page_url": page_url,
                    "api_url": url,
                    "status": resp.status,
                    "ok": 200 <= resp.status < 400,
                    "content_type": resp.headers.get("content-type", ""),
                    "transport": "direct_with_chrome_cookies",
                },
            }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"淘宝 Cookie 直连 fallback 失败: {exc}", "data": None}


def _direct_sycm_fetch(url: str, page_url: str | None = None) -> dict:
    if _is_production_env():
        return _direct_cookie_blocked_result("direct SYCM fallback", url)
    return _direct_taobao_fetch(url, page_url=page_url)


def _fetch_throttle_seconds(url: str) -> tuple[str, float, float]:
    host = urlparse(url).netloc.lower()
    if not host or not (host.endswith("taobao.com") or host.endswith("alimama.com")):
        return "", 0.0, 0.0
    default_interval = _num(os.environ.get("TMALL_MCP_FETCH_MIN_INTERVAL_SECONDS"), 0.8)
    jitter = max(0.0, _num(os.environ.get("TMALL_MCP_FETCH_JITTER_SECONDS"), 0.2))
    if host.endswith("alimama.com"):
        interval = _num(os.environ.get("TMALL_MCP_ALIMAMA_MIN_INTERVAL_SECONDS"), default_interval)
        return "alimama", max(0.0, interval), jitter
    if host == "sycm.taobao.com":
        interval = _num(os.environ.get("TMALL_MCP_SYCM_MIN_INTERVAL_SECONDS"), default_interval)
        return "sycm", max(0.0, interval), jitter
    return host, max(0.0, default_interval), jitter


def _throttle_external_fetch(url: str) -> None:
    if os.environ.get("TMALL_MCP_FETCH_THROTTLE_DISABLED") == "1":
        return
    key, min_interval, jitter = _fetch_throttle_seconds(url)
    if not key or min_interval <= 0:
        return
    sleep_for = 0.0
    with _FETCH_THROTTLE_LOCK:
        now = time.monotonic()
        last_slot = _FETCH_THROTTLE_LAST.get(key, 0.0)
        next_slot = max(now, last_slot + min_interval)
        if next_slot > now:
            sleep_for = next_slot - now + random.uniform(0, jitter)
        _FETCH_THROTTLE_LAST[key] = now + sleep_for
    if sleep_for > 0:
        time.sleep(sleep_for)


def _current_collection_meta() -> ToolMeta | None:
    meta = TMALL_TOOL_META.get(_CURRENT_TOOL_NAME.get(""))
    if not meta or meta.platform == "composite":
        return None
    return meta


def _current_shop_id() -> str:
    args = _CURRENT_TOOL_ARGS.get({}) or {}
    for key in ("shop_id", "shopId", "shop", "store_id", "storeId"):
        value = args.get(key)
        if value:
            return str(value)
    return (
        os.environ.get("SKILLFORGE_SHOP_ID")
        or os.environ.get("SHOP_ID")
        or os.environ.get("TMALL_SHOP_ID")
        or "default"
    )


def _current_optional_arg(*keys: str) -> str | None:
    args = _CURRENT_TOOL_ARGS.get({}) or {}
    for key in keys:
        value = args.get(key)
        if value:
            return str(value)
    return None


def _record_collection_proof(meta: ToolMeta, fetch_result: dict) -> None:
    proof = fetch_result.get("proof") if isinstance(fetch_result.get("proof"), dict) else {}
    proof_id = fetch_result.get("proof_id") or proof.get("proof_id")
    proofs = _CURRENT_PROOFS.get()
    if not isinstance(proofs, list) or not proof_id:
        return
    proofs.append({
        "tool_name": meta.tool_name,
        "platform": meta.platform,
        "data_scope": meta.data_scope,
        "endpoint_family": meta.endpoint_family,
        "warning_group": meta.warning_group,
        "proof_id": proof_id,
        "credential_alias": fetch_result.get("credential_alias") or proof.get("credential_alias"),
        "status": "success" if fetch_result.get("success") else "failed",
    })


def _collection_fetch_json(
    meta: ToolMeta,
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: Any = None,
    page_url: str | None = None,
) -> dict:
    fetch_kwargs = {
        "meta": meta,
        "shop_id": _current_shop_id(),
        "params": {
            "url": url,
            "method": method,
            "headers": headers or {},
            "body": body,
            "page_url": page_url,
        },
    }
    source_id = _current_optional_arg("source_id", "sourceId", "data_source_id", "dataSourceId")
    credential_scope = _current_optional_arg("credential_scope", "credentialScope")
    credential_plan_id = _current_optional_arg("credential_plan_id", "credentialPlanId")
    if source_id:
        fetch_kwargs["source_id"] = source_id
    if credential_scope:
        fetch_kwargs["credential_scope"] = credential_scope
    if credential_plan_id:
        fetch_kwargs["credential_plan_id"] = credential_plan_id
    result = collection_client.fetch(**fetch_kwargs)
    _record_collection_proof(meta, result)
    return result


def _fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: Any = None,
    page_url: str | None = None,
) -> dict:
    meta = _current_collection_meta()
    if meta is not None:
        return _collection_fetch_json(meta, url, method=method, headers=headers, body=body, page_url=page_url)
    if _CURRENT_TOOL_NAME.get(""):
        return _direct_cookie_blocked_result("browser_mcp._fetch_json fallback", url)
    if url:
        _throttle_external_fetch(url)
    if url and _should_direct_cookie_fetch(url) and method.upper() in {"GET", "POST"}:
        direct = _direct_taobao_fetch(url, method=method, headers=headers, body=body, page_url=page_url)
        if direct.get("success") and ((direct.get("data") or {}).get("data") is not None):
            return direct
        block_reason = _direct_html_block_reason(direct)
        if block_reason and _should_fast_fail_direct_html(url):
            failed = dict(direct)
            failed["success"] = False
            failed["error"] = block_reason
            return failed
    result = browser_mcp._fetch_json(url=url, method=method, headers=headers, body=body, page_url=page_url)
    if _needs_sycm_fallback(result, url, method):
        return _direct_sycm_fetch(url, page_url=page_url)
    return result


def _api_payload(fetch_result: dict) -> dict:
    if not fetch_result.get("success"):
        raise RuntimeError(fetch_result.get("error") or "fetch failed")
    outer = fetch_result.get("data") or {}
    if isinstance(outer, dict) and outer.get("status") not in (None, 200):
        raise RuntimeError(f"HTTP {outer.get('status')}: {outer.get('text_preview') or ''}")
    is_transport_wrapper = isinstance(outer, dict) and any(
        key in outer for key in ("status", "ok", "url", "content_type", "text_preview")
    )
    payload = outer.get("data") if is_transport_wrapper and "data" in outer else outer
    if not isinstance(payload, dict):
        raise RuntimeError("接口未返回 JSON object")
    return payload


def _sycm_rank_url(
    *,
    date_range: str,
    date_type: str,
    page: int,
    order_by: str,
    order: str,
    page_size: int = 20,
) -> str:
    return (
        f"{SYCM_RANK_API}?dateRange={date_range}&dateType={date_type}"
        f"&pageSize={page_size}&page={page}&order={order}&orderBy={order_by}"
        "&keyword=&follow=false&cateId=&cateLevel="
        "&indexCode=payAmt,payItmCnt,payRate,itmUv,itemCartCnt,payByrCnt,itmPv,payPct,refundAmt"
    )


def _sycm_rank_page(date_range: str, date_type: str) -> str:
    return SYCM_RANK_PAGE.format(date_range_q=_q_date_range(date_range), date_type=date_type)


def _rank_body(payload: dict) -> dict:
    if payload.get("code") not in (0, "0", None):
        raise RuntimeError(f"生意参谋业务码异常: {payload.get('code')} {payload.get('message') or ''}")
    data = payload.get("data") or {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(inner, dict):
        raise RuntimeError("商品排行结构异常")
    return {"outer": data, "inner": inner}


def _normalize_rank_row(row: dict, rank: int) -> dict:
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    item_id = item.get("itemId") or _val(row, "itemId")
    result = {
        "rank": rank,
        "itemId": str(item_id or ""),
        "title": item.get("title") or "",
        "detailUrl": _normalize_tmall_url(item.get("detailUrl"), item_id),
        "pictUrl": item.get("pictUrl"),
        "itemStatus": row.get("itemStatus"),
        "itemTag": row.get("itemTag"),
        "payAmt": _val(row, "payAmt"),
        "payAmtCycleCrc": _cycle(row, "payAmt"),
        "payItmCnt": _val(row, "payItmCnt"),
        "payItmCntCycleCrc": _cycle(row, "payItmCnt"),
        "payByrCnt": _val(row, "payByrCnt"),
        "payRate": _val(row, "payRate"),
        "payRateCycleCrc": _cycle(row, "payRate"),
        "itmUv": _val(row, "itmUv"),
        "itmUvCycleCrc": _cycle(row, "itmUv"),
        "itmPv": _val(row, "itmPv"),
        "itemCartCnt": _val(row, "itemCartCnt"),
        "itemCartCntCycleCrc": _cycle(row, "itemCartCnt"),
        "payPct": _val(row, "payPct"),
        "refundAmt": _val(row, "refundAmt"),
        "refundAmtCycleCrc": _cycle(row, "refundAmt"),
    }
    return result


def _normalize_tmall_url(url: Any, item_id: Any = None) -> str:
    if isinstance(url, str) and url:
        text = url.replace("&amp;", "&")
        if text.startswith("//"):
            return "https:" + text
        return text
    return f"https://detail.tmall.com/item.htm?id={item_id}" if item_id else ""


def tmall_sycm_item_rank_top(args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 20), 500))
    date_range = args.get("dateRange") or _today_range()
    date_type = args.get("dateType") or "today"
    order_by = args.get("orderBy") or "payAmt"
    order = args.get("order") or "desc"
    page_url = _sycm_rank_page(date_range, date_type)

    rows: list[dict] = []
    record_count = None
    update_time = None
    page = 1
    while len(rows) < limit:
        url = _sycm_rank_url(
            date_range=date_range,
            date_type=date_type,
            page=page,
            order_by=order_by,
            order=order,
        )
        payload = _api_payload(_fetch_json(url, page_url=page_url))
        parsed = _rank_body(payload)
        inner = parsed["inner"]
        if record_count is None:
            record_count = inner.get("recordCount")
            update_time = (parsed["outer"] or {}).get("updateTime")
        page_rows = inner.get("data") or []
        if not page_rows:
            break
        for row in page_rows:
            if len(rows) >= limit:
                break
            rows.append(_normalize_rank_row(row, len(rows) + 1))
        if record_count and len(rows) >= min(limit, int(record_count)):
            break
        page += 1

    return {
        "source": "sycm_item_rank",
        "dateRange": date_range,
        "dateType": date_type,
        "granularity": "live_snapshot" if date_type == "today" else date_type,
        "asOfTime": update_time,
        "supportsMinuteCompare": False,
        "orderBy": order_by,
        "recordCount": record_count,
        "updateTime": update_time,
        "rowsCollected": len(rows),
        "rows": rows,
    }


def tmall_sycm_item_rank_summary(args: dict) -> dict:
    args = {**args, "limit": 1}
    result = tmall_sycm_item_rank_top(args)
    result.pop("rows", None)
    return result


def _sycm_detail_page(item_id: str, date_range: str, date_type: str) -> str:
    return SYCM_DETAIL_PAGE.format(
        item_id=item_id,
        date_type=date_type,
        date_range_q=_q_date_range(date_range),
    )


def _sycm_api_body(fetch_result: dict) -> Any:
    payload = _api_payload(fetch_result)
    if "content" in payload and isinstance(payload["content"], dict):
        payload = payload["content"]
    challenge_url = _taobao_security_challenge_url(payload)
    if challenge_url:
        raise RuntimeError(f"淘宝安全校验拦截: {challenge_url}")
    if payload.get("code") not in (0, "0", None):
        raise RuntimeError(f"业务码异常: {payload.get('code')} {payload.get('message') or ''}")
    return payload.get("data")


def _flow_source_tree_url(item_id: str, date_range: str, date_type: str) -> str:
    return (
        "https://sycm.taobao.com/flow/item/source/tree/support.json?"
        f"dateRange={date_range}&dateType={date_type}&pageSize=100&page=1"
        f"&order=desc&orderBy=uv&itemId={item_id}&flowBizType=classic"
        "&activateBoost=sourceChannel&crowdType=all"
        "&indexCode=uv,pv,cltItmCnt,cartByrCnt,payByrCnt,payRate,payAmt,payItmCnt"
    )


def _flow_source_live_url(item_id: str, date_range: str, device: str = "2") -> str:
    return (
        "https://sycm.taobao.com/flow/v6/live/item/source/v3.json?"
        f"itemId={item_id}&dateType=today&dateRange={date_range}&page=1"
        "&order=desc&orderBy=uv"
        "&indexCode=uv,pv,payByrCnt,payRate,payAmt,payItmCnt,cartByrCnt,cltItmCnt"
        "&dtMaxAge=600000&__fallbackToCache=true"
        f"&device={device}"
    )


def _live_flow_roots(data: Any) -> list[dict]:
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    if isinstance(rows, dict):
        rows = rows.get("item")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _flow_node_without_cycle_fields(node: dict) -> dict:
    return {
        key: value
        for key, value in node.items()
        if not (key.endswith("CycleCrc") or key.endswith("ChangePct"))
    }


def _flow_sources_result(
    *,
    item_id: str,
    date_range: str,
    date_type: str,
    roots: list[dict],
    page_url: str,
    api_url: str,
    requested_date_range: Any = None,
    input_url: str = "",
    data_quality: list[str] | None = None,
    source: str = "sycm_item_flow_sources",
    as_of_time: Any = None,
    update_time: Any = None,
) -> dict:
    flat = _flatten_flow(roots)
    normalized_roots = [_normalize_flow_node(n, path=[]) for n in roots if isinstance(n, dict)]
    if date_type == "today":
        flat = [_flow_node_without_cycle_fields(row) for row in flat]
        normalized_roots = [_flow_node_without_cycle_fields(row) for row in normalized_roots]
    return {
        "source": source,
        "itemId": item_id,
        "requestedDateRange": requested_date_range,
        "effectiveDateRange": date_range,
        "dateType": date_type,
        "granularity": _sycm_granularity(date_type),
        "asOfTime": as_of_time or update_time,
        "updateTime": update_time or as_of_time,
        "supportsMinuteCompare": False,
        "rootCount": len(roots),
        "nodeCount": len(flat),
        "roots": normalized_roots,
        "nodes": flat,
        "itemArchivesUrl": page_url,
        "apiUrl": api_url,
        "inputUrl": input_url,
        "dataQuality": data_quality or [],
    }


def _direct_sycm_item_flow_sources(
    *,
    item_id: str,
    date_range: str,
    date_type: str,
    requested_date_range: Any = None,
    input_url: str = "",
    data_quality: list[str] | None = None,
) -> dict:
    if not _direct_cookie_fetch_allowed():
        raise RuntimeError("MCP_DIRECT_COOKIE_BLOCKED")
    page_url = _sycm_detail_page(item_id, date_range, date_type)
    api_url = (
        _flow_source_live_url(item_id, date_range)
        if date_type == "today"
        else _flow_source_tree_url(item_id, date_range, date_type)
    )
    _throttle_external_fetch(api_url)
    data = _sycm_api_body(
        _direct_taobao_fetch(api_url, page_url=page_url, timeout=_direct_flow_fetch_timeout())
    )
    roots = _live_flow_roots(data) if date_type == "today" else data if isinstance(data, list) else []
    update_time = data.get("updateTime") if isinstance(data, dict) else None
    return _flow_sources_result(
        item_id=item_id,
        date_range=date_range,
        date_type=date_type,
        roots=roots,
        page_url=page_url,
        api_url=api_url,
        requested_date_range=requested_date_range,
        input_url=input_url,
        data_quality=data_quality,
        source="sycm_item_flow_sources_direct",
        as_of_time=update_time,
        update_time=update_time,
    )


def _taobao_security_challenge_url(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    ret = payload.get("ret")
    ret_text = " ".join(str(item) for item in ret) if isinstance(ret, list) else str(ret or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    url = str(data.get("url") or payload.get("url") or "")
    haystack = f"{ret_text} {url}".lower()
    if "fail_sys_user_validate" in haystack or "rgv587" in haystack or "/punish" in haystack or "x5sec" in haystack:
        return url or ret_text
    return ""


def _flow_update_dates(page_url: str) -> dict:
    url = (
        "https://sycm.taobao.com/oneauth/api/commDateByLocation.json?"
        "locationCodes=flow_monitor_itemsource_v4,flow_monitor_itemsource_crowd_v4,"
        "flow_monitor_itemsource,flow_monitor_itemsource_crowd"
        "&targetUrl=http%3A%2F%2Fsycm.taobao.com%2Fcc%2Fitem_archives"
        f"&_={_stamp_ms()}"
    )
    try:
        data = _sycm_api_body(_fetch_json(url, page_url=page_url))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _flow_latest_update_day(page_url: str) -> str:
    dates = _flow_update_dates(page_url)
    for key in (
        "flow_monitor_itemsource_v4",
        "flow_monitor_itemsource",
        "flow_monitor_itemsource_crowd_v4",
        "flow_monitor_itemsource_crowd",
    ):
        row = dates.get(key) if isinstance(dates.get(key), dict) else {}
        value = str(row.get("updateDay") or row.get("updateNDay") or "").strip()
        if value:
            return value[:10]
    return ""


def _date_range_end_day(date_range: Any) -> str:
    text = str(date_range or "").strip()
    if not text:
        return ""
    end = text.split("|")[-1].strip()
    return end[:10]


def _default_flow_date_type(args: dict) -> str:
    if args.get("dateType"):
        return str(args.get("dateType"))
    end_day = _date_range_end_day(args.get("dateRange"))
    if end_day and end_day < date.today().isoformat():
        return "day"
    return "today"


def _resolve_flow_period(args: dict, item_id: str) -> tuple[str, str, list[str], bool]:
    requested_range = args.get("dateRange")
    requested_type = _default_flow_date_type(args)
    date_range = str(requested_range or (_today_range() if requested_type == "today" else _last_complete_day_range()))
    date_type = requested_type
    page_url = _sycm_detail_page(item_id, date_range, "day" if date_type == "today" else date_type)
    latest_day = _flow_latest_update_day(page_url)
    notes: list[str] = []
    used_latest_fallback = False
    if latest_day and not requested_range and date_type != "today":
        date_range = f"{latest_day}|{latest_day}"
    if latest_day and (
        date_type == "day" and _date_range_end_day(date_range) > latest_day
    ):
        original_range = date_range
        original_type = date_type
        date_range = f"{latest_day}|{latest_day}"
        date_type = "day"
        used_latest_fallback = True
        notes.append(
            "商品360流量来源按生意参谋已发布日期采集："
            f"请求{original_type} {original_range}，实际使用day {date_range}"
        )
    return date_range, date_type, notes, used_latest_fallback


def tmall_sycm_item_detail(args: dict) -> dict:
    args = _resolve_item_archives_args(args)
    item_id = str(args["itemId"])
    date_range = args.get("dateRange") or _last_complete_day_range()
    date_type = args.get("dateType") or "day"
    page_url = _sycm_detail_page(item_id, date_range, date_type)
    stamp = int(date.today().strftime("%Y%m%d"))
    urls = {
        "crowdInfo": (
            "https://sycm.taobao.com/cc/item/crowd/info.json?"
            f"dateRange={date_range}&dateType={date_type}&itemId={item_id}&_={stamp}"
        ),
        "propertySuggestions": f"https://sycm.taobao.com/cc/item/property/suggestions.json?itemId={item_id}&_={stamp}",
        "itemCrowds": f"https://sycm.taobao.com/cc/item/getItemCrowds.json?itemId={item_id}&_={stamp}",
        "newProduct": f"https://sycm.taobao.com/cc/item/new/product/check.json?itemId={item_id}&_={stamp}",
        "supersku": f"https://sycm.taobao.com/cc/supersku/verify.json?itemIdList={item_id}&_={stamp}",
        "priceWarn": (
            "https://sycm.taobao.com/portal/risk/priceUpControl/queryItemWarnInfo.json?"
            f"itemIds={item_id}&_={stamp}"
        ),
    }
    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, url in urls.items():
        try:
            raw[name] = _sycm_api_body(_fetch_json(url, page_url=page_url))
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)

    info = raw.get("crowdInfo") if isinstance(raw.get("crowdInfo"), dict) else {}
    suggestions = raw.get("propertySuggestions") if isinstance(raw.get("propertySuggestions"), list) else []
    crowds = raw.get("itemCrowds") if isinstance(raw.get("itemCrowds"), dict) else {}
    return {
        "source": "sycm_item_detail",
        "itemId": item_id,
        "dateRange": date_range,
        "dateType": date_type,
        "item": {
            "title": info.get("title"),
            "pictUrl": info.get("pictUrl"),
            "detailUrl": _normalize_tmall_url(info.get("detailUrl"), item_id),
            "tags": info.get("tags") or [],
            "itemScore": info.get("itemScore"),
            "itemLevel": info.get("itemLevel"),
            "rank": info.get("rank"),
            "isMonitored": info.get("isMonitored"),
            "isNewProduct": raw.get("newProduct"),
        },
        "diagnostics": [
            {
                "type": x.get("diagnoseType"),
                "shortTips": x.get("shortTips"),
                "suggestion": x.get("suggestion"),
                "effectDesc": x.get("effectDesc"),
            }
            for x in suggestions
            if isinstance(x, dict)
        ],
        "crowds": crowds.get("crowdList") or [],
        "priceWarnings": raw.get("priceWarn") or [],
        "supersku": raw.get("supersku"),
        "errors": errors,
        "raw": raw,
    }


def _normalize_flow_node(node: dict, *, path: list[str]) -> dict:
    name = _val(node, "pageName") or _val(node, "sourceName") or ""
    full_path = [*path, str(name)] if name else list(path)
    return {
        "level": _val(node, "pageLevel") or len(full_path),
        "name": name,
        "path": " > ".join(full_path),
        "uv": _val(node, "uv"),
        "pv": _val(node, "pv"),
        "payAmt": _val(node, "payAmt"),
        "payAmtCycleCrc": _cycle(node, "payAmt"),
        "payAmtChangePct": _cycle_change_pct(node, "payAmt"),
        "payItmCnt": _val(node, "payItmCnt"),
        "payItmCntCycleCrc": _cycle(node, "payItmCnt"),
        "payItmCntChangePct": _cycle_change_pct(node, "payItmCnt"),
        "payByrCnt": _val(node, "payByrCnt"),
        "payByrCntCycleCrc": _cycle(node, "payByrCnt"),
        "payByrCntChangePct": _cycle_change_pct(node, "payByrCnt"),
        "payRate": _val(node, "payRate"),
        "payRateCycleCrc": _cycle(node, "payRate"),
        "payRateChangePct": _cycle_change_pct(node, "payRate"),
        "cartByrCnt": _val(node, "cartByrCnt"),
        "cartByrCntCycleCrc": _cycle(node, "cartByrCnt"),
        "cartByrCntChangePct": _cycle_change_pct(node, "cartByrCnt"),
        "cltItmCnt": _val(node, "cltItmCnt"),
        "cltItmCntCycleCrc": _cycle(node, "cltItmCnt"),
        "cltItmCntChangePct": _cycle_change_pct(node, "cltItmCnt"),
        "crtByrCnt": _val(node, "crtByrCnt"),
        "crtByrCntCycleCrc": _cycle(node, "crtByrCnt"),
        "crtByrCntChangePct": _cycle_change_pct(node, "crtByrCnt"),
        "uvCycleCrc": _cycle(node, "uv"),
        "uvChangePct": _cycle_change_pct(node, "uv"),
        "pvCycleCrc": _cycle(node, "pv"),
        "pvChangePct": _cycle_change_pct(node, "pv"),
    }


def _flatten_flow(nodes: list[dict], path: list[str] | None = None) -> list[dict]:
    flat: list[dict] = []
    path = path or []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        current = _normalize_flow_node(node, path=path)
        flat.append(current)
        children = node.get("children") if isinstance(node.get("children"), list) else []
        child_path = current["path"].split(" > ") if current["path"] else path
        flat.extend(_flatten_flow(children, child_path))
    return flat


def tmall_sycm_item_flow_sources(args: dict) -> dict:
    args = _resolve_item_archives_args(args)
    item_id = str(args["itemId"])
    date_range, date_type, period_quality, _ = _resolve_flow_period(args, item_id)
    if _minute_compare_requested(args) or date_type in {"hour", "minute"}:
        response = _unsupported_minute_response("sycm_item_flow_sources", args, item_id=item_id)
        response.update({
            "effectiveDateRange": date_range,
            "rootCount": 0,
            "nodeCount": 0,
            "roots": [],
            "nodes": [],
        })
        return response
    page_url = _sycm_detail_page(item_id, date_range, date_type)
    if date_type == "today":
        device = str(args.get("device") or "2").strip() or "2"
        url = _flow_source_live_url(item_id, date_range, device)
        data = _sycm_api_body(_fetch_json(url, page_url=page_url))
        roots = _live_flow_roots(data)
        update_time = data.get("updateTime") if isinstance(data, dict) else None
        return _flow_sources_result(
            item_id=item_id,
            date_range=date_range,
            date_type=date_type,
            roots=roots,
            page_url=page_url,
            api_url=url,
            requested_date_range=args.get("dateRange"),
            input_url=args.get("_itemArchivesInputUrl") or "",
            data_quality=[
                *(args.get("_itemArchivesWarnings") or []),
                *period_quality,
                "商品360实时来源使用flow/v6/live/item/source/v3.json；仅输出当前实时快照，不支持历史日期回放",
            ],
            as_of_time=update_time,
            update_time=update_time,
        )
    url = _flow_source_tree_url(item_id, date_range, date_type)
    data = _sycm_api_body(_fetch_json(url, page_url=page_url))
    roots = data if isinstance(data, list) else []
    return _flow_sources_result(
        item_id=item_id,
        date_range=date_range,
        date_type=date_type,
        roots=roots,
        page_url=page_url,
        api_url=url,
        requested_date_range=args.get("dateRange"),
        input_url=args.get("_itemArchivesInputUrl") or "",
        data_quality=[*(args.get("_itemArchivesWarnings") or []), *period_quality],
    )


def _flow_text(node: dict) -> str:
    return f"{node.get('path') or ''} {node.get('name') or ''}"


def _is_paid_flow(node: dict) -> bool:
    text = _flow_text(node)
    paid_terms = ("付费", "推广", "直通车", "万相台", "引力魔方", "关键词推广")
    return any(term in text for term in paid_terms)


def _is_keyword_promotion_flow(node: dict) -> bool:
    text = _flow_text(node)
    if "关键词推广" in text or "直通车" in text:
        return True
    return _is_paid_flow(node) and "关键词" in text


def _is_search_flow(node: dict) -> bool:
    text = _flow_text(node)
    if _is_paid_flow(node):
        return False
    return "搜索" in text


def _search_node_score(node: dict) -> tuple[int, float]:
    name = str(node.get("name") or "").strip()
    path = str(node.get("path") or "").strip()
    exact_search = name == "搜索" or path.endswith(" > 搜索")
    return (1 if exact_search else 0, _num(node.get("uv")))


def _keyword_promotion_node_score(node: dict) -> tuple[int, int, int, float]:
    name = str(node.get("name") or "").strip()
    path = str(node.get("path") or "").strip()
    return (
        1 if name == "关键词推广" or path.endswith(" > 关键词推广") else 0,
        1 if "无界" in path else 0,
        1 if "付费推广" in path else 0,
        _num(node.get("uv")),
    )


def _best_search_flow_node(nodes: list[dict]) -> dict | None:
    matches = [node for node in nodes if isinstance(node, dict) and _is_search_flow(node)]
    if not matches:
        return None
    return max(matches, key=_search_node_score)


def _best_keyword_promotion_flow_node(nodes: list[dict]) -> dict | None:
    matches = [node for node in nodes if isinstance(node, dict) and _is_keyword_promotion_flow(node)]
    if not matches:
        return None
    return max(matches, key=_keyword_promotion_node_score)


def _best_flow_node(nodes: list[dict], predicate) -> dict | None:
    matches = [node for node in nodes if isinstance(node, dict) and predicate(node)]
    if not matches:
        return None
    return max(matches, key=lambda node: _num(node.get("uv")))


def _aggregate_flow_nodes(name: str, nodes: list[dict]) -> dict | None:
    if not nodes:
        return None
    uv = sum(_num(node.get("uv")) for node in nodes if isinstance(node, dict))
    pay_buyer_count = sum(_num(node.get("payByrCnt")) for node in nodes if isinstance(node, dict))
    pv = sum(_num(node.get("pv")) for node in nodes if isinstance(node, dict))
    pay_rate = pay_buyer_count / uv if uv > 0 else 0
    return {
        "name": name,
        "path": name,
        "uv": uv,
        "pv": pv,
        "payByrCnt": pay_buyer_count,
        "payRate": pay_rate,
    }


def _conversion_rate(node: dict | None) -> float:
    if not node:
        return 0.0
    rate = _num(node.get("payRate"), -1)
    if rate >= 0:
        return rate
    uv = _num(node.get("uv"))
    return _num(node.get("payByrCnt")) / uv if uv > 0 else 0.0


def _node_has_metric(node: dict | None, key: str) -> bool:
    if not isinstance(node, dict) or key not in node:
        return False
    value = node.get(key)
    return value is not None and str(value).strip() != ""


def _node_metric(node: dict | None, key: str) -> float | None:
    return _num(node.get(key)) if _node_has_metric(node, key) else None


def _node_cycle_ratio(node: dict | None, key: str) -> float | None:
    cycle_key = f"{key}CycleCrc"
    return _num(node.get(cycle_key)) if _node_has_metric(node, cycle_key) else None


def _node_cycle_change_pct(node: dict | None, key: str) -> float | None:
    ratio = _node_cycle_ratio(node, key)
    return round(ratio * 100, 2) if ratio is not None else None


def _previous_from_cycle(current: float | None, cycle_ratio: float | None) -> float | None:
    if current is None or cycle_ratio is None or cycle_ratio <= -0.95:
        return None
    denominator = 1.0 + cycle_ratio
    if denominator <= 0:
        return None
    return current / denominator


def _metric_compare_values(current: dict | None, previous: dict | None, key: str) -> tuple[float | None, float | None, float | None, str]:
    current_value = _node_metric(current, key)
    previous_value = _node_metric(previous, key)
    compare_source = "node" if previous_value is not None else ""
    if previous_value is None:
        cycle_ratio = _node_cycle_ratio(current, key)
        previous_value = _previous_from_cycle(current_value, cycle_ratio)
        if previous_value is not None:
            compare_source = "cycleCrc"
    change_pct = _pct(current_value, previous_value) if current_value is not None and previous_value is not None else None
    if change_pct is None:
        change_pct = _node_cycle_change_pct(current, key)
        if change_pct is not None and not compare_source:
            compare_source = "cycleCrc"
    return current_value, previous_value, change_pct, compare_source


def _source_metric(name: str, current: dict | None, previous: dict | None, *, fallback: str = "") -> dict:
    current_found = bool(current)
    current_uv, previous_uv, uv_change_pct, uv_compare_source = _metric_compare_values(current, previous, "uv")
    current_pay_buyer, previous_pay_buyer, pay_buyer_change_pct, pay_buyer_compare_source = _metric_compare_values(
        current,
        previous,
        "payByrCnt",
    )
    current_pay_amount, previous_pay_amount, pay_amount_change_pct, pay_amount_compare_source = _metric_compare_values(
        current,
        previous,
        "payAmt",
    )
    current_rate = _conversion_rate(current) if current_found else None
    previous_rate = _conversion_rate(previous) if previous else None
    rate_compare_source = "node" if previous_rate is not None else ""
    if previous_rate is None:
        rate_compare_source = "cycleCrc" if _node_cycle_ratio(current, "payRate") is not None else ""
        previous_rate = _previous_from_cycle(current_rate, _node_cycle_ratio(current, "payRate"))
    conversion_change_pct = (
        _pct(current_rate, previous_rate)
        if current_rate is not None and previous_rate is not None
        else _node_cycle_change_pct(current, "payRate")
    )
    compare_found = bool(previous) or any(
        source == "cycleCrc"
        for source in (uv_compare_source, pay_buyer_compare_source, pay_amount_compare_source, rate_compare_source)
    )
    return {
        "name": name,
        "found": current_found,
        "compareFound": compare_found,
        "visitor": int(round(current_uv)) if current_uv is not None else None,
        "compareVisitor": int(round(previous_uv)) if previous_uv is not None else None,
        "visitorChangePct": uv_change_pct,
        "conversionRate": current_rate,
        "compareConversionRate": previous_rate,
        "conversionChangePct": conversion_change_pct,
        "payBuyerCount": current_pay_buyer,
        "comparePayBuyerCount": previous_pay_buyer,
        "payBuyerChangePct": pay_buyer_change_pct,
        "payAmount": current_pay_amount,
        "comparePayAmount": previous_pay_amount,
        "payAmountChangePct": pay_amount_change_pct,
        "sourcePath": (current or {}).get("path") or "",
        "compareSource": uv_compare_source or rate_compare_source or pay_buyer_compare_source or pay_amount_compare_source,
        "fallback": fallback,
    }


def _flow_roots(result: dict) -> list[dict]:
    roots = result.get("roots") if isinstance(result.get("roots"), list) else []
    return [row for row in roots if isinstance(row, dict)]


def _flow_nodes(result: dict) -> list[dict]:
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    return [row for row in nodes if isinstance(row, dict)]


def _flow_summary_for_metrics(result: dict) -> dict:
    return {
        "source": result.get("source"),
        "rootCount": result.get("rootCount"),
        "nodeCount": result.get("nodeCount"),
        "effectiveDateRange": result.get("effectiveDateRange"),
        "dateType": result.get("dateType"),
        "error": result.get("error"),
    }


def _sycm_item_360_metrics_from_flows(
    *,
    item_id: str,
    date_range: str,
    date_type: str,
    compare_date_range: str,
    compare_date_type: str,
    current: dict,
    previous: dict,
    include_detail: bool,
    input_url: str = "",
    data_quality: list[str] | None = None,
) -> dict:
    quality: list[str] = list(data_quality or [])
    current_nodes = _flow_nodes(current)
    previous_nodes = _flow_nodes(previous)
    current_roots = _flow_roots(current)
    previous_roots = _flow_roots(previous)

    current_search = _best_search_flow_node(current_nodes)
    previous_search = _best_search_flow_node(previous_nodes)
    current_keyword_promotion = _best_keyword_promotion_flow_node(current_nodes) or _best_keyword_promotion_flow_node(
        current_roots,
    )
    previous_keyword_promotion = _best_keyword_promotion_flow_node(previous_nodes) or _best_keyword_promotion_flow_node(
        previous_roots,
    )
    current_paid = _best_flow_node(current_roots, _is_paid_flow) or _aggregate_flow_nodes(
        "付费推广汇总",
        [node for node in current_roots if _is_paid_flow(node)],
    )
    previous_paid = _best_flow_node(previous_roots, _is_paid_flow) or _aggregate_flow_nodes(
        "付费推广汇总",
        [node for node in previous_roots if _is_paid_flow(node)],
    )
    current_free = _aggregate_flow_nodes("免费流量汇总", [node for node in current_roots if not _is_paid_flow(node)])
    previous_free = _aggregate_flow_nodes("免费流量汇总", [node for node in previous_roots if not _is_paid_flow(node)])

    if not current_search:
        quality.append("商品360来源树未命中搜索节点，免费搜索/搜索转化必须待补采；freeTraffic仅作结构参考，不用于该指标兜底")
    if not current_paid:
        quality.append("商品360来源树未命中付费推广根渠道，付费商品360指标不可判定")
    if not current_keyword_promotion:
        quality.append("商品360来源树未命中关键词推广节点，关键词推广访客/转化环比不可判定")
    if not previous_nodes and not any(
        _node_cycle_ratio(node, "uv") is not None or _node_cycle_ratio(node, "payRate") is not None
        for node in (current_search, current_keyword_promotion, current_paid, current_free)
    ):
        quality.append("上一周期来源树为空，且当前节点未返回cycleCrc，环比不可判定")

    other_sources = []
    for root in sorted(current_roots, key=lambda row: _num(row.get("uv")), reverse=True)[:10]:
        other_sources.append({
            "name": root.get("name") or "",
            "path": root.get("path") or "",
            "visitor": int(round(_num(root.get("uv")))),
            "conversionRate": _conversion_rate(root),
            "isPaid": _is_paid_flow(root),
        })

    detail = None
    if include_detail:
        try:
            detail = tmall_sycm_item_detail({"itemId": item_id, "dateRange": date_range, "dateType": date_type})
        except Exception as exc:  # noqa: BLE001
            detail = {"source": "sycm_item_detail", "itemId": item_id, "error": str(exc)}
            quality.append(f"商品360基础诊断采集失败：{exc}")

    return {
        "source": "sycm_item_360_metrics",
        "itemId": item_id,
        "dateRange": date_range,
        "dateType": date_type,
        "compareDateRange": compare_date_range,
        "compareDateType": compare_date_type,
        "granularity": current.get("granularity") or _sycm_granularity(date_type),
        "asOfTime": current.get("asOfTime") or current.get("updateTime"),
        "updateTime": current.get("updateTime") or current.get("asOfTime"),
        "supportsMinuteCompare": False,
        "itemArchivesUrl": _sycm_detail_page(item_id, date_range, date_type),
        "compareItemArchivesUrl": _sycm_detail_page(item_id, compare_date_range, compare_date_type),
        "inputUrl": input_url,
        "freeSearch": _source_metric("免费搜索", current_search, previous_search),
        "keywordPromotion": _source_metric("关键词推广", current_keyword_promotion, previous_keyword_promotion),
        "freeTraffic": _source_metric(
            "免费流量汇总",
            current_free,
            previous_free,
            fallback="" if current_search else "freeTraffic",
        ),
        "paidTraffic": _source_metric("付费推广汇总", current_paid, previous_paid),
        "otherSources": other_sources,
        "currentFlow": _flow_summary_for_metrics(current),
        "compareFlow": _flow_summary_for_metrics(previous),
        "detail": detail,
        "dataQuality": quality,
    }


def tmall_sycm_item_360_metrics(args: dict) -> dict:
    args = _resolve_item_archives_args(args)
    item_id = str(args["itemId"])
    if _minute_compare_requested(args):
        response = _unsupported_minute_response("sycm_item_360_metrics", args, item_id=item_id)
        response.update({
            "freeSearch": _source_metric("免费搜索", None, None),
            "keywordPromotion": _source_metric("关键词推广", None, None),
            "freeTraffic": _source_metric("免费流量汇总", None, None),
            "paidTraffic": _source_metric("付费推广汇总", None, None),
            "otherSources": [],
            "currentFlow": {},
            "compareFlow": {},
            "detail": None,
            "dataQuality": response["dataQuality"],
        })
        return response
    date_range, date_type, period_quality, used_latest_fallback = _resolve_flow_period(args, item_id)
    requested_compare_range = args.get("compareDateRange")
    if (
        used_latest_fallback
        and requested_compare_range
        and _date_range_end_day(requested_compare_range) >= _date_range_end_day(date_range)
    ):
        compare_date_range = _previous_equal_date_range(date_range)
        period_quality.append(
            "商品360流量来源对比周期已随已发布日期调整："
            f"请求{requested_compare_range}，实际使用{compare_date_range}"
        )
    else:
        compare_date_range = requested_compare_range or _previous_equal_date_range(date_range)
    compare_date_type = args.get("compareDateType") or date_type
    include_detail = bool(args.get("includeDetail", True))
    data_quality: list[str] = list(period_quality)

    current = tmall_sycm_item_flow_sources({
        "itemId": item_id,
        "dateRange": date_range,
        "dateType": date_type,
    })
    if date_type == "today" or compare_date_type == "today":
        previous = {
            "source": "sycm_item_flow_sources",
            "itemId": item_id,
            "effectiveDateRange": compare_date_range,
            "dateType": compare_date_type,
            "rootCount": 0,
            "nodeCount": 0,
            "roots": [],
            "nodes": [],
        }
        data_quality.append("商品360实时来源接口不支持历史日期回放，对比周期不输出；环比不可判定")
    else:
        try:
            previous = tmall_sycm_item_flow_sources({
                "itemId": item_id,
                "dateRange": compare_date_range,
                "dateType": compare_date_type,
            })
        except Exception as exc:  # noqa: BLE001
            previous = {
                "source": "sycm_item_flow_sources",
                "itemId": item_id,
                "effectiveDateRange": compare_date_range,
                "dateType": compare_date_type,
                "rootCount": 0,
                "nodeCount": 0,
                "roots": [],
                "nodes": [],
                "error": str(exc),
            }
            data_quality.append(f"对比周期来源树采集失败，已优先使用当前节点cycleCrc环比：{exc}")

    return _sycm_item_360_metrics_from_flows(
        item_id=item_id,
        date_range=date_range,
        date_type=date_type,
        compare_date_range=compare_date_range,
        compare_date_type=compare_date_type,
        current=current,
        previous=previous,
        include_detail=include_detail,
        input_url=args.get("_itemArchivesInputUrl") or "",
        data_quality=data_quality + (args.get("_itemArchivesWarnings") or []),
    )


def _metric_status(value: Any, *, lower_is_bad: bool = True, warn_abs: float = 0.01) -> str:
    if value is None:
        return "不可判定"
    number = _num(value)
    if abs(number) <= warn_abs:
        return "持平"
    if lower_is_bad:
        return "下降" if number < 0 else "上升"
    return "升高" if number > 0 else "下降"


def _required_flow_basis_from_metrics(metrics: dict) -> dict:
    free = metrics.get("freeSearch") if isinstance(metrics.get("freeSearch"), dict) else {}
    keyword = metrics.get("keywordPromotion") if isinstance(metrics.get("keywordPromotion"), dict) else {}
    return {
        "source": "tmall_item_flow_required_metrics",
        "itemId": metrics.get("itemId"),
        "dateRange": metrics.get("dateRange"),
        "compareDateRange": metrics.get("compareDateRange"),
        "dateType": metrics.get("dateType"),
        "compareDateType": metrics.get("compareDateType"),
        "granularity": metrics.get("granularity") or "day",
        "asOfTime": metrics.get("asOfTime"),
        "updateTime": metrics.get("updateTime"),
        "supported": metrics.get("supported", True),
        "supportsMinuteCompare": False,
        "item_archives_url": metrics.get("itemArchivesUrl") or "",
        "compare_item_archives_url": metrics.get("compareItemArchivesUrl") or "",
        "input_url": metrics.get("inputUrl") or "",
        "free_flow_basis": {
            "source_path": free.get("sourcePath"),
            "search_visitor": free.get("visitor"),
            "search_visitor_compare": free.get("compareVisitor"),
            "search_visitor_change_pct": free.get("visitorChangePct"),
            "search_visitor_status": _metric_status(free.get("visitorChangePct")),
            "search_conversion_rate": free.get("conversionRate"),
            "search_conversion_compare_rate": free.get("compareConversionRate"),
            "search_conversion_change_pct": free.get("conversionChangePct"),
            "search_conversion_status": _metric_status(free.get("conversionChangePct")),
            "search_pay_buyer_count": free.get("payBuyerCount"),
            "search_pay_buyer_compare": free.get("comparePayBuyerCount"),
            "search_pay_buyer_change_pct": free.get("payBuyerChangePct"),
            "search_pay_amount": free.get("payAmount"),
            "search_pay_amount_compare": free.get("comparePayAmount"),
            "search_pay_amount_change_pct": free.get("payAmountChangePct"),
            "found": bool(free.get("found")) and bool(free.get("compareFound")),
            "current_found": bool(free.get("found")),
            "compare_found": bool(free.get("compareFound")),
            "compare_source": free.get("compareSource") or "",
        },
        "paid_flow_basis": {
            "source_path": keyword.get("sourcePath"),
            "keyword_promotion_visitor": keyword.get("visitor"),
            "keyword_promotion_visitor_compare": keyword.get("compareVisitor"),
            "keyword_promotion_visitor_change_pct": keyword.get("visitorChangePct"),
            "keyword_promotion_visitor_status": _metric_status(keyword.get("visitorChangePct")),
            "keyword_promotion_conversion_rate": keyword.get("conversionRate"),
            "keyword_promotion_conversion_compare_rate": keyword.get("compareConversionRate"),
            "keyword_promotion_conversion_change_pct": keyword.get("conversionChangePct"),
            "keyword_promotion_conversion_status": _metric_status(keyword.get("conversionChangePct")),
            "keyword_promotion_pay_buyer_count": keyword.get("payBuyerCount"),
            "keyword_promotion_pay_buyer_compare": keyword.get("comparePayBuyerCount"),
            "keyword_promotion_pay_buyer_change_pct": keyword.get("payBuyerChangePct"),
            "keyword_promotion_pay_amount": keyword.get("payAmount"),
            "keyword_promotion_pay_amount_compare": keyword.get("comparePayAmount"),
            "keyword_promotion_pay_amount_change_pct": keyword.get("payAmountChangePct"),
            "found": bool(keyword.get("found")) and bool(keyword.get("compareFound")),
            "current_found": bool(keyword.get("found")),
            "compare_found": bool(keyword.get("compareFound")),
            "compare_source": keyword.get("compareSource") or "",
        },
        "data_quality": metrics.get("dataQuality") or [],
        "raw_metrics": metrics,
    }


def tmall_item_flow_required_metrics(args: dict) -> dict:
    args = _resolve_item_archives_args(args)
    if _minute_compare_requested(args):
        response = _unsupported_minute_response("tmall_item_flow_required_metrics", args, item_id=str(args["itemId"]))
        response.update({
            "free_flow_basis": {"found": False},
            "paid_flow_basis": {"found": False},
            "data_quality": response["dataQuality"],
            "raw_metrics": response,
        })
        return response
    metrics = tmall_sycm_item_360_metrics({
        "itemId": args["itemId"],
        "dateRange": args.get("dateRange") or _today_range(),
        "dateType": _default_flow_date_type(args),
        "compareDateRange": args.get("compareDateRange") or _previous_equal_date_range(
            args.get("dateRange") or _today_range()
        ),
        "compareDateType": args.get("compareDateType") or _default_flow_date_type(args),
        "includeDetail": False,
        "itemArchivesUrl": args.get("_itemArchivesInputUrl") or "",
    })
    return _required_flow_basis_from_metrics(metrics)


def tmall_item_flow_required_metrics_batch(args: dict) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    item_ids = _item_ids_arg(args.get("itemIds"))
    limit = max(1, min(int(args.get("limit") or len(item_ids) or 200), 500))
    selected_ids = item_ids[:limit]
    workers = max(1, min(int(args.get("workers") or os.environ.get("TMALL_MCP_BATCH_WORKERS") or 4), 8, len(selected_ids) or 1))
    rows: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    request_base = {
        "dateRange": args.get("dateRange") or _last_complete_day_range(),
        "dateType": args.get("dateType") or "day",
        "compareDateRange": args.get("compareDateRange") or _previous_equal_date_range(
            args.get("dateRange") or _last_complete_day_range()
        ),
        "compareDateType": args.get("compareDateType") or args.get("dateType") or "day",
    }
    child_context_args = _inherited_child_tool_args(args)
    def _collect_one(item_id: str) -> tuple[str, dict | None, str | None]:
        try:
            result = _run_child_tool("tmall_item_flow_required_metrics", tmall_item_flow_required_metrics, {
                **child_context_args,
                "itemId": item_id,
                "dateRange": request_base["dateRange"],
                "dateType": request_base["dateType"],
                "compareDateRange": request_base["compareDateRange"],
                "compareDateType": request_base["compareDateType"],
            })
        except Exception as exc:  # noqa: BLE001
            return item_id, None, str(exc)
        return item_id, result, None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tmall-flow-batch") as executor:
        future_map = {executor.submit(_collect_one, item_id): item_id for item_id in selected_ids}
        for future in as_completed(future_map):
            item_id, result, error = future.result()
            if result is not None:
                rows[item_id] = result
                continue
            message = error or "unknown error"
            rows[item_id] = {
                "source": "tmall_item_flow_required_metrics",
                "itemId": item_id,
                "dateRange": request_base["dateRange"],
                "compareDateRange": request_base["compareDateRange"],
                "dateType": request_base["dateType"],
                "compareDateType": request_base["compareDateType"],
                "granularity": "day",
                "error": message,
                "free_flow_basis": {"found": False},
                "paid_flow_basis": {"found": False},
                "data_quality": [message],
            }
            errors.append({"itemId": item_id, "error": message})
    return {
        "source": "tmall_item_flow_required_metrics_batch",
        "dateRange": request_base["dateRange"],
        "compareDateRange": request_base["compareDateRange"],
        "dateType": request_base["dateType"],
        "compareDateType": request_base["compareDateType"],
        "requestedCount": len(item_ids),
        "rowsCollected": len(rows),
        "results": rows,
        "errors": errors,
        "workers": workers,
    }


def _market_page(date_range: str, date_type: str, cate_id: str, parent_cate_id: str = "", cate_flag: int | str = 0) -> str:
    return SYCM_MARKET_RANK_PAGE.format(
        date_range_q=_q_date_range(date_range),
        date_type=date_type,
        parent_cate_id=parent_cate_id,
        cate_id=cate_id,
        cate_flag=cate_flag,
    )


def _stamp_ms() -> int:
    return int(time.time() * 1000)


def _normalize_market_category(row: Any) -> dict:
    if isinstance(row, list):
        return {
            "parentCateId": str(row[0]) if len(row) > 0 else "",
            "cateId": str(row[1]) if len(row) > 1 else "",
            "name": row[2] if len(row) > 2 else "",
            "level": row[3] if len(row) > 3 else None,
            "marketVersion": row[4] if len(row) > 4 else None,
            "isLeaf": row[5] if len(row) > 5 else None,
            "rootCateId": str(row[6]) if len(row) > 6 else "",
            "rootCateName": row[7] if len(row) > 7 else "",
        }
    if isinstance(row, dict):
        return {
            "parentCateId": str(row.get("parentCateId") or row.get("parentId") or ""),
            "cateId": str(row.get("cateId") or row.get("id") or ""),
            "name": row.get("cateName") or row.get("name") or "",
            "level": row.get("level"),
            "marketVersion": row.get("marketVersion"),
            "isLeaf": row.get("isLeaf"),
            "rootCateId": str(row.get("rootCateId") or ""),
            "rootCateName": row.get("rootCateName") or "",
        }
    return {}


def _market_categories(page_url: str) -> list[dict]:
    url = f"{SYCM_MARKET_CATE_API}?marketVersion=free&_={_stamp_ms()}"
    data = _sycm_api_body(_fetch_json(url, page_url=page_url))
    rows = data if isinstance(data, list) else []
    return [c for c in (_normalize_market_category(row) for row in rows) if c.get("cateId")]


def _default_market_cate(categories: list[dict]) -> str:
    for row in categories:
        if str(row.get("isLeaf") or "").upper() == "Y":
            return str(row.get("cateId") or "")
    return str((categories[-1] if categories else {}).get("cateId") or "")


def _market_category_from_args(args: dict) -> dict:
    preset_key = str(args.get("categoryPreset") or args.get("category") or "").strip()
    preset = MARKET_CATEGORY_PRESETS.get(preset_key, {}) if preset_key else {}
    return {
        "label": preset.get("label") or str(args.get("categoryLabel") or ""),
        "parentCateId": str(args.get("parentCateId") or preset.get("parentCateId") or ""),
        "cateId": str(args.get("cateId") or preset.get("cateId") or ""),
        "cateFlag": int(args.get("cateFlag", preset.get("cateFlag", 0)) or 0),
        "preset": preset_key if preset else "",
    }


def _market_update_dates(page_url: str) -> dict:
    url = (
        "https://sycm.taobao.com/oneauth/api/commDateByLocation.json?"
        "locationCodes=cate_mkt_rank_itm,cate_mkt_rank_slr,cate_mkt_rank_ct,"
        f"slr_ind_rfd_itm,cate_mkt_visit_sns_rank_itm&marketVersion=free&_={_stamp_ms()}"
    )
    try:
        data = _sycm_api_body(_fetch_json(url, page_url=page_url))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _market_price_segments(date_range: str, date_type: str, cate_id: str, seller_type: int, page_url: str) -> list[dict]:
    url = (
        f"{SYCM_MARKET_PRICE_SEG_API}?dateRange={date_range}&dateType={date_type}"
        f"&cateId={cate_id}&sellerType={seller_type}&marketVersion=free&_={_stamp_ms()}"
    )
    try:
        data = _sycm_api_body(_fetch_json(url, page_url=page_url))
    except Exception:
        return []
    rows = data if isinstance(data, list) else []
    return [
        {
            "priceSegId": row.get("priceSegId"),
            "priceSegName": row.get("priceSegName"),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _normalize_market_rank_row(row: dict, rank: int) -> dict:
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    item_id = item.get("itemId") or _val(row, "itemId")
    normalized = {
        "rank": rank,
        "itemId": str(item_id or ""),
        "title": item.get("title") or item.get("itemTitle") or "",
        "detailUrl": _normalize_tmall_url(item.get("detailUrl"), item_id),
        "pictUrl": item.get("pictUrl"),
        "shopTitle": item.get("shopTitle") or item.get("sellerNick"),
        "price": _val(row, "price"),
        "payByrCnt": _val(row, "payByrCnt"),
        "uv": _val(row, "uv"),
        "payAmt": _val(row, "payAmt"),
        "payRate": _val(row, "payRate"),
        "tradeIndex": _val(row, "tradeIndex"),
        "itmPv": _val(row, "itmPv"),
        "itemCartCnt": _val(row, "itemCartCnt"),
        "cltItmCnt": _val(row, "cltItmCnt"),
    }
    metrics: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"item", "itemId"}:
            continue
        if isinstance(value, dict) and "value" in value:
            metrics[key] = {
                "value": value.get("value"),
                "cycleCrc": value.get("cycleCrc"),
                "rank": value.get("rank"),
            }
    normalized["metrics"] = metrics
    return normalized


def _compact_market_rank_row(row: dict) -> dict:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    core_keyword = metrics.get("coreKeyWord") if isinstance(metrics.get("coreKeyWord"), dict) else {}
    is_self = metrics.get("isSelfItem") if isinstance(metrics.get("isSelfItem"), dict) else {}
    cate_rank = metrics.get("cateRankId") if isinstance(metrics.get("cateRankId"), dict) else {}
    return {
        "rank": row.get("rank") or cate_rank.get("value"),
        "itemId": row.get("itemId") or row.get("item_id") or "",
        "title": row.get("title") or "",
        "shopTitle": row.get("shopTitle"),
        "price": row.get("price"),
        "payByrCnt": row.get("payByrCnt"),
        "uv": row.get("uv"),
        "payAmt": row.get("payAmt"),
        "payRate": row.get("payRate"),
        "tradeIndex": row.get("tradeIndex"),
        "coreKeywords": core_keyword.get("value"),
        "isSelfItem": is_self.get("value"),
        "cateRankId": cate_rank.get("value") or row.get("rank"),
    }


def _market_rank_rows_for_output(rows: list[dict], args: dict) -> list[dict]:
    if args.get("includeDetailRows") is True or args.get("fullRows") is True:
        return rows
    return [_compact_market_rank_row(row) for row in rows]


def _market_rank_cache_dir() -> Path:
    raw = os.environ.get("TMALL_MCP_MARKET_RANK_CACHE_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".skillforge_cache" / "tmall-link-decline-analysis-v2" / "market_rank"


def _market_rank_cache_key(parts: dict[str, Any]) -> str:
    compact = {
        key: parts.get(key)
        for key in (
            "dateRange", "dateType", "cateId", "parentCateId", "cateFlag",
            "rankType", "sellerType", "priceSeg", "minPrice", "maxPrice",
            "keyword", "indexCode", "limit",
        )
    }
    text = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _market_rank_cache_path(cache_key: str) -> Path:
    return _market_rank_cache_dir() / f"{cache_key}.json"


def _save_market_rank_cache(cache_key: str, result: dict) -> None:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    if not rows or result.get("error"):
        return
    payload = dict(result)
    payload["_cache"] = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cache_key": cache_key,
    }
    try:
        cache_dir = _market_rank_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        _market_rank_cache_path(cache_key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def _load_market_rank_cache(cache_key: str) -> dict:
    path = _market_rank_cache_path(cache_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _challenge_url_from_text(text: str) -> str:
    marker = "https://"
    start = text.find(marker)
    if start < 0:
        return ""
    tail = text[start:]
    for sep in (" ", "\n", "\t", "\"", "'"):
        if sep in tail:
            tail = tail.split(sep, 1)[0]
    return tail


def _market_rank_with_cache_fallback(result: dict, cache_key: str, page_url: str) -> dict:
    error_kind = str(result.get("error_kind") or "")
    if error_kind != "html_challenge":
        return result
    error_text = str(result.get("error") or result.get("message") or "")
    result["verificationRequired"] = True
    result["verificationPageUrl"] = page_url
    result["verificationUrl"] = _challenge_url_from_text(error_text)
    result["retryable"] = True
    result["recovery"] = "在已连接的浏览器里打开 verificationPageUrl 完成淘宝安全校验后重试；系统会优先使用同参数最近一次成功缓存兜底。"
    cached = _load_market_rank_cache(cache_key)
    cached_rows = cached.get("rows") if isinstance(cached.get("rows"), list) else []
    if not cached_rows:
        return result
    cached = dict(cached)
    cached["source"] = str(cached.get("source") or "sycm_market_rank") + "_cache"
    cached["cache_status"] = "stale_on_security_challenge"
    cached["current_error"] = error_text
    cached["current_error_kind"] = error_kind
    cached["verificationRequired"] = True
    cached["verificationPageUrl"] = page_url
    cached["verificationUrl"] = result.get("verificationUrl") or ""
    cached["retryable"] = True
    cached["dataQuality"] = [
        "当前市场排行 API 触发淘宝安全校验，已返回同参数最近一次成功缓存；完成浏览器安全校验后可重试刷新。",
    ]
    return cached


def _market_rank_error_kind(*, code: Any = None, message: str = "", error: str = "") -> str:
    code_text = str(code or "").strip()
    detail = f"{message} {error}".lower()
    if code_text == "600091" or "no permission" in detail:
        return "permission_denied"
    if (
        "html challenge" in detail
        or "text/html" in detail
        or "安全校验" in detail
        or "challenge" in detail
        or "fail_sys_user_validate" in detail
        or "rgv587" in detail
        or "/punish" in detail
        or "x5sec" in detail
    ):
        return "html_challenge"
    if code_text:
        return "business_error"
    return "fetch_failed" if detail else ""


def _market_rank_browser_fallback(
    *, date_range, date_type, cate_id, parent_cate_id, cate_flag,
    rank_type, seller_type, price_seg, min_price_q, max_price_q, keyword, index_code,
    limit, page_url, category_label, category_preset, categories, args,
) -> dict:
    """直连 API 无权限时，通过浏览器页面上下文调用市场排行 API。"""
    rows: list[dict] = []
    record_count = None
    page = 1
    last_error = ""
    last_code = None
    last_message = ""
    last_transport = "browser_fetch"
    rank_api = SYCM_MARKET_ITEM_LIVE_RANK_API if str(date_type).lower() == "today" else SYCM_MARKET_ITEM_RANK_API
    while len(rows) < limit:
        page_size = min(50, max(10, limit - len(rows)))
        url = (
            f"{rank_api}?dateRange={date_range}&dateType={date_type}"
            f"&pageSize={page_size}&page={page}&cateId={cate_id}&rankType={rank_type}"
            f"&minPrice={quote(min_price_q, safe='')}&maxPrice={quote(max_price_q, safe='')}&priceSeg={quote(price_seg, safe='')}"
            f"&sellerType={seller_type}&keyWord={quote(keyword, safe='')}"
            f"&parentCateId={quote(parent_cate_id, safe='')}&cateFlag={cate_flag}"
            f"&indexCode={quote(index_code, safe='')}&marketVersion=free&_={_stamp_ms()}"
        )
        raw = _fetch_json(url, method="GET", page_url=page_url)
        proof = raw.get("proof") if isinstance(raw.get("proof"), dict) else {}
        if proof.get("transport"):
            last_transport = str(proof.get("transport"))
        if not raw.get("success"):
            last_error = str(raw.get("error") or "browser fetch failed")
            break
        outer = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        payload = outer.get("data") if isinstance(outer.get("data"), dict) else outer
        if isinstance(payload, dict) and payload.get("code") not in (0, "0", None):
            last_code = payload.get("code")
            last_message = str(payload.get("message") or "")
            last_error = last_message
            break  # 浏览器端也权限不足
        inner = payload.get("data") if isinstance(payload, dict) else {}
        page_rows = inner.get("data") if isinstance(inner.get("data"), list) else []
        if record_count is None:
            record_count = inner.get("recordCount") or inner.get("total") or inner.get("count")
        if not page_rows:
            break
        for row in page_rows:
            if len(rows) >= limit:
                break
            if isinstance(row, dict):
                rows.append(_normalize_market_rank_row(row, len(rows) + 1))
        if record_count and len(rows) >= min(limit, int(record_count)):
            break
        page += 1
    result = {
        "source": "sycm_market_rank_browser",
        "dateRange": date_range,
        "dateType": date_type,
        "categoryPreset": category_preset,
        "categoryLabel": category_label,
        "parentCateId": parent_cate_id,
        "cateId": cate_id,
        "cateFlag": cate_flag,
        "category": (
            next((c for c in categories if str(c.get("cateId")) == cate_id), None)
            or {"parentCateId": parent_cate_id, "cateId": cate_id, "name": category_label}
        ),
        "rankType": rank_type,
        "sellerType": seller_type,
        "minPrice": min_price_q,
        "maxPrice": max_price_q,
        "priceSeg": price_seg,
        "priceRange": f"{min_price_q}-{max_price_q}" if min_price_q or max_price_q else "",
        "recordCount": record_count,
        "rowsCollected": len(rows),
        "rows": _market_rank_rows_for_output(rows, args),
    }
    if last_code not in (None, "", 0, "0"):
        result["code"] = last_code
        result["message"] = last_message
        result["error_kind"] = _market_rank_error_kind(code=last_code, message=last_message, error=last_error)
    if last_error:
        result["error"] = last_error
        result["error_kind"] = result.get("error_kind") or _market_rank_error_kind(
            code=last_code,
            message=last_message,
            error=last_error,
        )
    if last_transport:
        result["transport"] = last_transport
    return result


def tmall_sycm_market_rank(args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 10), 300))
    date_range = args.get("dateRange") or _recent_complete_days_range(7)
    date_type = args.get("dateType") or "recent7"
    rank_type = args.get("rankType") or "gmv"
    seller_type = int(args.get("sellerType", -1))
    keyword = str(args.get("keyword") or "")
    price_seg = str(args.get("priceSeg") or "")
    min_price = args.get("minPrice")
    max_price = args.get("maxPrice")
    min_price_q = "" if min_price in (None, "") else str(min_price)
    max_price_q = "" if max_price in (None, "") else str(max_price)
    index_code = args.get("indexCode") or "payByrCnt,uv"
    selected_category = _market_category_from_args(args)
    parent_cate_id = selected_category["parentCateId"]
    cate_flag = selected_category["cateFlag"]

    placeholder_page = _market_page(date_range, date_type, selected_category["cateId"], parent_cate_id, cate_flag)
    categories = _market_categories(placeholder_page)
    cate_id = selected_category["cateId"] or _default_market_cate(categories)
    if not cate_id:
        raise RuntimeError("未能从生意参谋市场排行获取默认类目，请显式传 cateId")
    page_url = _market_page(date_range, date_type, cate_id, parent_cate_id, cate_flag)
    price_segments = _market_price_segments(date_range, date_type, cate_id, seller_type, page_url)
    update_dates = _market_update_dates(page_url)
    cache_key = _market_rank_cache_key({
        "dateRange": date_range,
        "dateType": date_type,
        "cateId": cate_id,
        "parentCateId": parent_cate_id,
        "cateFlag": cate_flag,
        "rankType": rank_type,
        "sellerType": seller_type,
        "priceSeg": price_seg,
        "minPrice": min_price_q,
        "maxPrice": max_price_q,
        "keyword": keyword,
        "indexCode": index_code,
        "limit": limit,
    })

    rows: list[dict] = []
    record_count = None
    page = 1
    rank_api = SYCM_MARKET_ITEM_LIVE_RANK_API if str(date_type).lower() == "today" else SYCM_MARKET_ITEM_RANK_API
    while len(rows) < limit:
        page_size = min(50, max(10, limit - len(rows)))
        url = (
            f"{rank_api}?dateRange={date_range}&dateType={date_type}"
            f"&pageSize={page_size}&page={page}&cateId={cate_id}&rankType={rank_type}"
            f"&minPrice={quote(min_price_q, safe='')}&maxPrice={quote(max_price_q, safe='')}&priceSeg={quote(price_seg, safe='')}"
            f"&sellerType={seller_type}&keyWord={quote(keyword, safe='')}"
            f"&parentCateId={quote(parent_cate_id, safe='')}&cateFlag={cate_flag}"
            f"&indexCode={quote(index_code, safe='')}&marketVersion=free&_={_stamp_ms()}"
        )
        try:
            payload = _sycm_api_body(_fetch_json(url, page_url=page_url))
        except RuntimeError as exc:
            error_text = str(exc)
            # 600091 No permission — 直连 API 无权限，降级到浏览器页面采集
            if "600091" in error_text or "No permission" in error_text:
                fallback_result = _market_rank_browser_fallback(
                    date_range=date_range, date_type=date_type,
                    cate_id=cate_id, parent_cate_id=parent_cate_id, cate_flag=cate_flag,
                    rank_type=rank_type, seller_type=seller_type,
                    price_seg=price_seg, min_price_q=min_price_q, max_price_q=max_price_q,
                    keyword=keyword, index_code=index_code,
                    limit=limit, page_url=page_url,
                    category_label=selected_category["label"],
                    category_preset=selected_category["preset"],
                    categories=categories,
                    args=args,
                )
                _save_market_rank_cache(cache_key, fallback_result)
                return _market_rank_with_cache_fallback(fallback_result, cache_key, page_url)
            failed_result = {
                "source": "sycm_market_rank",
                "dateRange": date_range,
                "dateType": date_type,
                "categoryPreset": selected_category["preset"],
                "categoryLabel": selected_category["label"],
                "parentCateId": parent_cate_id,
                "cateId": cate_id,
                "cateFlag": cate_flag,
                "category": (
                    next((c for c in categories if str(c.get("cateId")) == cate_id), None)
                    or {
                        "parentCateId": parent_cate_id,
                        "cateId": cate_id,
                        "name": selected_category["label"],
                    }
                ),
                "rankType": rank_type,
                "sellerType": seller_type,
                "minPrice": min_price_q,
                "maxPrice": max_price_q,
                "priceRange": f"{min_price_q}-{max_price_q}" if min_price_q or max_price_q else "",
                "recordCount": None,
                "rowsCollected": 0,
                "updateDates": update_dates,
                "priceSegments": price_segments,
                "categoriesSample": categories[:5],
                "rows": [],
                "error": error_text,
                "error_kind": _market_rank_error_kind(error=error_text),
                "transport": "direct_with_chrome_cookies",
            }
            return _market_rank_with_cache_fallback(failed_result, cache_key, page_url)
        data = payload if isinstance(payload, dict) else {}
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        page_rows = inner.get("data") if isinstance(inner.get("data"), list) else []
        if record_count is None:
            record_count = inner.get("recordCount") or inner.get("total") or inner.get("count")
        if not page_rows:
            break
        for row in page_rows:
            if len(rows) >= limit:
                break
            if isinstance(row, dict):
                rows.append(_normalize_market_rank_row(row, len(rows) + 1))
        if record_count and len(rows) >= min(limit, int(record_count)):
            break
        page += 1

    result = {
        "source": "sycm_market_rank",
        "dateRange": date_range,
        "dateType": date_type,
        "categoryPreset": selected_category["preset"],
        "categoryLabel": selected_category["label"],
        "parentCateId": parent_cate_id,
        "cateId": cate_id,
        "cateFlag": cate_flag,
        "category": (
            next((c for c in categories if str(c.get("cateId")) == cate_id), None)
            or {
                "parentCateId": parent_cate_id,
                "cateId": cate_id,
                "name": selected_category["label"],
            }
        ),
        "rankType": rank_type,
        "sellerType": seller_type,
        "minPrice": min_price_q,
        "maxPrice": max_price_q,
        "priceRange": f"{min_price_q}-{max_price_q}" if min_price_q or max_price_q else "",
        "recordCount": record_count,
        "rowsCollected": len(rows),
        "updateDates": update_dates,
        "priceSegments": price_segments,
        "categoriesSample": categories[:5],
        "rows": _market_rank_rows_for_output(rows, args),
    }
    _save_market_rank_cache(cache_key, result)
    return result


def _normalize_activity(row: dict) -> dict:
    return {
        "activityId": row.get("activityId") or row.get("id"),
        "activityName": row.get("activityName") or row.get("name"),
        "activityStart": row.get("activityStart"),
        "activityEnd": row.get("activityEnd"),
        "preheatStart": row.get("preheatStart"),
        "preheatEnd": row.get("preheatEnd"),
        "activityStatus": row.get("activityStatus"),
        "activityType": row.get("activityType"),
        "activityFlag": row.get("activityFlag"),
        "activitySubFlag": row.get("activitySubFlag"),
        "activityUv": row.get("activityUv"),
        "activityPayamt": row.get("activityPayamt"),
        "addCartItemCnt": row.get("addCartItemCnt"),
        "itemQtyPayamtWeight": row.get("itemQtyPayamtWeight"),
        "rawKeys": list(row.keys())[:30],
    }


def _item_ids_arg(value: Any) -> list[str]:
    if isinstance(value, str):
        return [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def tmall_sycm_activity_price(args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 20), 200))
    activity_status = int(args.get("activityStatus", -1))
    activity_type = int(args.get("activityType", -1))
    activity_start_type = args.get("activityStartType") or "recentHalfYear"
    item_ids = _item_ids_arg(args.get("itemIds"))
    page_url = "https://sycm.taobao.com/portal/home.htm"
    errors: dict[str, str] = {}
    raw: dict[str, Any] = {}

    activity_url = (
        f"{SYCM_ACTIVITY_LIST_API}?activityStatus={activity_status}&activityType={activity_type}"
        f"&activityStartType={activity_start_type}&orderBy=activityStart&order=desc&page=1"
        f"&pageSize={limit}&activitySeason=-1&activityCycle=-1&_={_stamp_ms()}"
    )
    try:
        data = _sycm_api_body(_fetch_json(activity_url, page_url=page_url))
        raw["activityList"] = data
    except Exception as exc:  # noqa: BLE001
        errors["activityList"] = str(exc)

    try:
        data = _sycm_api_body(_fetch_json(
            f"https://sycm.taobao.com/portal/gmv/rank/activity/info.json?marketVersion=none&_={_stamp_ms()}",
            page_url=page_url,
        ))
        raw["activityRankInfo"] = data
    except Exception as exc:  # noqa: BLE001
        errors["activityRankInfo"] = str(exc)

    try:
        data = _sycm_api_body(_fetch_json(
            f"https://sycm.taobao.com/portal/live/alarm/high/add/price.json?sceneType=popup&_={_stamp_ms()}",
            page_url=page_url,
        ))
        raw["highAddPriceAlarm"] = data
    except Exception as exc:  # noqa: BLE001
        errors["highAddPriceAlarm"] = str(exc)

    if item_ids:
        try:
            data = _sycm_api_body(_fetch_json(
                "https://sycm.taobao.com/portal/risk/priceUpControl/queryItemWarnInfo.json?"
                f"itemIds={quote(','.join(item_ids), safe=',')}&_={_stamp_ms()}",
                page_url=page_url,
            ))
            raw["itemPriceWarnings"] = data
        except Exception as exc:  # noqa: BLE001
            errors["itemPriceWarnings"] = str(exc)

    activity_outer = raw.get("activityList") if isinstance(raw.get("activityList"), dict) else {}
    activity_data = activity_outer.get("data") if isinstance(activity_outer.get("data"), dict) else activity_outer
    activity_rows = activity_data.get("data") if isinstance(activity_data.get("data"), list) else []

    item_warning_rows: list[Any] = []
    warnings_raw = raw.get("itemPriceWarnings")
    if isinstance(warnings_raw, list):
        item_warning_rows = warnings_raw
    elif isinstance(warnings_raw, dict):
        for key in ("data", "list", "items", "warnList"):
            if isinstance(warnings_raw.get(key), list):
                item_warning_rows = warnings_raw[key]
                break
        if not item_warning_rows:
            item_warning_rows = [warnings_raw]

    return {
        "source": "sycm_activity_price",
        "itemIds": item_ids,
        "activityCount": activity_data.get("recordCount") or activity_data.get("total") or len(activity_rows),
        "activitiesCollected": len(activity_rows),
        "activities": [_normalize_activity(row) for row in activity_rows[:limit] if isinstance(row, dict)],
        "activityRankInfo": raw.get("activityRankInfo"),
        "highAddPriceAlarm": raw.get("highAddPriceAlarm"),
        "itemPriceWarnings": item_warning_rows,
        "errors": errors,
        "rawDataKeys": {key: list(value.keys())[:30] if isinstance(value, dict) else type(value).__name__ for key, value in raw.items()},
    }


def _item_warning_matches(row: dict, item_id: str) -> bool:
    return str(row.get("itemId") or row.get("item_id") or row.get("auctionId") or "") == str(item_id)


def _warning_text(row: dict) -> str:
    parts = [
        row.get("title"),
        row.get("desc"),
        row.get("message"),
        row.get("warningMsg"),
        row.get("warnReason"),
        row.get("riskReason"),
    ]
    return "；".join(str(x) for x in parts if x)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _ms_to_local_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        seconds = float(value) / 1000
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _seller_payload(fetch_result: dict) -> dict:
    payload = _api_payload(fetch_result)
    if isinstance(payload.get("ret"), list) and payload.get("ret"):
        raise RuntimeError("；".join(str(x) for x in payload.get("ret") or []))
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("structErrorMessage") or payload.get("msgInfo") or "商家中心接口返回失败"))
    return payload


def _seller_model(payload: dict) -> Any:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and ("model" in data or "success" in data):
        return data.get("model") if "model" in data else data
    if isinstance(payload.get("model"), (dict, list)):
        return payload.get("model")
    return data if data is not None else payload


def _collect_texts(value: Any, *, limit: int = 80) -> list[str]:
    texts: list[str] = []

    def visit(node: Any) -> None:
        if len(texts) >= limit:
            return
        if isinstance(node, dict):
            for key in ("text", "title", "description", "code", "name"):
                text = node.get(key)
                if isinstance(text, str) and text.strip():
                    texts.append(" ".join(text.split()))
            for key in ("contents", "children"):
                visit(node.get(key))
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    seen: set[str] = set()
    unique: list[str] = []
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique[:limit]


def _normalize_star_item(row: dict[str, Any]) -> dict[str, Any]:
    item_id = str(row.get("itemId") or row.get("item_id") or "")
    material_json = row.get("materialJsonMap") if isinstance(row.get("materialJsonMap"), dict) else {}
    search_flow = material_json.get("searchFlow") if isinstance(material_json.get("searchFlow"), dict) else {}
    convert_task = material_json.get("convertTask") if isinstance(material_json.get("convertTask"), dict) else {}
    star_protect = row.get("starProtectInfo") if isinstance(row.get("starProtectInfo"), dict) else {}
    column_texts = _collect_texts(row.get("columns"))
    suppress_texts = [
        text for text in column_texts
        if any(keyword in text for keyword in ("限流", "高价", "suppress", "惩罚", "流量预警"))
    ]
    return {
        "itemId": item_id,
        "title": row.get("title") or "",
        "price": row.get("price"),
        "recommendedPrice": row.get("maxDepreciateSkuRecommendPrice"),
        "discount": row.get("discount"),
        "saleNum": row.get("saleNum"),
        "sellerRealStarLevel": row.get("sellerRealStarLevel"),
        "sellerQuality": row.get("sellerQuality"),
        "canUpgrade": bool(row.get("canUpgrade")),
        "canGoStarProtect": bool(row.get("canGoStarProtect")),
        "starProtect": bool(star_protect.get("starProtect")),
        "searchFlowStatus": search_flow.get("status"),
        "searchFlowName": search_flow.get("name"),
        "searchFlowLevel": search_flow.get("itemFlowLevel"),
        "searchFlowLevelName": search_flow.get("itemFlowLevelName"),
        "convertStage": convert_task.get("convertStage"),
        "convertStageName": convert_task.get("convertStageName"),
        "sellerSearchLevelName": convert_task.get("sellerSearchLevelName"),
        "negativeChannels": row.get("negativeChannels") if isinstance(row.get("negativeChannels"), list) else [],
        "suppressSignals": suppress_texts[:10],
        "diagnosticTexts": column_texts[:30],
        "detailUrl": f"https://detail.tmall.com/item.htm?id={quote(item_id)}" if item_id else "",
    }


def _star_level_status(item: dict[str, Any] | None) -> str:
    if not item:
        return "未找到商品级价格力记录"
    star_raw = item.get("sellerRealStarLevel")
    star = _int_value(star_raw, -1)
    if star >= 5:
        return "五星价格力"
    if star >= 4:
        return f"{star}星价格力，可继续优化至五星"
    if star >= 0:
        return f"{star}星价格力，未达五星"
    return "价格力星级未知"


def tmall_seller_price_competitiveness_check(args: dict) -> dict:
    item_id = str(args["itemId"])
    limit = max(1, min(int(args.get("limit") or 100), 300))
    page_size = min(max(limit, 20), 50)
    errors: dict[str, str] = {}
    raw_summary: dict[str, Any] = {}
    star_items: list[dict[str, Any]] = []
    total = None

    try:
        payload = _seller_payload(_fetch_json(
            f"{SELLER_PRICE_CONTROL_API}?pageNum=1&pageSize=10",
            page_url=SELLER_PRICE_HOME_PAGE,
        ))
        price_control = _seller_model(payload)
        raw_summary["priceControl"] = price_control
    except Exception as exc:  # noqa: BLE001
        errors["priceControl"] = str(exc)
        price_control = {}

    for name, url in {
        "controlPlans": SELLER_CONTROL_PLANS_API,
        "coreData": SELLER_CORE_DATA_API,
        "strategySuggestions": SELLER_STRATEGY_SUGGESTIONS_API,
    }.items():
        try:
            payload = _seller_payload(_fetch_json(url, page_url=SELLER_PRICE_HOME_PAGE))
            raw_summary[name] = _seller_model(payload)
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)

    max_pages = max(1, (limit + page_size - 1) // page_size)
    for page in range(1, max_pages + 1):
        try:
            payload = _seller_payload(_fetch_json(
                f"{SELLER_STAR_ITEMS_API}?pageNum={page}&pageSize={page_size}",
                page_url=SELLER_PRICE_HOME_PAGE,
            ))
            model = _seller_model(payload)
            if isinstance(model, dict):
                if total is None:
                    total = model.get("total")
                rows = model.get("singleStarItemResponses")
                if isinstance(rows, list):
                    star_items.extend(_normalize_star_item(row) for row in rows if isinstance(row, dict))
            if any(row.get("itemId") == item_id for row in star_items):
                break
            if total is not None and len(star_items) >= min(limit, _int_value(total, limit)):
                break
        except Exception as exc:  # noqa: BLE001
            errors[f"starItemsPage{page}"] = str(exc)
            break

    matching = [row for row in star_items if row.get("itemId") == item_id]
    item = matching[0] if matching else None
    price_control_dict = price_control if isinstance(price_control, dict) else {}
    control_items = price_control_dict.get("itemList") if isinstance(price_control_dict.get("itemList"), list) else []
    suppress_matches = [
        row for row in control_items
        if isinstance(row, dict) and _item_warning_matches(row, item_id)
    ]
    suppress_count = sum(_int_value(price_control_dict.get(key)) for key in ("suppressNum", "totalSuppressWarnNum", "totalPunishCount"))
    if suppress_matches:
        high_price_status = "命中高价限流/价格预警"
    elif suppress_count > 0:
        high_price_status = "店铺存在高价限流/价格预警，当前商品未命中已采集列表"
    else:
        high_price_status = "未发现高价限流商品"

    if item:
        recommended = item.get("recommendedPrice") or "无建议价"
        current_price = item.get("price") or "未知"
        price_status = f"当前价 {current_price}，建议价 {recommended}"
        if item.get("canUpgrade"):
            price_status += "，有降价升星机会"
    else:
        price_status = "未找到商品级价格力记录，需页面搜索核对"
    if item:
        data_gap = ""
    elif total is not None and len(star_items) >= _int_value(total, len(star_items)):
        data_gap = "已采集完整商家中心价格力列表，当前商品未命中；可能不在价格竞争力页面覆盖范围、已下架或类目不适用，需页面搜索核对。"
    else:
        data_gap = "已确认商家中心价格力 API，但当前分页未命中该商品；需扩大分页或在价格竞争力页搜索核对。"

    return {
        "source": "tmall_seller_price_competitiveness_check",
        "itemId": item_id,
        "api_coverage": "seller_center_price_api" if star_items or price_control_dict else "error",
        "price_star_status": _star_level_status(item),
        "price_status": price_status,
        "high_price_limit_status": high_price_status,
        "is_high_price_limited": bool(suppress_matches),
        "price_warning_count": len(suppress_matches),
        "store_high_price_counts": {
            "preWarnNum": price_control_dict.get("preWarnNum"),
            "suppressNum": price_control_dict.get("suppressNum"),
            "totalSuppressWarnNum": price_control_dict.get("totalSuppressWarnNum"),
            "totalPunishCount": price_control_dict.get("totalPunishCount"),
            "extraTotalSuppressWarnNum": price_control_dict.get("extraTotalSuppressWarnNum"),
        },
        "item": item,
        "itemsCollected": len(star_items),
        "total": total,
        "strategySuggestions": raw_summary.get("strategySuggestions") if isinstance(raw_summary.get("strategySuggestions"), list) else [],
        "controlPlans": raw_summary.get("controlPlans") if isinstance(raw_summary.get("controlPlans"), list) else [],
        "coreData": raw_summary.get("coreData"),
        "errors": errors,
        "data_gap": data_gap,
        "source_links": [
            {"label": "天猫商家中心-价格竞争力", "url": SELLER_PRICE_HOME_PAGE},
            {"label": "五星价格力/高价限流商品", "url": SELLER_PRICE_HOME_PAGE},
        ],
    }


def _normalize_seller_activity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "activityId": str(row.get("activityId") or ""),
        "name": row.get("name") or row.get("activityName") or "",
        "toolName": row.get("toolName") or "",
        "bizToolCode": row.get("bizToolCode") or "",
        "status": row.get("status"),
        "rawStatus": row.get("rawStatus"),
        "statusDesc": row.get("statusDesc") or "",
        "startTime": _ms_to_local_text(row.get("startTime")),
        "endTime": _ms_to_local_text(row.get("endTime")),
        "url": row.get("url") or "",
    }


def tmall_seller_marketing_activity_list(args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 50), 200))
    item_id = str(args.get("itemId") or "")
    page_size = min(limit, 100)
    rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    total_count = None
    try:
        payload = _seller_payload(_fetch_json(
            SELLER_MIX_ACTIVITY_API,
            method="POST",
            headers={"content-type": "application/json;charset=UTF-8"},
            body={"curPage": 1, "pageSize": page_size},
            page_url=SELLER_MARKETING_TOOLS_PAGE,
        ))
        model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
        total_count = model.get("totalCount")
        data_list = model.get("dataList") if isinstance(model.get("dataList"), list) else []
        rows = [_normalize_seller_activity(row) for row in data_list if isinstance(row, dict)]
    except Exception as exc:  # noqa: BLE001
        errors["getMixActivityList"] = str(exc)

    active_rows = [row for row in rows if "生效" in str(row.get("statusDesc")) or row.get("status") == 2]
    paused_rows = [row for row in rows if "暂停" in str(row.get("statusDesc")) or row.get("status") == 0]
    ended_rows = [row for row in rows if "结束" in str(row.get("statusDesc")) or row.get("status") == 3]
    if active_rows:
        activity_status = f"全店有 {len(active_rows)} 个生效活动"
    elif rows:
        activity_status = "未发现生效活动"
    else:
        activity_status = "活动列表未采集"
    data_gap = "该接口是活动级列表，不返回商品 ID 维度参与明细；商品是否掉线还需对应活动详情 API。" if item_id else ""
    return {
        "source": "tmall_seller_marketing_activity_list",
        "itemId": item_id,
        "api_coverage": "seller_center_marketing_activity_api" if rows else "error",
        "activity_status": activity_status,
        "activity_count": len(rows),
        "active_count": len(active_rows),
        "paused_count": len(paused_rows),
        "ended_count": len(ended_rows),
        "totalCount": total_count,
        "activities": rows[:limit],
        "errors": errors,
        "data_gap": data_gap,
        "source_link": {"label": "天猫商家中心-营销工具", "url": SELLER_MARKETING_TOOLS_PAGE},
    }


async def _wait_for_seller_mtop(cdp: CdpClient, page_url: str, wait_seconds: float) -> None:
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("Page.navigate", {"url": page_url})
    deadline = time.time() + max(2.0, min(wait_seconds, 10.0))
    last_state: Any = None
    while time.time() < deadline:
        last_state = await cdp.evaluate(
            "({href: location.href, ready: document.readyState, hasMtop: !!(window.lib && window.lib.mtop && window.lib.mtop.request)})"
        )
        if isinstance(last_state, dict) and last_state.get("hasMtop"):
            return
        await asyncio.sleep(0.4)
    raise RuntimeError(f"天猫商家中心 MTOP 未就绪: {last_state}")


async def _seller_mtop_request(
    cdp: CdpClient,
    *,
    api: str,
    data: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    return await _detail_mtop_request(
        cdp,
        api=api,
        version="1.0",
        data=data,
        options={"dataType": "json"},
        timeout_seconds=timeout_seconds,
    )


def _risk_num(row: dict[str, Any]) -> int:
    return _int_value(row.get("riskNum"))


def _normalize_price_risk_detail(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "riskCode": row.get("riskCode"),
        "riskTitle": row.get("riskTitle"),
        "riskDesc": row.get("riskDesc"),
        "riskNum": _risk_num(row),
        "riskScope": row.get("riskScope"),
        "riskScopeName": row.get("riskScopeName"),
        "hasRisk": _truthy(row.get("hasRisk")),
        "priority": _int_value(row.get("priority")),
    }


def _normalize_risk_record(row: dict[str, Any]) -> dict[str, Any]:
    item_id = str(row.get("itemId") or row.get("item_id") or row.get("auctionId") or "")
    activity_id = str(row.get("activityId") or row.get("activity_id") or "")
    return {
        "itemId": item_id,
        "activityId": activity_id,
        "title": row.get("title") or row.get("itemTitle") or row.get("activityName") or "",
        "riskCode": row.get("riskCode"),
        "riskTitle": row.get("riskTitle"),
        "riskReason": row.get("riskReason") or row.get("reason") or row.get("desc") or "",
        "price": row.get("price") or row.get("discountPrice") or row.get("actualPrice"),
        "rawKeys": list(row.keys())[:30],
    }


async def _tmall_seller_price_risk_check(args: dict) -> dict:
    if _is_production_env():
        return _direct_cookie_blocked_payload("seller price risk CDP fetch")
    item_id = str(args.get("itemId") or "")
    limit = max(1, min(int(args.get("limit") or 20), 100))
    wait_seconds = float(args.get("waitSeconds") or 3)
    timeout_seconds = max(8.0, min(float(args.get("timeoutSeconds") or 20), 45.0))
    errors: list[dict[str, str]] = []
    count_details: list[dict[str, Any]] = []
    risk_records: list[dict[str, Any]] = []

    ws_url = get_page_ws_url(CDP_JSON_URL)
    async with CdpClient(ws_url, timeout_seconds=timeout_seconds) as cdp:
        await _wait_for_seller_mtop(cdp, SELLER_PRICE_RISK_PAGE, wait_seconds)
        count_result = await _seller_mtop_request(
            cdp,
            api="mtop.taobao.argus.MTopApiRouterService.process",
            data={
                "api": "GetRiskDetectCountInfo",
                "params": "{}",
                "pageVersion": "2.1.0",
                "source": "qianniulist",
            },
            timeout_seconds=timeout_seconds,
        )
        if not count_result.get("ok"):
            errors.append({"api": "GetRiskDetectCountInfo", "error": str(count_result.get("error"))})
        model = (((count_result.get("data") or {}).get("data") or {}).get("model") or {}) if isinstance(count_result, dict) else {}
        raw_details = model.get("riskDetectCountDetails") if isinstance(model.get("riskDetectCountDetails"), list) else []
        count_details = [_normalize_price_risk_detail(row) for row in raw_details if isinstance(row, dict)]

        for detail in count_details:
            if len(risk_records) >= limit:
                break
            if detail.get("riskNum", 0) <= 0 and not item_id:
                continue
            if detail.get("riskNum", 0) <= 0 and item_id:
                continue
            risk_code = detail.get("riskCode")
            if not risk_code:
                continue
            list_result = await _seller_mtop_request(
                cdp,
                api="mtop.taobao.argus.MTopApiRouterService.process",
                data={
                    "api": "GetRiskDetectListInfo",
                    "params": json.dumps({"size": limit, "riskCode": risk_code}, ensure_ascii=False),
                    "pageVersion": "2.1.0",
                    "source": "qianniulist",
                },
                timeout_seconds=timeout_seconds,
            )
            if not list_result.get("ok"):
                errors.append({"api": f"GetRiskDetectListInfo:{risk_code}", "error": str(list_result.get("error"))})
                continue
            list_model = (((list_result.get("data") or {}).get("data") or {}).get("model") or {}) if isinstance(list_result, dict) else {}
            records = list_model.get("riskDetectRecords") if isinstance(list_model.get("riskDetectRecords"), list) else []
            for record in records:
                if not isinstance(record, dict):
                    continue
                normalized = _normalize_risk_record({**record, "riskCode": risk_code, "riskTitle": detail.get("riskTitle")})
                risk_records.append(normalized)
                if len(risk_records) >= limit:
                    break

    item_records = [
        row for row in risk_records
        if item_id and str(row.get("itemId") or "") == item_id
    ]
    active_risk_count = sum(_risk_num(row) for row in count_details)
    risk_status = "未发现营销价格风险"
    if item_records:
        risk_status = "当前商品命中价格/资损风险"
    elif active_risk_count > 0:
        risk_status = "店铺存在价格/资损风险，当前商品未命中已采集记录"

    return {
        "source": "tmall_seller_price_risk_check",
        "itemId": item_id,
        "api_coverage": "seller_center_price_risk_mtop_api",
        "risk_status": risk_status,
        "active_risk_count": active_risk_count,
        "risk_type_count": len([row for row in count_details if _risk_num(row) > 0]),
        "risk_details": count_details,
        "records": risk_records[:limit],
        "item_records": item_records,
        "errors": errors,
        "data_gap": "" if not item_id or item_records or active_risk_count == 0 else "风险列表为店铺/活动维度，当前采集记录未命中该商品。",
        "source_link": {"label": "天猫商家中心-营销风险", "url": SELLER_PRICE_RISK_PAGE},
    }


def tmall_seller_price_risk_check(args: dict) -> dict:
    return asyncio.run(_tmall_seller_price_risk_check(args))


def tmall_item_activity_price_check(args: dict) -> dict:
    item_id = str(args["itemId"])
    limit = int(args.get("limit") or 20)
    activity: dict[str, Any] = {}
    seller_price: dict[str, Any] = {}
    seller_activities: dict[str, Any] = {}
    seller_risk: dict[str, Any] = {}
    seller_errors: dict[str, str] = {}
    try:
        activity = tmall_sycm_activity_price({
            "itemIds": [item_id],
            "limit": limit,
        })
    except Exception as exc:  # noqa: BLE001
        seller_errors["sycmActivityPrice"] = str(exc)
    try:
        seller_price = tmall_seller_price_competitiveness_check({"itemId": item_id, "limit": max(limit, 100)})
    except Exception as exc:  # noqa: BLE001
        seller_errors["priceCompetitiveness"] = str(exc)
    try:
        seller_activities = tmall_seller_marketing_activity_list({"itemId": item_id, "limit": max(limit, 50)})
    except Exception as exc:  # noqa: BLE001
        seller_errors["marketingActivityList"] = str(exc)
    try:
        seller_risk = tmall_seller_price_risk_check({
            "itemId": item_id,
            "limit": limit,
            "waitSeconds": args.get("waitSeconds") or 3,
            "timeoutSeconds": args.get("timeoutSeconds") or 20,
        })
    except Exception as exc:  # noqa: BLE001
        seller_errors["priceRisk"] = str(exc)
    item_warnings = [
        row for row in activity.get("itemPriceWarnings") or []
        if isinstance(row, dict) and _item_warning_matches(row, item_id)
    ]
    activities = [
        row for row in activity.get("activities") or []
        if isinstance(row, dict) and _item_warning_matches(row, item_id)
    ]
    high_price_raw = activity.get("highAddPriceAlarm") if isinstance(activity.get("highAddPriceAlarm"), dict) else {}
    high_price_rows = []
    for key in ("data", "list", "items"):
        if isinstance(high_price_raw.get(key), list):
            high_price_rows.extend(high_price_raw[key])
    high_price_matches = [
        row for row in high_price_rows
        if isinstance(row, dict) and _item_warning_matches(row, item_id)
    ]
    risk_rows = [*item_warnings, *high_price_matches]
    seller_risk_records = seller_risk.get("item_records") if isinstance(seller_risk.get("item_records"), list) else []
    price_warning_summary = [_warning_text(row) for row in risk_rows[:5]]
    price_warning_summary.extend(
        str(row.get("riskTitle") or row.get("riskReason") or row.get("title") or "")
        for row in seller_risk_records[:5]
        if isinstance(row, dict)
    )
    seller_price_status = seller_price.get("price_status") if isinstance(seller_price, dict) else ""
    seller_risk_status = seller_risk.get("risk_status") if isinstance(seller_risk, dict) else ""
    if seller_risk_records or risk_rows:
        price_status = "有价格/资损风险提醒"
    elif seller_risk_status == "未发现营销价格风险" and seller_price_status:
        price_status = f"未发现价格资损风险；{seller_price_status}"
    elif seller_price_status:
        price_status = seller_price_status
    else:
        price_status = "未发现价格风险提醒" if not risk_rows else "有价格风险提醒"

    seller_activity_status = seller_activities.get("activity_status") if isinstance(seller_activities, dict) else ""
    if seller_activity_status:
        activity_status = f"{seller_activity_status}；商品级参与待核对"
    else:
        activity_status = "命中商品级活动" if activities else "未命中商品级活动，需页面核对是否掉线"
    item_activity_membership_status = (
        "命中生意参谋商品级活动记录" if activities else "商品级活动参与待核对"
    )
    final_price_status = (
        f"SKU 最终到手价待核对；{price_status}"
        if price_status else "SKU 最终到手价待核对"
    )

    coverage_parts = []
    if seller_price:
        coverage_parts.append("seller_center_price_api")
    if seller_activities:
        coverage_parts.append("seller_center_activity_api")
    if seller_risk:
        coverage_parts.append("seller_center_price_risk_mtop_api")
    if activity:
        coverage_parts.append("sycm_activity_price_api")
    return {
        "source": "tmall_item_activity_price_check",
        "itemId": item_id,
        "activity_status": activity_status,
        "item_activity_membership_status": item_activity_membership_status,
        "activity_count": len(activities),
        "seller_activity_count": seller_activities.get("activity_count"),
        "seller_active_activity_count": seller_activities.get("active_count"),
        "price_status": price_status,
        "final_price_status": final_price_status,
        "price_star_status": seller_price.get("price_star_status") or "",
        "high_price_limit_status": seller_price.get("high_price_limit_status") or "",
        "price_warning_count": len(risk_rows) + len(seller_risk_records),
        "price_warning_summary": [text for text in price_warning_summary if text][:5],
        "api_coverage": "+".join(dict.fromkeys(coverage_parts)) or "partial",
        "data_gap": (
            "已确认商家中心价格力、营销活动列表、营销风险 API；"
            "剩余缺口是商品 ID 维度活动参与/掉线详情，以及 SKU 最终到手价/优惠叠加后的价格正确性。"
        ),
        "source_links": [
            {
                "label": "天猫商家中心-价格竞争力",
                "url": SELLER_PRICE_HOME_PAGE,
            },
            {
                "label": "天猫商家中心-营销工具活动列表",
                "url": SELLER_MARKETING_TOOLS_PAGE,
            },
            {
                "label": "天猫商家中心-营销风险",
                "url": SELLER_PRICE_RISK_PAGE,
            },
            {
                "label": "生意参谋活动/价格风险",
                "url": "https://sycm.taobao.com/portal/home.htm",
            },
            {
                "label": "天猫详情页",
                "url": f"https://detail.tmall.com/item.htm?id={quote(item_id)}",
            },
        ],
        "seller_center": {
            "price_competitiveness": seller_price,
            "marketing_activities": seller_activities,
            "price_risk": seller_risk,
            "errors": seller_errors,
        },
        "raw": activity,
    }


def _alimama_access(page_url: str) -> dict:
    cache_ttl = max(0.0, _num(os.environ.get("TMALL_MCP_ALIMAMA_ACCESS_CACHE_SECONDS"), 180.0))
    cache_key = "universalBP"
    now = time.monotonic()
    cached = _ALIMAMA_ACCESS_CACHE.get(cache_key)
    if cached and cache_ttl > 0 and now - cached[0] < cache_ttl:
        return dict(cached[1])

    payload = _api_payload(_fetch_json(ALIMAMA_CHECK_ACCESS_URL, method="POST", page_url=page_url))
    info = payload.get("info") or {}
    data = payload.get("data") or {}
    if not info.get("ok"):
        raise RuntimeError(info.get("message") or "阿里妈妈登录态无效")
    access = data.get("accessInfo") or {}
    csrf = access.get("csrfId")
    if not csrf:
        raise RuntimeError("阿里妈妈 checkAccess 未返回 csrfId")
    result = {
        "csrfId": csrf,
        "accountName": ((data.get("meta") or {}).get("nickName")),
        "memberId": ((data.get("meta") or {}).get("memberId")),
    }
    _ALIMAMA_ACCESS_CACHE[cache_key] = (now, result)
    return dict(result)


def _alimama_biz(promotion_type: str) -> tuple[str, str]:
    if promotion_type == "display":
        return "onebpDisplay", "display"
    return "onebpSearch", "search"


def _normalize_report(row: dict | None) -> dict:
    row = row or {}
    return {
        "charge": row.get("charge"),
        "directPayAmount": row.get("alipayDirAmt"),
        "totalPayAmount": row.get("alipayInshopAmt"),
        "roi": row.get("roi"),
        "cvr": row.get("cvr"),
        "ecpc": row.get("ecpc"),
        "ctr": row.get("ctr"),
        "click": row.get("click"),
        "adPv": row.get("adPv"),
        "directPayNum": row.get("alipayDirNum"),
        "totalPayNum": row.get("alipayInshopNum"),
        "cartCost": row.get("cartCost"),
        "cartNum": row.get("cartDirNum"),
        "cartRate": row.get("cartRate"),
    }


def _alimama_signature(result: dict) -> dict:
    summary = result.get("pageSummary") if isinstance(result.get("pageSummary"), dict) else {}
    plans = result.get("plans") if isinstance(result.get("plans"), list) else []
    campaign_ids = [str(plan.get("campaignId")) for plan in plans[:20] if isinstance(plan, dict) and plan.get("campaignId")]
    campaign_names = [str(plan.get("campaignName")) for plan in plans[:20] if isinstance(plan, dict) and plan.get("campaignName")]
    return {
        "count": int(_num(result.get("count"), -1)),
        "campaignIds": campaign_ids,
        "campaignNames": campaign_names,
        "charge": round(_num(summary.get("charge")), 2),
        "click": int(round(_num(summary.get("click")))),
        "roi": round(_num(summary.get("roi")), 4),
        "ppc": round(_num(summary.get("ecpc")), 4),
    }


def _alimama_signature_equal(left: dict, right: dict) -> bool:
    left_ids = left.get("campaignIds") or []
    right_ids = right.get("campaignIds") or []
    has_signal = bool(left_ids or right_ids) or any(
        _num(row.get(key)) > 0
        for row in (left, right)
        for key in ("count", "charge", "click")
    )
    if not has_signal:
        return False
    if left_ids and right_ids and left_ids != right_ids:
        return False
    left_names = left.get("campaignNames") or []
    right_names = right.get("campaignNames") or []
    if left_names and right_names and left_names != right_names:
        return False
    comparable = ("count", "charge", "click", "roi", "ppc")
    return all(left.get(key) == right.get(key) for key in comparable)


def _alimama_account_probe(args: dict, *, promotion_type: str, date_range: str, page_size: int) -> dict | None:
    probe_item_id = str(os.environ.get("ALIMAMA_SCOPE_PROBE_ITEM_ID") or "9999999999999")
    if str(args.get("itemId") or "") == probe_item_id:
        return None
    key = (promotion_type, date_range, str(page_size), probe_item_id)
    if key not in _ALIMAMA_ACCOUNT_SIGNATURE_CACHE:
        probe_args = {
            **_inherited_child_tool_args(args),
            "itemId": probe_item_id,
            "promotionType": promotion_type,
            "dateRange": date_range,
            "pageSize": page_size,
            "_skipScopeProbe": True,
        }
        if "dateType" in args and "dateType" not in probe_args:
            probe_args["dateType"] = args["dateType"]
        _ALIMAMA_ACCOUNT_SIGNATURE_CACHE[key] = _run_child_tool(
            "tmall_alimama_item_promotion",
            tmall_alimama_item_promotion,
            probe_args,
        )
    return _ALIMAMA_ACCOUNT_SIGNATURE_CACHE[key]


def _alimama_scope_check(result: dict, args: dict, *, promotion_type: str, date_range: str, page_size: int) -> dict:
    if args.get("_skipScopeProbe") or os.environ.get("ALIMAMA_DISABLE_SCOPE_PROBE") == "1":
        return {"status": "skipped", "accountLevelAggregate": False}
    target_signature = _alimama_signature(result)
    try:
        probe = _alimama_account_probe(args, promotion_type=promotion_type, date_range=date_range, page_size=page_size)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unchecked",
            "accountLevelAggregate": False,
            "error": str(exc),
            "targetSignature": target_signature,
        }
    if not probe:
        return {"status": "skipped", "accountLevelAggregate": False, "targetSignature": target_signature}
    probe_signature = _alimama_signature(probe)
    aggregate = _alimama_signature_equal(target_signature, probe_signature)
    return {
        "status": "account_aggregate" if aggregate else "item_scoped_or_unverified",
        "accountLevelAggregate": aggregate,
        "probeItemId": str(probe.get("itemId") or ""),
        "targetSignature": target_signature,
        "probeSignature": probe_signature,
        "message": (
            "阿里妈妈 itemId 筛选未生效：目标商品与无效商品ID返回同一批计划/汇总，当前仅能作为账户/计划聚合值，不能按商品归因。"
            if aggregate else "目标商品与无效商品ID返回不同结果，暂按商品筛选结果使用。"
        ),
    }


def _first_present(row: dict, *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _collect_nested_rows(row: dict, keys: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for key in keys:
        value = row.get(key)
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            nested = value.get("list") or value.get("data") or value.get("items")
            if isinstance(nested, list):
                out.extend(item for item in nested if isinstance(item, dict))
    return out


def _normalize_keyword_detail(row: dict, *, plan_id: Any = None, plan_name: Any = None) -> dict:
    return {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "keyword_id": _first_present(row, "keywordId", "wordId", "bidwordId", "id"),
        "keyword": _first_present(row, "keyword", "keyWord", "word", "bidword", "bidWord", "keywordName", "wordName", "name"),
        "match_type": _first_present(row, "matchType", "matchScope", "match_type"),
        "bid_price": _first_present(row, "bidPrice", "maxPrice", "price"),
        "charge": _first_present(row, "charge", "cost", "spend"),
        "roi": _first_present(row, "roi"),
        "ppc": _first_present(row, "ppc", "ecpc", "cpc"),
        "click": _first_present(row, "click", "paidVisitorCount"),
        "ctr": _first_present(row, "ctr"),
        "cvr": _first_present(row, "cvr"),
        "status": _first_present(row, "onlineStatus", "status"),
    }


def _normalize_creative_detail(row: dict, *, plan_id: Any = None, plan_name: Any = None) -> dict:
    return {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "creative_id": _first_present(row, "creativeId", "materialId", "id"),
        "creative_name": _first_present(row, "creativeName", "materialName", "title", "name"),
        "image_url": _first_present(row, "imageUrl", "imgUrl", "materialUrl", "picUrl"),
        "charge": _first_present(row, "charge", "cost", "spend"),
        "roi": _first_present(row, "roi"),
        "ppc": _first_present(row, "ppc", "ecpc", "cpc"),
        "click": _first_present(row, "click", "paidVisitorCount"),
        "ctr": _first_present(row, "ctr"),
        "cvr": _first_present(row, "cvr"),
        "status": _first_present(row, "onlineStatus", "status"),
    }


def _normalize_campaign(row: dict) -> dict:
    report = row.get("reportInfoMap") or row.get("reportInfo") or {}
    adgroups = row.get("adgroupList") if isinstance(row.get("adgroupList"), list) else []
    first_adgroup = adgroups[0] if adgroups and isinstance(adgroups[0], dict) else {}
    plan_id = row.get("campaignId") or first_adgroup.get("campaignId")
    plan_name = row.get("campaignName") or first_adgroup.get("campaignName") or first_adgroup.get("adgroupName")
    keyword_rows = _collect_nested_rows(row, ("keywordList", "keywords", "wordList", "bidwordList", "keywordDataList"))
    creative_rows = _collect_nested_rows(row, ("creativeList", "creatives", "materialList", "creativeDataList"))
    for adgroup in adgroups:
        if isinstance(adgroup, dict):
            keyword_rows.extend(_collect_nested_rows(adgroup, ("keywordList", "keywords", "wordList", "bidwordList", "keywordDataList")))
            creative_rows.extend(_collect_nested_rows(adgroup, ("creativeList", "creatives", "materialList", "creativeDataList")))
    return {
        "campaignId": plan_id,
        "campaignName": plan_name,
        "onlineStatus": row.get("onlineStatus") or first_adgroup.get("onlineStatus"),
        "stage": row.get("coldBootStage") or row.get("stage"),
        "budget": row.get("budget") or row.get("dayBudget") or row.get("mainDayBudget") or row.get("campaignBudget"),
        "charge": _val(report, "charge") if isinstance(report, dict) else None,
        "directPayAmount": _val(report, "alipayDirAmt") if isinstance(report, dict) else None,
        "totalPayAmount": _val(report, "alipayInshopAmt") if isinstance(report, dict) else None,
        "roi": _val(report, "roi") if isinstance(report, dict) else None,
        "cvr": _val(report, "cvr") if isinstance(report, dict) else None,
        "ecpc": _val(report, "ecpc") if isinstance(report, dict) else None,
        "ctr": _val(report, "ctr") if isinstance(report, dict) else None,
        "click": _val(report, "click") if isinstance(report, dict) else None,
        "adPv": _val(report, "adPv") if isinstance(report, dict) else None,
        "keywords": [_normalize_keyword_detail(item, plan_id=plan_id, plan_name=plan_name) for item in keyword_rows[:50]],
        "creatives": [_normalize_creative_detail(item, plan_id=plan_id, plan_name=plan_name) for item in creative_rows[:50]],
        "rawKeys": list(row.keys())[:30],
    }


def tmall_alimama_item_promotion(args: dict) -> dict:
    item_id = str(args["itemId"])
    promotion_type = args.get("promotionType") or "search"
    biz_code, page_name = _alimama_biz(promotion_type)
    date_range = args.get("dateRange") or _today_range()
    start, end = _date_bounds(date_range)
    use_realtime = _alimama_realtime_requested(args, date_range)
    offset = int(args.get("offset") or 0)
    page_size = int(args.get("pageSize") or 40)
    page_url = (
        f"https://one.alimama.com/index.html#!/manage/{page_name}"
        f"?offset={offset}&searchKey=itemId&searchValue={item_id}"
    )
    access = _alimama_access(page_url)
    fields = (
        "charge,alipayDirAmt,roi,cvr,ecpc,ctr,alipayDirNum,cartCost,"
        "cartDirNum,alipayInshopAmt,click,cartRate,adPv,alipayInshopNum"
    )
    if promotion_type == "display":
        fields = "charge,alipayDirAmt,alipayInshopAmt,ecpc,roi,cvr,ctr,adPv,click"
    body = {
        "mx_bizCode": biz_code,
        "bizCode": biz_code,
        "offset": offset,
        "pageSize": page_size,
        "orderField": "",
        "orderBy": "",
        "queryRuleAuto": "1",
        "adgroupRequired": False,
        "adzoneRequired": promotion_type == "display",
        "itemId": int(item_id),
        "rptQuery": {
            "fields": fields,
            "conditionList": [
                {
                    "sourceList": ["scene", "campaign_list"],
                    "adzonePkgIdList": (
                        ALIMAMA_DISPLAY_ADZONE_PKGS if promotion_type == "display" else ALIMAMA_SEARCH_ADZONE_PKGS
                    ),
                    "startTime": start,
                    "endTime": end,
                    "isRt": use_realtime,
                }
            ],
        },
    }
    if promotion_type == "search":
        body["searchDetentTypeList"] = ["first_place"]
    else:
        body["moreFilters"] = ["oss_replace_none", "ntc_replace_none", "fq_replace_none"]
    url = (
        "https://one.alimama.com/campaign/horizontal/findPage.json"
        f"?csrfId={access['csrfId']}&bizCode={biz_code}"
    )
    payload = _api_payload(_fetch_json(
        url,
        method="POST",
        headers={"content-type": "application/json;charset=UTF-8"},
        body=body,
        page_url=page_url,
    ))
    info = payload.get("info") or {}
    if info and info.get("ok") is False:
        raise RuntimeError(info.get("message") or "阿里妈妈接口返回失败")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows = data.get("list") if isinstance(data.get("list"), list) else []
    plans = [_normalize_campaign(row) for row in rows if isinstance(row, dict)]
    campaign_ids = [p.get("campaignId") for p in plans if p.get("campaignId")]
    page_summary = None
    if campaign_ids:
        report_body = {
            "bizCode": biz_code,
            "mx_bizCode": biz_code,
            "byPage": False,
            "fromRealTime": use_realtime,
            "startTime": start,
            "endTime": end,
            "splitType": "sum",
            "computeType": "sum",
            "sourceList": ["scene", "campaign_list"],
            "queryDomains": [],
            "queryFieldIn": fields.split(","),
            "adzonePkgIdIn": ALIMAMA_DISPLAY_ADZONE_PKGS if promotion_type == "display" else ALIMAMA_SEARCH_ADZONE_PKGS,
            "strategyCampaignIdIn": campaign_ids,
            "csrfId": access["csrfId"],
        }
        report_url = (
            "https://one.alimama.com/report/query.json"
            f"?csrfId={access['csrfId']}&bizCode={biz_code}"
        )
        try:
            report_payload = _api_payload(_fetch_json(
                report_url,
                method="POST",
                headers={"content-type": "application/json;charset=UTF-8"},
                body=report_body,
                page_url=page_url,
            ))
            report_info = report_payload.get("info") or {}
            if not report_info or report_info.get("ok") is not False:
                report_data = report_payload.get("data") if isinstance(report_payload.get("data"), dict) else {}
                report_rows = report_data.get("list") if isinstance(report_data.get("list"), list) else []
                page_summary = _normalize_report(report_rows[0] if report_rows else {})
        except Exception as exc:  # noqa: BLE001
            page_summary = {"error": str(exc)}
    # 关键词/创意级明细：API 返回的计划列表通常不包含嵌套的 keywords/creatives，
    # _normalize_campaign 已尝试从 campaign/ adgroup 对象中提取，但多数情况下为空。
    # 如需完整关键词/创意清单，需要逐计划调 report/query.json 并按 keyword/creative 维度拆分。
    plan_details_available = any(
        (p.get("keywords") and len(p["keywords"]) > 0) or (p.get("creatives") and len(p["creatives"]) > 0)
        for p in plans
    )
    data_notes = [
        "plan_count 为当前查询条件下（itemId + 日期范围）阿里妈妈返回的计划总数",
        "若多个商品返回相同 plan_count，说明 API 返回的是账号级聚合，非单商品专属",
    ]
    if not plan_details_available:
        data_notes.append("关键词/创意级明细未返回，需逐计划单独查询 report/query.json（按 keyword/creative 维度拆分）")
        data_notes.append("低 ROI 关键词/高 PPC 关键词/低效创意 列表可能为空不代表没有，而是 API 不返回嵌套明细")
    partial_result = {
        "source": "alimama_item_promotion",
        "itemId": item_id,
        "promotionType": promotion_type,
        "bizCode": biz_code,
        "dateRange": date_range,
        "dateType": "today" if use_realtime else "day",
        "granularity": _alimama_granularity(use_realtime),
        "asOfTime": args.get("asOfTime") or time.strftime("%H:%M"),
        "accountName": access.get("accountName"),
        "memberId": access.get("memberId"),
        "count": data.get("count"),
        "offset": offset,
        "pageSize": page_size,
        "pageSummary": page_summary,
        "plans": plans,
        "planDetailsAvailable": plan_details_available,
        "dataNotes": data_notes,
        "rawDataKeys": list(data.keys())[:30],
    }
    scope_check = _alimama_scope_check(
        partial_result,
        args,
        promotion_type=promotion_type,
        date_range=date_range,
        page_size=page_size,
    )
    if scope_check.get("accountLevelAggregate"):
        data_notes.append(scope_check.get("message") or "阿里妈妈返回疑似账户/计划聚合口径，不能按商品归因")
    partial_result["scopeCheck"] = scope_check
    partial_result["accountLevelAggregate"] = bool(scope_check.get("accountLevelAggregate"))
    partial_result["dataNotes"] = data_notes
    return partial_result


def _promotion_types_arg(value: Any) -> list[str]:
    if isinstance(value, list):
        types = [str(item) for item in value if str(item) in {"search", "display"}]
        return types or ["search", "display"]
    if value == "search":
        return ["search"]
    if value == "display":
        return ["display"]
    return ["search", "display"]


def _plan_count(result: dict) -> int:
    count = result.get("count")
    if count is not None:
        try:
            return int(count)
        except (TypeError, ValueError):
            pass
    plans = result.get("plans") if isinstance(result.get("plans"), list) else []
    return len(plans)


def _promotion_campaign_ids(result: dict) -> list[str]:
    segments = result.get("segments") if isinstance(result.get("segments"), dict) else {}
    ids: list[str] = []
    for segment in segments.values():
        if not isinstance(segment, dict):
            continue
        current = segment.get("current") if isinstance(segment.get("current"), dict) else {}
        plans = current.get("plans") if isinstance(current.get("plans"), list) else []
        for row in plans:
            if not isinstance(row, dict):
                continue
            campaign_id = row.get("campaignId")
            if campaign_id in (None, ""):
                continue
            ids.append(str(campaign_id))
    deduped = sorted(set(ids))
    return deduped


def _campaign_id_fingerprint(campaign_ids: list[str]) -> str:
    if not campaign_ids:
        return ""
    joined = ",".join(campaign_ids)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _summary_number(summary: dict, key: str) -> float:
    return _num(summary.get(key)) if isinstance(summary, dict) else 0.0


def _summary_ratio_from_report_or_derived(summaries: list[dict], key: str, numerator: float, denominator: float) -> float:
    if len(summaries) == 1 and _has_value(summaries[0].get(key)):
        return _num(summaries[0].get(key))
    weighted_denominator = sum(_summary_number(row, "click" if key == "cvr" else "adPv" if key == "ctr" else "charge") for row in summaries)
    weighted_value = sum(
        _summary_number(row, key) * _summary_number(row, "click" if key == "cvr" else "adPv" if key == "ctr" else "charge")
        for row in summaries
        if _has_value(row.get(key))
    )
    if weighted_denominator > 0 and weighted_value > 0:
        return weighted_value / weighted_denominator
    return numerator / denominator if denominator > 0 and numerator > 0 else 0.0


def _ratio(numerator: Any, denominator: Any) -> float:
    denominator_num = _num(denominator)
    numerator_num = _num(numerator)
    return numerator_num / denominator_num if denominator_num > 0 and numerator_num > 0 else 0.0


def _aggregate_promotion_summary(results: list[dict]) -> dict:
    scoped_results = [row for row in results if isinstance(row, dict) and not row.get("accountLevelAggregate")]
    account_aggregate_count = sum(1 for row in results if isinstance(row, dict) and row.get("accountLevelAggregate"))
    results = scoped_results
    summaries = [row.get("pageSummary") for row in results if isinstance(row.get("pageSummary"), dict)]
    charge = sum(_summary_number(row, "charge") for row in summaries)
    click = sum(_summary_number(row, "click") for row in summaries)
    ad_pv = sum(_summary_number(row, "adPv") for row in summaries)
    direct_pay_amount = sum(_summary_number(row, "directPayAmount") for row in summaries)
    total_pay_amount = sum(_summary_number(row, "totalPayAmount") for row in summaries)
    direct_pay_num = sum(_summary_number(row, "directPayNum") for row in summaries)
    total_pay_num = sum(_summary_number(row, "totalPayNum") for row in summaries)
    cart_num = sum(_summary_number(row, "cartNum") for row in summaries)
    plan_count = sum(_plan_count(row) for row in results)
    # 阿里妈妈 report/query.json 的 roi 字段口径是总成交金额 / 花费。
    # 业务待办展示的“实时直接 ROI”必须使用直接成交金额 / 花费。
    direct_roi = _ratio(direct_pay_amount, charge)
    total_roi = _summary_ratio_from_report_or_derived(summaries, "roi", total_pay_amount, charge)
    ppc = _summary_ratio_from_report_or_derived(summaries, "ecpc", charge, click)
    ctr = _summary_ratio_from_report_or_derived(summaries, "ctr", click, ad_pv)
    cvr = _summary_ratio_from_report_or_derived(summaries, "cvr", direct_pay_num, click)
    return {
        "charge": round(charge, 4),
        "paidVisitorCount": int(round(click)),
        "click": int(round(click)),
        "adPv": int(round(ad_pv)),
        "directPayAmount": round(direct_pay_amount, 4),
        "totalPayAmount": round(total_pay_amount, 4),
        "directPayNum": int(round(direct_pay_num)),
        "totalPayNum": int(round(total_pay_num)),
        "cartNum": int(round(cart_num)),
        "roi": round(direct_roi, 4),
        "directRoi": round(direct_roi, 4),
        "totalRoi": round(total_roi, 4),
        "ppc": round(ppc, 4),
        "ecpc": round(ppc, 4),
        "ctr": round(ctr, 6),
        "cvr": round(cvr, 6),
        "planCount": plan_count,
        "accountAggregateCount": account_aggregate_count,
        "hasItemScopedData": bool(summaries or plan_count),
    }


def _promotion_delta(current: dict, previous: dict) -> dict:
    return {
        "paidVisitorChangePct": _pct(current.get("paidVisitorCount"), previous.get("paidVisitorCount")),
        "cvrChangePct": _pct(current.get("cvr"), previous.get("cvr")),
        "roiDelta": round(_num(current.get("roi")) - _num(previous.get("roi")), 4),
        "roiChangePct": _pct(current.get("roi"), previous.get("roi")),
        "ppcDelta": round(_num(current.get("ppc")) - _num(previous.get("ppc")), 4),
        "ppcChangePct": _pct(current.get("ppc"), previous.get("ppc")),
        "chargeChangePct": _pct(current.get("charge"), previous.get("charge")),
        "directPayAmountChangePct": _pct(current.get("directPayAmount"), previous.get("directPayAmount")),
        "totalPayAmountChangePct": _pct(current.get("totalPayAmount"), previous.get("totalPayAmount")),
        "ctrChangePct": _pct(current.get("ctr"), previous.get("ctr")),
    }


def _promotion_plan_diagnostics(result: dict) -> dict:
    segments = result.get("segments") if isinstance(result.get("segments"), dict) else {}
    search_segment = segments.get("search") if isinstance(segments.get("search"), dict) else {}
    current = search_segment.get("current") if isinstance(search_segment.get("current"), dict) else {}
    account_level_aggregate = bool(current.get("accountLevelAggregate"))
    plans = [] if account_level_aggregate else (current.get("plans") if isinstance(current.get("plans"), list) else [])
    low_roi_plans = []
    high_ppc_plans = []
    low_roi_keywords = []
    high_ppc_keywords = []
    inefficient_creatives = []
    abnormal_budget = []
    for row in plans:
        if not isinstance(row, dict):
            continue
        normalized = {
            "plan_id": row.get("campaignId"),
            "plan_name": row.get("campaignName"),
            "roi": row.get("roi"),
            "charge": row.get("charge"),
            "ppc": row.get("ecpc"),
            "click": row.get("click"),
            "ctr": row.get("ctr"),
            "status": row.get("onlineStatus"),
            "budget": row.get("budget"),
        }
        if _num(row.get("charge")) > 0 and _num(row.get("roi")) < 1.5:
            low_roi_plans.append(normalized)
        if _num(row.get("ecpc")) > 0 and _num(row.get("ecpc")) >= 4:
            high_ppc_plans.append(normalized)
        status = str(row.get("onlineStatus") or "").lower()
        if status and status not in {"online", "1", "true", "正常"}:
            abnormal_budget.append(normalized)
        for keyword in row.get("keywords") if isinstance(row.get("keywords"), list) else []:
            if not isinstance(keyword, dict):
                continue
            if _num(keyword.get("charge")) > 0 and _num(keyword.get("roi")) < 1.5:
                low_roi_keywords.append(keyword)
            if _num(keyword.get("ppc")) >= 4:
                high_ppc_keywords.append(keyword)
        for creative in row.get("creatives") if isinstance(row.get("creatives"), list) else []:
            if not isinstance(creative, dict):
                continue
            low_roi = _num(creative.get("charge")) > 0 and _num(creative.get("roi")) < 1.5
            low_ctr = 0 < _num(creative.get("ctr")) < 0.005
            if low_roi or low_ctr:
                inefficient_creatives.append(creative)
    quality = []
    if account_level_aggregate:
        quality.append("计划列表为账户/计划聚合口径，已剔除商品级付费明细，需商品级/逐计划补采")
    if not plans:
        quality.append("未返回商品级计划明细" if account_level_aggregate else "未返回计划级明细")
    else:
        plan_details_available = current.get("planDetailsAvailable", False)
        if not plan_details_available:
            quality.append("API 返回了计划列表但未包含关键词/创意嵌套明细，低ROI词/高PPC词/低效创意需逐计划单独查询")
        if not low_roi_keywords and not high_ppc_keywords:
            quality.append("关键词级明细未返回或未命中低 ROI/高 PPC 词")
        if not inefficient_creatives:
            quality.append("创意素材级明细未返回或未命中低效创意")
    # 透传 data notes
    for note in (result.get("dataNotes") or [])[:5]:
        if note not in quality:
            quality.append(note)
    return {
        "low_roi_plans": low_roi_plans[:5],
        "low_roi_keywords": low_roi_keywords[:10],
        "high_ppc_keywords": high_ppc_keywords[:10],
        "inefficient_creatives": inefficient_creatives[:10],
        "budget_status": (
            "异常计划：" + "、".join(str(row.get("plan_name") or row.get("plan_id")) for row in abnormal_budget[:5])
            if abnormal_budget else "未发现计划状态异常；预算字段需后台复核"
        ),
        "detail_status": (
            "账户/计划聚合明细已剔除，商品级计划/关键词/创意待补采"
            if account_level_aggregate else
            "已采集计划级明细；关键词/创意级命中异常"
            if low_roi_keywords or high_ppc_keywords or inefficient_creatives else
            "已采集计划级明细；关键词/创意级明细待补采"
            if plans else "缺少计划/关键词/创意级明细"
        ),
        "detail_quality": quality,
    }


def tmall_alimama_item_promotion_compare(args: dict) -> dict:
    item_id = str(args["itemId"])
    if _minute_compare_requested(args):
        response = _unsupported_minute_response("alimama_item_promotion_compare", args, item_id=item_id)
        response.update({
            "promotionTypes": _promotion_types_arg(args.get("promotionTypes")),
            "summary": {"current": {}, "compare": {}, "delta": {}},
            "segments": {},
        })
        return response
    date_range = args.get("dateRange") or _today_range()
    compare_date_range = args.get("compareDateRange") or _previous_equal_date_range(date_range)
    page_size = int(args.get("pageSize") or 40)
    promotion_types = _promotion_types_arg(args.get("promotionTypes"))
    segments: dict[str, Any] = {}
    current_results: list[dict] = []
    compare_results: list[dict] = []
    data_quality: list[str] = []
    child_context_args = _inherited_child_tool_args(args)
    child_proofs = _CURRENT_PROOFS.get()

    from concurrent.futures import ThreadPoolExecutor, as_completed

    for promotion_type in promotion_types:
        def _fetch_current(pt=promotion_type):
            try:
                if isinstance(child_proofs, list):
                    _CURRENT_PROOFS.set(child_proofs)
                return _run_child_tool("tmall_alimama_item_promotion", tmall_alimama_item_promotion, {
                    **child_context_args,
                    "itemId": item_id, "promotionType": pt,
                    "dateRange": date_range, "dateType": args.get("dateType"),
                    "pageSize": page_size,
                })
            except Exception as exc:
                return {"source": "alimama_item_promotion", "promotionType": pt, "error": str(exc), "_error": exc}

        def _fetch_previous(pt=promotion_type):
            try:
                if isinstance(child_proofs, list):
                    _CURRENT_PROOFS.set(child_proofs)
                return _run_child_tool("tmall_alimama_item_promotion", tmall_alimama_item_promotion, {
                    **child_context_args,
                    "itemId": item_id, "promotionType": pt,
                    "dateRange": compare_date_range,
                    "dateType": args.get("compareDateType") or args.get("dateType"),
                    "pageSize": page_size,
                })
            except Exception as exc:
                return {"source": "alimama_item_promotion", "promotionType": pt, "error": str(exc), "_error": exc}

        # Platform collection usually has a small credential pool per shop.
        # Parallel current/compare calls can race on the same governed cookie and
        # make one side return NO_COOKIE, so keep this pair sequential unless a
        # local test explicitly opts into the old behavior.
        if os.environ.get("TMALL_MCP_ALIMAMA_PARALLEL_COMPARE") == "1":
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_cur = pool.submit(_fetch_current)
                fut_prev = pool.submit(_fetch_previous)
                current = fut_cur.result()
                previous = fut_prev.result()
        else:
            current = _fetch_current()
            previous = _fetch_previous()

        if isinstance(current, dict) and current.get("_error"):
            data_quality.append(f"{promotion_type} 当前期采集失败：{current['_error']}")
            current.pop("_error", None)
        if isinstance(previous, dict) and previous.get("_error"):
            data_quality.append(f"{promotion_type} 对比期采集失败：{previous['_error']}")
            previous.pop("_error", None)

        current_results.append(current)
        compare_results.append(previous)
        current_summary = _aggregate_promotion_summary([current])
        previous_summary = _aggregate_promotion_summary([previous])
        segments[promotion_type] = {
            "current": current,
            "compare": previous,
            "summary": {
                "current": current_summary,
                "compare": previous_summary,
                "delta": _promotion_delta(current_summary, previous_summary),
            },
        }

    current_total = _aggregate_promotion_summary(current_results)
    previous_total = _aggregate_promotion_summary(compare_results)
    aggregate_results = [
        row for row in [*current_results, *compare_results]
        if isinstance(row, dict) and row.get("accountLevelAggregate")
    ]
    if aggregate_results:
        data_quality.append("阿里妈妈 itemId 筛选返回账户/计划聚合口径，已剔除汇总，不能按商品级付费 ROI/明细归因")
    if current_total["planCount"] == 0:
        data_quality.append("阿里妈妈未返回商品级推广计划，可能是该商品无计划或筛选权限/口径不匹配")
    if previous_total["paidVisitorCount"] <= 0:
        data_quality.append("对比期点击为 0，付费访客/转化环比不可判定")

    return {
        "source": "alimama_item_promotion_compare",
        "itemId": item_id,
        "dateRange": date_range,
        "compareDateRange": compare_date_range,
        "dateType": args.get("dateType") or _default_alimama_date_type(date_range),
        "compareDateType": args.get("compareDateType") or _default_alimama_date_type(compare_date_range),
        "granularity": _alimama_granularity(_alimama_realtime_requested(args, date_range)),
        "asOfTime": args.get("asOfTime") or time.strftime("%H:%M"),
        "supportsMinuteCompare": False,
        "promotionTypes": promotion_types,
        "summary": {
            "current": current_total,
            "compare": previous_total,
            "delta": _promotion_delta(current_total, previous_total),
        },
        "segments": segments,
        "dataQuality": data_quality,
        "accountLevelAggregate": bool(aggregate_results),
        "scopeChecks": [row.get("scopeCheck") for row in aggregate_results if isinstance(row.get("scopeCheck"), dict)],
    }


def tmall_item_promotion_required_metrics(args: dict) -> dict:
    if _minute_compare_requested(args):
        response = _unsupported_minute_response("tmall_item_promotion_required_metrics", args, item_id=str(args["itemId"]))
        response.update({
            "promotionType": "search",
            "roi": None,
            "compare_roi": None,
            "roi_delta": None,
            "roi_change_pct": None,
            "charge": None,
            "compare_charge": None,
            "charge_change_pct": None,
            "ppc": None,
            "compare_ppc": None,
            "ppc_delta": None,
            "ppc_change_pct": None,
            "click": None,
            "compare_click": None,
            "click_change_pct": None,
            "conversion_rate": None,
            "compare_conversion_rate": None,
            "conversion_change_pct": None,
            "plan_count": 0,
            "low_roi_plans": [],
            "low_roi_keywords": [],
            "high_ppc_keywords": [],
            "inefficient_creatives": [],
            "budget_status": "分钟级付费数据未确认",
            "detail_status": "分钟级 API 未确认，不能按 0 值判断",
            "data_quality": response["dataQuality"],
        })
        return response
    result = _run_child_tool("tmall_alimama_item_promotion_compare", tmall_alimama_item_promotion_compare, {
        "itemId": args["itemId"],
        "promotionTypes": "search",
        "dateRange": args.get("dateRange") or _today_range(),
        "compareDateRange": args.get("compareDateRange") or _previous_equal_date_range(
            args.get("dateRange") or _today_range()
        ),
        "pageSize": int(args.get("pageSize") or 40),
    })
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    current = summary.get("current") if isinstance(summary.get("current"), dict) else {}
    compare = summary.get("compare") if isinstance(summary.get("compare"), dict) else {}
    delta = summary.get("delta") if isinstance(summary.get("delta"), dict) else {}
    diagnostics = _promotion_plan_diagnostics(result)
    campaign_ids = _promotion_campaign_ids(result)
    campaign_fingerprint = _campaign_id_fingerprint(campaign_ids)
    data_quality = list(result.get("dataQuality") or [])
    data_quality.extend(diagnostics.get("detail_quality") or [])
    account_level_aggregate = bool(result.get("accountLevelAggregate")) or _num(current.get("accountAggregateCount")) > 0
    current_direct_roi = (
        round(_ratio(current.get("directPayAmount"), current.get("charge")), 4)
        if _has_value(current.get("directPayAmount")) and _num(current.get("charge")) > 0
        else (current.get("directRoi") if _has_value(current.get("directRoi")) else None)
    )
    compare_direct_roi = (
        round(_ratio(compare.get("directPayAmount"), compare.get("charge")), 4)
        if _has_value(compare.get("directPayAmount")) and _num(compare.get("charge")) > 0
        else (compare.get("directRoi") if _has_value(compare.get("directRoi")) else None)
    )
    direct_roi_delta = (
        round(_num(current_direct_roi) - _num(compare_direct_roi), 4)
        if current_direct_roi is not None and compare_direct_roi is not None
        else None
    )
    direct_roi_change_pct = (
        _pct(current_direct_roi, compare_direct_roi)
        if current_direct_roi is not None and compare_direct_roi is not None
        else None
    )
    current_total_roi = current.get("totalRoi") if _has_value(current.get("totalRoi")) else current.get("roi")
    return {
        "source": "tmall_item_promotion_required_metrics",
        "itemId": str(args["itemId"]),
        "dateRange": result.get("dateRange"),
        "compareDateRange": result.get("compareDateRange"),
        "dateType": result.get("dateType"),
        "compareDateType": result.get("compareDateType"),
        "granularity": result.get("granularity") or "live_snapshot",
        "asOfTime": result.get("asOfTime"),
        "supported": result.get("supported", True),
        "supportsMinuteCompare": False,
        "promotionType": "search",
        "roi": current_direct_roi,
        "compare_roi": compare_direct_roi,
        "roi_delta": direct_roi_delta,
        "roi_change_pct": direct_roi_change_pct,
        "charge": current.get("charge"),
        "compare_charge": compare.get("charge"),
        "charge_change_pct": delta.get("chargeChangePct"),
        "direct_pay_amount": current.get("directPayAmount"),
        "compare_direct_pay_amount": compare.get("directPayAmount"),
        "direct_pay_amount_change_pct": delta.get("directPayAmountChangePct"),
        "total_pay_amount": current.get("totalPayAmount"),
        "compare_total_pay_amount": compare.get("totalPayAmount"),
        "total_pay_amount_change_pct": delta.get("totalPayAmountChangePct"),
        "ppc": current.get("ppc"),
        "compare_ppc": compare.get("ppc"),
        "ppc_delta": delta.get("ppcDelta"),
        "ppc_change_pct": delta.get("ppcChangePct"),
        "click": current.get("click"),
        "compare_click": compare.get("click"),
        "click_change_pct": delta.get("paidVisitorChangePct"),
        "conversion_rate": current.get("cvr"),
        "compare_conversion_rate": compare.get("cvr"),
        "conversion_change_pct": delta.get("cvrChangePct"),
        "ctr": current.get("ctr"),
        "compare_ctr": compare.get("ctr"),
        "ctr_change_pct": delta.get("ctrChangePct"),
        "manage_search_bottom_total": {
            "charge": current.get("charge"),
            "direct_pay_amount": current.get("directPayAmount"),
            "direct_roi": current_direct_roi,
            "total_roi": current_total_roi,
            "roi": current_direct_roi,
            "click_conversion_rate": current.get("cvr"),
            "average_click_cost": current.get("ppc"),
            "click_rate": current.get("ctr"),
            "basis": "one.alimama.com manage/search itemId筛选底部合计；直接ROI=直接成交金额/花费",
        },
        "plan_count": current.get("planCount"),
        "campaign_ids_sample": campaign_ids[:10],
        "campaign_id_fingerprint": campaign_fingerprint,
        "account_level_aggregate": account_level_aggregate,
        "attribution_confidence": "低" if account_level_aggregate else "中",
        "scope_checks": result.get("scopeChecks") if isinstance(result.get("scopeChecks"), list) else [],
        "low_roi_plans": diagnostics.get("low_roi_plans") or [],
        "low_roi_keywords": diagnostics.get("low_roi_keywords") or [],
        "high_ppc_keywords": diagnostics.get("high_ppc_keywords") or [],
        "inefficient_creatives": diagnostics.get("inefficient_creatives") or [],
        "budget_status": diagnostics.get("budget_status"),
        "detail_status": diagnostics.get("detail_status"),
        "data_quality": data_quality,
        "source_link": {
            "label": "阿里妈妈/天猫商家中心-关键词推广",
            "url": f"https://one.alimama.com/index.html#!/manage/search?offset=0&searchKey=itemId&searchValue={quote(str(args['itemId']))}",
        },
        "raw": result,
    }


def _json_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


async def _wait_for_detail_mtop(cdp: CdpClient, item_id: str, wait_seconds: float) -> None:
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("Page.navigate", {"url": f"https://detail.tmall.com/item.htm?id={quote(item_id)}"})
    deadline = time.time() + max(2.0, min(wait_seconds, 8.0))
    last_state: Any = None
    while time.time() < deadline:
        last_state = await cdp.evaluate(
            "({href: location.href, ready: document.readyState, hasMtop: !!(window.lib && window.lib.mtop && window.lib.mtop.request)})"
        )
        if isinstance(last_state, dict) and last_state.get("hasMtop"):
            return
        await asyncio.sleep(0.4)
    raise RuntimeError(f"天猫详情页 MTOP 未就绪: {last_state}")


async def _detail_mtop_request(
    cdp: CdpClient,
    *,
    api: str,
    version: str,
    data: dict[str, Any],
    options: dict[str, Any] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    opts = {
        "api": api,
        "v": version,
        "dataType": "jsonp",
        "timeout": int(max(3000, min(timeout_seconds * 1000, 30000))),
        "data": data,
    }
    opts.update(options or {})
    expression = f"""
new Promise(resolve => {{
  const req = {_json_script(opts)};
  try {{
    if (!(window.lib && window.lib.mtop && window.lib.mtop.request)) {{
      resolve(JSON.stringify({{"ok": false, "error": "lib.mtop not ready"}}));
      return;
    }}
    window.lib.mtop.request(
      req,
      data => resolve(JSON.stringify({{"ok": true, "data": data}})),
      err => resolve(JSON.stringify({{"ok": false, "error": err}}))
    );
  }} catch (e) {{
    resolve(JSON.stringify({{"ok": false, "error": String(e), "stack": e && e.stack}}));
  }}
  setTimeout(() => resolve(JSON.stringify({{"ok": false, "error": "timeout"}})), {int(timeout_seconds * 1000)});
}})
"""
    raw = await cdp.evaluate(expression)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"MTOP 返回非 JSON: {raw[:200]}"}
        return parsed if isinstance(parsed, dict) else {"ok": True, "data": parsed}
    return raw if isinstance(raw, dict) else {"ok": False, "error": "MTOP 返回为空"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_rate_item(row: dict[str, Any], rank: int) -> dict[str, Any]:
    content = _clean_text(
        row.get("feedback")
        or row.get("content")
        or row.get("rateContent")
        or row.get("comment")
        or row.get("appendComment")
    )
    user = _clean_text(row.get("displayUserNick") or row.get("userNick") or row.get("nick") or row.get("user"))
    date_text = _clean_text(
        row.get("rateDate")
        or row.get("feedbackDate")
        or row.get("createTime")
        or row.get("gmtCreateTime")
        or row.get("gmtCreate")
        or row.get("date")
    )
    sku = _clean_text(row.get("skuValueStr") or row.get("skuInfo") or row.get("sku") or row.get("skuText"))
    is_pinned = bool(
        row.get("isTop")
        or row.get("top")
        or row.get("isPinned")
        or row.get("topTag")
        or "置顶" in _clean_text(row.get("tag") or row.get("tags") or row)
    )
    keywords, level = _risk_level(content)
    return {
        "rank": rank,
        "id": str(row.get("id") or row.get("rateId") or row.get("feedId") or ""),
        "user": user,
        "date": date_text,
        "sku": sku,
        "content": content,
        "isPinned": is_pinned,
        "riskKeywords": keywords,
        "riskLevel": level,
        "rawKeys": list(row.keys())[:30],
    }


def _normalize_ask_item(row: dict[str, Any], rank: int) -> dict[str, Any]:
    answers = row.get("topAnswerList") if isinstance(row.get("topAnswerList"), list) else []
    answer = ""
    if answers:
        first_answer = answers[0] if isinstance(answers[0], dict) else {}
        answer = _clean_text(first_answer.get("answerTitle") or first_answer.get("answer") or first_answer.get("content"))
    question = _clean_text(row.get("questionTitle") or row.get("question") or row.get("title") or row.get("content"))
    text = f"{question} {answer}"
    keywords, level = _risk_level(text)
    return {
        "rank": rank,
        "questionId": str(row.get("questionId") or ""),
        "question": question,
        "answer": answer,
        "answerCount": row.get("answerCount"),
        "date": row.get("gmtCreate") or row.get("gmtCreateStr"),
        "riskKeywords": keywords,
        "riskLevel": level,
        "rawKeys": list(row.keys())[:30],
    }


async def _tmall_item_reviews_api(args: dict) -> dict:
    if _is_production_env():
        return _direct_cookie_blocked_payload("item reviews CDP fetch")
    item_id = str(args["itemId"])
    limit = max(1, min(int(args.get("limit") or 20), 50))
    wait_seconds = float(args.get("waitSeconds") or 2)
    timeout_seconds = max(8.0, min(float(args.get("timeoutSeconds") or 20), 45.0))
    ws_url = get_page_ws_url(CDP_JSON_URL, item_id=item_id)

    async with CdpClient(ws_url, timeout_seconds=timeout_seconds) as cdp:
        await _wait_for_detail_mtop(cdp, item_id, wait_seconds)
        user_id = await cdp.evaluate(
            "window.__itempage_userinfo?.userNumId || window.__ICE_APP_CONTEXT__?.loaderData?.home?.data?.res?.user?.userNumId || ''"
        )
        rate_payload = {
            "showTrueCount": False,
            "auctionNumId": item_id,
            "pageNo": 1,
            "pageSize": limit,
            "orderType": "",
            "searchImpr": "-8",
            "expression": "",
            "skuVids": "",
            "rateSrc": "pc_rate_list",
            "rateType": "",
            "foldFlag": "0",
        }
        ask_payload = {
            "itemId": item_id,
            "pageSize": min(limit, 20),
            "page": 1,
            "type": "mix_group",
            "tagId": "",
            "extraInfo": json.dumps({"searchText": ""}, ensure_ascii=False),
            "ecode": 0,
            "biz": "pc",
        }
        if user_id:
            ask_payload["userId"] = user_id
        rate_result, ask_result = await asyncio.gather(
            _detail_mtop_request(
                cdp,
                api="mtop.taobao.rate.detaillist.get",
                version="6.0",
                data=rate_payload,
                options={"ecode": 1, "valueType": "string"},
                timeout_seconds=timeout_seconds,
            ),
            _detail_mtop_request(
                cdp,
                api="mtop.taobao.wdj.list.merge.search",
                version="1.0",
                data=ask_payload,
                options={"ecode": 0},
                timeout_seconds=timeout_seconds,
            ),
        )

    rate_data = ((rate_result.get("data") or {}).get("data") or {}) if isinstance(rate_result, dict) else {}
    ask_data = ((ask_result.get("data") or {}).get("data") or {}) if isinstance(ask_result, dict) else {}
    rate_rows = rate_data.get("rateList") if isinstance(rate_data.get("rateList"), list) else []
    if not rate_rows and isinstance(rate_data.get("feedList"), list):
        rate_rows = rate_data.get("feedList") or []
    question_rows = ask_data.get("questionList") if isinstance(ask_data.get("questionList"), list) else []
    reviews = [
        _normalize_rate_item(row, idx + 1)
        for idx, row in enumerate(rate_rows[:limit])
        if isinstance(row, dict)
    ]
    ask_answers = [
        _normalize_ask_item(row, idx + 1)
        for idx, row in enumerate(question_rows[:limit])
        if isinstance(row, dict)
    ]
    risk_items = [row for row in reviews if row.get("riskLevel") in {"medium", "high"}]
    qa_risk_items = [row for row in ask_answers if row.get("riskLevel") in {"medium", "high"}]
    api_errors = []
    if not rate_result.get("ok"):
        api_errors.append({"api": "mtop.taobao.rate.detaillist.get", "error": rate_result.get("error")})
    if not ask_result.get("ok"):
        api_errors.append({"api": "mtop.taobao.wdj.list.merge.search", "error": ask_result.get("error")})

    return {
        "source": "tmall_item_reviews_api",
        "itemId": item_id,
        "ok": bool(rate_result.get("ok") or ask_result.get("ok")),
        "api_coverage": "mtop_api",
        "api_endpoints": [
            "mtop.taobao.rate.detaillist.get/6.0",
            "mtop.taobao.wdj.list.merge.search/1.0",
        ],
        "reviewTotalText": rate_data.get("feedAllCountFuzzy") or rate_data.get("feedAllCount") or "",
        "reviewCount": len(reviews),
        "qaCount": len(ask_answers),
        "askCount": len(ask_answers),
        "askCollectionStatus": "api_collected" if ask_result.get("ok") else "api_error",
        "reviews": reviews,
        "askAnswers": ask_answers,
        "riskItems": risk_items,
        "qaRiskItems": qa_risk_items,
        "needsManualAction": any(
            (row.get("isPinned") and row.get("riskLevel") in {"medium", "high"}) or row.get("riskLevel") == "high"
            for row in reviews[:10]
        ) or any(row.get("riskLevel") == "high" for row in qa_risk_items[:10]),
        "errors": api_errors,
        "raw": {
            "rate": {
                "feedAllCount": rate_data.get("feedAllCount"),
                "feedAllCountFuzzy": rate_data.get("feedAllCountFuzzy"),
                "hasNext": rate_data.get("hasNext"),
                "imprItemVOS": rate_data.get("imprItemVOS")[:10] if isinstance(rate_data.get("imprItemVOS"), list) else [],
            },
            "ask": {
                "hasNext": ask_data.get("hasNext"),
                "foldingCount": ask_data.get("foldingCount"),
                "item": ask_data.get("item") if isinstance(ask_data.get("item"), dict) else {},
            },
        },
    }


async def _tmall_item_review_qa_check(args: dict) -> dict:
    item_id = str(args["itemId"])
    limit = max(1, min(int(args.get("limit") or 10), 50))
    wait_seconds = float(args.get("waitSeconds") or 2)
    timeout_seconds = max(5.0, min(float(args.get("timeoutSeconds") or 15), 45.0))
    try:
        raw = await _tmall_item_reviews_api({
            "itemId": item_id,
            "limit": limit,
            "waitSeconds": wait_seconds,
            "timeoutSeconds": timeout_seconds,
        })
        if raw.get("error") == MCP_DIRECT_COOKIE_BLOCKED:
            raw["reviewCount"] = 0
            raw["qaCount"] = 0
            return {
                "source": "tmall_item_review_qa_check",
                "itemId": item_id,
                "error": MCP_DIRECT_COOKIE_BLOCKED,
                "review_count": 0,
                "qa_count": 0,
                "ask_collection_status": "blocked",
                "has_pinned_negative_review": False,
                "is_new_pinned": False,
                "position_change": "",
                "risk_keywords": [],
                "screenshot_url": "",
                "has_negative_review_risk": False,
                "has_negative_qa_risk": False,
                "needs_manual_action": False,
                "negative_summary": "",
                "api_coverage": "blocked_direct_browser_access",
                "data_gap": MCP_DIRECT_COOKIE_BLOCKED,
                "raw": raw,
            }
    except Exception as exc:  # noqa: BLE001
        if MCP_DIRECT_COOKIE_BLOCKED in str(exc) or _is_production_env():
            raw = {"ok": False, "itemId": item_id, "error": MCP_DIRECT_COOKIE_BLOCKED, "reviewCount": 0, "qaCount": 0}
        else:
            try:
                raw = await asyncio.wait_for(
                    collect_reviews(item_id, limit, CDP_JSON_URL, wait_seconds, include_ask_all=True),
                    timeout=timeout_seconds,
                )
                raw["api_fallback_error"] = str(exc)
            except Exception as fallback_exc:  # noqa: BLE001
                raw = {"ok": False, "itemId": item_id, "error": f"{exc}; fallback: {fallback_exc}", "reviewCount": 0, "qaCount": 0}
    reviews = raw.get("reviews") if isinstance(raw.get("reviews"), list) else []
    risk_items = raw.get("riskItems") if isinstance(raw.get("riskItems"), list) else []
    qa_risk_items = raw.get("qaRiskItems") if isinstance(raw.get("qaRiskItems"), list) else []
    pinned_negative = [
        row for row in reviews[:10]
        if isinstance(row, dict) and row.get("isPinned") and row.get("riskLevel") in {"medium", "high"}
    ]
    first_risk = (pinned_negative or risk_items or [{}])[0]
    risk_keywords = []
    if isinstance(first_risk, dict) and isinstance(first_risk.get("riskKeywords"), list):
        risk_keywords = first_risk.get("riskKeywords") or []
    return {
        "source": "tmall_item_review_qa_check",
        "itemId": item_id,
        "review_count": raw.get("reviewCount") or len(reviews),
        "qa_count": raw.get("qaCount") or raw.get("askCount") or len(raw.get("askAnswers") or []),
        "ask_collection_status": raw.get("askCollectionStatus") or "unknown",
        "has_pinned_negative_review": bool(pinned_negative),
        "is_new_pinned": bool(pinned_negative),
        "position_change": "Top10 内置顶/疑似新置顶" if pinned_negative else "未发现置顶差评",
        "risk_keywords": risk_keywords[:6],
        "screenshot_url": "",
        "has_negative_review_risk": bool(risk_items),
        "has_negative_qa_risk": bool(qa_risk_items),
        "needs_manual_action": bool(pinned_negative or qa_risk_items),
        "negative_summary": str(
            (first_risk or {}).get("content")
            or (first_risk or {}).get("question")
            or (first_risk or {}).get("answer")
            or ""
        )[:300],
        "api_coverage": raw.get("api_coverage") or "dom_cdp_fallback",
        "data_gap": "" if raw.get("api_coverage") == "mtop_api" else "MTOP API 不可用时才降级天猫详情页 DOM/CDP 采集。",
        "source_link": {
            "label": "天猫详情页评价/问大家",
            "url": f"https://detail.tmall.com/item.htm?id={quote(item_id)}",
        },
        "raw": raw,
    }


def _missing_required_apis() -> list[dict[str, str]]:
    return [
        {
            "key": "item_level_activity_membership_api",
            "name": "天猫商家中心商品级活动参与/掉线详情 API",
            "status": "partial",
            "note": "已确认营销工具活动列表 API，但它是活动级状态；还不能直接证明某个商品 ID 是否仍在单品宝/商品券/买赠活动内。",
        },
        {
            "key": "official_final_hand_price_api",
            "name": "SKU 最终到手价/优惠叠加价格正确性 API",
            "status": "partial",
            "note": "已确认价格竞争力、五星价格力、高价限流和营销价格风险 API；仍缺少按商品/SKU 直接返回最终到手价是否正确的官方稳定接口。",
        },
    ]


def tmall_link_decline_required_context(args: dict) -> dict:
    args = _resolve_item_archives_args(args)
    item_id = str(args["itemId"])
    include_activity_price = bool(args.get("includeActivityPrice", True))
    include_review_qa = bool(args.get("includeReviewQA", True))
    result: dict[str, Any] = {
        "source": "tmall_link_decline_required_context",
        "itemId": item_id,
        "missing_apis": _missing_required_apis(),
    }
    errors: list[dict[str, str]] = []
    required_calls: list[tuple[str, Any, dict[str, Any]]] = [
        ("flow_metrics", tmall_item_flow_required_metrics, args),
        ("promotion_metrics", tmall_item_promotion_required_metrics, args),
    ]
    if include_activity_price:
        required_calls.append(("activity_price_check", tmall_item_activity_price_check, args))

    # 并行调用 flow + promotion + activity，避免串行超时叠加
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=len(required_calls)) as pool:
        fut_map = {pool.submit(fn, call_args): key for key, fn, call_args in required_calls}
        for fut in as_completed(fut_map):
            key = fut_map[fut]
            try:
                result[key] = fut.result()
            except Exception as exc:
                result[key] = {"error": str(exc)}
                errors.append({"source": key, "error": str(exc)})
    if include_review_qa:
        try:
            result["review_qa_check"] = asyncio.run(_tmall_item_review_qa_check({
                "itemId": item_id,
                "limit": args.get("reviewLimit") or 10,
                "waitSeconds": args.get("waitSeconds") or 2,
                "timeoutSeconds": args.get("timeoutSeconds") or 15,
            }))
        except Exception as exc:  # noqa: BLE001
            result["review_qa_check"] = {"error": str(exc)}
            errors.append({"source": "review_qa_check", "error": str(exc)})
    result["errors"] = errors
    return result


async def _tmall_item_reviews(args: dict) -> dict:
    item_id = str(args["itemId"])
    limit = max(1, min(int(args.get("limit") or 20), 50))
    wait_seconds = float(args.get("waitSeconds") or 2)
    timeout_seconds = max(5.0, min(float(args.get("timeoutSeconds") or 15), 45.0))
    try:
        result = await _tmall_item_reviews_api({
            "itemId": item_id,
            "limit": limit,
            "waitSeconds": wait_seconds,
            "timeoutSeconds": timeout_seconds,
        })
        if result.get("error") == MCP_DIRECT_COOKIE_BLOCKED:
            return result
        return result
    except Exception as exc:  # noqa: BLE001
        if MCP_DIRECT_COOKIE_BLOCKED in str(exc) or _is_production_env():
            return {
                "source": "tmall_item_reviews",
                "itemId": item_id,
                "ok": False,
                "error": MCP_DIRECT_COOKIE_BLOCKED,
                "reviewCount": 0,
                "qaCount": 0,
                "api_coverage": "blocked_direct_browser_access",
            }
        include_ask_all = bool(args.get("includeAskAll", True))
        result = await asyncio.wait_for(
            collect_reviews(item_id, limit, CDP_JSON_URL, wait_seconds, include_ask_all=include_ask_all),
            timeout=timeout_seconds,
        )
        result["api_fallback_error"] = str(exc)
        return result


_CHILD_TOOL_CONTEXT_KEYS = (
    "shop_id",
    "shopId",
    "source_id",
    "sourceId",
    "data_source_id",
    "dataSourceId",
    "credential_scope",
    "credentialScope",
    "credential_plan_id",
    "credentialPlanId",
)


def _inherited_child_tool_args(parent_args: dict | None) -> dict:
    if not isinstance(parent_args, dict):
        return {}
    return {key: parent_args[key] for key in _CHILD_TOOL_CONTEXT_KEYS if key in parent_args}


def _run_child_tool(tool_name: str, func, args: dict) -> Any:
    parent_args = _CURRENT_TOOL_ARGS.get({}) or {}
    child_args = {**_inherited_child_tool_args(parent_args), **dict(args or {})}
    token_name = _CURRENT_TOOL_NAME.set(tool_name)
    token_args = _CURRENT_TOOL_ARGS.set(child_args)
    try:
        return func(child_args)
    finally:
        _CURRENT_TOOL_ARGS.reset(token_args)
        _CURRENT_TOOL_NAME.reset(token_name)


def _compact_rows(value: Any, limit: int = 10) -> list:
    if not isinstance(value, dict):
        return []
    rows = value.get("rows")
    if isinstance(rows, list):
        return rows[:limit]
    return []


def tmall_store_weekly_snapshot(args: dict) -> dict:
    date_range = args.get("dateRange") or _recent_complete_days_range(7)
    date_type = args.get("dateType") or "recent7"
    shop_id = _current_shop_id()
    proofs: list[dict] = []
    token_proofs = _CURRENT_PROOFS.set(proofs)
    facts: dict[str, Any] = {}
    missing_scopes: list[str] = []

    try:
        child_specs = [
            (
                "tmall_sycm_item_rank_top",
                tmall_sycm_item_rank_top,
                {
                    "limit": int(args.get("rankLimit") or args.get("limit") or 20),
                    "dateRange": date_range,
                    "dateType": date_type,
                    "orderBy": args.get("orderBy") or "payAmt",
                },
            ),
            (
                "tmall_sycm_market_rank",
                tmall_sycm_market_rank,
                {
                    "limit": int(args.get("marketLimit") or 10),
                    "dateRange": date_range,
                    "dateType": date_type,
                    "categoryPreset": args.get("categoryPreset") or "contraceptive_condom",
                    "includeDetailRows": bool(args.get("includeDetailRows", False)),
                },
            ),
        ]
        if args.get("itemId"):
            child_specs.append((
                "tmall_item_promotion_required_metrics",
                tmall_item_promotion_required_metrics,
                {
                    "itemId": str(args["itemId"]),
                    "dateRange": args.get("promotionDateRange") or _today_range(),
                    "compareDateRange": args.get("compareDateRange"),
                },
            ))

        for tool_name, func, child_args in child_specs:
            child_args.setdefault("shop_id", shop_id)
            meta = TMALL_TOOL_META[tool_name]
            try:
                facts[tool_name] = _run_child_tool(tool_name, func, child_args)
            except Exception as exc:  # noqa: BLE001
                missing_scopes.append(meta.data_scope)
                facts[tool_name] = {"source": tool_name, "error": str(exc), "data_scope": meta.data_scope}
    finally:
        _CURRENT_PROOFS.reset(token_proofs)

    child_calls = proofs
    collected_scope_count = len({item.get("data_scope") for item in child_calls if item.get("status") == "success"})
    requested_scope_count = max(1, collected_scope_count + len(set(missing_scopes)))
    snapshot_body = {"dateRange": date_range, "dateType": date_type, "facts": facts, "proofs": child_calls}
    return {
        "context_pack": {
            "meta": {
                "source": "tmall_store_weekly_snapshot",
                "shop_id": shop_id,
                "dateRange": date_range,
                "dateType": date_type,
            },
            "store_summary": {
                "item_rank_top": _compact_rows(facts.get("tmall_sycm_item_rank_top"), limit=10),
                "market_rank_top": _compact_rows(facts.get("tmall_sycm_market_rank"), limit=10),
            },
            "facts": facts,
            "analysis_focus": args.get("analysis_focus") or args.get("analysisFocus") or [],
            "excluded": {"missing_data_scopes": sorted(set(missing_scopes))},
        },
        "_skillforge_meta": {
            "data_proofs": [item["proof_id"] for item in child_calls if item.get("proof_id")],
            "data_health_ratio": round(collected_scope_count / requested_scope_count, 4),
            "missing_data_scopes": sorted(set(missing_scopes)),
            "mixed_credentials": len({item.get("credential_alias") for item in child_calls if item.get("credential_alias")}) > 1,
            "snapshot_hash": hashlib.sha256(json.dumps(snapshot_body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
            "child_tool_calls": child_calls,
        },
    }


def _handle_tool_call_dispatch(name: str, arguments: dict) -> str:
    """工具派发（同步，由 SyncMcpServer 基类在独立线程中调用 → asyncio.run 安全）。"""
    if name == "tmall_sycm_item_rank_summary":
        return json.dumps(tmall_sycm_item_rank_summary(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_sycm_item_rank_top":
        return json.dumps(tmall_sycm_item_rank_top(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_sycm_item_detail":
        return json.dumps(tmall_sycm_item_detail(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_sycm_item_flow_sources":
        return json.dumps(tmall_sycm_item_flow_sources(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_sycm_item_360_metrics":
        return json.dumps(tmall_sycm_item_360_metrics(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_item_flow_required_metrics":
        return json.dumps(tmall_item_flow_required_metrics(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_item_flow_required_metrics_batch":
        return json.dumps(tmall_item_flow_required_metrics_batch(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_sycm_market_rank":
        return json.dumps(tmall_sycm_market_rank(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_sycm_activity_price":
        return json.dumps(tmall_sycm_activity_price(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_seller_price_competitiveness_check":
        return json.dumps(tmall_seller_price_competitiveness_check(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_seller_marketing_activity_list":
        return json.dumps(tmall_seller_marketing_activity_list(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_seller_price_risk_check":
        return json.dumps(tmall_seller_price_risk_check(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_item_activity_price_check":
        return json.dumps(tmall_item_activity_price_check(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_alimama_item_promotion":
        return json.dumps(tmall_alimama_item_promotion(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_alimama_item_promotion_compare":
        return json.dumps(tmall_alimama_item_promotion_compare(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_item_promotion_required_metrics":
        return json.dumps(tmall_item_promotion_required_metrics(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_item_reviews":
        return json.dumps(asyncio.run(_tmall_item_reviews(arguments)), ensure_ascii=False, indent=2)
    if name == "tmall_item_reviews_api":
        return json.dumps(asyncio.run(_tmall_item_reviews_api(arguments)), ensure_ascii=False, indent=2)
    if name == "tmall_item_review_qa_check":
        return json.dumps(asyncio.run(_tmall_item_review_qa_check(arguments)), ensure_ascii=False, indent=2)
    if name == "tmall_link_decline_required_context":
        return json.dumps(tmall_link_decline_required_context(arguments), ensure_ascii=False, indent=2)
    if name == "tmall_store_weekly_snapshot":
        return json.dumps(tmall_store_weekly_snapshot(arguments), ensure_ascii=False, indent=2)
    return f"未知工具: {name}"


def _handle_tool_call_impl(name: str, arguments: dict) -> str:
    token_name = _CURRENT_TOOL_NAME.set(name)
    token_args = _CURRENT_TOOL_ARGS.set(dict(arguments or {}))
    token_proofs = _CURRENT_PROOFS.set([])
    try:
        text = _handle_tool_call_dispatch(name, arguments)
        proofs = _CURRENT_PROOFS.get() or []
        if not proofs:
            return text
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if not isinstance(payload, dict):
            return text
        meta = dict(payload.get("_skillforge_meta") or {})
        proof_ids = [item["proof_id"] for item in proofs if item.get("proof_id")]
        existing_ids = meta.get("data_proofs") if isinstance(meta.get("data_proofs"), list) else []
        meta["data_proofs"] = [*existing_ids, *[proof_id for proof_id in proof_ids if proof_id not in existing_ids]]
        existing_proofs = meta.get("collection_proofs") if isinstance(meta.get("collection_proofs"), list) else []
        meta["collection_proofs"] = [*existing_proofs, *proofs]
        payload["_skillforge_meta"] = meta
        return json.dumps(payload, ensure_ascii=False, indent=2)
    finally:
        _CURRENT_PROOFS.reset(token_proofs)
        _CURRENT_TOOL_ARGS.reset(token_args)
        _CURRENT_TOOL_NAME.reset(token_name)


# ── MCP Server ──

class TmallMcpServer(SyncMcpServer):
    """Tmall MCP Server — 混合 sync/async 后端，SyncMcpServer 基类将同步 handle_tool_call
    运行在独立线程中（asyncio.to_thread），确保内部 asyncio.run() 不会嵌套 event loop，
    同时外层有 asyncio.wait_for 超时保护。
    """

    server_name = "tmall"
    DEFAULT_TIMEOUT = 90  # tmall 工具多依赖 CDP/HTTP 调用，默认超时比其它 server 高

    TOOLS = [
        {"name": "tmall_sycm_item_rank_summary", "description": "全店排行摘要：今日 VS 昨日访客/转化/成交前三商品 label 及关键指标变化。", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 50}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}, "dateType": {"type": "string", "default": "today"}}}},
        {"name": "tmall_sycm_item_rank_top", "description": "商品排行榜 Top N，含访客/支付金额/转化等 realtime 指标和环比。", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 50}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}, "dateType": {"type": "string", "default": "today"}, "orderBy": {"type": "string", "default": "realtime_payment"}, "orderDir": {"type": "string", "default": "desc"}}}},
        {"name": "tmall_sycm_item_detail", "description": "商品360详情：属性诊断/人群/等级/流量结构。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}, "dateType": {"type": "string", "default": "today"}}, "required": ["itemId"]}},
        {"name": "tmall_sycm_item_flow_sources", "description": "商品流量来源：按日期/商品查询免费/付费/搜索/推荐等渠道访客，可传item_archives链接自动解析商品ID。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "itemArchivesUrl": {"type": "string"}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}, "dateType": {"type": "string", "default": "today"}}}},
        {"name": "tmall_sycm_item_360_metrics", "description": "商品360核心指标：经营优势-搜索访客/转化/成交、付费推广-无界-关键词推广、流量来源结构，可传item_archives链接自动解析商品ID。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "itemArchivesUrl": {"type": "string"}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}, "dateType": {"type": "string", "default": "today"}}}},
        {"name": "tmall_item_flow_required_metrics", "description": "商品流量来源必采集合：经营优势-搜索访客/转化/成交 + 付费推广-无界-关键词推广访客/转化，可传item_archives链接自动解析商品ID和day/today口径。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "itemArchivesUrl": {"type": "string"}, "dateRange": {"type": "string"}}}},
        {"name": "tmall_item_flow_required_metrics_batch", "description": "批量获取商品360流量来源搜索节点日数据。", "inputSchema": {"type": "object", "properties": {"itemIds": {"type": "array", "items": {"type": "string"}}, "dateRange": {"type": "string"}, "compareDateRange": {"type": "string"}, "limit": {"type": "integer", "default": 200}}, "required": ["itemIds"]}},
        {"name": "tmall_sycm_market_rank", "description": "市场排行：指定类目 Top 商品排行，默认压缩 Top300 行以降低 MCP 输出体积。", "inputSchema": {"type": "object", "properties": {"parentCateId": {"type": "string"}, "cateId": {"type": "string"}, "cateFlag": {"type": "integer", "default": 2}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}, "dateType": {"type": "string", "default": "today"}, "rankType": {"type": "string", "default": "gmv"}, "sellerType": {"type": "integer", "default": -1}, "limit": {"type": "integer", "default": 300}, "includeDetailRows": {"type": "boolean", "default": False}}}},
        {"name": "tmall_sycm_activity_price", "description": "活动价格数据：大促/日常活动期间商品价格与优惠信息。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "dateRange": {"type": "string", "default": "2026-04-24|2026-04-24"}}, "required": ["itemId"]}},
        {"name": "tmall_seller_price_competitiveness_check", "description": "商家中心价格竞争力/五星价格力检查。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "waitSeconds": {"type": "integer", "default": 2}, "timeoutSeconds": {"type": "integer", "default": 15}}, "required": ["itemId"]}},
        {"name": "tmall_seller_marketing_activity_list", "description": "商家中心营销活动列表：单品宝/商品券/买赠等。", "inputSchema": {"type": "object", "properties": {"waitSeconds": {"type": "integer", "default": 3}, "timeoutSeconds": {"type": "integer", "default": 20}}}},
        {"name": "tmall_seller_price_risk_check", "description": "商家中心价格风险检查：高价限流/资损/价格异常。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "waitSeconds": {"type": "integer", "default": 2}, "timeoutSeconds": {"type": "integer", "default": 15}}, "required": ["itemId"]}},
        {"name": "tmall_item_activity_price_check", "description": "组合采集商家中心价格竞争力/营销活动/价格风险 + 生意参谋活动价格。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "waitSeconds": {"type": "integer", "default": 3}, "timeoutSeconds": {"type": "integer", "default": 30}}, "required": ["itemId"]}},
        {"name": "tmall_alimama_item_promotion", "description": "阿里妈妈商品推广实时数据：花费/ROI/CPC/PPC 及环比。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "dateRange": {"type": "string"}}, "required": ["itemId"]}},
        {"name": "tmall_alimama_item_promotion_compare", "description": "阿里妈妈商品推广两期对比：当前期 VS 对比期花费/ROI/CPC 及环比。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "dateRange": {"type": "string"}, "compareDateRange": {"type": "string"}}, "required": ["itemId"]}},
        {"name": "tmall_item_promotion_required_metrics", "description": "商品推广必采集合：manage/search itemId筛选底部合计（花费、直接成交金额、投入产出比、点击转化率、平均点击花费、点击率）及环比。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "dateRange": {"type": "string"}}, "required": ["itemId", "dateRange"]}},
        {"name": "tmall_item_reviews", "description": "商品评价采集：top N 评价内容/日期/SKU/追评/图片。优先 MTOP API，失败降级 DOM。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "limit": {"type": "integer", "default": 10}, "waitSeconds": {"type": "integer", "default": 2}, "timeoutSeconds": {"type": "integer", "default": 15}, "includeAskAll": {"type": "boolean", "default": True}}, "required": ["itemId"]}},
        {"name": "tmall_item_reviews_api", "description": "商品评价：仅 MTOP API 采集（不做 DOM fallback），适合快速采样。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "limit": {"type": "integer", "default": 10}, "timeoutSeconds": {"type": "integer", "default": 12}}, "required": ["itemId"]}},
        {"name": "tmall_item_review_qa_check", "description": "评价问大家综合检查：top评价+问大家+置顶差评风险+采集口径。优先MTOP API，失败DOM。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "limit": {"type": "integer", "default": 10}, "waitSeconds": {"type": "integer", "default": 2}, "timeoutSeconds": {"type": "integer", "default": 15}}, "required": ["itemId"]}},
        {"name": "tmall_link_decline_required_context", "description": "天猫链接下滑必采上下文：流量+推广+活动价格+评价问大家。生产Skill采集入口。", "inputSchema": {"type": "object", "properties": {"itemId": {"type": "string"}, "dateRange": {"type": "string"}, "includeActivityPrice": {"type": "boolean", "default": True}, "includeReviewQA": {"type": "boolean", "default": True}, "reviewLimit": {"type": "integer", "default": 10}, "waitSeconds": {"type": "integer", "default": 2}, "timeoutSeconds": {"type": "integer", "default": 15}}, "required": ["itemId"]}},
        {"name": "tmall_store_weekly_snapshot", "description": "全店周度采集快照：组合商品排行、市场排行和可选推广指标，返回 context_pack 与子采集 proof 汇总。", "inputSchema": {"type": "object", "properties": {"shop_id": {"type": "string"}, "dateRange": {"type": "string"}, "dateType": {"type": "string", "default": "recent7"}, "itemId": {"type": "string"}, "limit": {"type": "integer", "default": 20}, "marketLimit": {"type": "integer", "default": 10}, "categoryPreset": {"type": "string", "default": "contraceptive_condom"}}}},
    ]

    def effective_timeout(self, tool_name: str, tool_args: dict) -> int:
        if tool_name in ("tmall_item_review_qa_check", "tmall_item_reviews", "tmall_link_decline_required_context"):
            return max(self.DEFAULT_TIMEOUT, int(tool_args.get("timeoutSeconds", 15)) + 15)
        if tool_name == "tmall_item_flow_required_metrics_batch":
            item_count = len(_item_ids_arg(tool_args.get("itemIds")))
            return max(self.DEFAULT_TIMEOUT, min(1800, item_count * 20 + 30))
        if tool_name == "tmall_sycm_item_rank_top":
            return max(self.DEFAULT_TIMEOUT, 120)
        if tool_name == "tmall_sycm_market_rank":
            return max(self.DEFAULT_TIMEOUT, 120)
        return self.DEFAULT_TIMEOUT

    def handle_tool_call(self, name: str, arguments: dict) -> str:
        return _handle_tool_call_impl(name, arguments)


TmallMcpServer.TOOLS = _with_tool_meta(_with_catalog_schema(TmallMcpServer.TOOLS))

# 向后兼容：模块级 handle_tool_call（供测试直接调用）
_server = TmallMcpServer()
handle_tool_call = _handle_tool_call_impl

if __name__ == "__main__":
    if not (os.environ.get("SKILLFORGE_PLATFORM_URL") or os.environ.get("SKILLFORGE_BASE_URL") or os.environ.get("SKILLFORGE_HTTP_BASE")):
        sys.stderr.write("[tmall-mcp] mcp_env_missing: SKILLFORGE_PLATFORM_URL\n")
        sys.exit(2)
    try:
        asyncio.run(_server.main())
    except KeyboardInterrupt:
        pass
