import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_codex_project_type_matrix.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("codex_project_type_matrix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_specs_covers_100_distinct_codex_project_types():
    matrix = _load_module()
    specs = matrix.build_specs()
    assert len(specs) == 100
    assert len({spec.project_id for spec in specs}) == 100
    assert len({spec.domain for spec in specs}) == 20
    assert len({spec.pattern for spec in specs}) == 5
    assert len({spec.framework for spec in specs}) == 5
    assert len({spec.department for spec in specs}) == 10
    assert {spec.kind for spec in specs} == {"internal_tool", "dashboard", "web_static"}


def test_type_matrix_packages_are_static_project_gateway_ready():
    matrix = _load_module()
    for spec in matrix.build_specs()[:12]:
        package, manifest = matrix.build_project_package(spec)
        assert manifest["project_id"] == spec.project_id
        assert manifest["entry"] == spec.entry
        assert "ai.cheap" in " ".join(manifest["capabilities"])
        with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
            names = set(tar.getnames())
            manifest_payload = yaml.safe_load(tar.extractfile("projectforge.yaml").read().decode("utf-8"))
            entry_payload = tar.extractfile(spec.entry).read().decode("utf-8")
        assert manifest_payload["metadata"]["source"] == "codex_project_type_matrix"
        assert spec.entry in names
        assert "data-sf-report" in entry_payload
        assert "data-sf-todo" in entry_payload
        assert "data-sf-ai" in entry_payload
        assert {"assets/type-app.js", "styles/type.css"} <= names


def test_summarize_requires_runtime_and_result_pass_counts():
    matrix = _load_module()
    rows = [
        {
            "uploaded": True,
            "runtime_status": "pass",
            "normal_run_ready": True,
            "result_eval_status": "pass",
            "service_invoked": True,
            "service_ok": True,
            "service_result_ready": True,
            "kind": "dashboard",
            "department": "AI小组",
            "pattern": "dashboard",
            "framework": "vue-vite",
        }
        for _ in range(100)
    ]
    summary = matrix.summarize(rows)
    assert summary["type_count"] == 100
    assert summary["uploaded_count"] == 100
    assert summary["runtime_pass_count"] == 100
    assert summary["normal_runtime_ready_count"] == 100
    assert summary["result_eval_pass_count"] == 100
    assert summary["service_invoked_count"] == 100
    assert summary["service_ok_count"] == 100
    assert summary["service_result_ready_count"] == 100


def test_service_invoke_payload_records_department_io_without_external_model_by_default():
    matrix = _load_module()
    spec = matrix.build_specs()[0]
    payload = matrix.service_invoke_payload_for_spec(spec, batch_id="batch-test")

    assert payload["request_id"] == "codex-type-service-batch-test-001"
    assert payload["input"]["department"] == spec.department
    assert payload["output"]["reports"]
    assert payload["output"]["todos"]
    assert payload["output"]["todos"][0]["kind"] in {"review", "dispatch", "train_model"}
    assert payload["output"]["proofs"][0]["credential_location"] == "platform_only"
