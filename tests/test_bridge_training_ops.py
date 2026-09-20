import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_bridge_training_inference_keeps_4b_budget_on_8gb_gpu(monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "_runtime_gpu_headroom_mb", lambda: 7652)

    assert skillforgebridge._training_inference_effective_max_new_tokens("qwen3.5-4b", 2048) == 2048
    assert skillforgebridge._training_inference_effective_max_new_tokens("qwen3.5-7b", 2048) == 1536


@pytest.mark.asyncio
async def test_bridge_training_submit_job_persists_manifest(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "train_20260521_0001",
            "job_type": "lora",
            "title": "商品动作推荐训练",
            "department": "EC",
            "target_gateway_id": "node-1",
            "dataset_ref": "dataset://ec/actions/v1",
            "spec": {"epochs": 2},
            "control": {"callback_path": "/api/training/jobs/train_20260521_0001/gateway-result"},
        },
    )

    assert ok is True
    assert error is None
    assert result["accepted"] is True
    assert result["status"] == "running"
    assert result["state_stored"] is True
    assert "state_path" not in result

    get_ok, get_result, get_error = await skillforgebridge.handle_bridge_op(
        "training.get_job",
        {"job_id": "train_20260521_0001"},
    )
    assert get_ok is True
    assert get_error is None
    assert get_result["job_id"] == "train_20260521_0001"
    assert get_result["payload"]["spec"] == {"epochs": 2}


@pytest.mark.asyncio
async def test_bridge_training_submit_job_starts_local_qlora_runner_with_dataset_package(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_TRAINING_GATEWAY_URL", "")

    started = []

    def fake_start_local_training_job(normalized):
        started.append(normalized)
        return None

    monkeypatch.setattr(skillforgebridge, "_start_local_training_job", fake_start_local_training_job)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "train_20260521_local",
            "job_type": "qlora",
            "title": "本地 QLoRA 验证",
            "department": "EC",
            "target_gateway_id": "node-1",
            "dataset_ref": "learning-artifacts://skill-a/training/latest",
            "spec": {
                "base_model": "Qwen/Qwen3.5-4B",
                "model": {"profile": "qwen3.5-4b"},
                "parameters": {"max_steps": 3},
            },
            "dataset_package": {
                "format": "skillforge_sft_jsonl_v1",
                "source": "learning_artifacts",
                "dataset_ref": "learning-artifacts://skill-a/training/latest",
                "manifest_hash": "hash-a",
                "sample_count": 1,
                "train_count": 1,
                "eval_count": 0,
                "input_contract": {
                    "schema": "skillforge_sft_jsonl_v1",
                    "required_fields": ["instruction", "input", "output"],
                    "lineage_field": "metadata",
                },
                "output_contract": {
                    "result_mode": "bridge_op_callback",
                    "callback_required": True,
                    "requires_artifact_sha256": True,
                },
                "lineage": {
                    "source": "learning_artifacts",
                    "training_job_id": "train_20260521_local",
                    "target_skill_id": "skill-a",
                    "learning_artifact_ids": ["artifact-1"],
                    "manifest_hash": "hash-a",
                },
                "raw_payload_returned": False,
                "samples": [
                    {
                        "id": "artifact-1",
                        "split": "train",
                        "instruction": "分析",
                        "input": "{\"a\":1}",
                        "output": "{\"b\":2}",
                        "metadata": {
                            "learning_artifact_id": "artifact-1",
                            "input_sha256": "input-hash-a",
                            "output_sha256": "output-hash-a",
                            "sample_sha256": "sample-hash-a",
                        },
                    }
                ],
            },
        },
    )

    assert ok is True
    assert error is None
    assert result["mode"] == "bridge_local_qlora"
    assert result["status"] == "running"
    assert result["dataset_package"]["samples"] == "[REDACTED]"
    assert result["dataset_package"]["input_contract"]["required_fields"] == ["instruction", "input", "output"]
    assert result["dataset_package"]["output_contract"]["callback_required"] is True
    assert result["dataset_package"]["lineage"]["learning_artifact_ids"] == ["artifact-1"]
    assert result["dataset_package"]["sample_fingerprints"] == [{
        "id": "artifact-1",
        "split": "train",
        "input_sha256": "input-hash-a",
        "output_sha256": "output-hash-a",
        "sample_sha256": "sample-hash-a",
    }]
    assert len(started) == 1
    assert started[0]["dataset_package"]["samples"][0]["id"] == "artifact-1"

    stored = skillforgebridge._load_training_job_record("train_20260521_local")
    assert stored["mode"] == "bridge_local_qlora"
    assert stored["payload"]["dataset_package"]["samples"] == "[REDACTED]"
    assert stored["payload"]["dataset_package"]["input_contract"]["lineage_field"] == "metadata"
    assert stored["payload"]["dataset_package"]["lineage"]["manifest_hash"] == "hash-a"
    assert stored["payload"]["dataset_package"]["sample_fingerprints"][0]["sample_sha256"] == "sample-hash-a"
    encoded_stored = json.dumps(stored, ensure_ascii=False)
    assert "{\"a\":1}" not in encoded_stored
    assert "{\"b\":2}" not in encoded_stored


@pytest.mark.asyncio
async def test_bridge_training_dataset_transfer_ops_and_dataset_ref_job(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_TRAINING_GATEWAY_URL", "")

    package = {
        "format": "skillforge_sft_jsonl_v1",
        "source": "learning_artifacts",
        "dataset_ref": "learning-artifacts://skill-a/training/latest",
        "manifest_hash": "manifest-a",
        "sample_count": 1,
        "train_count": 1,
        "eval_count": 0,
        "samples": [{
            "id": "artifact-1",
            "split": "train",
            "instruction": "分析",
            "input": "{\"keyword\":\"安全套\"}",
            "output": "{\"category\":\"成人用品\"}",
            "metadata": {"sample_sha256": "sample-a"},
        }],
    }
    raw = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()

    write_ok, write_result, write_error = await skillforgebridge.handle_bridge_op(
        "training.dataset_write_chunk",
        {
            "dataset_id": "sfds-source",
            "offset": 0,
            "content_base64": base64.b64encode(raw).decode("ascii"),
        },
    )
    assert write_ok is True
    assert write_error is None
    assert write_result["status"] == "ok"

    commit_ok, commit_result, commit_error = await skillforgebridge.handle_bridge_op(
        "training.dataset_commit",
        {"dataset_id": "sfds-source", "sha256": sha, "metadata": {"sample_count": 1}, "force": True},
    )
    assert commit_ok is True
    assert commit_error is None
    assert commit_result["status"] == "succeeded"
    assert Path(commit_result["dataset_path"]).read_bytes() == raw

    status_ok, status_result, status_error = await skillforgebridge.handle_bridge_op(
        "training.dataset_status",
        {"dataset_id": "sfds-source"},
    )
    assert status_ok is True
    assert status_error is None
    assert status_result["exists"] is True
    assert status_result["sha256"] == sha

    manifest_ok, manifest, manifest_error = await skillforgebridge.handle_bridge_op(
        "training.dataset_export_manifest",
        {"dataset_id": "sfds-source", "hash_files": True},
    )
    assert manifest_ok is True
    assert manifest_error is None
    assert manifest["asset_type"] == "training_dataset"
    assert any(item["path"] == "dataset.json" for item in manifest["files"])

    for entry in manifest["files"]:
        chunk_ok, chunk, chunk_error = await skillforgebridge.handle_bridge_op(
            "training.dataset_export_file_chunk",
            {"dataset_id": "sfds-source", "file_path": entry["path"], "offset": 0, "max_bytes": 1024 * 1024},
        )
        assert chunk_ok is True
        assert chunk_error is None
        if entry["path"] == "dataset.json":
            assert base64.b64decode(chunk["content_base64"]) == raw

        relay_ok, relay_result, relay_error = await skillforgebridge.handle_bridge_op(
            "training.dataset_import_relay_chunk",
            {
                "dataset_id": "sfds-target",
                "manifest_sha256": manifest["manifest_sha256"],
                "file_path": entry["path"],
                "offset": 0,
                "content_base64": chunk["content_base64"],
            },
        )
        assert relay_ok is True
        assert relay_error is None
        assert relay_result["status"] == "ok"

    relay_commit_ok, relay_commit, relay_commit_error = await skillforgebridge.handle_bridge_op(
        "training.dataset_import_relay_commit",
        {
            "dataset_id": "sfds-target",
            "manifest": manifest,
            "manifest_sha256": manifest["manifest_sha256"],
        },
    )
    assert relay_commit_ok is True
    assert relay_commit_error is None
    assert relay_commit["status"] == "succeeded"
    assert Path(relay_commit["dataset_path"]).read_bytes() == raw

    started = []

    def fake_start_local_training_job(normalized):
        started.append(normalized)
        return None

    monkeypatch.setattr(skillforgebridge, "_start_local_training_job", fake_start_local_training_job)

    submit_ok, submit_result, submit_error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "train_dataset_ref",
            "job_type": "qlora",
            "title": "本地 dataset ref QLoRA 验证",
            "department": "EC",
            "target_gateway_id": "node-1",
            "dataset_ref": "learning-artifacts://skill-a/training/latest",
            "spec": {"model": {"profile": "qwen3.5-4b"}, "parameters": {"max_steps": 1}},
            "dataset_package_ref": {
                "storage": "bridge_dataset",
                "dataset_id": "sfds-source",
                "dataset_path": commit_result["dataset_path"],
                "sha256": sha,
                "sample_count": 1,
            },
        },
    )

    assert submit_ok is True
    assert submit_error is None
    assert submit_result["mode"] == "bridge_local_qlora"
    assert len(started) == 1
    assert started[0]["dataset_package_ref"]["dataset_path"] == commit_result["dataset_path"]
    assert started[0]["dataset_package_ref"]["sha256"] == sha


