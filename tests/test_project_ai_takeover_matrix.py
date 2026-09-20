import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_project_ai_takeover_matrix.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("project_ai_takeover_matrix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ai_takeover_specs_cover_100_provider_and_call_variants():
    module = _load_module()
    specs = module.build_specs(100)

    assert len(specs) == 100
    assert len({spec.project_id for spec in specs}) == 100
    assert {spec.provider for spec in specs} == {"openai", "anthropic", "gemini", "dashscope", "deepseek"}
    assert {spec.call_style for spec in specs} == {"fetch", "xhr", "beacon", "worker_fetch", "module_fetch"}
    assert len({spec.app_shape for spec in specs}) == 5


def test_ai_takeover_package_contains_provider_endpoint_but_no_frontend_secret():
    module = _load_module()
    spec = module.build_specs(100)[0]
    package, manifest = module.build_project_package(spec)

    assert manifest["project_id"] == "ai_takeover_001"
    assert "ai.cheap.generate" in manifest["capabilities"]
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        names = set(tar.getnames())
        manifest_payload = yaml.safe_load(tar.extractfile("projectforge.yaml").read().decode("utf-8"))
        all_text = "\n".join(
            tar.extractfile(name).read().decode("utf-8")
            for name in names
            if name.endswith((".html", ".js", ".yaml"))
        )

    assert manifest_payload["metadata"]["source"] == "project_ai_takeover_matrix"
    assert "web/index.html" in names
    assert "web/assets/app.js" in names
    assert spec.endpoint in all_text
    assert "OPENAI_API_KEY" not in all_text
    assert "Authorization" not in all_text
    assert "Bearer sk-" not in all_text


def test_ai_takeover_summary_requires_gateway_and_service_counts():
    module = _load_module()
    rows = [
        {
            "uploaded": True,
            "direct_ai_endpoint_count": 1,
            "gateway_injected": True,
            "ai_takeover_status": "pass",
            "runtime_status": "pass",
            "service_invoked": True,
            "service_ok": True,
            "service_result_ready": True,
            "provider": "openai",
            "call_style": "fetch",
            "app_shape": "chat_agent",
            "file_shape": "inline_html",
        }
        for _ in range(100)
    ]
    summary = module.summarize(rows)

    assert summary["type_count"] == 100
    assert summary["direct_ai_detected_count"] == 100
    assert summary["gateway_injected_count"] == 100
    assert summary["ai_takeover_pass_count"] == 100
    assert summary["service_invoked_count"] == 100
    assert summary["service_result_ready_count"] == 100
