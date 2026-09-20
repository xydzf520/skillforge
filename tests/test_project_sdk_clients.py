import importlib.util
import sys
from pathlib import Path

import pytest


SDK_PATH = Path(__file__).resolve().parents[1] / "sdk" / "python" / "skillforge_project_sdk.py"


def load_python_sdk_module():
    spec = importlib.util.spec_from_file_location("skillforge_project_sdk_under_test", SDK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_project_sdk_requires_https_for_external_base_url():
    sdk_module = load_python_sdk_module()

    with pytest.raises(ValueError, match="HTTPS"):
        sdk_module.SkillForgeProjectSdk(
            base_url="http://api.skillforge.example.com",
            project_id="proj_1",
            token="token",
        )

    local = sdk_module.SkillForgeProjectSdk(
        base_url="http://localhost:8000/",
        project_id="proj_1",
        token="token",
    )
    assert local.base_url == "http://localhost:8000"


def test_python_project_sdk_retries_transient_status_with_same_request_id(monkeypatch):
    sdk_module = load_python_sdk_module()
    responses = [
        sdk_module.httpx.Response(503, json={"error": {"code": "UPSTREAM_BUSY", "message": "busy", "detail": {}}}),
        sdk_module.httpx.Response(200, json={"ok": True}),
    ]
    calls = []

    class FakeClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, *, headers=None, json=None):
            calls.append({"method": method, "url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return responses.pop(0)

    monkeypatch.setattr(sdk_module.httpx, "Client", FakeClient)
    monkeypatch.setattr(sdk_module, "sleep", lambda _seconds: None)

    client = sdk_module.SkillForgeProjectSdk(
        base_url="https://skillforge.example.com",
        project_id="proj_1",
        token="token",
    )

    result = client.start_run({"input": {"query": "daily"}}, request_id="fixed-request-id")

    assert result == {"ok": True}
    assert len(calls) == 2
    assert {call["json"]["request_id"] for call in calls} == {"fixed-request-id"}
    assert all(call["url"] == "https://skillforge.example.com/api/projects/sdk/runs" for call in calls)


def test_python_project_sdk_classifies_quota_error_without_retry(monkeypatch):
    sdk_module = load_python_sdk_module()
    responses = [
        sdk_module.httpx.Response(
            429,
            json={
                "error": {
                    "code": "PROJECT_SDK_RATE_LIMITED",
                    "message": "Project SDK 调用过于频繁",
                    "detail": {"error_category": "quota_error", "retry_after_seconds": 7},
                },
            },
        )
    ]
    calls = []

    class FakeClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, *, headers=None, json=None):
            calls.append({"method": method, "url": url, "headers": headers, "json": json})
            return responses.pop(0)

    monkeypatch.setattr(sdk_module.httpx, "Client", FakeClient)

    client = sdk_module.SkillForgeProjectSdk(
        base_url="https://skillforge.example.com",
        project_id="proj_1",
        token="token",
    )

    with pytest.raises(sdk_module.SkillForgeProjectSdkError) as exc_info:
        client.start_run(request_id="quota-request")

    assert len(calls) == 1
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "PROJECT_SDK_RATE_LIMITED"
    assert exc_info.value.category == "quota_error"
    assert exc_info.value.retry_after_seconds == 7


def test_python_project_sdk_training_asset_methods(monkeypatch):
    sdk_module = load_python_sdk_module()
    responses = [
        sdk_module.httpx.Response(200, json={"sample": {"id": "sample-1"}}),
        sdk_module.httpx.Response(200, json={"datasetVersion": {"id": "dataset-1"}}),
        sdk_module.httpx.Response(200, json={"training_sink": {"status": "pending"}}),
        sdk_module.httpx.Response(200, json={"asset": {"id": "asset-1"}}),
    ]
    calls = []

    class FakeClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return responses.pop(0)

    monkeypatch.setattr(sdk_module.httpx, "Client", FakeClient)

    client = sdk_module.SkillForgeProjectSdk(
        base_url="https://skillforge.example.com",
        project_id="proj_1",
        token="token",
    )

    client.record_training_sample(
        "run-1",
        {"messages": [{"role": "user", "content": "看图"}]},
        dataset_profile="vision_instruction_v1",
        asset_id="asset-1",
        request_id="sample-request",
    )
    client.create_dataset_version({"name": "vision", "dataset_profile": "vision_instruction_v1"})
    client.sync_training_dataset("dataset-1", {"target_gateway_id": "training-primary"})
    client.upload_asset("run-1", b"fake", file_name="frame.png", mime_type="image/png", metadata={"kind": "frame"})

    assert calls[0]["url"] == "https://skillforge.example.com/api/projects/sdk/runs/run-1/training-samples"
    assert calls[0]["json"]["request_id"] == "sample-request"
    assert calls[1]["url"] == "https://skillforge.example.com/api/projects/sdk/training/datasets"
    assert calls[2]["url"] == "https://skillforge.example.com/api/projects/sdk/training/datasets/dataset-1/sync"
    assert calls[3]["url"] == "https://skillforge.example.com/api/projects/sdk/runs/run-1/assets"
    assert calls[3]["files"]["file"][0] == "frame.png"
    assert calls[3]["files"]["file"][2] == "image/png"
    assert calls[3]["data"]["metadata_json"] == '{"kind":"frame"}'