@pytest.mark.asyncio
async def test_bridge_training_inference_runs_local_adapter_under_state_dir(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    adapter = tmp_path / "training_runs" / "train_1" / "output" / "adapter.tar.gz"
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(b"adapter")
    calls = []

    def fake_run(normalized):
        calls.append(normalized)
        return {
            "ok": True,
            "status": "completed",
            "text": "adapter inference ok",
            "text_sha256": "a" * 64,
            "metrics": {"generated_tokens": 4},
        }

    monkeypatch.setattr(skillforgebridge, "_run_local_training_inference", fake_run)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.inference",
        {
            "deployment_id": "deploy-1",
            "profile": "qwen3.5-4b",
            "artifact_uri": str(adapter),
            "prompt": "分析这个低消耗视频",
            "max_new_tokens": 16,
        },
    )

    assert ok is True
    assert error is None
    assert result["text"] == "adapter inference ok"
    assert calls[0]["artifact_uri"] == str(adapter.resolve())
    assert calls[0]["deployment_id"] == "deploy-1"
    assert calls[0]["requested_max_new_tokens"] == 16
    assert calls[0]["max_new_tokens"] == 16


def test_bridge_training_inference_token_budget_follows_gpu_headroom(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    adapter = tmp_path / "training_runs" / "train_1" / "output" / "adapter.tar.gz"
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(b"adapter")

    monkeypatch.setattr(skillforgebridge, "_runtime_gpu_headroom_mb", lambda: 24576)
    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": "qwen3.5-4b",
        "artifact_uri": str(adapter),
        "prompt": "分析",
        "max_new_tokens": 4096,
    })
    assert error is None
    assert normalized["requested_max_new_tokens"] == 4096
    assert normalized["max_new_tokens"] == 4096

    monkeypatch.setattr(skillforgebridge, "_runtime_gpu_headroom_mb", lambda: 8192)
    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": "qwen3.5-4b",
        "artifact_uri": str(adapter),
        "prompt": "分析",
        "max_new_tokens": 4096,
    })
    assert error is None
    assert normalized["requested_max_new_tokens"] == 4096
    assert normalized["max_new_tokens"] == 2048


def test_bridge_training_inference_allows_base_model_only(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "_runtime_gpu_headroom_mb", lambda: 8192)

    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": "qwen3.5-4b",
        "prompt": "写一个短视频脚本",
        "max_new_tokens": 1024,
        "base_model_only": True,
    })

    assert error is None
    assert normalized["base_model_only"] is True
    assert normalized["artifact_uri"] == ""
    assert normalized["artifact_format"] == "none"
    assert normalized["max_new_tokens"] == 1024


def test_bridge_training_inference_respects_explicit_prompt_char_limit(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "_runtime_gpu_headroom_mb", lambda: 8192)

    prompt = "前缀" + ("很长" * 4000) + "<|assistant|>"
    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": "qwen3.5-4b",
        "prompt": prompt,
        "max_prompt_chars": 7000,
        "max_new_tokens": 128,
        "base_model_only": True,
    })

    assert error is None
    assert len(normalized["prompt"]) == 7000
    assert normalized["prompt"] == prompt[:7000]


@pytest.mark.asyncio
async def test_bridge_training_inference_rejects_artifact_outside_state_dir(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path / "state")
    outside = tmp_path / "outside-adapter.tar.gz"
    outside.write_bytes(b"adapter")

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.inference",
        {
            "profile": "qwen3.5-4b",
            "artifact_uri": str(outside),
            "prompt": "分析",
        },
    )

    assert ok is False
    assert result is None
    assert "under bridge state dir" in error["message"]


def test_bridge_capabilities_advertise_builtin_intelligence_analyze(monkeypatch, tmp_path):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "LOCAL_INTELLIGENCE_GATEWAY_URL", "")
    monkeypatch.delenv("LOCAL_INTELLIGENCE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("INTELLIGENCE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("AGENT_ANALYSIS_GATEWAY_URL", raising=False)

    cap = skillforgebridge.discover_local_capabilities()

    assert "intelligence.analyze" in cap["ops"]


def test_bridge_capabilities_keep_gb10_unified_memory_gpu(monkeypatch, tmp_path):
    from bridge import skillforgebridge

    class Result:
        returncode = 0
        stdout = "NVIDIA GB10, [N/A], [N/A], [N/A], 7\n"
        stderr = ""

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge.subprocess, "run", lambda *args, **kwargs: Result())
    monkeypatch.setattr(skillforgebridge, "_training_gateway_base_url", lambda: "")

    cap = skillforgebridge.discover_local_capabilities()

    assert cap["gpu"][0]["name"] == "NVIDIA GB10"
    assert cap["gpu"][0]["backend"] == "cuda"
    assert cap["gpu"][0]["unified_memory"] is True
    assert cap["gpu"][0]["vram_total_mb"] > 0
    assert cap["training"]["gateway"] is True
    assert "qlora" in cap["training"]["supported_tasks"]


def test_bridge_capabilities_do_not_mark_apple_gpu_as_cuda_training(monkeypatch, tmp_path):
    from bridge import skillforgebridge

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        if args[:3] == ["sysctl", "-n", "hw.memsize"]:
            return Result(stdout=str(512 * (1 << 30)))
        if args[:3] == ["sysctl", "-n", "machdep.cpu.brand_string"]:
            return Result(stdout="Apple M3 Ultra\n")
        if args and args[0] == "vm_stat":
            return Result(stdout="Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 1048576.\n")
        if args and args[0] == "nvidia-smi":
            raise FileNotFoundError("nvidia-smi")
        return Result()

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)
    monkeypatch.setattr(skillforgebridge, "_training_gateway_base_url", lambda: "")

    cap = skillforgebridge.discover_local_capabilities()

    assert cap["gpu"][0]["name"] == "Apple M3 Ultra"
    assert cap["gpu"][0]["backend"] == "metal"
    assert cap["gpu"][0]["unified_memory"] is True
    assert cap["memory"]["total_gb"] == 512.0
    assert cap["training"]["gateway"] is False
    assert cap["training"]["supported_tasks"] == ["eval"]


