import hashlib
import hmac
import importlib.util
import json
import sys
from pathlib import Path


def _load_yuyidata_mcp_server():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    path = scripts_dir / "yuyidata_mcp_server.py"
    spec = importlib.util.spec_from_file_location("yuyidata_mcp_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["yuyidata_mcp_server"] = module
    spec.loader.exec_module(module)
    return module


def test_api_catalog_lists_documented_endpoints(monkeypatch):
    monkeypatch.setenv("YUYIDATA_APP_KEY", "key-1")
    mod = _load_yuyidata_mcp_server()

    data = json.loads(mod.handle_tool_call("yuyidata_api_catalog", {}))

    assert data["credential"]["appKeyConfigured"] is True
    ids = {item["id"] for item in data["endpoints"]}
    assert "upload_text_session" in ids
    assert "comment_check_result" in ids
    assert "order_result" in ids
    assert len(data["endpoints"]) == 13


def test_api_catalog_accepts_gateway_injected_credentials(monkeypatch):
    monkeypatch.delenv("YUYIDATA_APP_KEY", raising=False)
    monkeypatch.delenv("YUYIDATA_APP_SECRET", raising=False)
    mod = _load_yuyidata_mcp_server()

    data = json.loads(mod.handle_tool_call("yuyidata_api_catalog", {
        "appKey": "key-1",
        "baseUrl": "https://api.test",
    }))

    assert data["baseUrl"] == "https://api.test"
    assert data["credential"]["appKeyConfigured"] is True
    assert data["credential"]["appSecretConfigured"] is False


def test_retrieve_task_adds_hmac_sign(monkeypatch):
    monkeypatch.setenv("YUYIDATA_APP_KEY", "abc")
    monkeypatch.setenv("YUYIDATA_APP_SECRET", "secret")
    mod = _load_yuyidata_mcp_server()
    captured = {}

    def fake_post(path, body, *, timeout=None):
        captured["path"] = path
        captured["body"] = body
        return {"ok": True, "status": 200, "data": {"code": 200, "data": "task-1"}}

    monkeypatch.setattr(mod, "_post_json", fake_post)

    result = mod.yuyidata_create_sessions_retrieve_task({
        "startDate": "2026-04-21 00:00:00",
        "endDate": "2026-04-22 00:00:00",
        "dialogueOnly": 1,
        "dryRun": False,
    })

    expected = hmac.new(
        b"secret",
        b"abc2026-04-21 00:00:002026-04-22 00:00:001",
        hashlib.sha256,
    ).hexdigest()
    assert captured["path"] == "/openapi/v3/sessions/retrieve"
    assert captured["body"]["sign"] == expected
    assert result["ok"] is True
    assert result["request"]["body"]["appKey"] == "********"
    assert result["request"]["body"]["sign"] == "********"


def test_retrieve_task_defaults_to_dry_run(monkeypatch):
    monkeypatch.setenv("YUYIDATA_APP_KEY", "abc")
    monkeypatch.setenv("YUYIDATA_APP_SECRET", "secret")
    mod = _load_yuyidata_mcp_server()

    result = mod.yuyidata_create_sessions_retrieve_task({
        "startDate": "2026-04-21 00:00:00",
        "endDate": "2026-04-22 00:00:00",
        "dialogueOnly": 1,
    })

    assert result["dryRun"] is True
    assert result["body"]["appKey"] == "********"
    assert result["body"]["sign"] == "********"
    assert result["url"].endswith("/openapi/v3/sessions/retrieve")


def test_tool_registry_marks_yuyidata_mutating_tools_as_write():
    mod = _load_yuyidata_mcp_server()
    from skillforge_mcp_runtime import TOOL_REGISTRY

    for tool_name in mod.YUYIDATA_WRITE_TOOLS:
        assert TOOL_REGISTRY[tool_name].write is True

    for tool_name in (
        "yuyidata_api_catalog",
        "yuyidata_get_task_info",
        "yuyidata_download_task_file",
        "yuyidata_check_comments",
        "yuyidata_build_sso_url",
        "yuyidata_check_training",
        "yuyidata_check_customized_data",
        "yuyidata_check_orders",
    ):
        assert TOOL_REGISTRY[tool_name].write is False


def test_comment_check_sign_and_limit(monkeypatch):
    monkeypatch.setenv("YUYIDATA_APP_KEY", "abc")
    monkeypatch.setenv("YUYIDATA_APP_SECRET", "secret")
    mod = _load_yuyidata_mcp_server()

    result = mod.yuyidata_check_comments({
        "startDate": "2026-04-21 00:00:00",
        "endDate": "2026-04-22 00:00:00",
        "offset": 0,
        "limit": 500,
        "dryRun": True,
    })

    expected = hmac.new(
        b"secret",
        b"abc2026-04-21 00:00:002026-04-22 00:00:000100",
        hashlib.sha256,
    ).hexdigest()
    assert result["dryRun"] is True
    assert result["body"]["limit"] == 100
    assert result["body"]["sign"] == "********"
    raw_body = mod._paged_body({
        "startDate": "2026-04-21 00:00:00",
        "endDate": "2026-04-22 00:00:00",
        "offset": 0,
        "limit": 500,
    })
    assert mod._comment_sign(raw_body, "secret") == expected


def test_upload_defaults_to_dry_run_and_redacts_key(monkeypatch):
    monkeypatch.setenv("YUYIDATA_APP_KEY", "abc")
    mod = _load_yuyidata_mcp_server()

    result = mod.yuyidata_upload_text_session({
        "data": {
            "date": "2026-04-21",
            "customerId": "c1",
            "agentId": "a1",
            "msgData": [{"speaker": "a1", "time": "10:00:00", "msg": "你好"}],
        },
    })

    assert result["dryRun"] is True
    assert result["body"]["appKey"] == "********"
    assert result["url"].endswith("/openapi/v3/session/upload/text")


def test_build_sso_url(monkeypatch):
    monkeypatch.setenv("YUYIDATA_APP_KEY", "abc")
    mod = _load_yuyidata_mcp_server()

    result = mod.yuyidata_build_sso_url({"userId": "u1", "time": 1733988230})

    expected = hashlib.md5(b"u11733988230abc").hexdigest()
    assert f"sign={expected}" in result["url"]
    assert result["expiresInSeconds"] == 600


def test_download_task_file_supports_unlimited_lines(monkeypatch):
    mod = _load_yuyidata_mcp_server()

    lines = b"\n".join(json.dumps({"id": idx}).encode("utf-8") for idx in range(3))

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _size=-1):
            return lines

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())

    result = mod.yuyidata_download_task_file({
        "filePath": "https://files.example.test/task.ndjson",
        "maxLines": 0,
        "maxBytes": 0,
    })

    assert result["recordsReturned"] == 3
    assert result["maxLines"] == 0
    assert result["maxBytes"] == 512 * 1024 * 1024
    assert result["maxBytes"] == mod.MAX_DOWNLOAD_BYTES
    assert result["truncated"] is False


def test_download_task_file_reports_line_truncation(monkeypatch):
    mod = _load_yuyidata_mcp_server()

    lines = b"\n".join(json.dumps({"id": idx}).encode("utf-8") for idx in range(3))

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _size=-1):
            return lines

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())

    result = mod.yuyidata_download_task_file({
        "filePath": "https://files.example.test/task.ndjson",
        "maxLines": 2,
        "maxBytes": 1024,
    })

    assert result["recordsReturned"] == 2
    assert result["truncated"] is True
    assert result["linesTruncated"] is True
    assert result["bytesTruncated"] is False
