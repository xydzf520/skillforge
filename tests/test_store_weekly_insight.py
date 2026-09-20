import ast
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PLAN_DOC = REPO_ROOT / "docs/plans/2026-05-11-production-skill-adaptation-guide.md"


def _load_tmall_mcp_server():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    path = SCRIPTS_DIR / "tmall_mcp_server.py"
    spec = importlib.util.spec_from_file_location("tmall_mcp_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["tmall_mcp_server"] = module
    spec.loader.exec_module(module)
    return module


def test_store_weekly_snapshot_mcp_has_no_llm_or_intelligence_imports():
    source = (SCRIPTS_DIR / "tmall_mcp_server.py").read_text()
    tree = ast.parse(source)
    banned = {
        "anthropic",
        "openai",
        "deepseek",
        "dashscope",
        "langchain_openai",
        "langchain_anthropic",
    }
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not (imports & banned)
    assert "app.intelligence" not in source
    assert "sf.analyze" not in source


def test_store_weekly_snapshot_child_tools_use_child_toolmeta(monkeypatch):
    mod = _load_tmall_mcp_server()
    calls = []

    def fake_collection_fetch(meta, shop_id, params, timeout=None):
        calls.append((meta, shop_id, params, timeout))
        if meta.platform == "composite":
            raise AssertionError("composite ToolMeta must not be used for child fetch")
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

    child_calls = data["_skillforge_meta"]["child_tool_calls"]
    assert len(calls) >= 2
    assert len(child_calls) == len(data["_skillforge_meta"]["data_proofs"])
    assert [call[0].tool_name for call in calls] == [row["tool_name"] for row in child_calls]
    assert all(row["platform"] != "composite" for row in child_calls)
    assert data["_skillforge_meta"]["data_health_ratio"] == 1


def test_production_skill_adaptation_guide_keeps_skill_boundary_clear():
    text = PLAN_DOC.read_text()

    assert 'sf.fetch_api("mcp://tmall_store_weekly_snapshot"' in text
    assert "sf.analyze(" in text
    assert "不传 `model`" in text
    assert "Skill 不自己选择、轮换或合并 Cookie" in text
    assert "不要新建 `tmall_store_weekly_insight`" in text