def test_bridge_capabilities_advertise_mac_resident_openwebui_models(monkeypatch, tmp_path):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "inference-primary")
    monkeypatch.setattr(skillforgebridge.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(skillforgebridge.os, "uname", lambda: SimpleNamespace(sysname="Darwin"))
    monkeypatch.setattr(skillforgebridge, "_training_gateway_base_url", lambda: "")
    (tmp_path / "openwebui_models.json").write_text(json.dumps({
        "models": {
            "qwen3.5-4b-lora": {
                "id": "qwen3.5-4b-lora",
                "display_name": "Qwen3.5 4B LoRA",
                "deployment_id": "deploy-mac-active",
                "job_id": "train-mac",
                "runtime_profile": "mlx-qwen3.5-4b-lora",
                "artifact_uri": "/Users/secret/models/adapter",
                "updated_at": "2026-07-23T00:00:00Z",
            }
        }
    }), encoding="utf-8")

    cap = skillforgebridge.discover_local_capabilities()
    encoded = json.dumps(cap, ensure_ascii=False)

    assert cap["platform"] == "Darwin"
    assert cap["resident_models"][0]["model"] == "qwen3.5-4b-lora"
    assert cap["resident_models"][0]["deployment_id"] == "deploy-mac-active"
    assert cap["resident_models"][0]["loaded"] is True
    assert cap["training"]["resident_models"][0]["runtime_profile"] == "mlx-qwen3.5-4b-lora"
    assert "/Users/secret" not in encoded


@pytest.mark.asyncio
async def test_bridge_builtin_intelligence_analyze_uses_deployed_model_text(monkeypatch, tmp_path):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_INTELLIGENCE_GATEWAY_URL", "")
    monkeypatch.delenv("LOCAL_INTELLIGENCE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("INTELLIGENCE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("AGENT_ANALYSIS_GATEWAY_URL", raising=False)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "intelligence.analyze",
        {
            "skill_id": "samplebrand-video-low-consumption-operator-v1",
            "run_id": "run-analysis-1",
            "context_pack": {
                "video_id": "video-1",
                "topic_key": "同主题-安全套",
                "skillforge": {
                    "deployed_model_inference": {
                        "model_deployment_id": "deploy-1",
                        "text": "高消耗参照开头三秒更直接，低消耗视频利益点和投放承接不足。",
                        "text_sha256": "a" * 64,
                    }
                },
                "high_consumption_videos": [{"title": "高点击素材A"}],
                "todos": [{"title": "补查预算和人群包"}],
            },
        },
    )

    assert ok is True
    assert error is None
    assert result["backend"] == "bridge_local_structured_analyzer"
    assert result["model"] == "bridge-local-analysis-agent"
    assert "高消耗参照开头三秒" in result["raw_output"]
    output = result["output"]
    assert "小模型依据" in output["summary"]
    assert output["evidence"][0]["type"] == "deployed_model_inference"
    assert any("补查预算" in item for item in output["recommendations"])


def test_bridge_redaction_keeps_token_metric_fields():
    from bridge import skillforgebridge

    redacted = skillforgebridge._redact_training_value(
        {
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "generated_tokens": 5,
                "total_tokens": 15,
            },
            "access_token": "secret-token",
        }
    )

    assert redacted["usage"]["prompt_tokens"] == 12
    assert redacted["usage"]["completion_tokens"] == 3
    assert redacted["usage"]["generated_tokens"] == 5
    assert redacted["usage"]["total_tokens"] == 15
    assert redacted["access_token"] == "[REDACTED]"


def test_bridge_local_qlora_worker_records_eval_metrics_and_report_artifact(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    python_path = tmp_path / "training_env" / "qlora_1b" / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    model_root = tmp_path / "training_models" / "qwen3.5-4b"
    model_dir = model_root / "model"
    model_dir.mkdir(parents=True)

    monkeypatch.setattr(skillforgebridge, "_training_bootstrap_python_path", lambda profile: python_path)
    monkeypatch.setattr(skillforgebridge, "_training_model_profile_dir", lambda profile: model_root)

    captured = {}

    class Completed:
        returncode = 0
        stderr = ""
        stdout = (
            "SKILLFORGE_TRAINING_RESULT="
            + json.dumps({
                "ok": True,
                "status": "completed",
                "metrics": {
                    "train_loss": 1.23,
                    "win_rate": 0.75,
                    "eval_mode": "holdout_generation_smoke",
                    "eval_evaluated_samples": 2,
                    "eval_pass_count": 1,
                    "eval_generation_success_rate": 0.5,
                },
                "artifacts": [
                    {
                        "id": "train_eval_worker:adapter",
                        "type": "lora_adapter",
                        "name": "adapter.tar.gz",
                        "uri": str(tmp_path / "adapter.tar.gz"),
                        "sha256": "a" * 64,
                        "size_bytes": 10,
                    },
                    {
                        "id": "train_eval_worker:eval_report",
                        "type": "eval_report",
                        "name": "eval_report.json",
                        "uri": str(tmp_path / "eval_report.json"),
                        "sha256": "b" * 64,
                        "size_bytes": 20,
                    },
                ],
            })
        )

    def fake_run(cmd, *, capture_output, text, timeout, env):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        captured["env"] = env
        return Completed()

    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)

    normalized = {
        "job_id": "train_eval_worker",
        "job_type": "qlora",
        "target_gateway_id": "node-1",
        "spec": {
            "model": {"profile": "qwen3.5-4b"},
            "parameters": {
                "max_steps": 1,
                "max_seq_length": 128,
                "eval_max_samples": 2,
                "eval_max_new_tokens": 7,
            },
        },
        "dataset_package": {
            "format": "skillforge_sft_jsonl_v1",
            "dataset_ref": "learning-artifacts://skill-a/training/latest",
            "manifest_hash": "manifest-eval-worker",
            "sample_count": 2,
            "samples": [
                {"id": "s1", "split": "train", "instruction": "分析", "input": "低消耗", "output": "建议"},
                {"id": "s2", "split": "eval", "instruction": "分析", "input": "高点击低消耗", "output": "优化"},
            ],
        },
    }

    skillforgebridge._local_training_job_worker(normalized)

    assert captured["env"]["SKILLFORGE_EVAL_MAX_SAMPLES"] == "2"
    assert captured["env"]["SKILLFORGE_EVAL_MAX_NEW_TOKENS"] == "7"
    assert "SKILLFORGE_PARENT_ADAPTER_DIR" not in captured["env"]
    stored = skillforgebridge._load_training_job_record("train_eval_worker")
    assert stored["status"] == "completed"
    assert stored["metrics"]["win_rate"] == 0.75
    assert stored["metrics"]["eval_generation_success_rate"] == 0.5
    assert stored["metrics"]["dataset_ref"] == "learning-artifacts://skill-a/training/latest"
    assert stored["metrics"]["dataset_manifest_hash"] == "manifest-eval-worker"
    assert stored["metrics"]["parent_adapter_loaded"] is False
    assert stored["metrics"]["dataset_sample_ids"] == ["s1", "s2"]
    assert [item["type"] for item in stored["artifacts"]] == ["lora_adapter", "eval_report"]
    assert stored["payload"]["dataset_package"]["samples"] == "[REDACTED]"


