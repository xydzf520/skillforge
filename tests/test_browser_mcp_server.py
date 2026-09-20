import importlib.util
import json
from pathlib import Path


def _load_browser_mcp_server():
    path = Path(__file__).resolve().parents[1] / "scripts" / "browser_mcp_server.py"
    spec = importlib.util.spec_from_file_location("browser_mcp_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_browser_fetch_json_tool(monkeypatch):
    mod = _load_browser_mcp_server()

    def fake_fetch(url=None, method="GET", headers=None, body=None, page_url=None, registry_id=None, api_index=0):
        assert url == "https://api.example.com/data"
        return {
            "success": True,
            "data": {
                "url": url,
                "status": 200,
                "ok": True,
                "data": {"items": [1, 2, 3]},
            },
            "proof": {"response_hash": "abc", "data_keys": ["items"], "row_count": None},
        }

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)
    text = mod.handle_tool_call("browser_fetch_json", {"url": "https://api.example.com/data"})
    assert "items" in text
    assert "200" in text
    assert "response_hash" in text


def test_browser_fetch_json_tool_registry_replay(monkeypatch):
    mod = _load_browser_mcp_server()

    def fake_fetch(url=None, method="GET", headers=None, body=None, page_url=None, registry_id=None, api_index=0):
        assert url is None
        assert registry_id == 7
        assert api_index == 2
        return {
            "success": True,
            "data": {"status": 200, "data": {"items": [1]}},
            "proof": {
                "registry_id": registry_id,
                "api_index": api_index,
                "response_hash": "def",
                "verification_status": "verified",
            },
        }

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)
    text = mod.handle_tool_call("browser_fetch_json", {"registry_id": 7, "api_index": 2})
    assert '"registry_id": 7' in text
    assert '"verification_status": "verified"' in text


def test_browser_qianchuan_video_content_analysis_tool(monkeypatch):
    mod = _load_browser_mcp_server()
    calls = []

    def fake_fetch(url=None, method="GET", headers=None, body=None, page_url=None, registry_id=None, api_index=0):
        calls.append({"url": url, "method": method, "body": body, "page_url": page_url})
        if "statQuery" in url:
            assert method == "POST"
            assert body["DataSetKey"] == "roi2_video_material_analysis_insight"
            return {
                "success": True,
                "data": {
                    "url": url,
                    "status": 200,
                    "ok": True,
                    "data": {
                        "status_code": 0,
                        "data": {
                            "Rows": [
                                {
                                    "Dimensions": {"duration": {"Value": "0"}},
                                    "Metrics": {"live_watch_count_for_roi2_v2": {"Value": "5"}},
                                },
                                {
                                    "Dimensions": {"duration": {"Value": "1"}},
                                    "Metrics": {"live_watch_count_for_roi2_v2": {"Value": "8"}},
                                },
                            ]
                        },
                    },
                },
                "proof": {"response_hash": "stat"},
            }
        return {
            "success": True,
            "data": {"url": url, "status": 200, "ok": True, "data": {"status_code": 0, "data": {}}},
            "proof": {"response_hash": "optional"},
        }

    monkeypatch.setattr(mod, "_fetch_json", fake_fetch)
    text = mod.handle_tool_call("browser_qianchuan_video_content_analysis", {
        "aavid": "1855723231649801",
        "material_id": "7639640154815758378",
        "start_date": "2026-06-08",
        "end_date": "2026-06-14",
        "include_top_videos": False,
    })
    data = json.loads(text)

    assert data["ok"] is True
    assert data["summary"]["click_total"] == 13
    assert data["summary"]["click_peak"]["duration"] == 1
    assert any("statQuery" in call["url"] for call in calls)
    stat_call = next(call for call in calls if "statQuery" in call["url"])
    assert stat_call["body"]["Filters"]["Conditions"][2]["Field"] == "marketing_goal"
    assert stat_call["body"]["Filters"]["Conditions"][2]["Values"] == ["1"]


def test_browser_get_cached_apis_by_id(monkeypatch):
    mod = _load_browser_mcp_server()

    monkeypatch.setattr(mod, "_get_platform_api_detail", lambda item_id: {"id": item_id, "api_count": 3})
    text = mod.handle_tool_call("browser_get_cached_apis", {"id": 12})
    assert '"id": 12' in text
    assert '"api_count": 3' in text


def test_browser_get_cached_apis_compacts_large_capture(monkeypatch):
    mod = _load_browser_mcp_server()

    def fake_detail(item_id):
        return {
            "id": item_id,
            "domain": "sycm.taobao.com",
            "page_path": "/cc/item_rank",
            "api_count": 2,
            "apis_json": {
                "url": "https://sycm.taobao.com/cc/item_rank",
                "data_apis": [
                    {
                        "url": "//sycm.taobao.com/oneauth/api/permission.json",
                        "method": "GET",
                        "status": 200,
                        "body_preview": {"code": 0, "data": {"permission": True}},
                    },
                    {
                        "url": "https://sycm.taobao.com/cc/item/live/view/top.json?page=1",
                        "method": "GET",
                        "status": None,
                        "body_preview": {"code": 0, "data": {"items": [{"itemId": "A1", "uv": 123}]}},
                    },
                ],
            },
        }

    monkeypatch.setattr(mod, "_get_platform_api_detail", fake_detail)
    text = mod.handle_tool_call("browser_get_cached_apis", {"id": 12})
    assert "usage_hint" in text
    assert "live/view/top.json" in text
    assert "oneauth/api/permission" not in text
    assert "body_preview" not in text


def test_browser_get_cached_apis_full_opt_in(monkeypatch):
    mod = _load_browser_mcp_server()

    monkeypatch.setattr(
        mod,
        "_get_platform_api_detail",
        lambda item_id: {
            "id": item_id,
            "apis_json": {"data_apis": [{"url": "https://example.test/full", "body_preview": "RAW"}]},
        },
    )
    text = mod.handle_tool_call("browser_get_cached_apis", {"id": 12, "full": True})
    assert "body_preview" in text
    assert "usage_hint" not in text


def test_browser_get_cached_apis_by_filter(monkeypatch):
    mod = _load_browser_mcp_server()

    monkeypatch.setattr(
        mod,
        "_get_platform_apis",
        lambda domain=None: {
            "platforms": [
                {"id": 1, "domain": domain or "sycm.taobao.com", "page_path": "/a", "page_title": "A", "api_count": 2},
                {"id": 2, "domain": domain or "sycm.taobao.com", "page_path": "/b", "page_title": "B", "api_count": 5},
            ]
        },
    )
    text = mod.handle_tool_call("browser_get_cached_apis", {"domain": "sycm.taobao.com"})
    assert "匹配到 2 条缓存 API 记录" in text
    assert "[1]" in text
    assert "[2]" in text
