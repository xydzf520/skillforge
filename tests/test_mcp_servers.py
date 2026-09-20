import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_module(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_tmall_toolmeta_manifest_is_registered():
    runtime = _load_module("skillforge_mcp_runtime")
    _load_module("tmall_mcp_server")

    manifest = runtime.manifest()
    names = {item["tool_name"] for item in manifest["tools"]}

    assert "tmall_sycm_item_rank_top" in names
    assert "tmall_store_weekly_snapshot" in names
    for item in manifest["tools"]:
        if item["tool_name"].startswith("tmall_"):
            assert "." in item["data_scope"]
            assert item["platform"]
            assert item["endpoint_family"]
            assert item["warning_group"]


def test_tmall_mcp_tool_list_contains_toolmeta():
    mod = _load_module("tmall_mcp_server")

    tool = next(item for item in mod.TmallMcpServer.TOOLS if item["name"] == "tmall_store_weekly_snapshot")

    assert tool["toolMeta"]["platform"] == "composite"
    assert tool["toolMeta"]["data_scope"] == "tmall.store_weekly_snapshot"


def test_tmall_mcp_subprocess_requires_platform_url():
    env = os.environ.copy()
    env.pop("SKILLFORGE_PLATFORM_URL", None)
    env.pop("SKILLFORGE_BASE_URL", None)
    env.pop("SKILLFORGE_HTTP_BASE", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "tmall_mcp_server.py")],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert proc.returncode != 0
    assert "mcp_env_missing" in proc.stderr


def test_missing_optional_review_adapter_fails_explicitly():
    import pytest
    mod = _load_module("tmall_mcp_server")
    if not hasattr(mod, "_review_adapter_unavailable"):
        pytest.skip("deployment provides an optional review adapter")
    with pytest.raises(RuntimeError, match="optional_review_adapter_unavailable"):
        mod.get_page_ws_url("http://localhost:9222/json")