def test_bridge_local_qlora_daily_incremental_requires_parent_adapter(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    python_path = tmp_path / "training_env" / "qlora_1b" / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    model_root = tmp_path / "training_models" / "qwen3.5-4b"
    model_dir = model_root / "model"
    model_dir.mkdir(parents=True)

    monkeypatch.setattr(skillforgebridge, "_training_bootstrap_python_path", lambda profile: python_path)
    monkeypatch.setattr(skillforgebridge, "_training_model_profile_dir", lambda profile: model_root)

    normalized = {
        "job_id": "train_incremental_missing_parent",
        "job_type": "qlora",
        "target_gateway_id": "node-1",
        "spec": {
            "training_mode": "daily_incremental",
            "model": {"profile": "qwen3.5-4b"},
            "parent_model": {
                "model_deployment_id": "deploy-prev",
                "artifact_uri": "artifact://remote/adapter.tar.gz",
            },
            "parameters": {"max_steps": 1},
        },
        "dataset_package": {
            "format": "skillforge_sft_jsonl_v1",
            "dataset_ref": "learning-artifacts://skill-a/training/yesterday/2026-06-23",
            "manifest_hash": "manifest-incremental",
            "sample_count": 1,
            "samples": [
                {"id": "s1", "split": "train", "instruction": "分析", "input": "低消耗", "output": "建议"},
            ],
        },
    }

    skillforgebridge._local_training_job_worker(normalized)

    stored = skillforgebridge._load_training_job_record("train_incremental_missing_parent")
    assert stored["status"] == "failed"
    assert stored["failure_stage"] == "local_training"
    assert "daily incremental training requires previous adapter" in stored["error"]


def test_bridge_local_qlora_daily_incremental_loads_local_parent_adapter(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    python_path = tmp_path / "training_env" / "qlora_1b" / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    model_root = tmp_path / "training_models" / "qwen3.5-4b"
    model_dir = model_root / "model"
    model_dir.mkdir(parents=True)
    parent_adapter = tmp_path / "previous_adapter"
    parent_adapter.mkdir()
    (parent_adapter / "adapter_config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(skillforgebridge, "_training_bootstrap_python_path", lambda profile: python_path)
    monkeypatch.setattr(skillforgebridge, "_training_model_profile_dir", lambda profile: model_root)

    captured = {}

    class Completed:
        returncode = 0
        stderr = ""
        stdout = (
            "SKILLFORGE_TRAINING_RESULT="
            + json.dumps({
                "ok": True,
                "status": "completed",
                "metrics": {"train_loss": 1.0},
                "artifacts": [],
            })
        )

    def fake_run(cmd, *, capture_output, text, timeout, env):
        captured["env"] = env
        return Completed()

    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)

    normalized = {
        "job_id": "train_incremental_parent",
        "job_type": "qlora",
        "target_gateway_id": "node-1",
        "spec": {
            "training_mode": "daily_incremental",
            "model": {"profile": "qwen3.5-4b"},
            "parent_model": {
                "model_deployment_id": "deploy-prev",
                "artifact_uri": str(parent_adapter),
            },
            "parameters": {"max_steps": 1},
        },
        "dataset_package": {
            "format": "skillforge_sft_jsonl_v1",
            "dataset_ref": "learning-artifacts://skill-a/training/yesterday/2026-06-23",
            "manifest_hash": "manifest-incremental-parent",
            "sample_count": 1,
            "samples": [
                {"id": "s1", "split": "train", "instruction": "分析", "input": "低消耗", "output": "建议"},
            ],
        },
    }

    skillforgebridge._local_training_job_worker(normalized)

    assert captured["env"]["SKILLFORGE_PARENT_ADAPTER_DIR"] == str(parent_adapter.resolve())
    stored = skillforgebridge._load_training_job_record("train_incremental_parent")
    assert stored["status"] == "completed"
    assert stored["metrics"]["parent_adapter_loaded"] is True
    assert stored["metrics"]["parent_adapter_status"] == "loaded"


@pytest.mark.asyncio
async def test_bridge_training_bootstrap_env_dry_run_uses_whitelisted_plan(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.bootstrap_env",
        {
            "profile": "qlora-1b",
            "cuda": "cu121",
            "dry_run": True,
        },
    )

    assert ok is True
    assert error is None
    assert result["status"] == "planned"
    assert result["profile"] == "qlora-1b"
    assert result["dry_run"] is True
    assert result["supported_tasks"] == ["lora", "qlora", "eval", "merge", "inference"]
    assert [item["step"] for item in result["commands"]] == [
        "create_venv",
        "upgrade_packaging",
        "install_torch",
        "install_training_packages",
        "smoke_test",
    ]
    torch_step = next(item for item in result["commands"] if item["step"] == "install_torch")
    assert "https://download.pytorch.org/whl/cu121" in torch_step["args"]
    assert "torch==2.4.1" in torch_step["args"]
    assert not (tmp_path / "training_env" / "qlora_1b" / "env.json").exists()


@pytest.mark.asyncio
async def test_bridge_training_bootstrap_env_dry_run_supports_cu130(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.bootstrap_env",
        {"profile": "qlora-1b", "cuda": "cu130", "dry_run": True},
    )

    assert ok is True
    assert error is None
    assert result["cuda"] == "cu130"
    torch_step = next(item for item in result["commands"] if item["step"] == "install_torch")
    assert "https://download.pytorch.org/whl/cu130" in torch_step["args"]
    assert "torch==2.13.0+cu130" in torch_step["args"]


@pytest.mark.asyncio
async def test_bridge_training_bootstrap_env_rejects_unknown_profile(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.bootstrap_env",
        {
            "profile": "shell",
            "dry_run": True,
        },
    )

    assert ok is False
    assert result is None
    assert "unsupported training bootstrap profile" in error["message"]


@pytest.mark.asyncio
async def test_bridge_training_prepare_model_dry_run_uses_whitelisted_profile(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    bootstrap_dir = tmp_path / "training_env" / "qlora_1b"
    python_path = bootstrap_dir / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    skillforgebridge._write_training_bootstrap_status("qlora-1b", {
        "status": "succeeded",
        "profile": "qlora-1b",
        "cuda": "cu121",
        "python": str(python_path),
    })

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.prepare_model",
        {
            "profile": "qwen3.5-4b",
            "dry_run": True,
            "validate_mode": "metadata",
        },
    )

    assert ok is True
    assert error is None
    assert result["status"] == "planned"
    assert result["profile"] == "qwen3.5-4b"
    assert result["model_id"] == "Qwen/Qwen3.5-4B"
    assert result["bootstrap_profile"] == "qlora-1b"
    assert result["validate_mode"] == "metadata"
    assert result["source"] == "auto"
    assert not (tmp_path / "training_models" / "qwen3.5-4b" / "status.json").exists()


@pytest.mark.asyncio
async def test_bridge_training_model_status_uses_internal_model_ref_payload(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    source_dir = tmp_path / "internal-models" / "qwen36"
    source_dir.mkdir(parents=True)
    (source_dir / "config.json").write_text("{}", encoding="utf-8")
    (source_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.model_status",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "internal_model_ref": str(source_dir),
        },
    )

    assert ok is True
    assert error is None
    assert result["profile"] == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    assert result["internal_model_ref"] == str(source_dir)
    assert result["source_exists"] is True
    assert result["source_path"] == str(source_dir.resolve())
    assert result["source_probe"]["config"] is True


@pytest.mark.asyncio
async def test_bridge_training_configure_runtime_persists_qwen36_model_dir(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    state_dir = tmp_path / "state"
    source_dir = tmp_path / "internal-models" / "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    source_dir.mkdir(parents=True)
    (source_dir / "config.json").write_text("{}", encoding="utf-8")
    (source_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (source_dir / "model.safetensors.index.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", state_dir)
    monkeypatch.setattr(skillforgebridge, "ENV_PATH", state_dir / "env.json")
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.configure_runtime",
        {"env": {"SKILLFORGE_QWEN36_35B_MODEL_DIR": str(source_dir)}},
    )

    assert ok is True
    assert error is None
    assert result["configured"] is True
    assert result["env"]["SKILLFORGE_QWEN36_35B_MODEL_DIR"] == str(source_dir.resolve())
    assert skillforgebridge.ENV_PATH.exists()

    ok, status, error = await skillforgebridge.handle_bridge_op(
        "training.model_status",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
        },
    )

    assert ok is True
    assert error is None
    assert status["source_exists"] is True
    assert status["source_path"] == str(source_dir.resolve())
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)


@pytest.mark.asyncio
async def test_bridge_training_model_status_suggests_internal_model_candidates(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    candidate_root = tmp_path / "candidate-roots"
    candidate_dir = candidate_root / "llm" / "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")
    wrong_base_dir = candidate_root / "llm" / "qwen36-27b-abliterated"
    wrong_base_dir.mkdir(parents=True)
    (wrong_base_dir / "config.json").write_text("{}", encoding="utf-8")
    (wrong_base_dir / "model.safetensors.index.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(skillforgebridge, "TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS", (str(candidate_root),))
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.model_status",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "internal_model_ref": str(tmp_path / "missing-model"),
        },
    )

    assert ok is True
    assert error is None
    assert result["source_exists"] is False
    assert result["source_error"] == "internal model path does not exist"
    assert result["source_candidates"][0]["path"] == str(candidate_dir.resolve())
    assert result["source_candidates"][0]["has_config"] is True
    assert result["source_candidates"][0]["has_weights"] is True
    assert all(item["path"] != str(wrong_base_dir.resolve()) for item in result["source_candidates"])


@pytest.mark.asyncio
async def test_bridge_training_discover_models_returns_internal_candidates(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    candidate_root = tmp_path / "candidate-roots"
    candidate_dir = candidate_root / "models" / "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(skillforgebridge, "TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS", (str(candidate_root),))
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.discover_models",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
        },
    )

    assert ok is True
    assert error is None
    assert result["profile"] == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    assert result["scan_summary"]["candidate_count"] == 1
    assert result["candidates"][0]["path"] == str(candidate_dir.resolve())
    assert result["candidates"][0]["has_config"] is True
    assert result["candidates"][0]["has_weights"] is True


@pytest.mark.asyncio
async def test_bridge_training_explore_model_paths_uses_extra_roots_and_safe_metadata(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    candidate_root = tmp_path / "external-storage"
    candidate_dir = candidate_root / "Qwen36-35B-A3B" / "snapshots" / "main"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "config.json").write_text(
        json.dumps({
            "model_type": "qwen3",
            "architectures": ["Qwen3ForCausalLM"],
            "hidden_size": 8192,
            "api_key": "must-not-return",
        }),
        encoding="utf-8",
    )
    (candidate_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")
    skipped_dir = candidate_root / ".ssh" / "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    skipped_dir.mkdir(parents=True)
    (skipped_dir / "config.json").write_text("{}", encoding="utf-8")
    (skipped_dir / "model.safetensors").write_bytes(b"secret-weights")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(skillforgebridge, "TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS", ())
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.explore_model_paths",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "roots": [str(candidate_root)],
            "terms": ["Qwen36-35B-A3B"],
            "include_metadata": True,
            "max_depth": 5,
            "max_dirs": 100,
        },
    )

    assert ok is True
    assert error is None
    assert result["read_policy"]["mode"] == "controlled_read_only"
    assert result["read_policy"]["remote_shell"] is False
    assert result["candidates"][0]["path"] == str(candidate_dir.resolve())
    assert result["candidates"][0]["metadata"]["model_type"] == "qwen3"
    assert "api_key" not in result["candidates"][0]["metadata"]
    assert all(item["path"] != str(skipped_dir.resolve()) for item in result["candidates"])
    assert result["scan_summary"]["skipped_dirs"] >= 1


