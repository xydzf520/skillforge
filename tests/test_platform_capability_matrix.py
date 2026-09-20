import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_platform_capability_matrix.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("platform_capability_matrix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_json_skips_sf_update_banner():
    matrix = _load_module()
    payload = matrix.extract_json("插件有新版本\n更新内容...\n{\"ok\": true, \"count\": 2}")
    assert payload == {"ok": True, "count": 2}


def test_summarize_mcp_catalog_counts_write_external_and_schema():
    matrix = _load_module()
    catalog = {
        "servers": [
            {
                "name": "skillforge",
                "tools": [
                    {"name": "read", "inputSchema": {"type": "object"}, "meta": {"platform": "tmall", "write": False}},
                    {"name": "write", "inputSchema": {"type": "object"}, "meta": {"platform": "skillforge", "write": True}},
                    {"name": "bad", "meta": {"platform": "yuyidata", "write": False}},
                    {"name": "yuyidata_upload_sentence", "inputSchema": {"type": "object"}, "meta": {"platform": "yuyidata", "write": False}},
                ],
            }
        ]
    }
    summary = matrix.summarize_mcp_catalog(catalog)
    assert summary["tool_count"] == 4
    assert summary["write_tool_count"] == 1
    assert summary["by_platform"] == {"tmall": 1, "skillforge": 1, "yuyidata": 2}
    assert summary["schema_missing"] == ["bad"]
    assert "read" in summary["external_or_business_tools"]
    assert summary["potential_write_tools_by_name"] == ["write", "yuyidata_upload_sentence"]
    assert summary["write_meta_gaps"] == ["yuyidata_upload_sentence"]


def test_local_mcp_catalog_has_no_yuyidata_write_meta_gaps():
    matrix = _load_module()
    catalog, command = matrix.load_local_mcp_catalog_snapshot()

    summary = matrix.summarize_mcp_catalog(catalog)

    assert command.returncode == 0
    assert summary["tool_count"] >= 40
    assert summary["write_tool_count"] >= 9
    assert not [name for name in summary["write_meta_gaps"] if name.startswith("yuyidata_")]
