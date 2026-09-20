import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_tmall_mcp_server():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    path = scripts_dir / "tmall_mcp_server.py"
    spec = importlib.util.spec_from_file_location("tmall_mcp_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["tmall_mcp_server"] = module
    spec.loader.exec_module(module)
    return module


def _fetch_payload(payload):
    return {
        "success": True,
        "data": {
            "status": 200,
            "ok": True,
            "data": payload,
        },
    }


def test_api_payload_accepts_governed_collection_direct_json():
    mod = _load_tmall_mcp_server()

    payload = mod._api_payload({
        "success": True,
        "data": {
            "code": 0,
            "data": {
                "data": {
                    "recordCount": 0,
                    "data": [],
                },
            },
        },
        "proof": {"proof_id": "proof-rank"},
    })

    assert payload["code"] == 0
    assert payload["data"]["data"]["recordCount"] == 0


def test_sycm_item_rank_top_normalizes_rows(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        assert "item/live/view/top.json" in url
        return _fetch_payload({
            "code": 0,
            "data": {
                "updateTime": "2026-04-22 17:00:00",
                "data": {
                    "recordCount": 1,
                    "data": [
                        {
                            "item": {
                                "itemId": "8001",
                                "title": "商品A",
                                "detailUrl": "//detail.tmall.com/item.htm?id=8001",
                            },
                            "payAmt": {"value": 100.5, "cycleCrc": -0.2},
                            "itmUv": {"value": 20, "cycleCrc": -0.1},
                            "payRate": {"value": 0.05},
                        }
                    ],
                },
            },
        })

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    text = mod.handle_tool_call("tmall_sycm_item_rank_top", {"limit": 1, "dateRange": "2026-04-22|2026-04-22"})
    data = json.loads(text)
    assert data["recordCount"] == 1
    assert data["rows"][0]["itemId"] == "8001"
    assert data["rows"][0]["title"] == "商品A"
    assert data["rows"][0]["detailUrl"].startswith("https://detail.tmall.com/")
    assert data["rows"][0]["payAmtCycleCrc"] == -0.2
    assert data["asOfTime"] == "2026-04-22 17:00:00"
    assert data["supportsMinuteCompare"] is False


def test_sycm_item_rank_top_preserves_order_by_for_rank_metrics(monkeypatch):
    mod = _load_tmall_mcp_server()
    seen_urls = []

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        seen_urls.append(url)
        return _fetch_payload({
            "code": 0,
            "data": {
                "updateTime": "2026-04-22 17:00:00",
                "data": {
                    "recordCount": 1,
                    "data": [
                        {
                            "item": {"itemId": "8001", "title": "商品A"},
                            "payAmt": {"value": 100.5},
                            "itmUv": {"value": 20},
                            "payItmCnt": {"value": 3},
                        }
                    ],
                },
            },
        })

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = json.loads(mod.handle_tool_call("tmall_sycm_item_rank_top", {
        "limit": 1,
        "dateRange": "2026-04-22|2026-04-22",
        "orderBy": "payItmCnt",
    }))

    assert data["orderBy"] == "payItmCnt"
    assert "orderBy=payItmCnt" in seen_urls[0]


def test_sycm_item_flow_sources_flattens_tree(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        assert "source/tree/support.json" in url
        return _fetch_payload({
            "code": 0,
            "data": [
                {
                    "pageName": {"value": "付费推广"},
                    "pageLevel": {"value": 1},
                    "uv": {"value": 100},
                    "children": [
                        {
                            "pageName": {"value": "无界"},
                            "pageLevel": {"value": 2},
                            "uv": {"value": 80},
                            "children": [],
                        }
                    ],
                }
            ],
        })

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_sycm_item_flow_sources({"itemId": "8001", "dateRange": "2026-04-21|2026-04-21"})
    assert data["rootCount"] == 1
    assert data["nodeCount"] == 2
    assert data["nodes"][1]["path"] == "付费推广 > 无界"
    assert data["supportsMinuteCompare"] is False


def test_flow_minute_compare_returns_explicit_unsupported():
    mod = _load_tmall_mcp_server()

    data = mod.tmall_item_flow_required_metrics({
        "itemId": "8001",
        "timeRange": "2026-04-23 00:00:00|2026-04-23 08:55:00",
        "compareTimeRange": "2026-04-22 00:00:00|2026-04-22 08:55:00",
    })

    assert data["supported"] is False
    assert data["supportsMinuteCompare"] is False
    assert data["free_flow_basis"]["found"] is False
    assert "不支持分钟级" in data["data_quality"][0]


def test_sycm_item_360_metrics_compares_sources(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_flow(args):
        current = args["dateRange"] == "2026-04-22|2026-04-22"
        if current:
            roots = [
                {"name": "经营优势", "path": "经营优势", "uv": 120, "payByrCnt": 6},
                {"name": "付费推广", "path": "付费推广", "uv": 40, "payByrCnt": 1},
            ]
            nodes = [
                *roots,
                {"name": "手淘搜索", "path": "经营优势 > 手淘搜索", "uv": 80, "payByrCnt": 4},
            ]
        else:
            roots = [
                {"name": "经营优势", "path": "经营优势", "uv": 150, "payByrCnt": 9},
                {"name": "付费推广", "path": "付费推广", "uv": 20, "payByrCnt": 1},
            ]
            nodes = [
                *roots,
                {"name": "手淘搜索", "path": "经营优势 > 手淘搜索", "uv": 100, "payByrCnt": 8},
            ]
        return {
            "source": "sycm_item_flow_sources",
            "itemId": args["itemId"],
            "effectiveDateRange": args["dateRange"],
            "dateType": args.get("dateType") or "day",
            "rootCount": len(roots),
            "nodeCount": len(nodes),
            "roots": roots,
            "nodes": nodes,
        }

    def fake_detail(args):
        return {"source": "sycm_item_detail", "itemId": args["itemId"], "item": {"title": "商品A"}}

    monkeypatch.setattr(mod, "tmall_sycm_item_flow_sources", fake_flow)
    monkeypatch.setattr(mod, "tmall_sycm_item_detail", fake_detail)

    data = mod.tmall_sycm_item_360_metrics({
        "itemId": "8001",
        "dateRange": "2026-04-22|2026-04-22",
        "compareDateRange": "2026-04-21|2026-04-21",
    })
    assert data["freeSearch"]["visitor"] == 80
    assert data["freeSearch"]["compareVisitor"] == 100
    assert data["freeSearch"]["visitorChangePct"] == -20.0
    assert data["paidTraffic"]["visitor"] == 40
    assert data["paidTraffic"]["visitorChangePct"] == 100.0
    assert data["detail"]["item"]["title"] == "商品A"


def test_sycm_item_detail_reuses_browser_fetch(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        if "crowd/info" in url:
            return _fetch_payload({"code": 0, "data": {"title": "商品A", "itemScore": 88, "detailUrl": "//detail.tmall.com/item.htm?id=8001"}})
        if "property/suggestions" in url:
            return _fetch_payload({"code": 0, "data": [{"shortTips": "SKU属性存在可优化项", "effectDesc": "流量受限"}]})
        if "getItemCrowds" in url:
            return _fetch_payload({"code": 0, "data": {"crowdList": [{"crowdSize": 12, "crowdDesc": "访问未支付"}]}})
        if "new/product/check" in url:
            return _fetch_payload({"code": 0, "data": False})
        if "supersku" in url:
            return _fetch_payload({"code": 0, "data": [8001]})
        if "priceUpControl" in url:
            return _fetch_payload({"content": {"code": 0, "data": []}})
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_sycm_item_detail({"itemId": "8001", "dateRange": "2026-04-21|2026-04-21"})
    assert data["item"]["title"] == "商品A"
    assert data["diagnostics"][0]["shortTips"] == "SKU属性存在可优化项"
    assert data["crowds"][0]["crowdSize"] == 12


def test_sycm_market_rank_wraps_market_api(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        if "getCateInfo" in url:
            return _fetch_payload({
                "code": 0,
                "data": [
                    [0, 2813, "成人用品/情趣用品", 1, "free", "N", 2813, "成人用品/情趣用品"],
                    [2813, 50019618, "阴臀倒模", 0, "free", "Y", 2813, "成人用品/情趣用品"],
                ],
            })
        if "priceSeg/list" in url:
            assert "cateId=50019618" in url
            return _fetch_payload({"code": 0, "data": [{"priceSegId": "1", "priceSegName": "0-95"}]})
        if "commDateByLocation" in url:
            return _fetch_payload({"code": 0, "data": {"cate_mkt_rank_itm": {"updateDay": "2026-04-21"}}})
        if "item/offline/rank" in url:
            assert "rankType=gmv" in url
            return _fetch_payload({
                "code": 0,
                "data": {
                    "recordCount": 1,
                    "data": [
                        {
                            "item": {
                                "itemId": "9001",
                                "title": "竞品A",
                                "detailUrl": "//detail.tmall.com/item.htm?id=9001",
                            },
                            "uv": {"value": "1万 ~ 2.5万"},
                            "payByrCnt": {"value": "1000 ~ 2500"},
                            "cateRankId": {"value": 1},
                        }
                    ],
                },
            })
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_sycm_market_rank({"limit": 1})
    assert data["cateId"] == "50019618"
    assert data["recordCount"] == 1
    assert data["priceSegments"][0]["priceSegName"] == "0-95"
    assert data["rows"][0]["itemId"] == "9001"
    assert data["rows"][0]["uv"] == "1万 ~ 2.5万"


def test_sycm_market_rank_supports_condom_category_preset(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        if "getCateInfo" in url:
            assert "parentCateId=50024153" in page_url
            assert "cateId=50024154" in page_url
            assert "cateFlag=2" in page_url
            return _fetch_payload({"code": 0, "data": []})
        if "priceSeg/list" in url:
            assert "cateId=50024154" in url
            return _fetch_payload({"code": 0, "data": []})
        if "commDateByLocation" in url:
            return _fetch_payload({"code": 0, "data": {}})
        if "item/offline/rank" in url:
            assert "parentCateId=50024153" in url
            assert "cateId=50024154" in url
            assert "cateFlag=2" in url
            assert "minPrice=90" in url
            assert "maxPrice=110" in url
            return _fetch_payload({
                "code": 0,
                "data": {
                    "recordCount": 1,
                    "data": [
                        {
                            "item": {"itemId": "9002", "title": "避孕套竞品"},
                            "uv": {"value": 100},
                        }
                    ],
                },
            })
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_sycm_market_rank({
        "limit": 1,
        "categoryPreset": "contraceptive_condom",
        "minPrice": 90,
        "maxPrice": 110,
    })
    assert data["categoryLabel"] == "计生品类/避孕套"
    assert data["parentCateId"] == "50024153"
    assert data["cateId"] == "50024154"
    assert data["cateFlag"] == 2
    assert data["priceRange"] == "90-110"
    assert data["rows"][0]["itemId"] == "9002"


def test_sycm_market_rank_html_challenge_skips_slow_browser_fallback(monkeypatch):
    mod = _load_tmall_mcp_server()
    browser_calls = []

    def fake_direct(url, method="GET", headers=None, body=None, page_url=None):
        return {
            "success": True,
            "data": {
                "url": url,
                "status": 200,
                "ok": True,
                "content_type": "text/html;charset=UTF-8",
                "data": None,
                "text_preview": "_____tmd_____ /punish?x5secdata=abc",
            },
        }

    def fake_browser_fetch(**kwargs):
        browser_calls.append(kwargs)
        raise AssertionError("browser fallback should be skipped for market rank HTML challenge")

    monkeypatch.setattr(mod, "_direct_taobao_fetch", fake_direct)
    monkeypatch.setattr(mod.browser_mcp, "_fetch_json", fake_browser_fetch)
    monkeypatch.setenv("TMALL_MCP_DIRECT_FETCH_ALLOWED", "1")

    result = mod._fetch_json(f"{mod.SYCM_MARKET_ITEM_RANK_API}?page=1")

    assert result["success"] is False
    assert "安全校验" in result["error"]
    assert browser_calls == []


def test_tmall_tool_meta_exposes_market_rank_schema():
    mod = _load_tmall_mcp_server()

    meta = mod.TMALL_TOOL_META["tmall_sycm_market_rank"]
    schema = meta.input_schema or {}
    props = schema.get("properties") or {}

    assert meta.description.startswith("获取生意参谋市场排行竞品")
    assert props["parentCateId"]["description"] == "父类目 ID；计生品类/避孕套为 50024153"
    assert props["cateId"]["description"] == "市场类目 ID；不传则取当前店铺默认叶子类目"
    assert props["cateFlag"]["default"] == 0
    assert props["categoryPreset"]["description"].startswith("预置类目")
    assert props["limit"]["maximum"] == 300
    assert props["includeDetailRows"]["default"] is False
    assert props["indexCode"]["default"] == "payByrCnt,uv"


def test_tmall_server_tool_list_uses_catalog_market_rank_schema():
    mod = _load_tmall_mcp_server()
    tools = {tool["name"]: tool for tool in mod.TmallMcpServer.TOOLS}
    props = tools["tmall_sycm_market_rank"]["inputSchema"]["properties"]

    assert "categoryPreset" in props
    assert "indexCode" in props
    assert "minPrice" in props
    assert "maxPrice" in props
    assert tools["tmall_sycm_market_rank"]["toolMeta"]["data_scope"] == "sycm.market_rank"


def test_tmall_registered_tool_uses_collection_client_not_direct(monkeypatch):
    mod = _load_tmall_mcp_server()
    calls = []

    def fail_direct(*args, **kwargs):
        raise AssertionError("direct cookie fetch must not be used by registered tool")

    def fake_collection_fetch(
        meta,
        shop_id,
        params,
        source_id=None,
        credential_scope=None,
        credential_plan_id=None,
        timeout=None,
    ):
        calls.append((meta, shop_id, params, source_id, credential_scope, credential_plan_id))
        assert meta.tool_name == "tmall_sycm_item_rank_top"
        assert params["url"].startswith(mod.SYCM_RANK_API)
        return {
            "success": True,
            "data": {
                "url": params["url"],
                "status": 200,
                "ok": True,
                "data": {
                    "code": 0,
                    "data": {
                        "updateTime": "2026-04-22 17:00:00",
                        "data": {
                            "recordCount": 1,
                            "data": [{"item": {"itemId": "8001", "title": "商品A"}, "payAmt": {"value": 1}}],
                        },
                    },
                },
            },
            "proof": {"proof_id": "proof-rank", "response_hash": "hash-rank"},
            "proof_id": "proof-rank",
            "credential_alias": "sycm-shop-legacy",
        }

    monkeypatch.setattr(mod, "_direct_taobao_fetch", fail_direct)
    monkeypatch.setattr(mod.collection_client, "fetch", fake_collection_fetch)

    data = json.loads(mod.handle_tool_call(
        "tmall_sycm_item_rank_top",
        {
            "limit": 1,
            "shop_id": "shop-1",
            "source_id": "platform-sycm-team-a",
            "credential_scope": "team-a",
            "credential_plan_id": "plan-1",
        },
    ))

    assert data["recordCount"] == 1
    assert calls
    assert calls[0][3:] == ("platform-sycm-team-a", "team-a", "plan-1")
    assert data["_skillforge_meta"]["data_proofs"] == ["proof-rank"]
    assert data["_skillforge_meta"]["collection_proofs"][0]["proof_id"] == "proof-rank"
    assert data["_skillforge_meta"]["collection_proofs"][0]["tool_name"] == "tmall_sycm_item_rank_top"


def test_tmall_production_blocks_direct_cookie_and_browser_mcp(monkeypatch):
    mod = _load_tmall_mcp_server()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TMALL_MCP_DIRECT_FETCH_ALLOWED", "1")

    direct = mod._direct_taobao_fetch("https://sycm.taobao.com/cc/item/live/view/top.json")
    assert direct["success"] is False
    assert direct["error"] == "MCP_DIRECT_COOKIE_BLOCKED"

    def fail_browser_fetch(**kwargs):
        raise AssertionError("browser_mcp._fetch_json must be blocked for production MCP tools")

    monkeypatch.setattr(mod.browser_mcp, "_fetch_json", fail_browser_fetch)
    token_name = mod._CURRENT_TOOL_NAME.set("legacy_unregistered_tool")
    try:
        result = mod._fetch_json("https://sycm.taobao.com/cc/item/live/view/top.json")
    finally:
        mod._CURRENT_TOOL_NAME.reset(token_name)

    assert result["success"] is False
    assert result["error"] == "MCP_DIRECT_COOKIE_BLOCKED"


def test_tmall_production_blocks_direct_cdp_review_fallback(monkeypatch):
    mod = _load_tmall_mcp_server()
    monkeypatch.setenv("APP_ENV", "production")

    async def fail_collect_reviews(*args, **kwargs):
        raise AssertionError("CDP DOM fallback must not run in production")

    monkeypatch.setattr(mod, "collect_reviews", fail_collect_reviews)
    data = json.loads(mod.handle_tool_call("tmall_item_reviews", {"itemId": "8001"}))

    assert data["error"] == "MCP_DIRECT_COOKIE_BLOCKED"
    assert data["api_coverage"] == "blocked_direct_browser_access"


def test_tmall_store_weekly_snapshot_summarizes_child_proofs(monkeypatch):
    mod = _load_tmall_mcp_server()
    calls = []

    def fake_collection_fetch(meta, shop_id, params, **_kwargs):
        calls.append((meta.tool_name, params["url"]))
        proof_id = f"proof-{len(calls)}"
        if "item/live/view/top.json" in params["url"]:
            payload = {
                "code": 0,
                "data": {
                    "updateTime": "2026-04-22 17:00:00",
                    "data": {
                        "recordCount": 1,
                        "data": [{"item": {"itemId": "8001", "title": "商品A"}, "payAmt": {"value": 100}}],
                    },
                },
            }
        elif "getCateInfo" in params["url"]:
            payload = {"code": 0, "data": []}
        elif "priceSeg/list" in params["url"]:
            payload = {"code": 0, "data": []}
        elif "commDateByLocation" in params["url"]:
            payload = {"code": 0, "data": {}}
        elif "item/offline/rank" in params["url"] or "item/live/rank" in params["url"]:
            payload = {
                "code": 0,
                "data": {
                    "recordCount": 1,
                    "data": [{"item": {"itemId": "9001", "title": "竞品A"}, "uv": {"value": 10}}],
                },
            }
        else:
            raise AssertionError(params["url"])
        return {
            "success": True,
            "data": {"url": params["url"], "status": 200, "ok": True, "data": payload},
            "proof": {"proof_id": proof_id, "response_hash": proof_id},
            "proof_id": proof_id,
            "credential_alias": f"{meta.platform}-shop-legacy",
        }

    monkeypatch.setattr(mod.collection_client, "fetch", fake_collection_fetch)

    data = json.loads(mod.handle_tool_call("tmall_store_weekly_snapshot", {
        "shop_id": "shop-1",
        "dateRange": "2026-04-20|2026-04-26",
        "dateType": "recent7",
    }))

    meta = data["_skillforge_meta"]
    assert data["context_pack"]["meta"]["shop_id"] == "shop-1"
    assert meta["data_proofs"]
    assert len(meta["child_tool_calls"]) == len(meta["data_proofs"])
    assert {call["tool_name"] for call in meta["child_tool_calls"]} >= {
        "tmall_sycm_item_rank_top",
        "tmall_sycm_market_rank",
    }
    assert all(call["platform"] != "composite" for call in meta["child_tool_calls"])


def test_sycm_activity_price_wraps_activity_and_price_apis(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        if "getActivityListBy" in url:
            return _fetch_payload({
                "code": 0,
                "data": {
                    "data": {
                        "recordCount": 1,
                        "data": [
                            {
                                "id": 1,
                                "activityName": "五一狂欢",
                                "activityStart": "2026-05-01 00:00:00",
                                "activityStatus": 0,
                                "activityPayamt": 100,
                            }
                        ],
                    }
                },
            })
        if "rank/activity/info" in url:
            return _fetch_payload({"content": {"code": 0, "data": {"data": {"rank": 1}}}})
        if "high/add/price" in url:
            return _fetch_payload({"content": {"code": 0, "data": {"num": 2}}})
        if "queryItemWarnInfo" in url:
            assert "itemIds=8001" in url
            return _fetch_payload({"content": {"code": 0, "data": [{"itemId": "8001", "warnType": "price"}]}})
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_sycm_activity_price({"itemIds": "8001", "limit": 1})
    assert data["activityCount"] == 1
    assert data["activities"][0]["activityName"] == "五一狂欢"
    assert data["activityRankInfo"]["data"]["rank"] == 1
    assert data["highAddPriceAlarm"]["num"] == 2
    assert data["itemPriceWarnings"][0]["warnType"] == "price"


def test_alimama_item_promotion_uses_check_access_and_find_page(monkeypatch):
    mod = _load_tmall_mcp_server()
    calls = []

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        calls.append((url, method, headers, body))
        if "checkAccess" in url:
            return _fetch_payload({
                "data": {
                    "accessInfo": {"csrfId": "csrf-1"},
                    "meta": {"nickName": "店铺", "memberId": 123},
                },
                "info": {"ok": True},
            })
        if "campaign/horizontal/findPage.json" in url:
            assert "csrf-1" in url
            assert headers["content-type"].startswith("application/json")
            assert body["itemId"] in (8001, 9999999999999)
            assert body["bizCode"] == "onebpSearch"
            assert body["orderField"] == ""
            assert body["orderBy"] == ""
            condition = body["rptQuery"]["conditionList"][0]
            assert condition["adzonePkgIdList"] == mod.ALIMAMA_SEARCH_ADZONE_PKGS
            return _fetch_payload({
                "data": {
                    "count": 1,
                    "list": [
                        {
                            "campaignId": 9,
                            "campaignName": "计划A",
                            "reportInfoMap": {
                                "charge": {"value": 10},
                                "roi": {"value": 2.1},
                                "ecpc": {"value": 1.5},
                            },
                        }
                    ],
                },
                "info": {"ok": True},
            })
        assert "report/query.json" in url
        assert headers["content-type"].startswith("application/json")
        assert body["strategyCampaignIdIn"] == [9]
        return _fetch_payload({
            "data": {
                "list": [{"charge": 10, "roi": 2.1, "ecpc": 1.5, "click": 5}],
            },
            "info": {"ok": True},
        })

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_alimama_item_promotion({"itemId": "8001", "promotionType": "search"})
    assert data["accountName"] == "店铺"
    assert data["count"] == 1
    assert data["plans"][0]["campaignName"] == "计划A"
    assert data["plans"][0]["roi"] == 2.1
    assert data["pageSummary"]["charge"] == 10
    # checkAccess is cached across the real item and invalid-item probe.
    assert len(calls) == 5
    assert sum(1 for url, *_ in calls if "checkAccess" in url) == 1
    assert data["accountLevelAggregate"] is True


def test_alimama_item_promotion_compare_aggregates_current_and_previous(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_promotion(args):
        is_current = args["dateRange"] == "2026-04-22|2026-04-22"
        promotion_type = args["promotionType"]
        if promotion_type == "search":
            current = {"charge": 20, "click": 10, "directPayAmount": 30, "totalPayAmount": 60, "directPayNum": 2}
            previous = {"charge": 10, "click": 5, "directPayAmount": 15, "totalPayAmount": 20, "directPayNum": 1}
        else:
            current = {"charge": 10, "click": 5, "directPayAmount": 10, "totalPayAmount": 20, "directPayNum": 1}
            previous = {"charge": 10, "click": 5, "directPayAmount": 10, "totalPayAmount": 30, "directPayNum": 2}
        return {
            "source": "alimama_item_promotion",
            "itemId": args["itemId"],
            "promotionType": promotion_type,
            "dateRange": args["dateRange"],
            "count": 1,
            "pageSummary": current if is_current else previous,
            "plans": [{"campaignId": f"{promotion_type}-1"}],
        }

    monkeypatch.setattr(mod, "tmall_alimama_item_promotion", fake_promotion)

    data = mod.tmall_alimama_item_promotion_compare({
        "itemId": "8001",
        "dateRange": "2026-04-22|2026-04-22",
        "compareDateRange": "2026-04-21|2026-04-21",
    })
    assert data["summary"]["current"]["paidVisitorCount"] == 15
    assert data["summary"]["compare"]["paidVisitorCount"] == 10
    assert data["summary"]["current"]["roi"] == 1.3333
    assert data["summary"]["current"]["totalRoi"] == 2.6667
    assert data["summary"]["delta"]["paidVisitorChangePct"] == 50.0
    assert set(data["segments"]) == {"search", "display"}


def test_item_360_metrics_extracts_keyword_promotion(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_flow(args):
        current = args["dateRange"] == "2026-04-22|2026-04-22"
        if current:
            nodes = [
                {"name": "手淘搜索", "path": "免费流量 > 手淘搜索", "uv": 80, "payByrCnt": 4},
                {"name": "关键词推广", "path": "付费推广 > 关键词推广", "uv": 30, "payByrCnt": 3},
            ]
        else:
            nodes = [
                {"name": "手淘搜索", "path": "免费流量 > 手淘搜索", "uv": 100, "payByrCnt": 10},
                {"name": "关键词推广", "path": "付费推广 > 关键词推广", "uv": 60, "payByrCnt": 3},
            ]
        return {
            "source": "sycm_item_flow_sources",
            "itemId": args["itemId"],
            "effectiveDateRange": args["dateRange"],
            "dateType": "day",
            "rootCount": 1,
            "nodeCount": len(nodes),
            "roots": [{"name": "付费推广", "path": "付费推广", "uv": nodes[1]["uv"], "payByrCnt": nodes[1]["payByrCnt"]}],
            "nodes": nodes,
        }

    monkeypatch.setattr(mod, "tmall_sycm_item_flow_sources", fake_flow)

    data = mod.tmall_item_flow_required_metrics({
        "itemId": "8001",
        "dateRange": "2026-04-22|2026-04-22",
        "compareDateRange": "2026-04-21|2026-04-21",
    })
    assert data["free_flow_basis"]["search_visitor_change_pct"] == -20.0
    assert data["free_flow_basis"]["search_conversion_change_pct"] == -50.0
    assert data["paid_flow_basis"]["keyword_promotion_visitor_change_pct"] == -50.0
    assert data["paid_flow_basis"]["keyword_promotion_conversion_change_pct"] == 100.0


def test_required_flow_metrics_does_not_fetch_item_detail(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_flow(args):
        nodes = [
            {"name": "经营优势", "path": "经营优势", "uv": 100, "payByrCnt": 5},
            {"name": "搜索", "path": "经营优势 > 搜索", "uv": 80, "payByrCnt": 4},
        ]
        return {
            "source": "sycm_item_flow_sources",
            "itemId": args["itemId"],
            "effectiveDateRange": args["dateRange"],
            "dateType": "day",
            "rootCount": 1,
            "nodeCount": len(nodes),
            "roots": [nodes[0]],
            "nodes": nodes,
        }

    def fail_detail(args):
        raise AssertionError("tmall_item_flow_required_metrics must not fetch item detail")

    monkeypatch.setattr(mod, "tmall_sycm_item_flow_sources", fake_flow)
    monkeypatch.setattr(mod, "tmall_sycm_item_detail", fail_detail)

    data = mod.tmall_item_flow_required_metrics({
        "itemId": "8001",
        "dateRange": "2026-04-22|2026-04-22",
        "compareDateRange": "2026-04-21|2026-04-21",
    })

    assert data["free_flow_basis"]["found"] is True
    assert data["raw_metrics"]["detail"] is None


def test_required_flow_metrics_batch_collects_items(monkeypatch):
    mod = _load_tmall_mcp_server()
    seen = []

    def fake_required_flow(args):
        seen.append((
            args["itemId"],
            args["dateRange"],
            args["dateType"],
            args["compareDateRange"],
            args["compareDateType"],
        ))
        uv = 10
        compare_uv = 5
        return {
            "source": "tmall_item_flow_required_metrics",
            "itemId": args["itemId"],
            "dateRange": args["dateRange"],
            "compareDateRange": args["compareDateRange"],
            "dateType": args["dateType"],
            "compareDateType": args["compareDateType"],
            "free_flow_basis": {
                "found": True,
                "source_path": "经营优势 > 搜索",
                "search_visitor": uv,
                "search_visitor_compare": compare_uv,
                "search_visitor_change_pct": 100.0,
            },
            "paid_flow_basis": {"found": False},
        }

    monkeypatch.setattr(mod, "tmall_item_flow_required_metrics", fake_required_flow)

    data = mod.tmall_item_flow_required_metrics_batch({
        "itemIds": ["8001", "8002"],
        "dateRange": "2026-04-22|2026-04-22",
        "compareDateRange": "2026-04-21|2026-04-21",
        "workers": 1,
    })

    assert seen == [
        ("8001", "2026-04-22|2026-04-22", "day", "2026-04-21|2026-04-21", "day"),
        ("8002", "2026-04-22|2026-04-22", "day", "2026-04-21|2026-04-21", "day"),
    ]
    assert data["rowsCollected"] == 2
    assert set(data["results"]) == {"8001", "8002"}
    assert data["results"]["8001"]["free_flow_basis"]["search_visitor_change_pct"] == 100.0


def test_required_promotion_metrics_exposes_roi_charge_ppc(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_compare(args):
        return {
            "source": "alimama_item_promotion_compare",
            "itemId": args["itemId"],
            "dateRange": "2026-04-22|2026-04-22",
            "compareDateRange": "2026-04-21|2026-04-21",
            "summary": {
                "current": {"charge": 120, "directPayAmount": 80, "totalPayAmount": 288, "roi": 2.4, "ppc": 3.0, "click": 40, "cvr": 0.1, "ctr": 0.05, "planCount": 2},
                "compare": {"charge": 100, "directPayAmount": 100, "totalPayAmount": 300, "roi": 3.0, "ppc": 2.0, "click": 50, "cvr": 0.08, "ctr": 0.04, "planCount": 2},
                "delta": {
                    "roiDelta": -0.6,
                    "roiChangePct": -20.0,
                    "ppcDelta": 1.0,
                    "ppcChangePct": 50.0,
                    "chargeChangePct": 20.0,
                    "paidVisitorChangePct": -20.0,
                    "cvrChangePct": 25.0,
                    "directPayAmountChangePct": -20.0,
                    "totalPayAmountChangePct": -4.0,
                    "ctrChangePct": 25.0,
                },
            },
            "dataQuality": [],
        }

    monkeypatch.setattr(mod, "tmall_alimama_item_promotion_compare", fake_compare)

    data = mod.tmall_item_promotion_required_metrics({"itemId": "8001"})
    assert data["roi"] == 0.6667
    assert data["compare_roi"] == 1.0
    assert data["roi_delta"] == -0.3333
    assert data["roi_change_pct"] == -33.33
    assert data["charge_change_pct"] == 20.0
    assert data["direct_pay_amount"] == 80
    assert data["direct_pay_amount_change_pct"] == -20.0
    assert data["total_pay_amount"] == 288
    assert data["manage_search_bottom_total"]["direct_roi"] == 0.6667
    assert data["manage_search_bottom_total"]["total_roi"] == 2.4
    assert data["ctr"] == 0.05
    assert data["ctr_change_pct"] == 25.0
    assert data["ppc_delta"] == 1.0
    assert data["ppc_change_pct"] == 50.0
    assert "low_roi_plans" in data
    assert "detail_status" in data


def test_alimama_scope_signature_does_not_mark_two_empty_results_as_aggregate():
    mod = _load_tmall_mcp_server()

    empty = {"count": 0, "plans": [], "pageSummary": {"charge": 0, "click": 0, "roi": 0, "ecpc": 0}}

    assert mod._alimama_signature_equal(mod._alimama_signature(empty), mod._alimama_signature(empty)) is False


def test_alimama_item_promotion_marks_account_aggregate_when_probe_matches(monkeypatch):
    mod = _load_tmall_mcp_server()
    calls = []

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        calls.append((url, method, body))
        if "checkAccess" in url:
            return _fetch_payload({
                "data": {
                    "accessInfo": {"csrfId": "csrf-1"},
                    "meta": {"nickName": "店铺", "memberId": 123},
                },
                "info": {"ok": True},
            })
        if "campaign/horizontal/findPage.json" in url:
            return _fetch_payload({
                "data": {
                    "count": 1,
                    "list": [{"campaignId": 9, "campaignName": "计划A"}],
                },
                "info": {"ok": True},
            })
        if "report/query.json" in url:
            return _fetch_payload({
                "data": {"list": [{"charge": 10, "roi": 2.0, "ecpc": 1.0, "click": 10}]},
                "info": {"ok": True},
            })
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)
    mod._ALIMAMA_ACCOUNT_SIGNATURE_CACHE.clear()

    data = mod.tmall_alimama_item_promotion({"itemId": "8001", "promotionType": "search"})

    assert data["accountLevelAggregate"] is True
    assert data["scopeCheck"]["probeItemId"] == "9999999999999"
    assert "账户/计划聚合" in "".join(data["dataNotes"])


def test_required_promotion_excludes_account_aggregate_from_summary(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_compare(args):
        return {
            "source": "alimama_item_promotion_compare",
            "itemId": args["itemId"],
            "dateRange": "2026-04-22|2026-04-22",
            "compareDateRange": "2026-04-21|2026-04-21",
            "accountLevelAggregate": True,
            "summary": {
                "current": {"charge": 0, "roi": 0, "ppc": 0, "click": 0, "planCount": 0, "accountAggregateCount": 1},
                "compare": {"charge": 0, "roi": 0, "ppc": 0, "click": 0, "planCount": 0, "accountAggregateCount": 1},
                "delta": {},
            },
            "dataQuality": ["阿里妈妈 itemId 筛选返回账户/计划聚合口径"],
            "segments": {
                "search": {
                    "current": {
                        "accountLevelAggregate": True,
                        "planDetailsAvailable": False,
                        "plans": [
                            {"campaignId": "p1", "campaignName": "账户低ROI计划", "charge": 100, "roi": 0.8, "ecpc": 5, "click": 20},
                        ],
                    }
                }
            },
        }

    monkeypatch.setattr(mod, "tmall_alimama_item_promotion_compare", fake_compare)

    data = mod.tmall_item_promotion_required_metrics({"itemId": "8001"})

    assert data["account_level_aggregate"] is True
    assert data["plan_count"] == 0
    assert data["roi"] is None
    assert data["low_roi_plans"] == []
    assert "聚合" in data["detail_status"]
    assert any("账户/计划聚合" in item for item in data["data_quality"])


def test_required_promotion_minute_compare_is_not_silent_zero():
    mod = _load_tmall_mcp_server()

    data = mod.tmall_item_promotion_required_metrics({
        "itemId": "8001",
        "dateRange": "2026-04-23 00:00:00|2026-04-23 08:55:00",
        "compareDateRange": "2026-04-22 00:00:00|2026-04-22 08:55:00",
    })

    assert data["supported"] is False
    assert data["supportsMinuteCompare"] is False
    assert data["roi"] is None
    assert data["charge"] is None
    assert "不能按 0 值判断" in data["detail_status"]


def test_seller_price_competitiveness_uses_price_center_apis(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        if "pricecontrol/queryItemList" in url:
            return _fetch_payload({
                "success": True,
                "data": {
                    "preWarnNum": 0,
                    "suppressNum": 0,
                    "totalSuppressWarnNum": 0,
                    "totalPunishCount": 0,
                    "itemList": [],
                },
            })
        if "queryControlPlans" in url:
            return _fetch_payload({"success": True, "data": {"model": []}})
        if "coreData" in url:
            return _fetch_payload({"success": True, "data": {"highStarItemCount": 1}})
        if "queryStrategySuggestions" in url:
            return _fetch_payload({"success": True, "data": {"model": [{"title": "可升星"}]}})
        if "staritem/items" in url:
            return _fetch_payload({
                "success": True,
                "data": {
                    "total": 1,
                    "singleStarItemResponses": [
                        {
                            "itemId": 8001,
                            "title": "商品A",
                            "price": "¥59.90",
                            "maxDepreciateSkuRecommendPrice": "¥55.00",
                            "sellerRealStarLevel": "4",
                            "canUpgrade": True,
                            "columns": {"priceSuggest": [{"contents": [{"text": "降价享额外曝光"}]}]},
                            "materialJsonMap": {
                                "searchFlow": {"itemFlowLevelName": "较差", "status": "step_two_run"},
                                "convertTask": {"convertStageName": "预推中"},
                            },
                        }
                    ],
                },
            })
        raise AssertionError(url)

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_seller_price_competitiveness_check({"itemId": "8001"})
    assert data["api_coverage"] == "seller_center_price_api"
    assert data["price_star_status"] == "4星价格力，可继续优化至五星"
    assert "¥59.90" in data["price_status"]
    assert data["high_price_limit_status"] == "未发现高价限流商品"
    assert data["item"]["searchFlowLevelName"] == "较差"


def test_seller_marketing_activity_list_normalizes_status(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        assert "getMixActivityList" in url
        assert method == "POST"
        assert headers["content-type"].startswith("application/json")
        assert body["pageSize"] == 20
        return _fetch_payload({
            "success": True,
            "model": {
                "totalCount": 2,
                "dataList": [
                    {"activityId": 1, "name": "券A", "toolName": "商品券", "status": 2, "statusDesc": "生效中", "startTime": 1774972800000, "endTime": 1777564799000},
                    {"activityId": 2, "name": "券B", "toolName": "商品券", "status": 0, "statusDesc": "暂停中"},
                ],
            },
        })

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)

    data = mod.tmall_seller_marketing_activity_list({"itemId": "8001", "limit": 20})
    assert data["active_count"] == 1
    assert data["paused_count"] == 1
    assert data["activity_status"] == "全店有 1 个生效活动"
    assert "商品 ID 维度" in data["data_gap"]


def test_activity_price_check_combines_seller_center_context(monkeypatch):
    mod = _load_tmall_mcp_server()

    def fake_activity(args):
        return {
            "source": "sycm_activity_price",
            "itemIds": args["itemIds"],
            "activities": [],
            "itemPriceWarnings": [{"itemId": "8001", "desc": "价格风险"}],
            "highAddPriceAlarm": {},
        }

    monkeypatch.setattr(mod, "tmall_sycm_activity_price", fake_activity)
    monkeypatch.setattr(mod, "tmall_seller_price_competitiveness_check", lambda args: {
        "source": "tmall_seller_price_competitiveness_check",
        "price_status": "当前价 ¥59.90，建议价 ¥55.00，有降价升星机会",
        "price_star_status": "4星价格力，可继续优化至五星",
        "high_price_limit_status": "未发现高价限流商品",
    })
    monkeypatch.setattr(mod, "tmall_seller_marketing_activity_list", lambda args: {
        "source": "tmall_seller_marketing_activity_list",
        "activity_status": "全店有 1 个生效活动",
        "activity_count": 2,
        "active_count": 1,
    })
    monkeypatch.setattr(mod, "tmall_seller_price_risk_check", lambda args: {
        "source": "tmall_seller_price_risk_check",
        "risk_status": "未发现营销价格风险",
        "item_records": [],
    })

    data = mod.tmall_item_activity_price_check({"itemId": "8001"})
    assert data["price_status"] == "有价格/资损风险提醒"
    assert data["price_star_status"] == "4星价格力，可继续优化至五星"
    assert data["item_activity_membership_status"]
    assert data["final_price_status"]
    assert "seller_center_price_api" in data["api_coverage"]
    assert "商品 ID 维度活动参与" in data["data_gap"]


def test_required_context_lists_missing_apis(monkeypatch):
    mod = _load_tmall_mcp_server()

    monkeypatch.setattr(mod, "tmall_item_flow_required_metrics", lambda args: {"ok": True})
    monkeypatch.setattr(mod, "tmall_item_promotion_required_metrics", lambda args: {"ok": True})
    monkeypatch.setattr(mod, "tmall_item_activity_price_check", lambda args: {"ok": True})

    async def fake_review(args):
        return {"ok": True}

    monkeypatch.setattr(mod, "_tmall_item_review_qa_check", fake_review)

    data = mod.tmall_link_decline_required_context({"itemId": "8001"})
    keys = {row["key"] for row in data["missing_apis"]}
    assert "item_level_activity_membership_api" in keys
    assert "official_final_hand_price_api" in keys
    assert "exact_activity_dropout_and_price_correctness" not in keys
    assert "stable_ask_all_api" not in keys


def test_review_api_normalizers_detect_risks():
    mod = _load_tmall_mcp_server()

    review = mod._normalize_rate_item({
        "id": "r1",
        "displayUserNick": "u1",
        "rateDate": "2026-04-23",
        "skuValueStr": "套餐A",
        "feedback": "味道有点大",
        "isTop": True,
    }, 1)
    qa = mod._normalize_ask_item({
        "questionId": "q1",
        "questionTitle": "会过敏吗？",
        "topAnswerList": [{"answerTitle": "我没有过敏"}],
    }, 1)

    assert review["isPinned"] is True
    assert review["riskLevel"] == "medium"
    assert qa["riskLevel"] == "high"


def _load_local_tmall_skill_main():
    root = Path(__file__).resolve().parents[1]
    path = root / "skills-repo" / "tmall-link-decline-analysis-v2" / "scripts" / "main.py"
    if not path.exists():
        pytest.skip("local Docker-managed skills-repo checkout is not present")
    spec = importlib.util.spec_from_file_location("tmall_decline_rank_regression", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_tmall_skill_uses_visitor_and_realtime_pay_amount_rank_and_does_not_fake_search_conversion():
    mod = _load_local_tmall_skill_main()

    rows = [
        {
            "item_id": "a",
            "title": "A",
            "visitor_count": 100,
            "visitor_count_prev": 100,
            "conversion_rate": 0.10,
            "conversion_rate_prev": 0.10,
            "pay_amt": 1000,
            "pay_amt_prev": 1000,
            "pay_item_count": 10,
            "pay_item_count_prev": 10,
        },
        {
            "item_id": "b",
            "title": "B",
            "visitor_count": 300,
            "visitor_count_prev": 250,
            "conversion_rate": 0.02,
            "conversion_rate_prev": 0.04,
            "pay_amt": 800,
            "pay_amt_prev": 1000,
            "pay_item_count": 6,
            "pay_item_count_prev": 12,
        },
        {
            "item_id": "c",
            "title": "C",
            "visitor_count": 200,
            "visitor_count_prev": 200,
            "conversion_rate": 0.03,
            "conversion_rate_prev": 0.03,
            "pay_amt": 500,
            "pay_amt_prev": 500,
            "pay_item_count": 20,
            "pay_item_count_prev": 20,
        },
    ]

    output = mod.main({
        "date": "2026-04-30",
        "snapshot_mode": "off",
        "生意参谋_商品排行榜": rows,
        "生意参谋_商品排行榜_访客排序": [rows[1], rows[2], rows[0]],
    })
    cards = {card["item"]["id"]: card for card in output["商品诊断卡片"]}

    assert cards["b"]["ranking_visitors"] == 1
    assert cards["b"]["ranking_orders"] == 2
    assert cards["b"]["pay_amount_rank"] == 2
    metrics = {metric["name"]: metric for metric in cards["b"]["data_overview"]["metrics"]}
    assert metrics["全店成交排名"]["status"] == "店铺内按实时支付金额排序"
    assert "实时支付金额 payAmt" in metrics["全店成交排名"]["note"]
    assert metrics["搜索/免费转化环比"]["value"] == "待补采"
    assert "不能使用商品整体转化率替代" in metrics["搜索/免费转化环比"]["delta"]
    free_row = next(row for row in output["免费流分析结果"] if row["item_id"] == "b")
    assert free_row["free_conversion_rate"] is None
    assert free_row["conversion_change_pct"] is None


def test_item_archives_url_controls_flow_item_id_and_date(monkeypatch):
    mod = _load_tmall_mcp_server()
    seen = []

    def fake_fetch(url, method="GET", headers=None, body=None, page_url=None):
        seen.append((url, page_url))
        return _fetch_payload({
            "code": 0,
            "data": [
                {
                    "pageName": {"value": "经营优势"},
                    "uv": {"value": 100},
                    "children": [
                        {
                            "pageName": {"value": "搜索"},
                            "uv": {"value": 79},
                            "payByrCnt": {"value": 1},
                            "payRate": {"value": 0.012658227848101266},
                            "children": [],
                        }
                    ],
                }
            ],
        })

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)
    url = (
        "https://sycm.taobao.com/cc/item_archives?activeKey=flow"
        "&dateRange=2026-04-29%7C2026-04-29&dateType=day"
        "&itemId=800559674590&spm=a21ag.23983127.0.17"
    )

    data = mod.tmall_sycm_item_flow_sources({"itemArchivesUrl": url, "itemId": "wrong-item"})

    assert data["itemId"] == "800559674590"
    assert data["effectiveDateRange"] == "2026-04-29|2026-04-29"
    flow_url = next(url for url, _ in seen if "flow/item/source/tree/support.json" in url)
    assert "itemId=800559674590" in flow_url
    assert "wrong-item" not in flow_url
    assert "dateRange=2026-04-29|2026-04-29" in flow_url
    assert data["nodes"][1]["path"] == "经营优势 > 搜索"
    assert data["dataQuality"]