@pytest.mark.asyncio
async def test_bridge_training_model_export_and_import_from_internal_http(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    source_dir = tmp_path / "Qwen3.6-35B-A3B" / "snapshots" / "main"
    source_dir.mkdir(parents=True)
    (source_dir / "config.json").write_text(
        json.dumps({"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]}),
        encoding="utf-8",
    )
    (source_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (source_dir / "model-00001-of-00001.safetensors").write_bytes(b"test-weights")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("SKILLFORGE_MODEL_EXPORT_PUBLIC_HOST", "127.0.0.1")
    monkeypatch.setenv("SKILLFORGE_MODEL_EXPORT_BIND_HOST", "127.0.0.1")

    ok, exported, error = await skillforgebridge.handle_bridge_op(
        "training.model_export_start",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "source_path": str(source_dir),
            "ttl_seconds": 600,
        },
    )

    assert ok is True
    assert error is None
    assert exported["status"] == "ready"
    assert exported["token"] and exported["token"] != "[REDACTED]"
    assert exported["file_count"] == 3
    assert exported["export_url"].startswith("http://127.0.0.1:")

    ok, imported, error = await skillforgebridge.handle_bridge_op(
        "training.model_import_from_export",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "source_gateway_id": "data-primary",
            "source_path": str(source_dir),
            "export_url": exported["export_url"],
            "manifest_url": exported["manifest_url"],
            "manifest_sha256": exported["manifest_sha256"],
            "token": exported["token"],
            "force": True,
        },
    )

    assert ok is True
    assert error is None
    assert imported["status"] == "succeeded"
    imported_dir = Path(imported["internal_model_ref"])
    assert imported_dir.is_dir()
    assert (imported_dir / "config.json").is_file()
    assert (imported_dir / "tokenizer_config.json").is_file()
    assert (imported_dir / "model-00001-of-00001.safetensors").read_bytes() == b"test-weights"
    assert imported["source_gateway_id"] == "data-primary"

    ok, manifest, error = await skillforgebridge.handle_bridge_op(
        "training.model_export_manifest",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "source_path": str(source_dir),
        },
    )
    assert ok is True
    assert error is None
    assert manifest["status"] == "ready"
    assert manifest["file_count"] == 3

    for entry in manifest["files"]:
        offset = 0
        while True:
            ok, chunk, error = await skillforgebridge.handle_bridge_op(
                "training.model_export_file_chunk",
                {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                    "source_path": str(source_dir),
                    "file_path": entry["path"],
                    "offset": offset,
                    "max_bytes": 5,
                },
            )
            assert ok is True
            assert error is None
            ok, written, error = await skillforgebridge.handle_bridge_op(
                "training.model_import_relay_chunk",
                {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                    "manifest_sha256": manifest["manifest_sha256"],
                    "file_path": entry["path"],
                    "offset": offset,
                    "content_base64": chunk["content_base64"],
                },
            )
            assert ok is True
            assert error is None
            assert written["status"] == "ok"
            offset += chunk["size_bytes"]
            if chunk["eof"]:
                break

    ok, committed, error = await skillforgebridge.handle_bridge_op(
        "training.model_import_relay_commit",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "manifest": manifest,
            "manifest_sha256": manifest["manifest_sha256"],
        },
    )
    assert ok is True
    assert error is None
    assert committed["status"] == "succeeded"
    relay_dir = Path(committed["internal_model_ref"])
    assert (relay_dir / "model-00001-of-00001.safetensors").read_bytes() == b"test-weights"


@pytest.mark.asyncio
async def test_bridge_training_model_status_finds_deep_huggingface_snapshot_candidate(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    candidate_root = tmp_path / "home"
    candidate_dir = (
        candidate_root
        / "skillforge"
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
        / "snapshots"
        / "abc123"
    )
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(skillforgebridge, "TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS", (str(candidate_root),))
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.model_status",
        {
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "source": "internal",
            "internal_model_ref": str(tmp_path / "missing-model"),
        },
    )

    assert ok is True
    assert error is None
    paths = {item["path"] for item in result["source_candidates"]}
    assert str(candidate_dir.resolve()) in paths


@pytest.mark.asyncio
async def test_bridge_training_prepare_model_auto_discovers_and_persists_internal_path(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    state_dir = tmp_path / "state"
    candidate_root = tmp_path / "candidate-roots"
    candidate_dir = candidate_root / "models" / profile
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (candidate_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")

    python_path = state_dir / "training_env" / "qlora_1b" / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", state_dir)
    monkeypatch.setattr(skillforgebridge, "ENV_PATH", state_dir / "env.json")
    monkeypatch.setattr(skillforgebridge, "TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS", (str(candidate_root),))
    monkeypatch.delenv("SKILLFORGE_QWEN36_35B_MODEL_DIR", raising=False)
    monkeypatch.setitem(
        skillforgebridge.TRAINING_MODEL_PROFILES,
        profile,
        {**skillforgebridge.TRAINING_MODEL_PROFILES[profile], "prepare_packages": []},
    )
    skillforgebridge._write_training_bootstrap_status("qlora-1b", {
        "status": "succeeded",
        "profile": "qlora-1b",
        "cuda": "cu121",
        "python": str(python_path),
    })

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.prepare_model",
        {
            "profile": profile,
            "source": "internal",
            "internal_model_ref": str(tmp_path / "missing-model"),
            "validate_mode": "metadata",
            "timeout_seconds": 180,
            "auto_discover": True,
        },
    )

    assert ok is True
    assert error is None
    assert result["status"] == "succeeded"
    assert result["source_path"] == str(candidate_dir.resolve())
    assert result["internal_model_ref"] == str(candidate_dir.resolve())
    assert result["events"][0]["auto_discovered"] is True
    assert result["events"][0]["configured_env"] == "SKILLFORGE_QWEN36_35B_MODEL_DIR"
    assert (state_dir / "training_models" / profile / "model").resolve() == candidate_dir.resolve()
    assert json.loads(skillforgebridge.ENV_PATH.read_text(encoding="utf-8"))["SKILLFORGE_QWEN36_35B_MODEL_DIR"] == str(candidate_dir.resolve())


@pytest.mark.asyncio
async def test_bridge_training_prepare_model_internal_skips_missing_training_env(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    state_dir = tmp_path / "state"
    source_dir = tmp_path / "models" / profile
    source_dir.mkdir(parents=True)
    (source_dir / "config.json").write_text("{}", encoding="utf-8")
    (source_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (source_dir / "model-00001-of-00001.safetensors").write_bytes(b"weights")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", state_dir)
    monkeypatch.setattr(skillforgebridge, "ENV_PATH", state_dir / "env.json")

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.prepare_model",
        {
            "profile": profile,
            "source": "internal",
            "internal_model_ref": str(source_dir),
            "validate_mode": "metadata",
            "timeout_seconds": 180,
        },
    )

    assert ok is True
    assert error is None
    assert result["status"] == "succeeded"
    assert result["exists"] is True
    assert result["source_path"] == str(source_dir.resolve())
    assert (state_dir / "training_models" / profile / "model").resolve() == source_dir.resolve()
    assert not (state_dir / "training_env" / "qlora_1b" / "venv" / "bin" / "python").exists()


@pytest.mark.asyncio
async def test_bridge_training_prepare_model_rejects_unknown_profile(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.prepare_model",
        {
            "profile": "Qwen/anything",
            "dry_run": True,
        },
    )

    assert ok is False
    assert result is None
    assert "unsupported training model profile" in error["message"]


@pytest.mark.asyncio
async def test_bridge_training_submit_job_rejects_bad_payload(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "../escape",
            "job_type": "shell",
            "spec": [],
        },
    )

    assert ok is False
    assert result is None
    assert "invalid job_id" in error["message"]


@pytest.mark.asyncio
async def test_bridge_training_cancel_job_marks_manifest_cancelled(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")

    submit_ok, _, submit_error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "train_20260521_0002",
            "job_type": "eval",
            "title": "评估任务",
            "department": "EC",
            "target_gateway_id": "node-1",
            "spec": {},
        },
    )
    assert submit_ok is True
    assert submit_error is None

    cancel_ok, cancel_result, cancel_error = await skillforgebridge.handle_bridge_op(
        "training.cancel_job",
        {"job_id": "train_20260521_0002"},
    )
    assert cancel_ok is True
    assert cancel_error is None
    assert cancel_result == {
        "cancelled": True,
        "job_id": "train_20260521_0002",
        "status": "cancelled",
    }

    get_ok, get_result, get_error = await skillforgebridge.handle_bridge_op(
        "training.get_job",
        {"job_id": "train_20260521_0002"},
    )
    assert get_ok is True
    assert get_error is None
    assert get_result["status"] == "cancelled"
    assert get_result["events"][-1]["type"] == "cancelled"


@pytest.mark.asyncio
async def test_bridge_training_submit_job_forwards_to_configured_gateway_and_redacts(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_TRAINING_GATEWAY_URL", "http://127.0.0.1:17890")

    calls = []

    async def fake_call_training_gateway(method, path, payload=None, timeout=30):
        calls.append({"method": method, "path": path, "payload": payload, "timeout": timeout})
        return {
            "accepted": True,
            "status": "running",
            "gateway_job_id": "gw-train-1",
            "worker_id": "worker-a",
            "access_token": "gateway-token-should-not-leak",
            "headers": {"Authorization": "Bearer gateway-header-should-not-leak"},
        }

    monkeypatch.setattr(skillforgebridge, "_call_training_gateway", fake_call_training_gateway)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "train_20260521_0003",
            "job_type": "lora",
            "title": "商品动作推荐训练",
            "department": "EC",
            "target_gateway_id": "node-1",
            "spec": {"epochs": 2},
        },
    )

    assert ok is True
    assert error is None
    assert result["mode"] == "training_gateway"
    assert result["gateway_job_id"] == "gw-train-1"
    assert result["worker_id"] == "worker-a"
    assert calls == [{
        "method": "POST",
        "path": "/training/jobs",
        "payload": {
            "job_id": "train_20260521_0003",
            "job_type": "lora",
            "title": "商品动作推荐训练",
            "department": "EC",
            "target_skill_id": "",
            "target_gateway_id": "node-1",
            "dataset_ref": "",
            "training_strategy": "",
            "objective": "",
            "risk_level": "",
            "spec": {"epochs": 2},
            "control": {},
        },
        "timeout": 30,
    }]
    encoded_result = json.dumps(result, ensure_ascii=False)
    assert "gateway-token-should-not-leak" not in encoded_result
    assert "gateway-header-should-not-leak" not in encoded_result

    stored = skillforgebridge._load_training_job_record("train_20260521_0003")
    encoded_stored = json.dumps(stored, ensure_ascii=False)
    assert stored["status"] == "running"
    assert stored["gateway_result"]["access_token"] == "[REDACTED]"
    assert "gateway-token-should-not-leak" not in encoded_stored
    assert "gateway-header-should-not-leak" not in encoded_stored


@pytest.mark.asyncio
async def test_bridge_training_cancel_and_logs_forward_to_gateway(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_TRAINING_GATEWAY_URL", "http://127.0.0.1:17890")

    calls = []

    async def fake_call_training_gateway(method, path, payload=None, timeout=30):
        calls.append({"method": method, "path": path, "payload": payload, "timeout": timeout})
        if path.endswith("/cancel"):
            return {
                "cancelled": True,
                "job_id": payload["job_id"],
                "status": "cancelled",
                "access_token": "cancel-token-should-not-leak",
            }
        if path.endswith("/logs"):
            return {
                "status": "running",
                "lines": [
                    {"ts": "2026-05-21T10:00:00Z", "type": "info", "message": "api_key=log-secret-should-not-leak"}
                ],
            }
        return {"accepted": True, "status": "running", "worker_id": "worker-a"}

    monkeypatch.setattr(skillforgebridge, "_call_training_gateway", fake_call_training_gateway)

    submit_ok, _, submit_error = await skillforgebridge.handle_bridge_op(
        "training.submit_job",
        {
            "job_id": "train_20260521_0004",
            "job_type": "eval",
            "title": "评估任务",
            "department": "EC",
            "target_gateway_id": "node-1",
            "spec": {},
        },
    )
    assert submit_ok is True
    assert submit_error is None

    logs_ok, logs_result, logs_error = await skillforgebridge.handle_bridge_op(
        "training.stream_logs",
        {"job_id": "train_20260521_0004"},
    )
    assert logs_ok is True
    assert logs_error is None
    encoded_logs = json.dumps(logs_result, ensure_ascii=False)
    assert "[REDACTED]" in encoded_logs
    assert "log-secret-should-not-leak" not in encoded_logs

    cancel_ok, cancel_result, cancel_error = await skillforgebridge.handle_bridge_op(
        "training.cancel_job",
        {"job_id": "train_20260521_0004", "reason": "user_cancelled"},
    )
    assert cancel_ok is True
    assert cancel_error is None
    assert cancel_result["cancelled"] is True
    assert cancel_result["status"] == "cancelled"
    assert "cancel-token-should-not-leak" not in json.dumps(cancel_result, ensure_ascii=False)
    assert calls == [
        {"method": "POST", "path": "/training/jobs", "payload": {
            "job_id": "train_20260521_0004",
            "job_type": "eval",
            "title": "评估任务",
            "department": "EC",
            "target_skill_id": "",
            "target_gateway_id": "node-1",
            "dataset_ref": "",
            "training_strategy": "",
            "objective": "",
            "risk_level": "",
            "spec": {},
            "control": {},
        }, "timeout": 30},
        {"method": "GET", "path": "/training/jobs/train_20260521_0004/logs", "payload": None, "timeout": 15},
        {"method": "POST", "path": "/training/jobs/train_20260521_0004/cancel", "payload": {
            "job_id": "train_20260521_0004",
            "reason": "user_cancelled",
        }, "timeout": 15},
    ]


@pytest.mark.asyncio
async def test_bridge_training_download_artifact_forwards_to_gateway(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_TRAINING_GATEWAY_URL", "http://127.0.0.1:17890")

    calls = []

    async def fake_call_training_gateway_download(method, path, payload=None, timeout=60):
        calls.append({"method": method, "path": path, "payload": payload, "timeout": timeout})
        return {
            "filename": "model.bin",
            "content_type": "application/octet-stream",
            "content_base64": "bW9kZWw=",
            "sha256": "model-sha",
            "access_token": "download-token-should-not-leak",
        }

    monkeypatch.setattr(skillforgebridge, "_call_training_gateway_download", fake_call_training_gateway_download)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.download_artifact",
        {"job_id": "train_20260521_0005", "artifact_id": "1:0"},
    )

    assert ok is True
    assert error is None
    assert result["filename"] == "model.bin"
    assert result["content_base64"] == "bW9kZWw="
    assert result["access_token"] == "[REDACTED]"
    assert calls == [{
        "method": "GET",
        "path": "/training/jobs/train_20260521_0005/artifacts/1%3A0/download",
        "payload": None,
        "timeout": 60,
    }]


@pytest.mark.asyncio
async def test_bridge_training_download_artifact_reads_local_file_by_sha(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    monkeypatch.setattr(skillforgebridge, "LOCAL_TRAINING_GATEWAY_URL", "")

    adapter = tmp_path / "training_runs" / "train_local" / "output" / "adapter.tar.gz"
    adapter.parent.mkdir(parents=True)
    content = b"real-adapter"
    adapter.write_bytes(content)
    sha = skillforgebridge._training_sha256_file(adapter)
    skillforgebridge._write_training_job_record("train_local", {
        "job_id": "train_local",
        "status": "completed",
        "artifacts": [{
            "id": "train_local:adapter",
            "type": "lora_adapter",
            "name": "adapter.tar.gz",
            "uri": str(adapter),
            "sha256": sha,
            "size_bytes": len(content),
        }],
    })

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.download_artifact",
        {
            "job_id": "train_local",
            "artifact_id": "101:0",
            "artifact_name": "adapter.tar.gz",
            "sha256": sha,
        },
    )

    assert ok is True
    assert error is None
    assert result["filename"] == "adapter.tar.gz"
    assert result["sha256"] == sha
    assert result["content_base64"] == "cmVhbC1hZGFwdGVy"


@pytest.mark.asyncio
async def test_bridge_imported_artifact_registers_openwebui_model(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "inference-primary")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_ENABLED", "1")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PORT", "18080")

    content = b"mac-adapter"
    sha = skillforgebridge.hashlib.sha256(content).hexdigest()
    import_ok, imported, import_error = await skillforgebridge.handle_bridge_op(
        "training.import_artifact",
        {
            "job_id": "train_openwebui",
            "artifact_id": "train_openwebui:adapter",
            "filename": "adapter.tar.gz",
            "sha256": sha,
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
    )

    assert import_ok is True
    assert import_error is None
    assert imported["exists"] is True
    assert imported["sha256"] == sha
    assert (tmp_path / "training_imports" / "train_openwebui" / "adapter.tar.gz").read_bytes() == content

    register_ok, registered, register_error = await skillforgebridge.handle_bridge_op(
        "training.register_openwebui_model",
        {
            "model_id": "skill:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:ft",
            "aliases": ["qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"],
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
            "deployment_id": "deploy-openwebui",
            "job_id": "train_openwebui",
            "artifact_uri": imported["uri"],
            "artifact_sha256": sha,
        },
    )

    assert register_ok is True
    assert register_error is None
    assert registered["registered"] is True
    assert registered["base_url"] == "http://127.0.0.1:18080/v1"
    registry = skillforgebridge._load_openwebui_registry()
    assert "skill:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:ft" in registry["models"]
    assert "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive" in registry["models"]

    status_ok, status, status_error = await skillforgebridge.handle_bridge_op(
        "training.openwebui_status",
        {},
    )

    assert status_ok is True
    assert status_error is None
    assert status["enabled"] is True
    assert status["base_url"] == "http://127.0.0.1:18080/v1"
    assert status["model_count"] == 2
    assert {item["id"] for item in status["models"]} == {
        "skill:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:ft",
        "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
    }
    assert "server_reachable" in status


def test_bridge_openwebui_status_uses_public_host_for_wildcard_bind(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_ENABLED", "1")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_HOST", "0.0.0.0")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PUBLIC_HOST", "192.0.2.11")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PORT", "18080")

    registered = skillforgebridge._register_openwebui_model({
        "model_id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "base_model_only": True,
    })
    status = skillforgebridge._openwebui_status_sync()

    assert registered["base_url"] == "http://192.0.2.11:18080/v1"
    assert status["bind_host"] == "0.0.0.0"
    assert status["local_host"] == "127.0.0.1"
    assert status["host"] == "192.0.2.11"
    assert status["base_url"] == "http://192.0.2.11:18080/v1"
    assert status["local_base_url"] == "http://127.0.0.1:18080/v1"


@pytest.mark.asyncio
async def test_bridge_openwebui_chat_test_posts_chat_completion(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_ENABLED", "1")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PORT", "18080")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_API_KEY", "openwebui-secret")

    register_ok, registered, register_error = await skillforgebridge.handle_bridge_op(
        "training.register_openwebui_model",
        {
            "model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
            "base_model_only": True,
            "source_type": "skillforge_base_model",
        },
    )
    assert register_ok is True
    assert register_error is None
    assert registered["registered"] is True

    sent = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl-test",
                "choices": [{"message": {"content": "OpenWebUI chat works"}, "finish_reason": "stop"}],
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        sent["url"] = req.full_url
        sent["timeout"] = timeout
        sent["headers"] = {key.lower(): value for key, value in req.header_items()}
        sent["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(skillforgebridge.urllib.request, "urlopen", fake_urlopen)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.openwebui_chat_test",
        {
            "model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "prompt": "ping",
            "max_tokens": 8,
            "timeout_seconds": 60,
        },
    )

    assert ok is True
    assert error is None
    assert result["ok"] is True
    assert result["text"] == "OpenWebUI chat works"
    assert sent["url"] == "http://127.0.0.1:18080/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer openwebui-secret"
    assert sent["body"]["model"] == "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    assert sent["body"]["messages"] == [{"role": "user", "content": "ping"}]
    assert sent["body"]["max_tokens"] == 8


@pytest.mark.asyncio
async def test_bridge_openwebui_chat_test_preserves_long_prompt_and_context_fields(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_ENABLED", "1")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PORT", "18080")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_FORWARD_CONTEXT_WINDOW", "1")

    await skillforgebridge.handle_bridge_op(
        "training.register_openwebui_model",
        {
            "model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "runtime_profile": "mlx-qwen3.6-35b-lora",
            "base_model_only": True,
        },
    )

    sent = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl-long-context",
                "choices": [{"message": {"content": "long context ok"}, "finish_reason": "stop"}],
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        sent["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(skillforgebridge.urllib.request, "urlopen", fake_urlopen)
    prompt = "x" * 25000

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.openwebui_chat_test",
        {
            "model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "prompt": prompt,
            "max_tokens": 8,
            "max_input_chars": len(prompt),
            "context_window": 32768,
        },
    )

    assert ok is True
    assert error is None
    assert sent["body"]["messages"] == [{"role": "user", "content": prompt}]
    assert sent["body"]["context_window"] == 32768
    assert sent["body"]["num_ctx"] == 32768
    assert result["prompt_chars"] == len(prompt)
    assert result["max_input_chars"] == len(prompt)


@pytest.mark.asyncio
async def test_bridge_openwebui_chat_test_rejects_prompt_over_bridge_limit(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_ENABLED", "1")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PORT", "18080")

    await skillforgebridge.handle_bridge_op(
        "training.register_openwebui_model",
        {"model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"},
    )

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.openwebui_chat_test",
        {
            "model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "prompt": "x" * 50,
            "max_input_chars": 10,
        },
    )

    assert ok is False
    assert result["status"] == "failed"
    assert result["prompt_chars"] == 50
    assert result["max_input_chars"] == 10
    assert "Bridge input limit" in error["message"]


def test_bridge_openwebui_server_normalizes_inference_payload(tmp_path, monkeypatch):
    import socket
    import urllib.request

    from bridge import skillforgebridge

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    captured = {}

    def fake_run_local_training_inference(normalized):
        captured.update(normalized)
        return {
            "ok": True,
            "status": "completed",
            "text": "normalized chat works",
            "finish_reason": "stop",
            "metrics": {"generated_tokens": 3},
        }

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_ENABLED", "1")
    monkeypatch.setenv("SKILLFORGE_OPENWEBUI_PORT", str(port))
    monkeypatch.delenv("SKILLFORGE_OPENWEBUI_API_KEY", raising=False)
    monkeypatch.setattr(skillforgebridge, "_run_local_training_inference", fake_run_local_training_inference)

    registered = skillforgebridge._register_openwebui_model({
        "model_id": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "runtime_profile": "mlx-qwen3.6-35b-a3b-base",
        "base_model_only": True,
        "source_type": "skillforge_base_model",
    })
    assert registered["registered"] is True

    server = skillforgebridge._start_openwebui_server_if_enabled()
    try:
        body = json.dumps({
            "model": "skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    assert payload["choices"][0]["message"]["content"] == "normalized chat works"
    assert captured["bootstrap_profile"] == "qlora-1b"
    assert captured["artifact_format"] == "none"
    assert captured["base_model_only"] is True
    assert captured["runtime_profile"] == "mlx-qwen3.6-35b-a3b-base"
    assert captured["max_new_tokens"] == 8


def test_bridge_training_inference_accepts_file_uri_for_openwebui_model(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "_runtime_gpu_headroom_mb", lambda: 24576)
    adapter = tmp_path / "training_imports" / "train_file_uri" / "adapter.tar.gz"
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(b"adapter")

    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
        "artifact_uri": adapter.as_uri(),
        "prompt": "你好",
        "max_new_tokens": 2048,
    })

    assert error is None
    assert normalized["artifact_uri"] == str(adapter.resolve())
    assert normalized["artifact_format"] == "tar.gz"
    assert normalized["runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"
    assert normalized["max_new_tokens"] == 1536


def test_bridge_training_inference_mlx_uses_current_python_without_training_env(tmp_path, monkeypatch):
    import subprocess
    import sys

    from bridge import skillforgebridge

    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    model_dir = tmp_path / "training_models" / profile / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": profile,
        "runtime_profile": "mlx-qwen3.6-35b-a3b-base",
        "base_model_only": True,
        "prompt": "ping",
        "max_new_tokens": 8,
    })
    assert error is None

    calls = {}

    def fake_run(cmd, *, capture_output, text, timeout, env=None):
        calls["cmd"] = cmd
        if env is not None:
            calls["env"] = env
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='SKILLFORGE_INFERENCE_RESULT={"ok":true,"status":"completed","text":"pong","metrics":{}}\n',
            stderr="",
        )

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)

    result = skillforgebridge._run_local_training_inference(normalized)

    assert result["text"] == "pong"
    assert Path(calls["cmd"][0]).resolve() == Path(sys.executable).resolve()
    assert calls["env"]["SKILLFORGE_RUNTIME_PROFILE"] == "mlx-qwen3.6-35b-a3b-base"
    assert calls["env"]["SKILLFORGE_BASE_MODEL_ONLY"] == "1"


def test_bridge_mlx_python_installs_qwen35_support_when_missing(monkeypatch):
    import subprocess
    import sys

    from bridge import skillforgebridge

    upgraded = {"done": False}
    calls = []
    python_path = Path(sys.executable).resolve()

    def fake_has_module(candidate, module_name):
        if module_name == "mlx_lm":
            return True
        if module_name == "mlx_lm.models.qwen3_5_moe":
            return upgraded["done"]
        return False

    def fake_run(cmd, *, capture_output, text, timeout, env=None):
        calls.append(cmd)
        upgraded["done"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(skillforgebridge, "_mlx_python_candidates", lambda preferred: [python_path])
    monkeypatch.setattr(skillforgebridge, "_python_has_module", fake_has_module)
    monkeypatch.setattr(skillforgebridge, "_patch_mlx_lm_future_annotations", lambda candidate: None)
    monkeypatch.setattr(skillforgebridge, "OPENWEBUI_MLX_QWEN35_MOE_PACKAGE", "mlx-lm==0.30.7")
    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)

    result = skillforgebridge._ensure_mlx_inference_python(python_path, require_qwen35_moe=True)

    assert result == python_path
    assert len(calls) == 1
    assert calls[0][:4] == [str(python_path), "-m", "pip", "install"]
    assert "--upgrade" in calls[0]
    assert "--force-reinstall" in calls[0]
    assert "--no-deps" in calls[0]
    assert calls[0][-1] == "mlx-lm==0.30.7"


def test_bridge_mlx_compat_uses_custom_qwen35_moe_model_file(tmp_path, monkeypatch):
    import sys

    from bridge import skillforgebridge

    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    model_dir = tmp_path / "training_models" / profile / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(
        '{"model_type":"qwen3_5_moe","text_config":{"hidden_size":128,"moe_intermediate_size":64}}',
        encoding="utf-8",
    )
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "layers-0.safetensors").write_bytes(b"hf")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "_python_has_module", lambda candidate, module: True)

    compat_dir = skillforgebridge._mlx_compatible_hf_model_dir(profile, model_dir, Path(sys.executable))

    config = json.loads((compat_dir / "config.json").read_text(encoding="utf-8"))
    assert config["model_type"] == "qwen3_5_moe"
    assert config["model_file"] == "skillforge_qwen3_5_moe.py"
    assert (compat_dir / "skillforge_qwen3_5_moe.py").exists()
    assert (compat_dir / "model-00001-of-00001.safetensors").exists()


def test_bridge_mlx_compat_aliases_qwen35_to_qwen3_when_native_loader_missing(tmp_path, monkeypatch):
    import sys

    from bridge import skillforgebridge

    profile = "qwen3.5-4b"
    model_dir = tmp_path / "training_models" / profile / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(
        '{"model_type":"qwen3_5","hidden_size":128,"num_hidden_layers":2}',
        encoding="utf-8",
    )
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "layers-0.safetensors").write_bytes(b"hf")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(
        skillforgebridge,
        "_python_has_module",
        lambda candidate, module: module != "mlx_lm.models.qwen3_5",
    )

    compat_dir = skillforgebridge._mlx_compatible_hf_model_dir(profile, model_dir, Path(sys.executable))

    config = json.loads((compat_dir / "config.json").read_text(encoding="utf-8"))
    assert config["model_type"] == "qwen3"
    assert config["_skillforge_original_model_type"] == "qwen3_5"
    assert (compat_dir / "model-00001-of-00001.safetensors").exists()


def test_bridge_training_inference_mlx_converts_hf_sharded_model(tmp_path, monkeypatch):
    import subprocess

    from bridge import skillforgebridge

    profile = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    model_dir = tmp_path / "training_models" / profile / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(
        '{"model_type":"qwen3_5_moe","text_config":{"hidden_size":128,"moe_intermediate_size":64}}',
        encoding="utf-8",
    )
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    (model_dir / "layers-0.safetensors").write_bytes(b"hf")

    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": profile,
        "runtime_profile": "mlx-qwen3.6-35b-a3b-base",
        "base_model_only": True,
        "prompt": "ping",
        "max_new_tokens": 8,
    })
    assert error is None

    calls = {"convert": 0, "runner": 0}

    def fake_has_module(python_path, module_name):
        return module_name != "mlx_lm.models.qwen3_5_moe"

    def fake_run(cmd, *, capture_output, text, timeout, env=None):
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "mlx_lm.convert":
            calls["convert"] += 1
            hf_dir = Path(cmd[cmd.index("--hf-path") + 1])
            calls["hf_dir"] = str(hf_dir)
            assert (hf_dir / "model-00001-of-00001.safetensors").exists()
            compat_config = json.loads((hf_dir / "config.json").read_text(encoding="utf-8"))
            assert compat_config["model_type"] == "qwen3_moe"
            assert compat_config["hidden_size"] == 128
            assert compat_config["intermediate_size"] == 64
            assert compat_config["decoder_sparse_step"] == 1
            assert compat_config["mlp_only_layers"] == []
            assert compat_config["rope_theta"] == 1000000.0
            assert compat_config["norm_topk_prob"] is False
            out_dir = Path(cmd[cmd.index("--mlx-path") + 1])
            out_dir.mkdir(parents=True)
            (out_dir / "config.json").write_text("{}", encoding="utf-8")
            (out_dir / "weights.safetensors").write_bytes(b"mlx")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if env is not None:
            calls["runner"] += 1
            calls["model_dir"] = env["SKILLFORGE_MODEL_DIR"]
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='SKILLFORGE_INFERENCE_RESULT={"ok":true,"status":"completed","text":"pong","metrics":{}}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "OPENWEBUI_MLX_QWEN35_MOE_PACKAGE", "")
    monkeypatch.setattr(skillforgebridge, "_python_has_module", fake_has_module)
    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)

    result = skillforgebridge._run_local_training_inference(normalized)

    assert result["text"] == "pong"
    assert calls["convert"] == 1
    assert calls["runner"] == 1
    assert Path(calls["hf_dir"]).name == "mlx_hf_compat"
    assert Path(calls["model_dir"]).name == "mlx_model"


def test_bridge_training_inference_timeout_reports_runner_timeout(tmp_path, monkeypatch):
    import subprocess
    import sys

    from bridge import skillforgebridge

    profile = "qwen3.5-4b"
    model_dir = tmp_path / "training_models" / profile / "model"
    model_dir.mkdir(parents=True)

    normalized, error = skillforgebridge._normalize_training_inference_payload({
        "profile": profile,
        "runtime_profile": "cuda-qwen3.5-4b-lora",
        "base_model_only": True,
        "prompt": "ping",
        "max_new_tokens": 8,
        "timeout_seconds": 31,
    })
    assert error is None

    def fake_run(cmd, *, capture_output, text, timeout, env=None):
        raise subprocess.TimeoutExpired(cmd, timeout, output="loading", stderr="still loading")

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "_training_bootstrap_python_path", lambda profile: Path(sys.executable))
    monkeypatch.setattr(skillforgebridge, "_training_model_profile_dir", lambda profile: tmp_path / "training_models" / profile)
    monkeypatch.setattr(skillforgebridge.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        skillforgebridge._run_local_training_inference(normalized)

    message = str(exc_info.value)
    assert "local inference runner timed out after 31s" in message
    assert "stdout_tail:" in message
    assert "stderr_tail:" in message


@pytest.mark.asyncio
async def test_bridge_training_artifact_status_reports_missing_local_file(tmp_path, monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(skillforgebridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-1")
    missing = tmp_path / "training_runs" / "train_missing" / "output" / "adapter.tar.gz"
    skillforgebridge._write_training_job_record("train_missing", {
        "job_id": "train_missing",
        "status": "completed",
        "artifacts": [{
            "id": "train_missing:adapter",
            "type": "lora_adapter",
            "name": "adapter.tar.gz",
            "uri": str(missing),
            "sha256": "a" * 64,
        }],
    })

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "training.artifact_status",
        {
            "job_id": "train_missing",
            "artifact_id": "101:0",
            "artifact_name": "adapter.tar.gz",
            "sha256": "a" * 64,
        },
    )

    assert ok is True
    assert error is None
    assert result["exists"] is False
    assert "not found" in result["reason"]
