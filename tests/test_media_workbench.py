import asyncio
import inspect
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.common.exceptions import AppError
from app.media.speech_delivery import SpeechDeliveryError
from app.media.models import MediaGenerationJob
from app.media.service import (
    MEDIA_CAPABILITIES,
    MEDIA_DEFAULT_PRESET,
    MEDIA_QUALITY_ANALYSIS_POLICY_VERSION,
    MEDIA_QUALITY_DIMENSION_KEYS,
    MEDIA_TRAINING_QUALITY_MINIMUMS,
    _media_training_quality_gate,
    _media_quality_focus_crop,
    _media_quality_provider_label,
    _normalize_media_quality_evaluation,
    _deployment_model_version,
    _is_bound_media_deployment,
    _media_job_priority,
    _generated_media_technical_validation,
    _apply_ai_media_quality_guardrails,
    _apply_audio_delivery_gate,
    _build_media_repair_plan,
    _merge_collected_media_result,
    _dialogue_delivery_requested,
    _dialogue_delivery_auto_requested,
    _dialogue_visual_policy_gate_state,
    _mark_dialogue_delivery_awaiting_visual_gate,
    _record_dialogue_delivery_failure,
    _normalize_ai_media_quality_result,
    _normalize_people_presence_verifier,
    _merge_people_presence_verifier,
    _queue_media_quality_analysis,
    _review_rework_seed,
    _parse_plan_output,
    _score_media_instance,
    _validated_user_compiled_prompt,
    normalize_media_params,
    transition_media_job,
    process_dialogue_deliveries_once,
)
from app.media.workbench_v2 import _fit_script_excerpt, _normalized_library_metadata, _script_timing_for_clip
from app.media.ad_material_parser import (
    AD_MATERIAL_POLICY_VERSION,
    apply_ad_material_contract_to_plan,
    parse_ad_material_brief,
)
from app.media.h3_prompt_policy import (
    H3_PROMPT_MAX_CHARS,
    H3_PROMPT_POLICY_VERSION,
    brief_excludes_brand_fidelity,
    brief_excludes_people,
    brief_requires_brand_reference,
    compile_h3_plan,
    production_variant_for_candidate,
)
from app.media.production_policy import classify_production_intent, production_policy_for, quality_signal_for_intent


def _job(status="draft"):
    return MediaGenerationJob(
        id="mvj-test",
        project_id="samplebrand-material-workbench",
        project_run_id="pr-test",
        idempotency_key="idem-test",
        mode="text_to_video",
        status=status,
        prompt_json={},
        params_json=dict(MEDIA_DEFAULT_PRESET),
        reference_assets_json=[],
        workflow_template_id="h3_t2v_v1",
    )


def test_dialogue_delivery_failure_preserves_clean_plate_and_never_claims_completion():
    job = _job("awaiting_review")
    job.result_asset_id = "pra-clean-plate"
    job.prompt_json = {
        "requested_audio_prompt": "女：今晚早点回来。",
        "dialogue_delivery": {"requested": True, "visual_audio_enabled": False},
    }
    assert _dialogue_delivery_requested(job) is True
    _record_dialogue_delivery_failure(job, SpeechDeliveryError("SPEECH_PROVIDER_FAILED", "provider unavailable"))
    assert job.result_asset_id == "pra-clean-plate"
    assert job.result_json["dialogue_delivery"]["status"] == "failed"
    assert job.result_json["dialogue_delivery"]["renderer"]
    assert not job.result_json["dialogue_delivery"].get("artifact_id")


def test_dialogue_delivery_waits_for_visual_gate_without_replacing_clean_plate():
    job = _job("collecting")
    job.result_asset_id = "pra-clean-plate"
    job.prompt_json = {
        "requested_audio_prompt": "女：开了吗？\n男：开了。",
        "dialogue_delivery": {"requested": True, "visual_audio_enabled": False},
    }
    clean_plate = {
        "id": "pra-clean-plate",
        "sha256": "a" * 64,
        "mime_type": "video/mp4",
    }

    _mark_dialogue_delivery_awaiting_visual_gate(job, clean_plate)

    assert job.result_asset_id == "pra-clean-plate"
    assert job.result_json["clean_plate_asset"] == clean_plate
    delivery = job.result_json["dialogue_delivery"]
    assert delivery["status"] == "awaiting_visual_gate"
    assert delivery["renderer"]
    assert delivery["policy_version"]
    assert not delivery.get("artifact_id")
    assert not delivery.get("audio_asset_id")
    assert not delivery.get("final_asset_id")


def test_media_collection_never_auto_spends_dialogue_renderer_before_visual_gate():
    from app.media import service

    source = inspect.getsource(service._refresh_job)
    assert "_mark_dialogue_delivery_awaiting_visual_gate(job, primary_asset)" in source
    assert "_finalize_governed_dialogue(" not in source


@pytest.mark.asyncio
async def test_dialogue_worker_auto_delivers_only_after_visual_gate(monkeypatch):
    from app.media import service

    job = _job("awaiting_review")
    job.requested_by = "usr-operator"
    job.prompt_json = {
        "requested_audio_prompt": "女：今晚早点回来。",
        "h3_execution_profile": "clean_dialogue_plate_silent_v1",
        "dialogue_delivery": {
            "requested": True,
            "delivery_mode": "auto_after_visual_gate",
            "auto_finalize": True,
            "visual_audio_enabled": False,
        },
    }
    clean_plate = SimpleNamespace(id="pra-clean", project_id=job.project_id)
    job.result_json = {
        "asset": {"id": "pra-clean", "sha256": "a" * 64},
        "assets": [{"id": "pra-clean", "sha256": "a" * 64}],
        "clean_plate_asset": {"id": "pra-clean", "sha256": "a" * 64},
        "dialogue_delivery": {"status": "awaiting_visual_gate"},
        "quality_analysis": {
            "status": "completed",
            "assets": [{
                "status": "completed",
                "analysis": {"visual_policy_gate": {"passed": True, "findings": []}},
            }],
        },
    }

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [job]

    class FakeDb:
        commits = 0

        async def execute(self, _query):
            return Result()

        async def get(self, model, key):
            if model.__name__ == "ProjectRun":
                return SimpleNamespace(id=job.project_run_id, project_id=job.project_id)
            if model.__name__ == "User":
                return SimpleNamespace(id=job.requested_by)
            if model.__name__ == "ProjectRunAsset" and key == "pra-clean":
                return clean_plate
            return None

        async def commit(self):
            self.commits += 1

    async def fake_finalize(_db, _user, _run, target, _asset):
        final_asset = {"id": "pra-final", "sha256": "b" * 64}
        target.result_asset_id = "pra-final"
        target.result_json = {
            **target.result_json,
            "asset": final_asset,
            "assets": [final_asset],
            "dialogue_delivery": {
                **target.result_json["dialogue_delivery"],
                "status": "completed",
                "artifact_id": "pra-final",
            },
        }
        return target.result_json["dialogue_delivery"]

    monkeypatch.setattr(service, "_finalize_governed_dialogue", fake_finalize)
    db = FakeDb()

    assert _dialogue_delivery_auto_requested(job) is True
    assert _dialogue_visual_policy_gate_state(job)["passed"] is True
    stats = await process_dialogue_deliveries_once(db)

    assert stats == {"scanned": 1, "completed": 1, "failed": 0, "waiting": 0, "skipped": 0}
    assert job.result_asset_id == "pra-final"
    assert job.result_json["quality_analysis"]["status"] == "queued"
    assert db.commits == 2


@pytest.mark.asyncio
async def test_dialogue_worker_stops_stale_auto_render_without_repeat_spend():
    job = _job("awaiting_review")
    job.prompt_json = {
        "requested_audio_prompt": "女：今晚早点回来。",
        "dialogue_delivery": {
            "requested": True,
            "delivery_mode": "auto_after_visual_gate",
            "auto_finalize": True,
        },
    }
    job.result_json = {
        "dialogue_delivery": {
            "status": "auto_rendering",
            "auto_started_at": "2020-01-01T00:00:00+08:00",
        },
    }

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [job]

    class FakeDb:
        commits = 0

        async def execute(self, _query):
            return Result()

        async def get(self, *_args):
            raise AssertionError("stale renderer must stop before any provider lineage lookup")

        async def commit(self):
            self.commits += 1

    db = FakeDb()
    stats = await process_dialogue_deliveries_once(db)

    assert stats == {"scanned": 1, "completed": 0, "failed": 1, "waiting": 0, "skipped": 0}
    assert job.result_json["dialogue_delivery"]["status"] == "failed"
    assert job.result_json["dialogue_delivery"]["error_code"] == "SPEECH_DELIVERY_INTERRUPTED"
    assert db.commits == 1


def _workbench_web_source() -> str:
    web = Path(__file__).parents[1] / "demo-projects" / "material-workbench" / "web"
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(web.rglob("*")) if path.is_file())


def test_strict_product_replacement_uses_product_refs_without_inventing_actors():
    plan = compile_h3_plan(
        {
            "creative_goal": "错误的男女演员描述",
            "audience": "成年人",
            "h3_mode": "reference_replay",
            "duration_seconds": 5,
            "ratio": "9:16",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "近景",
                "subject": "商品散片与局部手部",
                "action": "严格保持源视频动作，只替换商品槽位",
                "scene": "桌面",
                "lighting": "暖光",
                "mood": "真实",
                "camera_command": "[Static shot]",
                "audio": "",
            }],
            "audio_prompt": "",
            "negative_constraints": [],
            "brand_guardrails": {},
            "assumptions": [],
            "warnings": [],
            "recommended_params": {
                "width": 480, "height": 864, "frames": 124, "fps": 24, "steps": 20,
                "seed": -1, "batch_count": 1, "audio_enabled": False,
            },
            "reference_roles": [
                {"role": "reference_video", "business_role": "motion_reference", "purpose": "源视频结构"},
                {"role": "reference_image", "business_role": "product_packshot", "purpose": "目标盒装商品"},
                {"role": "reference_image", "business_role": "product_detail", "purpose": "目标商品单片"},
            ],
        },
        brief={
            "request": "去掉源视频文字，一比一复刻，只换产品",
            "strip_reference_text": True,
            "reference_identity_policy": "replace_actor",
            "source_roles": [
                {"role": "motion_reference"},
                {"role": "product_packshot"},
                {"role": "product_detail"},
            ],
            "source_replication_contract": {
                "enabled": True,
                "mode": "structure_replay_product_replace",
                "source_people_presence": "partial_hands",
                "product_assets": [{"asset_id": "box"}, {"asset_id": "unit"}],
            },
        },
    )
    prompt = plan["integrated_multimodal_description"]
    assert plan["h3_execution_profile"] == "structure_replay_product_replace_no_source_text_v1"
    assert "approved target product package reference" in prompt
    assert "approved target loose-unit" in prompt
    assert "PARTIAL-HAND LOCK" in prompt
    assert "anonymous adult actors" not in prompt
    assert "EMPTY-HANDS AND NO-PROPS" not in prompt
    assert "only permitted printed pixels" in prompt
    assert plan["brand_guardrails"]["reference_identity_policy"] == "not_applicable"
    assert not any("换成新演员" in item for item in plan["warnings"])
    assert any("商品换品复刻去源文字" in item for item in plan["warnings"])


def test_short_clip_script_fit_keeps_first_speaker_turn_semantically_complete():
    first_turn = "女：你是说避孕套放冰箱冰一晚上会更好用，对吗？"
    script = f"{first_turn}\n男：对啊。\n女：你是说戴会影响体验原来是谎言，对吗？"

    assert _fit_script_excerpt(script, 5) == first_turn
    timing = _script_timing_for_clip(script, 5)
    assert timing["fits"] is False
    assert timing["shot_script"] == first_turn
    assert not timing["shot_script"].endswith("会更")


def test_user_compiled_prompt_override_preserves_validated_reference_contract():
    prompt = (
        "integrated_multimodal_description:\n"
        "<Picture 1> provides the locked first frame.\n"
        "[Shot 1] At 0.000-5.000s: [Static shot]; only subtle facial micro-motion."
    )
    assert _validated_user_compiled_prompt(prompt, [{"role": "first_frame"}]) == prompt


def test_user_compiled_prompt_override_rejects_missing_or_unexpected_references():
    with pytest.raises(AppError) as missing:
        _validated_user_compiled_prompt(
            "integrated_multimodal_description:\n[Shot 1] At 0.000-5.000s: [Static shot].",
            [{"role": "first_frame"}],
        )
    assert missing.value.code == "MEDIA_PROMPT_OVERRIDE_INVALID"
    assert missing.value.detail["missing"] == ["<Picture 1>"]

    with pytest.raises(AppError) as unexpected:
        _validated_user_compiled_prompt(
            "integrated_multimodal_description:\n<Picture 2> is not a validated input.",
            [{"role": "first_frame"}],
        )
    assert unexpected.value.detail["missing"] == ["<Picture 1>"]
    assert unexpected.value.detail["unexpected"] == ["<Picture 2>"]


def test_reference_replay_no_text_and_identity_controls_compile_to_hard_contracts():
    compiled = compile_h3_plan(
        {
            "creative_goal": "成年男女对话投流钩子",
            "h3_mode": "reference_replay",
            "duration_seconds": 5,
            "shots": [{
                "start_seconds": 0, "end_seconds": 5, "shot_size": "medium",
                "subject": "成年男女", "action": "自然对话", "scene": "客厅",
                "lighting": "自然光", "mood": "轻松", "camera": "[Static shot]", "sound": "环境声",
            }],
        },
        brief={
            "strip_reference_text": True,
            "reference_identity_policy": "replace_actor",
            "source_roles": [{"role": "motion_reference", "technical_role": "reference_video"}],
        },
    )
    prompt = compiled["integrated_multimodal_description"]
    assert compiled["brand_guardrails"]["reference_text_policy"] == "suppress_all_source_text"
    assert compiled["brand_guardrails"]["reference_identity_policy"] == "replace_actor"
    assert "REFERENCE VIDEO MOTION-ONLY LOCK" in prompt
    assert "REFERENCE IDENTITY REPLACEMENT LOCK" in prompt
    assert "Creative objective" not in prompt
    assert "成年男女对话投流钩子" not in prompt
    assert all(ord(character) < 128 for character in prompt)
    assert compiled["recommended_params"]["audio_enabled"] is False
    assert compiled["h3_execution_profile"] == "motion_only_no_text_ascii_v3"
    assert "EMPTY-HANDS AND NO-PROPS LOCK" in prompt
    assert "bottle" in prompt
    assert "replace that action with an empty-hand conversational gesture" in prompt
    assert any("任何一帧都不得出现字幕" in item for item in compiled["negative_constraints"])


def test_image_reference_dialogue_uses_silent_plate_and_governed_voiceover():
    compiled = compile_h3_plan(
        {
            "creative_goal": "成年男女自然对话",
            "h3_mode": "reference_replay",
            "duration_seconds": 5,
            "shots": [{
                "start_seconds": 0, "end_seconds": 5, "framing": "medium shot",
                "subject": "成年男女", "action": "自然对话", "scene": "客厅",
                "camera_command": "[Static shot]", "audio": "女：来抱一下。男：等一下。",
            }],
            "recommended_params": {"audio_enabled": True},
            "performance_timeline": [{
                "start_seconds": 0, "end_seconds": 2.5, "speaker": "女", "line": "来抱一下。",
                "speaker_action": "先吸气再轻微靠近", "listener_reaction": "看向说话人并自然眨眼",
                "eye_line": "看向对方",
            }],
        },
        brief={
            "strip_reference_text": True,
            "audio_enabled": True,
            "script": "女：来抱一下。\n男：等一下。",
            "source_roles": [{"role": "product_packshot", "technical_role": "reference_image"}],
        },
    )
    prompt = compiled["integrated_multimodal_description"]
    assert "reference_text_policy" not in compiled["brand_guardrails"]
    assert compiled["recommended_params"]["audio_enabled"] is False
    assert compiled["h3_execution_profile"] == "clean_dialogue_plate_silent_v1"
    assert compiled["requested_audio_prompt"].startswith("女：来抱一下")
    assert compiled["dialogue_delivery"]["status"] == "voiceover_renderer_required"
    assert compiled["dialogue_delivery"]["delivery_mode"] == "auto_after_visual_gate"
    assert compiled["dialogue_delivery"]["auto_finalize"] is True
    assert compiled["dialogue_delivery"]["visual_strategy"] == "silent_reaction_plate_v2"
    assert compiled["dialogue_delivery"]["native_lip_sync"] is False
    assert "PERFORMANCE TIMELINE" in prompt
    assert "OPENING HOOK" in prompt
    assert "first 1.5 seconds" in prompt
    assert "supported torso changes by 2-3 percent of frame height" in prompt
    assert "upper-body lean" in prompt or "upper torso forward" in prompt
    assert "waist-up medium two-shot" in prompt
    assert "BODY SUPPORT" in prompt
    assert "partner keeps both hands supported below the lower canvas boundary" in prompt
    assert "active adult may lift exactly one supported forearm into the lower third" in prompt
    assert "both complete shoulder lines" in prompt
    assert "GAZE -" in prompt
    assert "iris direction and shoulder angles point into the shared interaction space" in prompt
    assert "STAGGERED REACTION -" in prompt
    assert "reacts about 0.3 seconds later" in prompt
    assert "RESTING FACE -" in prompt
    assert "naturally closed relaxed lips" in prompt
    for unsafe_speech_cue in ("speaking", "spoken", "question", "dialogue", "conversation", "listens"):
        assert unsafe_speech_cue not in prompt.casefold()
    assert "来抱一下" not in prompt
    assert prompt.isascii()


def test_fine_tuned_model_version_is_bound_to_real_artifact():
    deployment = SimpleNamespace(
        id="deploy-1",
        model_family="material-planner",
        artifact_id="artifact-1",
        artifact_ref_json={"sha256": "ABCDEF1234567890" * 4},
    )
    assert _deployment_model_version(deployment) == "material-planner@abcdef123456"

    deployment.artifact_ref_json["model_version"] = "material-planner-lora-2026.08.11-v1"
    assert _deployment_model_version(deployment) == "material-planner-lora-2026.08.11-v1"


def test_collected_media_result_preserves_dispatch_prompt_upgrade_lineage():
    merged = _merge_collected_media_result(
        {
            "eta": {"seconds": 145},
            "prompt_upgrade": {
                "status": "completed",
                "from": "minimax-h3-context-ir-v1",
                "to": H3_PROMPT_POLICY_VERSION,
                "variant_key": "c6-p5-t3-m2",
            },
        },
        {
            "sha256": "a" * 64,
            "model_version": "MiniMax-H3-fl2va-int8-convrot",
            "media_probe": {"status": "ok", "duration_seconds": 5.167},
        },
        primary_asset={"id": "pra-1"},
        assets=[{"id": "pra-1"}],
        result_metas=[{"sha256": "a" * 64}],
    )

    assert merged["prompt_upgrade"]["variant_key"] == "c6-p5-t3-m2"
    assert merged["prompt_upgrade"]["to"] == H3_PROMPT_POLICY_VERSION
    assert merged["asset"]["id"] == "pra-1"
    assert merged["sha256"] == "a" * 64
    assert merged["media_probe"]["duration_seconds"] == 5.167


def test_generated_media_technical_validation_checks_streams_dimensions_duration_and_audio():
    params = {"width": 480, "height": 864, "frames": 124, "fps": 24, "audio_enabled": True}
    passed = _generated_media_technical_validation(
        {
            "status": "ok",
            "duration_seconds": 5.167,
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 480, "height": 864},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        },
        params,
    )
    assert passed == {
        "status": "passed",
        "issues": [],
        "duration_seconds": 5.167,
        "video_codec": "h264",
        "audio_codec": "aac",
    }

    failed = _generated_media_technical_validation(
        {
            "status": "ok",
            "duration_seconds": 3,
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280}],
        },
        params,
    )
    assert failed["status"] == "failed"
    assert any("分辨率不匹配" in issue for issue in failed["issues"])
    assert any("时长不匹配" in issue for issue in failed["issues"])
    assert any("缺少音频流" in issue for issue in failed["issues"])

    unverified = _generated_media_technical_validation(
        {"status": "unavailable", "detail": "ffprobe missing"},
        params,
    )
    assert unverified == {"status": "unverified", "issues": ["ffprobe missing"]}


def test_candidate_deployment_requires_project_target_gateway_and_sha256():
    deployment = SimpleNamespace(
        status="active",
        target_skill_ids_json=["project:samplebrand-material-workbench"],
        artifact_id="artifact-1",
        artifact_ref_json={"sha256": "a" * 64},
        deployment_target_gateway_id="data-primary",
    )
    assert _is_bound_media_deployment(deployment, "samplebrand-material-workbench") is True

    deployment.target_skill_ids_json = ["project:some-other-project"]
    assert _is_bound_media_deployment(deployment, "samplebrand-material-workbench") is False
    deployment.target_skill_ids_json = ["project:samplebrand-material-workbench"]
    deployment.artifact_ref_json = {"model_version": "unbound-placeholder"}
    assert _is_bound_media_deployment(deployment, "samplebrand-material-workbench") is False


def test_workbench_never_invents_a_fine_tuned_deployment():
    page = _workbench_web_source()
    backend = (Path(__file__).parents[1] / "app" / "media" / "service.py").read_text(encoding="utf-8")

    assert "deepseek-v4-flash" in page
    assert "候选微调模型" not in page
    assert '"deployed_model"' in backend
    assert '"artifact_id": latest_deployment.artifact_id' in backend


def test_media_job_priority_routes_pro_specialized_work_before_standard_jobs():
    standard = _media_job_priority(
        department_id="931765248",
        mode="text_to_video",
        params={"frames": 124, "batch_count": 1},
    )
    long_video = _media_job_priority(
        department_id="931765248",
        mode="text_to_video",
        params={"frames": 192, "batch_count": 1},
    )
    batch = _media_job_priority(
        department_id="931765248",
        mode="image_to_video",
        params={"frames": 124, "batch_count": 2},
    )
    reference = _media_job_priority(
        department_id="931765248",
        mode="reference_replay",
        params={"frames": 124, "batch_count": 1},
    )

    assert standard == 200
    assert long_video == batch == 250
    assert reference == 300


def test_media_h3_default_and_eight_second_params_are_legal():
    default = normalize_media_params({})
    benchmark = normalize_media_params({"frames": 192})

    assert default == MEDIA_DEFAULT_PRESET
    assert default["frames"] == 124
    assert benchmark["frames"] == 192
    assert benchmark["fps"] == 24


def test_media_quality_contract_keeps_every_workbench_dimension():
    page = _workbench_web_source()

    assert set(MEDIA_QUALITY_DIMENSION_KEYS) == {
        "prompt_alignment",
        "visual_continuity",
        "hook_strength",
        "shot_boundary_clarity",
        "batch_diversity",
        "commercial_readiness",
        "product_fidelity",
        "selling_point_coverage",
        "silhouette_safety",
    }
    for key in MEDIA_QUALITY_DIMENSION_KEYS:
        assert key in page

    normalized = _normalize_media_quality_evaluation(
        {
            "prompt_alignment": 5,
            "visual_continuity": 4,
            "hook_strength": 3,
            "shot_boundary_clarity": 2,
            "batch_diversity": 1,
            "commercial_readiness": 4,
            "product_fidelity": 5,
            "selling_point_coverage": 3,
            "silhouette_safety": 5,
            "notes": "保留完整九维评价",
        }
    )
    assert {key: normalized[key] for key in MEDIA_QUALITY_DIMENSION_KEYS} == {
        "prompt_alignment": 5,
        "visual_continuity": 4,
        "hook_strength": 3,
        "shot_boundary_clarity": 2,
        "batch_diversity": 1,
        "commercial_readiness": 4,
        "product_fidelity": 5,
        "selling_point_coverage": 3,
        "silhouette_safety": 5,
    }
    assert normalized["overall_score"] == pytest.approx(32 / 45, abs=0.001)
    assert normalized["source"] == "manual_workbench"


def test_ai_media_quality_analysis_is_advisory_complete_and_idempotent():
    normalized = _normalize_ai_media_quality_result(
        {
            "summary": "首屏有抽象构图，但商品与卖点不可辨识。",
            "scores": {key: index % 5 + 1 for index, key in enumerate(MEDIA_QUALITY_DIMENSION_KEYS)},
            "timeline": [{"time": "0-2s", "observation": "左侧出现矩形模块", "evidence": "右侧留白不足"}],
            "strengths": ["画面稳定"],
            "issues": ["没有真实商品露出"],
            "evidence": ["包装文字不可见"],
            "recommendation": {
                "decision": "retry_recommended",
                "reason": "商品辨识不足",
                "prompt_changes": ["使用已授权产品首帧"],
            },
            "confidence": 0.8,
        }
    )
    assert set(normalized["scores"]) == set(MEDIA_QUALITY_DIMENSION_KEYS)
    assert normalized["source"] == "ai_advisory_only"
    assert normalized["requires_human_review"] is True
    assert 0 < normalized["overall_score"] <= 1

    structured = _normalize_ai_media_quality_result(
        {
            "summary": "发现动作偏离。",
            "scores": {key: 3 for key in MEDIA_QUALITY_DIMENSION_KEYS},
            "issues": [{
                "issue": "首帧动作偏离",
                "evidence": "关键帧1中手部触碰另一人上臂",
                "impact": "降低提示词一致性",
            }],
        }
    )
    assert structured["issues"] == [
        "首帧动作偏离；证据：关键帧1中手部触碰另一人上臂；影响：降低提示词一致性"
    ]

    result = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    queued, changed = _queue_media_quality_analysis(
        result,
        requested_by="user-1",
        automatic=True,
    )
    assert changed is True
    assert queued["quality_analysis"]["status"] == "queued"
    assert queued["quality_analysis"]["policy_version"] == MEDIA_QUALITY_ANALYSIS_POLICY_VERSION
    assert queued["quality_analysis"]["automatic"] is True

    duplicate, changed = _queue_media_quality_analysis(
        queued,
        requested_by="user-1",
        automatic=True,
    )
    assert changed is False
    assert duplicate == queued

    stale = {
        **queued,
        "quality_analysis": {
            **queued["quality_analysis"],
            "status": "completed",
            "policy_version": "material-video-quality-v1",
        },
    }
    upgraded, changed = _queue_media_quality_analysis(
        stale,
        requested_by="user-1",
        automatic=False,
    )
    assert changed is True
    assert upgraded["quality_analysis"]["status"] == "queued"
    assert upgraded["quality_analysis"]["policy_version"] == MEDIA_QUALITY_ANALYSIS_POLICY_VERSION


def test_media_quality_provider_label_is_safe_and_identifies_siliconflow():
    assert _media_quality_provider_label({
        "ai.provider": "custom",
        "ai.api_base": "https://api.siliconflow.cn/v1",
        "ai.api_key": "must-not-leak",
    }) == "SiliconFlow"
    assert _media_quality_provider_label({
        "ai.provider": "custom",
        "ai.api_base": "https://vision.example.invalid/v1",
        "ai.api_key": "must-not-leak",
    }) == "OpenAI-compatible custom endpoint"


@pytest.mark.asyncio
async def test_media_quality_accepts_project_durable_asset_collected_from_newer_run(monkeypatch):
    from app.media import service

    job = _job("awaiting_review")
    job.result_json = {"assets": [{"id": "asset-cross-run", "sha256": "a" * 64}]}
    asset = SimpleNamespace(
        id="asset-cross-run",
        project_id=job.project_id,
        project_run_id="pr-newer-viewer-run",
        mime_type="video/mp4",
        metadata_json={"source": "minimax_h3", "media_job_id": job.id},
        sha256="a" * 64,
    )

    class FakeDb:
        async def get(self, model, row_id):
            return asset if row_id == asset.id else None

    async def analyze(_db, _user, _run, _job, analyzed_asset):
        assert analyzed_asset is asset
        return "vision-test", {"overall_score": 0.8, "runtime": {"provider": "SiliconFlow"}}

    monkeypatch.setattr(service, "_call_media_quality_model", analyze)
    result = await service._run_media_quality_analysis(
        FakeDb(), SimpleNamespace(id="user"), SimpleNamespace(id=job.project_run_id), job
    )

    assert result["status"] == "completed"
    assert result["model"] == "vision-test"
    assert result["provider"] == "SiliconFlow"
    assert result["model_origin"] == "platform_vision_profile"
    assert result["project_fine_tuned"] is False
    assert result["deployment_id"] is None
    assert result["artifact_id"] is None


@pytest.mark.asyncio
async def test_media_quality_accepts_governed_dialogue_mux_with_verified_h3_lineage(monkeypatch):
    from app.media import service

    job = _job("awaiting_review")
    job.result_json = {"assets": [{"id": "asset-final-mux", "sha256": "b" * 64}]}
    clean_plate = SimpleNamespace(
        id="asset-clean-plate",
        project_id=job.project_id,
        mime_type="video/mp4",
        metadata_json={"source": "minimax_h3", "media_job_id": job.id},
        sha256="a" * 64,
    )
    final_mux = SimpleNamespace(
        id="asset-final-mux",
        project_id=job.project_id,
        mime_type="video/mp4",
        metadata_json={
            "source": "governed_dialogue_mux",
            "media_job_id": job.id,
            "clean_plate_asset_id": clean_plate.id,
        },
        sha256="b" * 64,
    )

    class FakeDb:
        async def get(self, model, row_id):
            return {final_mux.id: final_mux, clean_plate.id: clean_plate}.get(row_id)

    async def analyze(_db, _user, _run, _job, analyzed_asset):
        assert analyzed_asset is final_mux
        return "vision-test", {"overall_score": 0.8, "runtime": {"provider": "SiliconFlow"}}

    monkeypatch.setattr(service, "_call_media_quality_model", analyze)
    result = await service._run_media_quality_analysis(
        FakeDb(), SimpleNamespace(id="user"), SimpleNamespace(id=job.project_run_id), job
    )

    assert result["status"] == "completed"
    assert result["model"] == "vision-test"


@pytest.mark.asyncio
async def test_media_quality_rejects_governed_dialogue_mux_without_verified_h3_lineage(monkeypatch):
    from app.media import service

    job = _job("awaiting_review")
    job.result_json = {"assets": [{"id": "asset-final-mux", "sha256": "b" * 64}]}
    final_mux = SimpleNamespace(
        id="asset-final-mux",
        project_id=job.project_id,
        mime_type="video/mp4",
        metadata_json={
            "source": "governed_dialogue_mux",
            "media_job_id": job.id,
            "clean_plate_asset_id": "missing-clean-plate",
        },
        sha256="b" * 64,
    )

    class FakeDb:
        async def get(self, model, row_id):
            return final_mux if row_id == final_mux.id else None

    async def should_not_analyze(*_args):
        raise AssertionError("unverified derived asset reached the vision model")

    monkeypatch.setattr(service, "_call_media_quality_model", should_not_analyze)
    with pytest.raises(RuntimeError, match="资产不属于当前 H3 任务"):
        await service._run_media_quality_analysis(
            FakeDb(), SimpleNamespace(id="user"), SimpleNamespace(id=job.project_run_id), job
        )


def test_media_quality_v4_compares_reference_images_and_declared_focus_crops_before_output_frames():
    from app.media import service

    source = Path(service.__file__).read_text(encoding="utf-8")
    assert MEDIA_QUALITY_ANALYSIS_POLICY_VERSION == "material-video-quality-v27"
    assert '"reference_image_count": len(reference_rows)' in source
    assert '"reference_images_and_ordered_keyframes"' in source
    assert "输入中很小的背景静物若在输出中显著变大" in source
    assert '"focus_region": focus_region or None' in source
    assert "局部放大图" in source
    assert '"frame-final"' in source
    assert '"final_frame_sampled"' in source
    assert "咖啡馆内静止的杯子、碟子等普通场景器具不是凭空生成的违规道具" in source
    assert "仅看到一条稳定竖线不足以报错" in source
    assert "边界两侧具体重复或断裂的对象" in source


def test_media_quality_uses_final_job_duration_when_legacy_mux_asset_lacks_validation():
    from app.media.service import _effective_media_technical_validation

    job = SimpleNamespace(
        result_json={
            "technical_validation": {
                "status": "passed",
                "duration_seconds": 9.418,
                "audio_codec": "aac",
            },
        },
    )
    legacy_mux = SimpleNamespace(
        metadata_json={
            "source": "governed_dialogue_mux",
            "media_job_id": "job-dialogue",
        },
    )

    effective = _effective_media_technical_validation(job, legacy_mux)

    assert effective["duration_seconds"] == 9.418
    assert effective["audio_codec"] == "aac"


def test_media_quality_prefers_asset_validation_over_job_fallback():
    from app.media.service import _effective_media_technical_validation

    job = SimpleNamespace(
        result_json={"technical_validation": {"duration_seconds": 5.0, "status": "passed"}},
    )
    mux = SimpleNamespace(
        metadata_json={
            "technical_validation": {"duration_seconds": 9.418, "status": "passed"},
        },
    )

    effective = _effective_media_technical_validation(job, mux)

    assert effective["duration_seconds"] == 9.418


def test_ai_media_quality_analysis_rejects_incomplete_nine_dimension_scores():
    with pytest.raises(ValueError, match="quality_scores_missing"):
        _normalize_ai_media_quality_result({"scores": {"prompt_alignment": 5}})


def test_ai_media_quality_normalizes_only_evidenced_visible_text_hard_findings():
    normalized = _normalize_ai_media_quality_result({
        "scores": {key: 3 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "hard_gate_findings": [
            {
                "code": "visible_text_detected",
                "confidence": 0.94,
                "frame_indices": [5, "5", 0, "bad"],
                "visible_text": "想呢了",
                "evidence": "关键帧5底部有白色汉字",
            },
            {"code": "invented_prop", "confidence": 1, "frame_indices": [1]},
        ],
    })

    assert normalized["hard_gate_findings"] == [{
        "code": "visible_text_detected",
        "confidence": 0.94,
        "frame_indices": [5],
        "visible_text": "想呢了",
        "contact_regions": "",
        "identity_similarity": "",
        "synchronized_actions": "",
        "evidence": "关键帧5底部有白色汉字",
        "surface_region": "",
        "reference_comparison": "",
        "reference_visible_text": "",
        "reference_image_indices": [],
    }]
    assert normalized["hard_gate_evaluated"] is True


def test_ai_media_quality_normalizes_evidenced_dialogue_contact_hard_finding():
    normalized = _normalize_ai_media_quality_result({
        "scores": {key: 3 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "hard_gate_findings": [{
            "code": "interpersonal_contact_detected",
            "confidence": 0.93,
            "frame_indices": [1, 5],
            "contact_regions": "左侧人物右手接触右侧人物腹部和双手",
            "evidence": "关键帧1手掌贴在腹部，关键帧5双手相接",
        }],
    })

    assert normalized["hard_gate_findings"] == [{
        "code": "interpersonal_contact_detected",
        "confidence": 0.93,
        "frame_indices": [1, 5],
        "visible_text": "",
        "contact_regions": "左侧人物右手接触右侧人物腹部和双手",
        "identity_similarity": "",
        "synchronized_actions": "",
        "evidence": "关键帧1手掌贴在腹部，关键帧5双手相接",
    }]


def test_ai_media_quality_normalizes_evidenced_unexpected_person_hard_finding():
    normalized = _normalize_ai_media_quality_result({
        "scores": {key: 3 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "hard_gate_findings": [{
            "code": "unexpected_person_detected",
            "confidence": 0.97,
            "frame_indices": [1, 2, 3],
            "observed_actor_count": "1",
            "person_regions": "画面中央完整男性头部、上半身和双手",
            "evidence": "关键帧1至3均能看到清晰真人脸和上半身",
        }],
    })

    assert normalized["hard_gate_findings"] == [{
        "code": "unexpected_person_detected",
        "confidence": 0.97,
        "frame_indices": [1, 2, 3],
        "visible_text": "",
        "contact_regions": "",
        "identity_similarity": "",
        "synchronized_actions": "",
        "evidence": "关键帧1至3均能看到清晰真人脸和上半身",
        "observed_actor_count": 1,
        "person_regions": "画面中央完整男性头部、上半身和双手",
    }]


def test_people_presence_verifier_normalizes_obvious_person_evidence():
    normalized = _normalize_people_presence_verifier({
        "person_present": True,
        "confidence": 0.99,
        "frame_indices": [1, "3", 99],
        "observed_actor_count": "1",
        "person_regions": "画面中央清晰男性正脸和上半身",
        "evidence": "关键帧1和3均可见完整真人脸、头部和肩膀",
    }, frame_count=5)

    assert normalized == {
        "status": "completed",
        "person_present": True,
        "confidence": 0.99,
        "frame_indices": [1, 3],
        "observed_actor_count": 1,
        "person_regions": "画面中央清晰男性正脸和上半身",
        "evidence": "关键帧1和3均可见完整真人脸、头部和肩膀",
        "source": "independent_people_presence_verifier",
    }


def test_people_presence_verifier_rejects_low_confidence_clean_claim():
    with pytest.raises(ValueError, match="people_presence_confidence_too_low"):
        _normalize_people_presence_verifier({
            "person_present": False,
            "confidence": 0.72,
            "frame_indices": [],
            "observed_actor_count": 0,
            "person_regions": "",
            "evidence": "画面较模糊，无法确定",
        }, frame_count=5)


def test_people_presence_verifier_injects_hard_gate_independently_of_broad_quality_model():
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": ["人物面部有合成感"],
        "hard_gate_findings": [],
        "hard_gate_evaluated": True,
    }
    verifier = _normalize_people_presence_verifier({
        "person_present": True,
        "confidence": 0.99,
        "frame_indices": [1, 2, 3],
        "observed_actor_count": 1,
        "person_regions": "画面中央正脸和上半身",
        "evidence": "关键帧1至3均可见清晰真人",
    }, frame_count=5)

    merged = _merge_people_presence_verifier(analysis, verifier)

    assert merged["people_presence_verifier"]["status"] == "completed"
    assert merged["hard_gate_findings"] == [{
        "code": "unexpected_person_detected",
        "confidence": 0.99,
        "frame_indices": [1, 2, 3],
        "observed_actor_count": 1,
        "person_regions": "画面中央正脸和上半身",
        "evidence": "关键帧1至3均可见清晰真人",
        "detector_source": "independent_people_presence_verifier",
    }]


def test_media_quality_no_person_tasks_run_independent_people_presence_verifier():
    from app.media import service

    source = inspect.getsource(service._call_media_quality_model)
    assert "people_presence_verifier_required = brief_excludes_people" in source
    assert "await _call_people_presence_verifier(" in source
    assert "_merge_people_presence_verifier(" in source


def test_media_submit_persists_exact_operator_input_in_production_brief():
    from app.media import service

    source = inspect.getsource(service._submit_job)
    production_brief_source = source.split('"production_brief": {', 1)[1].split("},", 1)[0]

    assert '"operator_input"' in production_brief_source


@pytest.mark.parametrize(
    "contract",
    [
        {"people_requested": False, "actor_count": 0},
        {"people_requested": True, "actor_count": 1},
    ],
)
def test_ai_media_quality_unexpected_person_blocks_explicit_no_people_brief(contract):
    job = _job("awaiting_review")
    job.prompt_json = {
        "production_brief": {"request": "生成抽象商品氛围底片，不要人物、人体或身体部位"},
        "ad_material_contract": contract,
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [{
            "code": "unexpected_person_detected",
            "confidence": 0.97,
            "frame_indices": [1, 2, 3],
            "observed_actor_count": 1,
            "person_regions": "画面中央完整男性头部、上半身和双手",
            "evidence": "关键帧1至3均能看到清晰真人脸和上半身",
        }],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["version"] == "material-visual-policy-gate-v2"
    assert guarded["visual_policy_gate"]["passed"] is False
    assert guarded["visual_policy_gate"]["findings"][0]["code"] == "unexpected_person_detected"
    assert guarded["scores"]["prompt_alignment"] == 1
    assert guarded["scores"]["hook_strength"] == 2
    assert guarded["scores"]["commercial_readiness"] == 1
    assert guarded["scores"]["silhouette_safety"] == 2
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "unexpected_person_hard_gate_failed" in guarded["deterministic_guardrails"]


def test_ai_media_quality_does_not_treat_text_as_hard_finding_when_text_is_allowed():
    job = _job("awaiting_review")
    job.prompt_json = {
        "ad_material_contract": {
            "people_requested": True,
            "actor_count": 1,
            "content_format": "talking_head",
        },
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 4 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [{
            "code": "visible_text_detected",
            "confidence": 0.99,
            "frame_indices": [2],
            "visible_text": "允许的字幕",
            "evidence": "关键帧2底部字幕清晰",
        }],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["passed"] is True
    assert guarded["visual_policy_gate"]["findings"] == []
    assert "visible_text_hard_gate_failed" not in guarded["deterministic_guardrails"]


def test_ai_media_quality_dialogue_contact_blocks_visual_gate():
    job = _job("awaiting_review")
    job.prompt_json = {"h3_execution_profile": "clean_dialogue_plate_silent_v1"}
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [{
            "code": "interpersonal_contact_detected",
            "confidence": 0.93,
            "frame_indices": [1, 5],
            "contact_regions": "手掌接触腹部和双手",
            "evidence": "关键帧1和5接触清晰",
        }],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["passed"] is False
    assert guarded["visual_policy_gate"]["findings"][0]["code"] == "interpersonal_contact_detected"
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "interpersonal_contact_hard_gate_failed" in guarded["deterministic_guardrails"]


def test_ai_media_quality_duplicate_actor_and_synchronized_performance_block_dialogue_gate():
    job = _job("awaiting_review")
    job.prompt_json = {"h3_execution_profile": "clean_dialogue_plate_silent_v1"}
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [
            {
                "code": "duplicate_actor_identity_detected",
                "confidence": 0.96,
                "frame_indices": [1, 2, 3, 4, 5],
                "identity_similarity": "same nose, jaw, eye spacing and hair silhouette",
                "evidence": "all sampled frames show mirrored copies of one face",
            },
            {
                "code": "synchronized_performance_detected",
                "confidence": 0.95,
                "frame_indices": [2, 3, 4, 5],
                "synchronized_actions": "both adults smile and open their mouths together",
                "evidence": "the same expression transition occurs on both faces",
            },
        ],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["passed"] is False
    assert {item["code"] for item in guarded["visual_policy_gate"]["findings"]} == {
        "duplicate_actor_identity_detected", "synchronized_performance_detected",
    }
    assert guarded["scores"]["visual_continuity"] == 2
    assert guarded["scores"]["hook_strength"] == 2
    assert guarded["scores"]["commercial_readiness"] == 1
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "duplicate_actor_identity_hard_gate_failed" in guarded["deterministic_guardrails"]
    assert "synchronized_performance_hard_gate_failed" in guarded["deterministic_guardrails"]


def test_ai_media_quality_blocks_actor_count_market_and_synthetic_face_mismatch():
    job = _job("awaiting_review")
    job.prompt_json = {
        "h3_execution_profile": "clean_people_plate_silent_v4",
        "ad_material_contract": {
            "people_requested": True,
            "content_format": "talking_head",
            "actor_count": 1,
            "cast_market": "mainland_china",
            "face_style": "authentic_live_action",
        },
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    raw = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [
            {
                "code": "actor_count_mismatch_detected",
                "confidence": 0.99,
                "frame_indices": [1, 2, 3],
                "expected_actor_count": 1,
                "observed_actor_count": 2,
                "evidence": "three sampled frames show two complete adult faces and bodies",
            },
            {
                "code": "cast_market_mismatch_detected",
                "confidence": 0.93,
                "frame_indices": [1, 2, 3, 4],
                "expected_cast_market": "mainland_china",
                "observed_market_cues": "casting, styling and streetscape consistently read as a non-local European campaign",
                "evidence": "the same non-local visual cues persist across four frames",
            },
            {
                "code": "synthetic_face_style_detected",
                "confidence": 0.94,
                "frame_indices": [1, 2, 3],
                "face_artifacts": "waxy poreless skin, mirrored symmetry and unstable under-eye texture",
                "evidence": "all three frames repeat multiple synthetic face artifacts",
            },
        ],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    normalized = _normalize_ai_media_quality_result(raw)
    guarded = _apply_ai_media_quality_guardrails(normalized, job)

    assert {item["code"] for item in guarded["visual_policy_gate"]["findings"]} == {
        "actor_count_mismatch_detected",
        "cast_market_mismatch_detected",
        "synthetic_face_style_detected",
    }
    assert guarded["visual_policy_gate"]["passed"] is False
    assert guarded["scores"]["prompt_alignment"] == 2
    assert guarded["scores"]["commercial_readiness"] == 1
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "actor_count_hard_gate_failed" in guarded["deterministic_guardrails"]
    assert "cast_market_hard_gate_failed" in guarded["deterministic_guardrails"]
    assert "synthetic_face_style_hard_gate_failed" in guarded["deterministic_guardrails"]


def test_ai_media_quality_visible_text_blocks_clean_plate_visual_gate():
    job = _job("awaiting_review")
    job.prompt_json = {
        "h3_execution_profile": "clean_dialogue_plate_silent_v1",
        "brand_guardrails": {"require_reference_image": False, "reason": "不展示品牌或真实商品"},
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [{
            "code": "visible_text_detected",
            "confidence": 0.94,
            "frame_indices": [5],
            "visible_text": "想呢了",
            "evidence": "关键帧5底部有白色汉字",
        }],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["passed"] is False
    assert guarded["visual_policy_gate"]["findings"][0]["visible_text"] == "想呢了"
    assert guarded["scores"]["prompt_alignment"] == 2
    assert guarded["scores"]["commercial_readiness"] == 1
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "visible_text_hard_gate_failed" in guarded["deterministic_guardrails"]


def test_ai_media_quality_does_not_hard_block_weak_or_unevidenced_text_guess():
    job = _job("awaiting_review")
    job.prompt_json = {"h3_execution_profile": "clean_dialogue_plate_silent_v1"}
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 3 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [{
            "code": "visible_text_detected",
            "confidence": 0.79,
            "frame_indices": [5],
            "visible_text": "疑似",
            "evidence": "疑似纹理",
        }],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["passed"] is True
    assert "visible_text_hard_gate_failed" not in guarded["deterministic_guardrails"]


def test_ai_media_quality_allows_print_that_matches_approved_product_reference():
    job = _job("awaiting_review")
    job.reference_assets_json = [{"asset_id": "pack", "business_role": "product_packshot", "role": "first_frame"}]
    job.prompt_json = {
        "brand_guardrails": {
            "reference_text_policy": "suppress_generated_text_preserve_approved_product_surface",
        },
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    normalized = _normalize_ai_media_quality_result({
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [
            "画面中出现了大量与已审核商品参考图不一致的包装表面印刷文字（如冰酥燃、爽麻情）。",
            "违反了 text_policy 和 brand_guardrails。",
        ],
        "reference_text_inventory": [{
            "reference_image_index": 1,
            "surface_region": "包装正面",
            "visible_text": "samplebrand 超快感 INTENSE 10只装 冰酥燃 爽麻情 示例品牌",
            "confidence": 0.98,
        }],
        "hard_gate_findings": [{
            "code": "visible_text_detected",
            "confidence": 0.99,
            "frame_indices": [1, 2, 3, 4, 5, 6],
            "visible_text": "冰酥燃 爽麻情",
            "surface_region": "包装正面",
            "reference_comparison": "absent_or_different",
            "reference_visible_text": "冰酥燃 爽麻情",
            "reference_image_indices": [1],
            "evidence": "包装表面存在这些字样",
        }],
        "recommendation": {"decision": "human_review", "reason": "待人工", "prompt_changes": []},
    })

    guarded = _apply_ai_media_quality_guardrails(normalized, job)

    assert guarded["visual_policy_gate"]["passed"] is True
    assert guarded["visual_policy_gate"]["findings"] == []
    assert not guarded["issues"]
    assert "approved_product_surface_text_not_a_generated_text_violation" in guarded["deterministic_guardrails"]
    assert "visible_text_hard_gate_failed" not in guarded["deterministic_guardrails"]


def test_ai_media_quality_blocks_new_text_absent_from_approved_product_reference():
    job = _job("awaiting_review")
    job.reference_assets_json = [{"asset_id": "pack", "business_role": "product_packshot", "role": "first_frame"}]
    job.prompt_json = {
        "brand_guardrails": {
            "reference_text_policy": "suppress_generated_text_preserve_approved_product_surface",
        },
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    normalized = _normalize_ai_media_quality_result({
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "reference_text_inventory": [{
            "reference_image_index": 1,
            "surface_region": "包装正面",
            "visible_text": "samplebrand 超快感 INTENSE 10只装 冰酥燃 爽麻情 示例品牌",
            "confidence": 0.98,
        }],
        "hard_gate_findings": [{
            "code": "visible_text_detected",
            "confidence": 0.99,
            "frame_indices": [3, 4],
            "visible_text": "限时99元",
            "surface_region": "包装上方新增角标",
            "reference_comparison": "absent_or_different",
            "reference_visible_text": "冰酥燃 爽麻情",
            "reference_image_indices": [1],
            "evidence": "关键帧3和4出现参考图没有的限时99元角标",
        }],
        "recommendation": {"decision": "human_review", "reason": "待人工", "prompt_changes": []},
    })

    guarded = _apply_ai_media_quality_guardrails(normalized, job)

    assert guarded["visual_policy_gate"]["passed"] is False
    assert guarded["visual_policy_gate"]["findings"][0]["visible_text"] == "限时99元"
    assert "visible_text_hard_gate_failed" in guarded["deterministic_guardrails"]


def test_verified_bridge_overlay_provenance_overrides_small_ocr_false_mismatch():
    job = _job("awaiting_review")
    pack_sha = "b" * 64
    detail_sha = "c" * 64
    job.reference_assets_json = [
        {
            "asset_id": "pack", "business_role": "product_packshot",
            "role": "overlay_image", "sha256": pack_sha,
        },
        {
            "asset_id": "detail", "business_role": "product_detail",
            "role": "overlay_image", "sha256": detail_sha,
        },
    ]
    job.prompt_json = {
        "product_overlay": {"enabled": True, "anchor": "bottom_right", "layout": "packshot_detail_duo_v1"},
        "brand_guardrails": {
            "reference_text_policy": "suppress_generated_text_preserve_approved_product_surface",
        },
        "production_brief": {"script": "真实口播文案", "request": "后半段植入两张真实商品图"},
        "ad_material_contract": {
            "content_format": "talking_head", "people_requested": True, "actor_count": 1,
            "assembly_plan": {"commercial_preference": "clean_people_plate_then_deterministic_product_overlay"},
        },
    }
    job.result_json = {
        "assets": [{"id": "pra-1", "sha256": "a" * 64}],
        "postprocess_provenance": {
            "product_overlay_applied": True,
            "product_overlay_sha256s": [pack_sha, detail_sha],
            "product_overlay_roles": ["product_packshot", "product_detail"],
            "product_overlay_config_sha256": "d" * 64,
        },
    }
    normalized = _normalize_ai_media_quality_result({
        "scores": {key: 3 for key in MEDIA_QUALITY_DIMENSION_KEYS} | {"product_fidelity": 1},
        "issues": ["产品包装文字与参考图严重不符，出现未审核文字。"],
        "reference_text_inventory": [{
            "reference_image_index": 1,
            "surface_region": "包装正面",
            "visible_text": "samplebrand 超快感",
            "confidence": 0.7,
        }],
        "hard_gate_findings": [{
            "code": "visible_text_detected",
            "confidence": 0.99,
            "frame_indices": [5, 10, 15],
            "visible_text": "冰酥燃爽麻情",
            "surface_region": "右下角商品植入区包装正面",
            "reference_comparison": "absent_or_different",
            "reference_visible_text": "samplebrand 超快感",
            "reference_image_indices": [1, 2],
            "evidence": "小尺寸 OCR 认为包装字样不同",
        }],
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    })

    guarded = _apply_ai_media_quality_guardrails(normalized, job)

    assert guarded["product_overlay_provenance"]["verified"] is True
    assert guarded["visual_policy_gate"]["passed"] is True
    assert guarded["issues"] == []
    assert guarded["scores"]["product_fidelity"] == 4
    assert "verified_bridge_overlay_provenance_restores_product_fidelity" in guarded["deterministic_guardrails"]
    assert "visible_text_hard_gate_failed" not in guarded["deterministic_guardrails"]


def test_ai_media_quality_guardrails_cap_unproven_product_and_batch_scores():
    job = _job("awaiting_review")
    job.reference_assets_json = []
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": ["画面疑似出现烟支，存在合规风险。"],
        "recommendation": {"decision": "human_review", "reason": "模型认为可用", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["model_scores"]["product_fidelity"] == 5
    assert guarded["scores"]["product_fidelity"] == 2
    assert guarded["scores"]["selling_point_coverage"] == 2
    assert guarded["scores"]["commercial_readiness"] == 2
    assert guarded["scores"]["batch_diversity"] == 3
    assert guarded["scores"]["silhouette_safety"] == 2
    assert guarded["overall_score"] == pytest.approx(31 / 45, abs=0.001)
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "no_authorized_product_image_caps_product_scores" in guarded["deterministic_guardrails"]
    assert "single_asset_batch_diversity_not_applicable" in guarded["deterministic_guardrails"]
    assert "visible_compliance_risk_caps_safety_scores" in guarded["deterministic_guardrails"]


def test_ai_media_quality_scopes_product_only_overlay_as_component_not_complete_ad():
    job = _job("awaiting_review")
    job.reference_assets_json = [
        {"asset_id": "pack", "business_role": "product_packshot", "role": "overlay_image"},
        {"asset_id": "detail", "business_role": "product_detail", "role": "overlay_image"},
    ]
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    job.prompt_json = {
        "product_overlay": {"enabled": True, "anchor": "center"},
        "production_brief": {
            "product": "示例品牌 超快感",
            "request": "生成5秒商品氛围投流镜头，保留干净卖点安全区，无新增文字。",
            "script": "",
        },
        "ad_material_contract": {
            "content_format": "product_visual",
            "executable_script": "",
            "assembly_plan": {
                "commercial_preference": "dynamic_clean_plate_then_deterministic_product_overlay",
            },
        },
    }
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": [],
        "recommendation": {
            "decision": "human_review",
            "reason": "包装清晰，可以直接投放。",
            "prompt_changes": [],
        },
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["material_stage"]["id"] == "assembled_product_component"
    assert guarded["material_stage"]["label"] == "商品镜头"
    assert guarded["material_stage"]["requires_assembly"] is True
    assert guarded["material_stage"]["commercial_positive_eligible"] is False
    assert guarded["dimension_applicability"]["product_fidelity"] is True
    assert guarded["dimension_applicability"]["selling_point_coverage"] is False
    assert guarded["dimension_applicability"]["commercial_readiness"] is False
    assert guarded["scores"]["product_fidelity"] == 5
    assert guarded["scores"]["selling_point_coverage"] == 3
    assert guarded["scores"]["commercial_readiness"] == 3
    assert guarded["recommendation"]["decision"] == "human_review"
    assert "待剪辑素材" in guarded["recommendation"]["reason"]
    assert "product_component_not_complete_ad" in guarded["deterministic_guardrails"]
    assert any("不能据此判定完整投流成片" in note for note in guarded["scope_notes"])


def test_ai_media_quality_keeps_scripted_product_overlay_as_complete_candidate():
    job = _job("awaiting_review")
    job.reference_assets_json = [
        {"asset_id": "pack", "business_role": "product_packshot", "role": "overlay_image"},
    ]
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    job.prompt_json = {
        "product_overlay": {"enabled": True, "anchor": "center"},
        "production_brief": {
            "product": "示例品牌 超快感",
            "request": "生成可直接投放的5秒商品成片。",
            "script": "现在点击购买，限时优惠。",
        },
        "ad_material_contract": {
            "content_format": "product_visual",
            "executable_script": "现在点击购买，限时优惠。",
            "assembly_plan": {
                "commercial_preference": "dynamic_clean_plate_then_deterministic_product_overlay",
            },
        },
    }
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": [],
        "recommendation": {
            "decision": "human_review",
            "reason": "等待人工复核活动依据。",
            "prompt_changes": [],
        },
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["material_stage"]["id"] == "assembled_product_material"
    assert guarded["material_stage"]["requires_assembly"] is False
    assert guarded["material_stage"]["commercial_positive_eligible"] is True
    assert guarded["dimension_applicability"]["selling_point_coverage"] is True
    assert guarded["dimension_applicability"]["commercial_readiness"] is True
    assert guarded["scores"]["selling_point_coverage"] == 5
    assert guarded["scores"]["commercial_readiness"] == 5
    assert "product_component_not_complete_ad" not in guarded["deterministic_guardrails"]


def test_ai_media_quality_guardrails_preserve_unresolved_human_annotations():
    job = _job("awaiting_review")
    job.reference_assets_json = []
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": ["模型认为无显著问题。"],
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(
        analysis,
        job,
        review_annotations=[{
            "start_seconds": 3,
            "end_seconds": 5,
            "category": "motion",
            "severity": "warning",
            "status": "open",
            "note": "女方短暂直视镜头，首帧手势跨入男方身体。",
        }],
    )

    assert any("人工审片未解决标注" in item and "女方短暂直视镜头" in item for item in guarded["issues"])
    assert guarded["scores"]["prompt_alignment"] == 4
    assert guarded["scores"]["visual_continuity"] == 4
    assert guarded["scores"]["hook_strength"] == 4
    assert guarded["scores"]["commercial_readiness"] == 2
    assert "unresolved_human_review_annotations_preserved" in guarded["deterministic_guardrails"]
    assert "女方短暂直视镜头，首帧手势跨入男方身体。" in guarded["recommendation"]["prompt_changes"]


def test_ai_media_quality_guardrails_cap_open_composition_feedback():
    job = _job("awaiting_review")
    job.reference_assets_json = []
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": [],
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(
        analysis,
        job,
        review_annotations=[{
            "start_seconds": 0,
            "end_seconds": 5,
            "category": "composition",
            "severity": "warning",
            "status": "open",
            "note": "咖啡馆漂移为居家沙发，两人由面对面变成并排。",
        }],
    )

    assert guarded["scores"]["prompt_alignment"] == 3
    assert guarded["scores"]["visual_continuity"] == 3
    assert guarded["scores"]["hook_strength"] == 3
    assert guarded["scores"]["commercial_readiness"] == 2
    assert any("咖啡馆漂移为居家沙发" in item for item in guarded["issues"])


def test_ai_media_quality_guardrails_soften_unverified_audio_and_respect_synthetic_packaging():
    job = _job("awaiting_review")
    job.reference_assets_json = [{"role": "first_frame", "asset_id": "pra-source"}]
    job.result_json = {"assets": [{"id": "pra-result", "sha256": "a" * 64}]}
    job.prompt_json = {
        "brand_guardrails": {
            "require_reference_image": False,
            "approved_product_image_required": False,
            "reason": "用户明确要求不展示品牌包装、Logo 或文字；不要求产品保真参考图。",
        }
    }
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": [
            "关键帧没有字幕，所以口播台词未完整呈现。",
            "因无商品展示，product_fidelity 与 selling_point_coverage 按合规性给予满分。",
        ],
        "recommendation": {"decision": "retry_recommended", "reason": "重新生成完整口播", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["recommendation"]["decision"] == "human_review"
    assert guarded["scores"]["batch_diversity"] == 3
    assert guarded["scores"]["product_fidelity"] == 2
    assert guarded["scores"]["selling_point_coverage"] == 2
    assert any("需由审片人实际听审" in issue for issue in guarded["issues"])
    assert not any("明确排除真实商品或品牌展示" in issue for issue in guarded["issues"])
    assert any("明确排除真实商品或品牌展示" in note for note in guarded["scope_notes"])
    assert guarded["dimension_applicability"]["product_fidelity"] is False
    assert guarded["dimension_applicability"]["selling_point_coverage"] is False
    assert guarded["dimension_applicability"]["commercial_readiness"] is False
    assert guarded["dimension_applicability"]["batch_diversity"] is False
    assert guarded["applicable_dimension_count"] == 5
    assert guarded["applicable_overall_score"] == pytest.approx(25 / 25, abs=0.001)
    assert guarded["material_stage"]["id"] == "component_plate"
    assert guarded["material_stage"]["requires_assembly"] is True
    assert guarded["material_stage"]["commercial_positive_eligible"] is False
    assert not any("给予满分" in issue for issue in guarded["issues"])
    assert "audio_not_analyzed_claim_softened" in guarded["deterministic_guardrails"]
    assert "brand_fidelity_excluded_caps_commercial_scores" in guarded["deterministic_guardrails"]
    assert "unverified_product_score_claim_removed" in guarded["deterministic_guardrails"]
    assert not guarded["recommendation"]["prompt_changes"]


def test_ai_media_quality_guardrails_use_verified_asr_instead_of_visual_audio_guess():
    job = _job("awaiting_review")
    job.result_json = {
        "assets": [{"id": "pra-result", "sha256": "a" * 64}],
        "dialogue_delivery": {
            "status": "completed",
            "transcription": {
                "status": "passed",
                "passed": True,
                "provider": "SiliconFlow",
                "model": "FunAudioLLM/SenseVoiceSmall",
                "coverage": 1.0,
            },
        },
    }
    job.prompt_json = {"requested_audio_prompt": "女：开了吗\n男：开了"}
    analysis = {
        "scores": {key: 4 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": ["关键帧没有字幕，所以口播台词未完整呈现。"],
        "recommendation": {"decision": "human_review", "reason": "需听审", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert not any("口播台词未完整呈现" in item for item in guarded["issues"])
    assert not any("音频内容尚未自动识别" in item for item in guarded["issues"])
    assert "verified_asr_overrides_visual_audio_claim" in guarded["deterministic_guardrails"]


def test_ai_media_quality_guardrails_remove_missing_subtitle_and_product_complaints_when_forbidden():
    job = _job("awaiting_review")
    job.reference_assets_json = []
    job.result_json = {"assets": [{"id": "pra-result", "sha256": "a" * 64}]}
    job.prompt_json = {
        "h3_execution_profile": "clean_dialogue_plate_silent_v1",
        "ad_material_contract": {"content_format": "dialogue", "subtitle_policy": "forbid_burned_in_text"},
        "brand_guardrails": {
            "require_reference_image": False,
            "approved_product_image_required": False,
            "reference_text_policy": "suppress_all_source_text",
            "reason": "只生成人物对话底片，不展示品牌包装、Logo 或文字。",
        },
    }
    analysis = {
        "scores": {key: 4 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 0.8,
        "issues": [
            "缺失字幕：画面中未出现任何可见文字或字幕，无法评估字幕覆盖。",
            "缺失商品植入：画面中未出现任何商品，无法评估商品真实性。",
            "男方在第 8 秒短暂直视镜头，破坏双人对话交流感。",
        ],
        "recommendation": {
            "decision": "retry_recommended",
            "reason": "需要补充字幕和商品。",
            "prompt_changes": ["添加字幕", "补充商品包装展示", "保持双方视线看向彼此"],
        },
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert not any("缺失字幕" in issue for issue in guarded["issues"])
    assert not any("缺失商品植入" in issue for issue in guarded["issues"])
    assert any("直视镜头" in issue for issue in guarded["issues"])
    assert guarded["recommendation"]["decision"] == "human_review"
    assert guarded["recommendation"]["prompt_changes"] == ["保持双方视线看向彼此"]
    assert "禁止烧录字幕" in guarded["recommendation"]["reason"]
    assert "forbidden_subtitle_absence_not_an_issue" in guarded["deterministic_guardrails"]
    assert "excluded_product_absence_not_an_issue" in guarded["deterministic_guardrails"]


def test_ai_media_quality_guardrails_use_embedded_brief_to_scope_clean_people_plate():
    job = _job("awaiting_review")
    job.reference_assets_json = []
    job.result_json = {"assets": [{"id": "pra-result", "sha256": "a" * 64}]}
    job.prompt_json = {
        "h3_execution_profile": "direct_clean_people_plate_silent_v1",
        "production_brief": {
            "product": "示例品牌001隐形套",
            "request": "画面不展示商品，商品与促销信息只由后续已审核素材和配音承载",
        },
    }
    analysis = {
        "scores": {key: 4 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 0.8,
        "issues": ["商品未展示", "卖点未视觉化", "男方动作略显僵硬。"],
        "recommendation": {
            "decision": "retry_recommended",
            "reason": "补充商品和卖点。",
            "prompt_changes": ["补充商品包装展示", "让男方动作更自然"],
        },
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["issues"] == ["男方动作略显僵硬。"]
    assert guarded["recommendation"]["decision"] == "human_review"
    assert guarded["recommendation"]["prompt_changes"] == ["让男方动作更自然"]
    assert "excluded_product_absence_not_an_issue" in guarded["deterministic_guardrails"]
    assert any("明确排除真实商品或品牌展示" in note for note in guarded["scope_notes"])


def test_ai_media_quality_guardrails_keep_detected_subtitle_as_real_issue():
    job = _job("awaiting_review")
    job.reference_assets_json = []
    job.result_json = {"assets": [{"id": "pra-result", "sha256": "a" * 64}]}
    job.prompt_json = {
        "h3_execution_profile": "clean_dialogue_plate_silent_v1",
        "ad_material_contract": {"subtitle_policy": "forbid_burned_in_text"},
    }
    analysis = {
        "scores": {key: 4 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 0.8,
        "issues": ["关键帧 3 出现了烧录字幕，违反无字底片要求。"],
        "recommendation": {"decision": "retry_recommended", "reason": "去除字幕", "prompt_changes": ["去除字幕"]},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert any("出现了烧录字幕" in issue for issue in guarded["issues"])
    assert "forbidden_subtitle_absence_not_an_issue" not in guarded["deterministic_guardrails"]


def test_character_first_frame_never_counts_as_verified_product_image():
    job = _job("awaiting_review")
    job.reference_assets_json = [{
        "role": "first_frame",
        "business_role": "character_first_frame",
        "asset_id": "pra-people",
    }]
    job.result_json = {"assets": [{"id": "pra-result", "sha256": "a" * 64}]}
    job.prompt_json = {"brand_guardrails": {"require_reference_image": True}}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "overall_score": 1.0,
        "issues": ["人物首帧生成了一个新的商品包装。"],
        "recommendation": {"decision": "human_review", "reason": "模型误认为有商品图", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["scores"]["product_fidelity"] == 2
    assert guarded["scores"]["selling_point_coverage"] == 2
    assert guarded["scores"]["commercial_readiness"] == 2
    assert guarded["recommendation"]["decision"] == "retry_recommended"
    assert "no_authorized_product_image_caps_product_scores" in guarded["deterministic_guardrails"]
    assert any("包装文字正确的产品图" in item for item in guarded["recommendation"]["prompt_changes"])


def test_media_training_quality_gate_rejects_defaults_and_low_commercial_scores():
    assert _normalize_media_quality_evaluation({})["overall_score"] == 0.0
    eligible, missing = _media_training_quality_gate({})
    assert eligible is False
    assert set(missing) == {f"quality.{key}>={minimum}" for key, minimum in MEDIA_TRAINING_QUALITY_MINIMUMS.items()}

    quality = {key: minimum for key, minimum in MEDIA_TRAINING_QUALITY_MINIMUMS.items()}
    quality["commercial_readiness"] = 3
    eligible, missing = _media_training_quality_gate(quality)
    assert eligible is False
    assert missing == ["quality.commercial_readiness>=4"]

    quality["commercial_readiness"] = 4
    assert _media_training_quality_gate(quality) == (True, [])

    quality["material_stage"] = {
        "id": "component_plate",
        "requires_assembly": True,
        "commercial_positive_eligible": False,
    }
    eligible, missing = _media_training_quality_gate(quality)
    assert eligible is False
    assert missing == ["material_stage.commercial_positive_eligible"]

    normalized = _normalize_media_quality_evaluation({"material_stage": quality["material_stage"]})
    assert normalized["material_stage"]["id"] == "component_plate"
    assert normalized["material_stage"]["requires_assembly"] is True
    assert normalized["material_stage"]["commercial_positive_eligible"] is False


@pytest.mark.asyncio
async def test_review_approval_does_not_make_unscored_media_training_eligible(monkeypatch):
    from app.media import service

    job = _job("awaiting_review")
    job.rights_json = {
        "copyright_authorized": True,
        "portrait_authorized": True,
        "voice_authorized": True,
        "malware_scan": "passed",
        "sensitive_data_scan": "passed",
        "redaction": "passed",
    }
    job.result_json = {"technical_validation": {"status": "passed"}}

    class FakeDb:
        async def get(self, _model, _row_id):
            return job

    async def no_outboxes(*_args, **_kwargs):
        return []

    async def blocked_sync(*_args, **_kwargs):
        return {"connector_ready": False}

    async def no_attempt(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_ensure_cloud_sync_outboxes", no_outboxes)
    monkeypatch.setattr(service, "_sync_cloud_video", blocked_sync)
    monkeypatch.setattr(service, "_latest_attempt", no_attempt)

    result = await service._review_job(
        FakeDb(),
        SimpleNamespace(id="reviewer"),
        SimpleNamespace(project_id=job.project_id),
        {
            "job_id": job.id,
            "decision": "approved",
            "quality_evaluation": {},
            "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": True},
        },
    )

    gate = result["job"]["review"]["training_gate"]
    assert result["job"]["status"] == "approved"
    assert result["job"]["training_eligibility"] == "blocked"
    assert gate["rights_missing"] == []
    assert set(gate["quality_missing"]) == {
        f"quality.{key}>={minimum}" for key, minimum in MEDIA_TRAINING_QUALITY_MINIMUMS.items()
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"width": 481},
        {"height": 865},
        {"frames": 500},
        {"steps": 0},
        {"batch_count": 9},
    ],
)
def test_media_h3_params_reject_invalid_values(payload):
    with pytest.raises(AppError) as exc:
        normalize_media_params(payload)
    assert exc.value.code == "MEDIA_PARAM_INVALID"


def test_media_state_machine_accepts_governed_path_and_rejects_skip():
    job = _job()
    for target in ("planned", "queued", "assigned", "running", "collecting", "awaiting_review", "approved", "syncing", "synced"):
        transition_media_job(job, target)
    assert job.status == "synced"

    invalid = _job("queued")
    with pytest.raises(AppError) as exc:
        transition_media_job(invalid, "approved")
    assert exc.value.code == "MEDIA_STATUS_TRANSITION_INVALID"


def test_plan_json_requires_complete_h3_contract():
    valid = {
        "creative_goal": "click",
        "audience": "adult",
        "selling_points": ["thin"],
        "shots": [{"index": 1}],
        "video_prompt": "prompt",
        "audio_prompt": "sound",
        "negative_constraints": ["no claims"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }
    parsed = _parse_plan_output(json.dumps(valid))
    assert parsed["prompt_policy_version"] == H3_PROMPT_POLICY_VERSION
    assert parsed["shots"][0]["camera_command"] == "[Static shot]"
    assert parsed["integrated_multimodal_description"].startswith("integrated_multimodal_description:")
    assert _parse_plan_output({**valid, "h3_mode": "arbitrary_workflow"}) is None
    missing = dict(valid)
    missing.pop("shots")
    assert _parse_plan_output(missing) is None


def test_plan_contract_repair_normalizes_noncreative_flash_schema_gaps():
    from app.media import service

    repaired, fields = service._repair_plan_output(
        {
            "goal": "双人自然对话开场",
            "target_audience": "成年人",
            "shot_list": [
                {
                    "start_seconds": 0,
                    "end_seconds": 5,
                    "subject": "两位成年女性在咖啡馆面对面对话",
                    "action": "左侧说话人抬手，右侧自然回应",
                }
            ],
            "mode": "text_to_video",
            "params": {"frames": 124, "fps": 24},
        },
        brief={"request": "生成自然双人对话投流镜头", "duration_seconds": 5, "ratio": "9:16"},
    )

    assert repaired is not None
    assert repaired["creative_goal"] == "双人自然对话开场"
    assert repaired["h3_mode"] == "text_to_video"
    assert repaired["prompt_policy_version"] == H3_PROMPT_POLICY_VERSION
    assert repaired["plan_contract_repair"]["applied"] is True
    assert "shots" in fields
    assert repaired["recommended_params"]["width"] == 480


@pytest.mark.asyncio
async def test_baseline_plan_uses_flash_after_deterministic_contract_repair(monkeypatch):
    from app.media import service

    calls = []

    async def fake_analyze(_db, _user, args, **_kwargs):
        calls.append(args["model_profile"])
        return {
            "model": "deepseek-v4-flash",
            "output": {
                "goal": "自然双人对话",
                "shot_list": [{"subject": "两位成年女性", "action": "面对面对话"}],
                "mode": "text_to_video",
            },
        }

    monkeypatch.setattr(service.codex_service, "_builtin_platform_ai_analyze", fake_analyze)
    model, plan = await service._call_baseline_plan(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(id="run-repaired-flash", execution_run_id=None),
        {"request": "生成自然双人对话投流镜头", "duration_seconds": 5},
    )

    assert calls == ["deepseek-v4-flash"]
    assert model == "deepseek-v4-flash"
    assert plan["plan_contract_repair"]["applied"] is True
    assert "requested_baseline_model" not in plan


@pytest.mark.asyncio
async def test_baseline_plan_reports_schema_fallback_without_claiming_flash_unavailable(monkeypatch):
    from app.media import service

    calls = []
    valid = {
        "creative_goal": "商品点击",
        "audience": "成年人",
        "selling_points": ["轻薄"],
        "shots": [{"subject": "成年伴侣", "action": "自然交流"}],
        "video_prompt": "自然对话",
        "audio_prompt": "",
        "negative_constraints": ["不要文字"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }

    async def fake_analyze(_db, _user, args, **_kwargs):
        calls.append(args["model_profile"])
        if args["model_profile"] == "deepseek-v4-flash":
            return {"model": "deepseek-v4-flash", "output": {"message": "not a storyboard"}}
        return {"model": "deepseek-chat", "output": valid}

    monkeypatch.setattr(service.codex_service, "_builtin_platform_ai_analyze", fake_analyze)
    model, plan = await service._call_baseline_plan(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(id="run-schema-fallback", execution_run_id=None),
        {"request": "做一条自然对话素材"},
    )

    assert calls == ["deepseek-v4-flash", "deepseek-v4-flash", "configured"]
    assert model == "deepseek-chat"
    assert "未通过 H3 方案结构校验" in plan["warnings"][0]
    assert "当前不可用" not in plan["warnings"][0]
    assert "invalid_structure" in plan["baseline_fallback_reason"]


@pytest.mark.asyncio
async def test_baseline_plan_reports_actual_configured_model_when_flash_is_unavailable(monkeypatch):
    from app.media import service

    calls = []
    valid = {
        "creative_goal": "商品点击",
        "audience": "成年人",
        "selling_points": ["轻薄"],
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "情侣"}],
        "video_prompt": "高级克制的亲密氛围",
        "audio_prompt": "轻柔环境音",
        "negative_constraints": ["不夸大功效"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }

    async def fake_analyze(_db, _user, args, **_kwargs):
        calls.append(args["model_profile"])
        if args["model_profile"] == "deepseek-v4-flash":
            raise AppError("PLATFORM_AI_CALL_FAILED", 502)
        return {"model": "deepseek-chat", "output": valid}

    monkeypatch.setattr(service.codex_service, "_builtin_platform_ai_analyze", fake_analyze)
    model, plan = await service._call_baseline_plan(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(id="run-1", execution_run_id=None),
        {"request": "做一条 5 秒投流素材"},
    )

    assert calls == ["deepseek-v4-flash", "deepseek-v4-flash", "configured"]
    assert model == "deepseek-chat"
    assert plan["requested_baseline_model"] == "deepseek-v4-flash"
    assert "实际模型 deepseek-chat" in plan["warnings"][0]
    assert "调用失败或超时" in plan["warnings"][0]


@pytest.mark.asyncio
async def test_baseline_plan_retries_flash_once_before_fallback(monkeypatch):
    from app.media import service

    calls = []
    valid = {
        "creative_goal": "商品点击",
        "audience": "成年人",
        "selling_points": ["轻薄"],
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "蓝色丝绸"}],
        "video_prompt": "蓝色丝绸与金色光带",
        "audio_prompt": "轻柔环境音",
        "negative_constraints": ["不要文字"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }

    async def fake_analyze(_db, _user, args, **_kwargs):
        calls.append(args["model_profile"])
        if len(calls) == 1:
            raise AppError("PLATFORM_AI_CALL_FAILED", 502)
        return {"model": "deepseek-v4-flash", "output": valid}

    monkeypatch.setattr(service.codex_service, "_builtin_platform_ai_analyze", fake_analyze)
    model, plan = await service._call_baseline_plan(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(id="run-retry", execution_run_id=None),
        {"request": "做一条 5 秒投流素材"},
    )

    assert calls == ["deepseek-v4-flash", "deepseek-v4-flash"]
    assert model == "deepseek-v4-flash"
    assert "requested_baseline_model" not in plan


@pytest.mark.asyncio
async def test_baseline_plan_timeout_moves_to_configured_fallback(monkeypatch):
    from app.media import service

    calls = []
    valid = {
        "creative_goal": "商品点击",
        "audience": "成年人",
        "selling_points": ["轻薄"],
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "成年伴侣"}],
        "video_prompt": "自然对话",
        "audio_prompt": "",
        "negative_constraints": ["不要文字"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }

    async def fake_analyze(_db, _user, args, **_kwargs):
        calls.append(args["model_profile"])
        if args["model_profile"] == "deepseek-v4-flash":
            await asyncio.Event().wait()
        return {"model": "configured-model", "output": valid}

    monkeypatch.setattr(service, "MEDIA_PLAN_ATTEMPT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(service.codex_service, "_builtin_platform_ai_analyze", fake_analyze)

    model, plan = await service._call_baseline_plan(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(id="run-timeout-fallback", execution_run_id=None),
        {"request": "做一条自然对话素材"},
    )

    assert calls == ["deepseek-v4-flash", "configured"]
    assert model == "configured-model"
    assert plan["requested_baseline_model"] == "deepseek-v4-flash"
    assert "timeout_after_0.01s" in plan["baseline_fallback_reason"]


@pytest.mark.asyncio
async def test_plan_compare_times_out_stalled_ai_without_creating_a_plan(monkeypatch):
    from app.media import service

    async def stalled_baseline(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def no_candidate(_deployment, _brief):
        return None, None, {}

    async def no_deployment(_db, _project):
        return None

    monkeypatch.setattr(service, "MEDIA_PLAN_COMPARE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(service, "_cached_baseline_plan", stalled_baseline)
    monkeypatch.setattr(service, "_call_candidate_plan", no_candidate)
    monkeypatch.setattr(service, "_active_candidate_deployment", no_deployment)

    with pytest.raises(AppError) as exc:
        await service._plan_compare(
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(id="samplebrand-material-workbench"),
            SimpleNamespace(id="run-timeout"),
            {"brief": {"request": "生成 5 秒投流素材"}},
        )

    assert exc.value.code == "MEDIA_PLAN_TIMEOUT"
    assert exc.value.detail["timeout_seconds"] == 0.01
    assert exc.value.detail["editable"] is True


@pytest.mark.asyncio
async def test_cached_baseline_plan_reuses_identical_success_and_recompiles(monkeypatch):
    from app.media import service

    calls = []
    stored = {}
    valid = {
        "creative_goal": "对话开场钩子",
        "audience": "成年人",
        "selling_points": [],
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "成年伴侣"}],
        "video_prompt": "自然对话",
        "audio_prompt": "",
        "negative_constraints": ["不要文字"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }

    async def fake_call(*_args, **_kwargs):
        calls.append("ai")
        return "deepseek-v4-flash", compile_h3_plan(valid, brief={"request": "自然对话"})

    async def fake_get(key):
        return stored.get(key)

    async def fake_set(key, value, ttl=None):
        stored[key] = value
        stored["ttl"] = ttl

    monkeypatch.setattr(service, "_call_baseline_plan", fake_call)
    monkeypatch.setattr(service, "cache_get", fake_get)
    monkeypatch.setattr(service, "cache_set", fake_set)
    brief = {"request": "自然对话", "duration_seconds": 5}
    args = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace(id="run-cache", execution_run_id=None), brief)

    first_model, first_plan = await service._cached_baseline_plan(*args)
    second_model, second_plan = await service._cached_baseline_plan(*args)

    assert calls == ["ai"]
    assert first_model == second_model == "deepseek-v4-flash"
    assert first_plan["integrated_multimodal_description"] == second_plan["integrated_multimodal_description"]
    assert stored["ttl"] == service.MEDIA_PLAN_RESULT_CACHE_TTL_SECONDS


@pytest.mark.asyncio
async def test_cached_baseline_plan_cache_outage_does_not_block_planning(monkeypatch):
    from app.media import service

    async def cache_down(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    async def fake_call(*_args, **_kwargs):
        return "deepseek-v4-flash", {
            "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
            "integrated_multimodal_description": "integrated_multimodal_description: safe",
        }

    monkeypatch.setattr(service, "cache_get", cache_down)
    monkeypatch.setattr(service, "cache_set", cache_down)
    monkeypatch.setattr(service, "_call_baseline_plan", fake_call)

    model, plan = await service._cached_baseline_plan(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(id="run-cache-down", execution_run_id=None),
        {"request": "自然对话"},
    )

    assert model == "deepseek-v4-flash"
    assert plan["prompt_policy_version"] == H3_PROMPT_POLICY_VERSION


def test_h3_compiler_uses_official_camera_allowlist_and_suppresses_unverified_brand_rendering():
    plan = {
        "creative_goal": "展示示例品牌包装上的中文品牌字",
        "audience": "成年人",
        "selling_points": ["轻薄"],
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "产品近景",
            "camera_command": "orbit wildly",
            "subject": "示例品牌包装",
            "action": "缓慢转动",
            "scene": "红色影棚",
            "lighting": "柔和轮廓光",
            "mood": "高级克制",
            "audio": "轻柔环境音",
        }],
        "video_prompt": "draft",
        "audio_prompt": "soft ambience",
        "negative_constraints": ["不夸大功效"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "assumptions": [],
        "warnings": [],
    }
    compiled = compile_h3_plan(plan, brief={"request": "包装和 Logo 必须真实"})
    assert compiled["h3_mode"] == "text_to_video"
    assert compiled["brand_guardrails"]["require_reference_image"] is False
    assert compiled["brand_guardrails"]["approved_product_image_required"] is True
    assert compiled["brand_guardrails"]["verified_product_evidence"] is False
    assert compiled["shots"][0]["camera_command"] == "[Static shot]"
    assert compiled["reference_roles"] == []
    assert "[Static shot]" in compiled["integrated_multimodal_description"]
    assert "PRODUCT REFERENCE LOCK" not in compiled["integrated_multimodal_description"]
    assert "FIRST FRAME IDENTITY LOCK" not in compiled["integrated_multimodal_description"]
    assert "UNVERIFIED PRODUCT SUPPRESSION" in compiled["integrated_multimodal_description"]
    assert "STATIC CAMERA LOCK" in compiled["integrated_multimodal_description"]
    assert "fixed focal length" in compiled["integrated_multimodal_description"]
    assert len(compiled["integrated_multimodal_description"]) <= 7000


def test_h3_compiler_character_frame_is_not_product_evidence_but_real_packshot_is():
    plan = {
        "creative_goal": "成年情侣讨论示例品牌铂金，包装和 Logo 必须真实",
        "audience": "成年人",
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "双人中景",
            "camera_command": "[Static shot]",
            "subject": "两位成年人对话",
            "action": "女方提问，男方自然回答",
            "scene": "明亮客厅",
            "lighting": "自然光",
            "mood": "轻松",
            "audio": "保留第一轮对话",
        }],
        "negative_constraints": [],
        "reference_roles": [{"role": "first_frame", "purpose": "成年情侣场景首帧"}],
        "h3_mode": "image_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "warnings": [],
    }
    character = compile_h3_plan(
        plan,
        brief={
            "request": "包装和 Logo 必须真实",
            "contains_person": True,
            "source_roles": [{"role": "character_first_frame", "technical_role": "first_frame"}],
        },
    )
    character_prompt = character["integrated_multimodal_description"]
    assert character["h3_mode"] == "image_to_video"
    assert character["brand_guardrails"]["require_reference_image"] is False
    assert character["brand_guardrails"]["approved_product_image_required"] is True
    assert character["brand_guardrails"]["character_frame_is_not_product_evidence"] is True
    assert "PRODUCT REFERENCE LOCK" not in character_prompt
    assert "UNVERIFIED PRODUCT SUPPRESSION" in character_prompt
    assert any("人物/场景首帧不是商品真实性依据" in item for item in character["negative_constraints"])

    packshot = compile_h3_plan(
        plan,
        brief={
            "request": "包装和 Logo 必须真实",
            "source_roles": [{"role": "product_packshot", "technical_role": "first_frame"}],
        },
    )
    assert packshot["brand_guardrails"]["require_reference_image"] is True
    assert packshot["brand_guardrails"]["verified_product_evidence"] is True
    assert packshot["brand_guardrails"]["approved_product_image_required"] is False
    assert "PRODUCT REFERENCE LOCK" in packshot["integrated_multimodal_description"]
    assert "UNVERIFIED PRODUCT SUPPRESSION" not in packshot["integrated_multimodal_description"]


def test_h3_compiler_warns_when_people_move_with_readable_product_packaging():
    plan = {
        "creative_goal": "成年情侣与商品包装同框",
        "audience": "成年人",
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "双人中景",
            "camera_command": "[Static shot]",
            "subject": "两位成年人和桌面商品包装",
            "action": "人物自然眨眼",
            "scene": "咖啡馆",
            "lighting": "自然光",
            "mood": "日常",
            "audio": "无声",
        }],
        "negative_constraints": [],
        "reference_roles": [],
        "h3_mode": "image_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "warnings": [],
    }

    compiled = compile_h3_plan(
        plan,
        brief={
            "request": "包装和 Logo 必须真实",
            "contains_person": True,
            "source_roles": [{"role": "product_packshot", "technical_role": "first_frame"}],
        },
    )

    assert any("H3 不能保证包装文字或 Logo 逐帧保真" in warning for warning in compiled["warnings"])
    assert any("不得自动成为训练正样本" in warning for warning in compiled["warnings"])
    assert "FIRST FRAME IDENTITY LOCK" in compiled["integrated_multimodal_description"]
    assert "stationary products and props must not be picked up" in compiled["integrated_multimodal_description"]


def test_h3_compiler_recommends_separate_workflow_for_people_with_unbranded_product_prop():
    plan = {
        "creative_goal": "验证成年情侣与无文字包装同框",
        "audience": "成年人",
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "双人中景",
            "camera_command": "[Static shot]",
            "subject": "两位成年人和桌面无文字深蓝包装盒",
            "action": "人物眨眼，包装完全不动",
            "scene": "咖啡馆桌面有商品盒",
            "lighting": "自然光",
            "mood": "日常",
            "audio": "无声",
        }],
        "negative_constraints": ["无品牌、无 Logo、无文字"],
        "reference_roles": [{"role": "first_frame", "purpose": "人物与物品首帧"}],
        "h3_mode": "image_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
        "warnings": [],
    }

    compiled = compile_h3_plan(
        plan,
        brief={"request": "无文字无 Logo，包装不动", "contains_person": True},
    )

    assert compiled["brand_guardrails"]["people_product_workflow"] == "separate_people_and_product_for_commercial_output"
    assert any("可能把静止商品当成可交互道具" in warning for warning in compiled["warnings"])
    assert any("正式投流默认应拆为人物镜头与独立商品镜头" in warning for warning in compiled["warnings"])


def test_h3_compiler_does_not_treat_no_product_clean_plate_as_product_prop():
    compiled = compile_h3_plan(
        {
            "creative_goal": "无商品元素的中国年轻成年情侣自然对话底片",
            "audience": "成年人",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 15,
                "framing": "腰部以上双人中景",
                "camera_command": "[Static shot]",
                "subject": "一位中国成年女性与一位中国成年男性面对面坐在沙发上",
                "action": "两人轮流作出自然闭口反应",
                "scene": "普通中国城市住宅客厅",
                "lighting": "自然窗光",
                "mood": "轻松真实",
                "audio": "无声",
            }],
            "negative_constraints": ["无商品、无包装、无 Logo、无文字、无字幕"],
            "reference_roles": [{"role": "character_first_frame", "technical_role": "first_frame"}],
            "h3_mode": "image_to_video",
            "recommended_params": MEDIA_DEFAULT_PRESET,
            "duration_seconds": 15,
            "ratio": "9:16",
            "brand_guardrails": {},
            "warnings": [],
        },
        brief={
            "request": "无商品元素、无文字、无字幕",
            "contains_person": True,
            "source_roles": [{"role": "character_first_frame", "technical_role": "first_frame"}],
        },
    )

    assert not any("静止商品当成可交互道具" in warning for warning in compiled["warnings"])
    assert compiled["prompt_policy_version"] == "minimax-h3-context-ir-v60"


def test_transparent_product_cutout_does_not_turn_real_package_material_transparent():
    compiled = compile_h3_plan(
        {
            "creative_goal": "把粉色真实商品转化为透明材质版本并展示",
            "audience": "中国大陆成年消费者",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 2.5,
                "framing": "商品近景",
                "camera_command": "[Static shot]",
                "subject": "白色透明包装盒",
                "action": "包装变透明并轻微转动",
                "scene": "摄影棚",
                "lighting": "柔光",
                "mood": "真实",
                "audio": "无声",
            }, {
                "start_seconds": 2.5,
                "end_seconds": 5,
                "framing": "商品特写",
                "camera_command": "[Push in]",
                "subject": "白色透明包装盒侧面",
                "action": "镜头移动到包装侧面并定格",
                "scene": "渐变背景",
                "lighting": "侧光",
                "mood": "精致",
                "audio": "无声",
            }],
            "negative_constraints": ["去掉全部文字"],
            "reference_roles": [{"role": "first_frame", "business_role": "product_packshot"}],
            "h3_mode": "image_to_video",
            "recommended_params": MEDIA_DEFAULT_PRESET,
            "duration_seconds": 5,
            "ratio": "9:16",
            "brand_guardrails": {},
        },
        brief={
            "request": "参考第一幕，做成透明包装的，保持真实包装几何、颜色和封边；去掉全部文字",
            "source_roles": [{
                "role": "product_packshot",
                "business_role": "product_packshot",
                "technical_role": "first_frame",
                "mention_token": "@示例品牌超快感-侧面透明图",
                "purpose": "真实商品首帧",
            }],
        },
    )

    guardrails = compiled["brand_guardrails"]
    prompt = compiled["integrated_multimodal_description"]
    assert compiled["prompt_policy_version"] == "minimax-h3-context-ir-v60"
    assert guardrails["product_source_semantics"] == "transparent_background_cutout"
    assert guardrails["product_material_transform"] == "forbidden"
    assert guardrails["product_geometry_color_lock"] is True
    assert guardrails["product_motion_policy"] == "locked_static_packshot"
    assert guardrails["reference_text_policy"] == "suppress_generated_text_preserve_approved_product_surface"
    assert "透明材质版本" not in compiled["creative_goal"]
    assert "白色透明包装" not in compiled["shots"][0]["subject"]
    assert "包装变透明" not in compiled["shots"][0]["action"]
    assert "APPROVED PACKAGE SOURCE LOCK" in prompt
    assert "PRODUCT PACKSHOT MOTION LOCK" in prompt
    assert "alpha channel belongs only to the input-image background" in prompt
    assert "transparent glass" not in prompt
    assert "blister container" not in prompt
    assert "NO-CAPTION VISUAL ISOLATION LOCK" not in prompt
    assert "AUDIO TRACK ONLY" not in prompt
    assert "不得添加任何文字" not in prompt
    assert "去掉全部文字" not in prompt
    assert len(compiled["shots"]) == 1
    assert compiled["shots"][0]["start_seconds"] == 0.0
    assert compiled["shots"][0]["end_seconds"] == 5.0
    assert "完全静止" in compiled["shots"][0]["action"]
    assert "沿用已审核首帧" in compiled["shots"][0]["scene"]
    assert "镜头移动" not in prompt
    assert compiled["shots"][0]["camera_command"] == "[Static shot]"
    assert "完全静止" in compiled["shots"][0]["action"]
    assert not any("不生成任何文字或Logo" in warning for warning in compiled["warnings"])
    assert any("已识别商品透明图" in warning for warning in compiled["warnings"])


def test_transparent_product_cutout_allows_unambiguous_material_redesign_request():
    compiled = compile_h3_plan(
        {
            "creative_goal": "把商品包装材质改为透明玻璃概念样机",
            "shots": [{
                "start_seconds": 0, "end_seconds": 5, "framing": "商品近景",
                "camera_command": "[Static shot]", "subject": "透明玻璃概念包装",
                "action": "保持静止", "scene": "摄影棚", "lighting": "柔光",
                "mood": "概念设计", "audio": "无声",
            }],
            "negative_constraints": [],
            "reference_roles": [{"role": "first_frame", "business_role": "product_packshot"}],
            "h3_mode": "image_to_video",
            "recommended_params": MEDIA_DEFAULT_PRESET,
            "duration_seconds": 5,
            "ratio": "9:16",
            "brand_guardrails": {},
        },
        brief={
            "request": "将包装材质改成透明，做成透明塑料盒概念样机",
            "source_roles": [{
                "role": "product_packshot", "business_role": "product_packshot",
                "technical_role": "first_frame", "mention_token": "@商品透明图",
            }],
        },
    )

    assert "product_source_semantics" not in compiled["brand_guardrails"]
    assert "TRANSPARENT CUTOUT SEMANTICS LOCK" not in compiled["integrated_multimodal_description"]


def test_h3_compiler_does_not_apply_static_camera_lock_to_moving_shot():
    compiled = compile_h3_plan({
        "creative_goal": "展示无文字的抽象材质",
        "audience": "成年人",
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "材质近景",
            "camera_command": "[Push in]",
            "subject": "无文字的蓝色方形材质",
            "action": "保持方形轮廓",
            "scene": "摄影棚",
            "lighting": "柔光",
            "mood": "克制",
            "audio": "无声",
        }],
        "negative_constraints": ["无品牌、无包装、无 Logo、无文字"],
        "reference_roles": [],
        "h3_mode": "text_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {},
    }, brief={"request": "无品牌、无包装、无 Logo、无文字的抽象镜头"})

    prompt = compiled["integrated_multimodal_description"]
    assert "[Push in]" in prompt
    assert "STATIC CAMERA LOCK" not in prompt
    assert "PRODUCT REFERENCE LOCK" not in prompt


def test_h3_compiler_injects_review_feedback_into_executable_prompt():
    plan = {
        "creative_goal": "保持双人对话和桌面商品稳定",
        "audience": "成年人",
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "双人中景",
            "camera_command": "[Static shot]",
            "subject": "两位成年人和桌面右下角小包装",
            "action": "女方提问，男方聆听",
            "scene": "日间厨房",
            "lighting": "自然光",
            "mood": "日常",
            "audio": "女方一句口播",
        }],
        "audio_prompt": "只保留女方一句口播",
        "negative_constraints": ["不得放大包装"],
        "reference_roles": [{"role": "first_frame", "purpose": "首帧图片（商品 / 人物）"}],
        "h3_mode": "image_to_video",
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "duration_seconds": 5,
        "ratio": "9:16",
        "brand_guardrails": {"require_reference_image": True},
        "review_rework": {
            "reason": "上一版把 SAMPLEBRAND 改成 BURIGX，且包装被放大",
            "annotations": [{
                "category": "brand",
                "severity": "critical",
                "start_seconds": 2,
                "end_seconds": 5,
                "note": "保持右下角 8%-10% 高度和原始字形",
                "created_by": "must-not-enter-runtime-prompt",
            }],
        },
    }

    compiled = compile_h3_plan(plan, brief={"request": "包装和 Logo 必须真实"})

    prompt = compiled["integrated_multimodal_description"]
    assert "REWORK DIRECTIVE" in prompt
    assert "SAMPLEBRAND 改成 BURIGX" in prompt
    assert "保持右下角 8%-10% 高度和原始字形" in prompt
    assert "must-not-enter-runtime-prompt" not in prompt
    assert compiled["review_rework"]["annotations"][0]["category"] == "brand"


def test_review_rework_seed_changes_per_idempotent_rework_request():
    first = _review_rework_seed("review-rework:job-1:1", 7)
    assert first == _review_rework_seed("review-rework:job-1:1", 7)
    assert first != 7
    assert first != _review_rework_seed("review-rework:job-1:2", 7)


def test_library_metadata_normalizes_invalid_default_role_to_media_contract():
    normalized = _normalized_library_metadata(
        {},
        {"default_role": "first_frame", "synthetic_test": True},
        media_type="image",
    )
    assert normalized["default_role"] == "character_first_frame"
    assert normalized["default_role_normalized_from"] == "first_frame"
    assert normalized["synthetic_test"] is True


def test_library_metadata_accepts_legacy_business_role_and_repairs_old_default():
    normalized = _normalized_library_metadata(
        {"default_role": "character_first_frame", "source": "legacy-sf"},
        {"business_role": "product_packshot"},
        media_type="image",
    )
    assert normalized["default_role"] == "product_packshot"
    assert normalized["business_role"] == "product_packshot"


def test_media_quality_focus_crop_zooms_declared_packaging_corner_only():
    region, filter_value = _media_quality_focus_crop("包装固定在桌面右下角，不得改变品牌文字或 Logo")
    assert region == "bottom_right"
    assert filter_value.startswith("crop=")
    assert filter_value.endswith("scale=480:-2")
    assert _media_quality_focus_crop("成年人双人中景，无品牌、无文字") == ("", "")
    assert _media_quality_focus_crop("clean people plate", overlay_anchor="bottom_right") == (
        "bottom_right",
        "crop=iw*0.55:ih*0.55:iw*0.45:ih*0.45,scale=480:-2",
    )
    assert _media_quality_focus_crop("dynamic commercial plate", overlay_anchor="center") == (
        "center",
        "crop=iw*0.55:ih*0.55:iw*0.225:ih*0.225,scale=480:-2",
    )


@pytest.mark.parametrize(
    "brief",
    [
        {"request": "纯抽象氛围，无人物、无品牌、无包装、无 Logo、无文字"},
        {"product": "无品牌抽象测试素材", "compliance_constraints": "不出现包装、Logo 和中文文字"},
        {"product": "示例品牌 001 隐形系列", "request": "不编造价格、活动、包装文字或功效"},
        {"request": "abstract silk lighting without logo, package text or brand mark"},
    ],
)
def test_h3_brand_reference_detector_respects_explicit_exclusions(brief):
    assert brief_requires_brand_reference(brief) is False


@pytest.mark.parametrize(
    "brief",
    [
        {"request": "画面不展示商品，商品与促销信息由后期承载"},
        {"request": "不呈现产品，只保留人物对话底片"},
    ],
)
def test_h3_brand_exclusion_detector_understands_business_product_terms(brief):
    assert brief_excludes_brand_fidelity(brief) is True


@pytest.mark.parametrize(
    "brief",
    [
        {"request": "生成抽象氛围，不要人物、人体或身体部位"},
        {"generation_requirements": "画面不出现人物，只保留商品和背景光影"},
        {"request": "abstract product plate without people or human body parts"},
    ],
)
def test_h3_people_exclusion_detector_uses_operator_brief(brief):
    assert brief_excludes_people(brief) is True


def test_h3_people_exclusion_detector_does_not_negate_positive_people_request():
    assert brief_excludes_people({"request": "中国年轻情侣自然对话，人物动作松弛"}) is False


def test_h3_people_exclusion_detector_ignores_nested_system_lineage():
    brief = {
        "product": "示例品牌 001 隐形套",
        "script": "女：你不是说越薄越没感觉吗？\n男：不是感觉不到你。",
        "request": "中国年轻情侣自然对话；不要字幕，不出现包装、Logo 或任何文字。",
        "ad_material_contract": {
            "people_requested": True,
            "actor_count": 2,
            "content_format": "dialogue",
            "performance_rules": [
                "不得补充第三人、背景人体或镜中人",
                "人物脚底受力自然，人体关节保持真实范围",
            ],
        },
    }

    assert brief_excludes_people(brief) is False


def test_ai_media_quality_keeps_people_for_dialogue_when_nested_rules_name_human_body():
    job = _job("awaiting_review")
    job.prompt_json = {
        "production_brief": {
            "script": "女：你不是说越薄越没感觉吗？\n男：不是感觉不到你。",
            "request": "中国年轻情侣自然对话；不要字幕，不出现包装、Logo 或任何文字。",
            "ad_material_contract": {
                "people_requested": True,
                "actor_count": 2,
                "content_format": "dialogue",
                "performance_rules": ["不得补充第三人或背景人体"],
            },
        },
        "ad_material_contract": {
            "people_requested": True,
            "actor_count": 2,
            "content_format": "dialogue",
        },
    }
    job.result_json = {"assets": [{"id": "pra-1", "sha256": "a" * 64}]}
    analysis = {
        "scores": {key: 5 for key in MEDIA_QUALITY_DIMENSION_KEYS},
        "issues": [],
        "hard_gate_findings": [{
            "code": "unexpected_person_detected",
            "confidence": 0.99,
            "frame_indices": [1, 2, 3],
            "observed_actor_count": 2,
            "person_regions": "画面中央两位成年人物",
            "evidence": "关键帧持续出现两位对话人物",
        }],
        "hard_gate_evaluated": True,
        "recommendation": {"decision": "human_review", "reason": "等待人工", "prompt_changes": []},
    }

    guarded = _apply_ai_media_quality_guardrails(analysis, job)

    assert guarded["visual_policy_gate"]["passed"] is True
    assert not any("无人物画面硬门禁失败" in issue for issue in guarded["issues"])
    assert "unexpected_person_hard_gate_failed" not in guarded["deterministic_guardrails"]


def test_h3_explicit_mode_and_negative_brand_requirements_override_model_guess():
    plan = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "蓝金流体", "camera_command": "[Push in]"}],
        "reference_roles": [{"role": "first_frame", "purpose": "模型误建议的产品图"}],
        "brand_guardrails": {"require_reference_image": True},
        "warnings": ["检测到包装、Logo 或文字真实性要求；已推荐图生视频，纯文生视频可能生成错误文字。"],
    }
    compiled = compile_h3_plan(
        plan,
        brief={
            "product": "抽象视觉测试，不展示真实包装",
            "compliance_constraints": "不得出现人物、包装、Logo 或文字",
            "requested_h3_mode": "reference_replay",
            "reference_roles": [{"role": "reference_video", "purpose": "仅参考节奏与镜头结构"}],
        },
    )

    assert compiled["h3_mode"] == "reference_replay"
    assert compiled["brand_guardrails"]["require_reference_image"] is False
    assert compiled["brand_guardrails"]["approved_product_image_required"] is False
    assert compiled["reference_roles"] == [{"role": "reference_video", "purpose": "仅参考节奏与镜头结构"}]
    assert "<Video 1>" in compiled["integrated_multimodal_description"]
    assert not any("纯文生视频可能生成错误文字" in item for item in compiled["warnings"])


def test_h3_explicit_non_display_request_overrides_product_name_brand_terms():
    plan = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "成年双人对话", "camera_command": "[Static shot]"}],
        "reference_roles": [{"role": "first_frame", "purpose": "人物首帧"}],
        "brand_guardrails": {"require_reference_image": True},
        "warnings": ["检测到包装、Logo 或文字真实性要求；已推荐图生视频，纯文生视频可能生成错误文字。"],
    }
    compiled = compile_h3_plan(
        plan,
        brief={
            "product": "示例品牌001隐形套 · 双人对话开场",
            "request": "只生成人物开场，不出现商品、包装、Logo、字幕、屏幕文字或价格。",
        },
    )

    assert compiled["h3_mode"] == "image_to_video"
    assert compiled["brand_guardrails"]["require_reference_image"] is False
    assert not any("纯文生视频可能生成错误文字" in item for item in compiled["warnings"])


def test_h3_product_overlay_compiles_silent_people_plate_for_runtime():
    compiled = compile_h3_plan(
        {
            "creative_goal": "示例品牌超快感商品植入双人对话",
            "audience": "成年人",
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "recommended_params": MEDIA_DEFAULT_PRESET,
            "subtitle_policy": "forbid_burned_in_text",
            "product_overlay": {
                "enabled": True,
                "anchor": "bottom_right",
                "width_ratio": 0.22,
            },
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "双人中景",
                "camera_command": "[Static shot]",
                "subject": "居家客厅的一男一女，男生体型稍胖，右下角放示例品牌包装",
                "action": "女生说话，男生回应，并展示商品包装",
                "scene": "现代居家客厅",
                "lighting": "自然窗光",
                "mood": "松弛",
                "audio": "女：宝，来抱一下。男：不要，没心情。",
            }],
            "performance_timeline": [],
            "audio_prompt": "女：宝，来抱一下。\n男：不要，没心情。",
            "negative_constraints": ["不得生成包装、Logo、商品卡、字幕或文字"],
            "reference_roles": [{
                "role": "reference_image",
                "business_role": "product_packshot",
                "purpose": "示例品牌超快感真实包装",
            }],
            "brand_guardrails": {},
            "warnings": [],
        },
        brief={
            "request": "双人自然对话，真实商品只用透明图受控植入",
            "contains_person": True,
            "source_roles": [{"role": "product_packshot", "business_role": "product_packshot"}],
        },
    )

    prompt = compiled["integrated_multimodal_description"]
    assert compiled["h3_execution_profile"] == "clean_people_plate_silent_v4"
    assert "CLEAN TWO-PERSON LIVE-ACTION SCENE PLATE" in prompt
    assert "SINGLE-PRESENTER LOCK" not in prompt
    assert "an adult woman and a slightly stocky adult man" in prompt
    assert "bao lai bao yi xia" not in prompt
    assert "不要" not in prompt
    assert "示例品牌" not in prompt
    assert "商品" not in prompt
    assert "包装" not in prompt
    assert "product" not in prompt.casefold()
    assert "package" not in prompt.casefold()
    assert "subtitle" not in prompt.casefold()
    assert "caption" not in prompt.casefold()
    assert prompt.isascii()
    # Business facts remain available to review/training even though the GPU
    # receives a clean visual projection.
    assert "包装" in compiled["negative_constraints"][0]
    assert compiled["audio_prompt"] == ""
    assert compiled["requested_audio_prompt"].startswith("女：宝")
    assert compiled["recommended_params"]["audio_enabled"] is False
    assert compiled["dialogue_delivery"] == {
        "requested": True,
        "status": "voiceover_renderer_required",
        "delivery_mode": "auto_after_visual_gate",
        "auto_finalize": True,
        "visual_audio_enabled": False,
        "visual_strategy": "silent_reaction_plate_v2",
        "native_lip_sync": False,
        "renderer": None,
        "reason": "joint_h3_dialogue_can_burn_caption_pixels",
    }


def test_h3_dialogue_keeps_script_but_skips_voice_renderer_for_explicit_silent_output():
    compiled = compile_h3_plan(
        {
            "creative_goal": "成年情侣自然对话静音底片",
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "recommended_params": {**MEDIA_DEFAULT_PRESET, "audio_enabled": False},
            "requested_audio_prompt": "旧的自动配音台词",
            "dialogue_delivery": {
                "requested": True,
                "delivery_mode": "auto_after_visual_gate",
                "auto_finalize": True,
            },
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "双人中景",
                "camera_command": "[Static shot]",
                "subject": "客厅中的成年情侣",
                "action": "女方先表达，男方延迟回应",
                "scene": "真实客厅",
                "lighting": "自然光",
                "mood": "轻松",
                "audio": "女：今晚早点回来。男：好。",
            }],
        },
        brief={
            "script": "女：今晚早点回来。\n男：好。",
            "script_timing": {"shot_script": "女：今晚早点回来。\n男：好。"},
            "audio_enabled": False,
            "contains_person": True,
        },
    )

    assert compiled["dialogue_delivery"]["requested"] is False
    assert compiled["dialogue_delivery"]["delivery_mode"] == "silent_output"
    assert compiled["dialogue_delivery"]["auto_finalize"] is False
    assert compiled["recommended_params"]["audio_enabled"] is False
    assert "requested_audio_prompt" not in compiled
    assert "今晚早点回来" not in compiled["integrated_multimodal_description"]
    assert compiled["h3_execution_profile"] == "clean_dialogue_plate_silent_v1"


def test_h3_plain_dialogue_uses_silent_ascii_plate_and_keeps_business_lineage():
    compiled = compile_h3_plan(
        {
            "creative_goal": "成年情侣居家对话投流钩子",
            "audience": "成年人",
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "recommended_params": MEDIA_DEFAULT_PRESET,
            "subtitle_policy": "forbid_burned_in_text",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "双人中景",
                "camera_command": "[Static shot]",
                "subject": "居家客厅的一对成年情侣",
                "action": "女生说‘宝，今晚早点回来’，男生自然回应",
                "scene": "现代居家客厅",
                "audio": "女：宝，今晚早点回来。",
            }],
            "performance_timeline": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "speaker": "女",
                "line": "宝，今晚早点回来。",
                "speaker_action": "自然说话",
                "listener_reaction": "男生保持呼吸和细小表情",
            }],
            "audio_prompt": "女：宝，今晚早点回来。",
            "negative_constraints": ["不得出现字幕、文字或水印"],
            "reference_roles": [],
            "brand_guardrails": {},
            "warnings": [],
        },
        brief={
            "script": "女：宝，今晚早点回来。",
            "audio_enabled": True,
            "script_timing": {"shot_script": "女：宝，今晚早点回来。"},
            "contains_person": True,
            "request": "同一对成年情侣自然轮流说话，不得出现字幕、文字或水印。",
        },
    )

    prompt = compiled["integrated_multimodal_description"]
    assert compiled["h3_execution_profile"] == "clean_dialogue_plate_silent_v1"
    assert compiled["audio_prompt"] == ""
    assert compiled["requested_audio_prompt"] == "女：宝，今晚早点回来。"
    assert compiled["recommended_params"]["audio_enabled"] is False
    assert compiled["dialogue_delivery"]["status"] == "voiceover_renderer_required"
    assert "CLEAN TWO-PERSON LIVE-ACTION PLATE" in prompt
    assert "宝" not in prompt
    assert "示例品牌" not in prompt
    assert prompt.isascii()


def test_ad_material_dialogue_preserves_cast_and_locks_named_cafe_scene():
    brief = {
        "product": "示例品牌 AIR / 铂金",
        "script": "左边女生：哎，你用过专为女性设计的套套吗？\n右边女生：真的有这种套套吗？",
        "request": (
            "轻奢落地窗咖啡馆，两位明确成年女性朋友面对面自然对话；"
            "一镜到底，不展示商品、包装、Logo、价格或文字。"
        ),
        "duration_seconds": 5,
        "audio_enabled": True,
    }
    contract = parse_ad_material_brief(brief, [], None, inferred_mode="text_to_video")
    plan = apply_ad_material_contract_to_plan(
        {
            "creative_goal": "真实工作表双人对话投流钩子",
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "recommended_params": MEDIA_DEFAULT_PRESET,
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 3.2,
                "framing": "双人中景",
                "camera_command": "[Static shot]",
                "subject": "一位成年女性和一位成年男性",
                "action": "左边女生自然发问，右边女生停顿后回应",
                "scene": "轻奢落地窗咖啡馆，桌面位于两人之间",
                "audio": brief["script"],
            }, {
                "start_seconds": 3.2,
                "end_seconds": 5,
                "framing": "双人中景",
                "camera_command": "[Static shot]",
                "subject": "一位成年女性和一位成年男性",
                "action": "右侧人物回应",
                "scene": "轻奢落地窗咖啡馆，桌面位于两人之间",
                "audio": brief["script"],
            }],
            "reference_roles": [],
            "negative_constraints": [],
            "brand_guardrails": {"require_reference_image": False},
            "warnings": [],
        },
        contract,
    )
    compiled = compile_h3_plan(plan, brief=brief)
    prompt = compiled["integrated_multimodal_description"]

    assert contract["policy_version"] == AD_MATERIAL_POLICY_VERSION == "frontdesk-ad-material-v21"
    assert contract["cast_market"] == "mainland_china"
    assert contract["face_style"] == "authentic_live_action"
    assert contract["actor_count"] == 2
    assert "两位明确不同的成年女性" in plan["shots"][0]["subject"]
    assert "男性" not in plan["shots"][0]["subject"]
    assert compiled["prompt_policy_version"] == "minimax-h3-context-ir-v60"
    assert compiled["h3_execution_profile"] == "clean_dialogue_plate_silent_v1"
    assert "two unrelated adult women" in prompt
    assert "an adult man" not in prompt
    assert len(prompt) <= H3_PROMPT_MAX_CHARS
    assert "SHOT TIMELINE" in prompt
    assert "floor-to-ceiling window" in prompt
    assert "cafe table" in prompt
    assert "CLEAN TWO-PERSON LIVE-ACTION PLATE" in prompt
    assert "waist-up medium two-shot" in prompt
    assert "one eye-level portrait camera" in prompt
    assert "VISIBLE COMMERCIAL APPEARANCE" in prompt
    assert "real pores" in prompt.casefold()
    assert "natural 50mm full-frame field of view" in prompt
    assert "each neck, torso, elbow and waist is anatomically continuous" in prompt
    assert "WARDROBE -" in prompt
    assert "muted sage collarless knit top" in prompt
    assert "charcoal-blue crew-neck knit top" in prompt
    assert "colors, silhouettes and fabric weave remain visibly different" in prompt
    assert "lavalier" not in prompt.casefold()
    assert "microphone" not in prompt.casefold()
    assert "caption" not in prompt.casefold()
    assert "subtitle" not in prompt.casefold()
    assert "GAZE -" in prompt
    assert "ENVIRONMENT GEOMETRY -" in prompt
    assert "BODY SUPPORT -" in prompt
    assert "COMPOSITION -" in prompt
    assert "left adult stands or sits nearer at x=32 percent" in prompt
    assert "IDENTITY CONTINUITY -" in prompt
    assert "EXPRESSION ARC -" in prompt
    assert "STAGGERED REACTION -" in prompt
    assert "short straight black bob" not in prompt
    assert "individualized, recognizable facial structure" in prompt
    assert "long black hair" not in prompt
    assert "refined natural commercial makeup" in prompt
    assert "visible pedestal" in prompt
    assert "both faces remain complementary inward three-quarter views" in prompt
    assert contract["dialogue_continuity_lock"]["checkpoints_percent"] == [40, 60, 80, 100]
    assert contract["dialogue_continuity_lock"]["version"] == "inward-partner-orientation-v1"
    assert "MID-TO-END ORIENTATION LOCK" in prompt
    assert "FINAL-FRAME SETTLE" in prompt
    assert "at 40, 60, 80 percent" in prompt
    assert "final twenty percent" in prompt
    assert "crossed partner eye-lines" in prompt
    assert "torso settles" in prompt
    assert "FRAMING LOCK" in prompt
    assert "fixed waist-up medium two-shot [Static shot]" in prompt
    assert "[Push in]" not in prompt
    assert "partner keeps both hands supported below the lower canvas boundary" in prompt
    assert "camera about 2.6 meters away" in prompt
    assert "空手" not in contract["opening_visual_hook"]["speaker_action"]
    assert "肩线" in contract["opening_visual_hook"]["speaker_action"]
    assert "shoulder rotation" in prompt.casefold()
    for unsafe_speech_cue in ("speaking", "spoken", "question", "dialogue", "conversation", "listens"):
        assert unsafe_speech_cue not in prompt.casefold()
    assert len(prompt) <= 7000
    assert "先先" not in plan["shots"][0]["action"]
    assert "右边女生" not in plan["shots"][0]["audio"]
    assert "左边女生" not in plan["shots"][1]["audio"]
    assert "两位明确不同的成年女性" in plan["shots"][1]["subject"]
    for harmful_negative_concept in ("split screen", "mirrored pair", "central symmetry boundary", "vertical architectural edge"):
        assert harmful_negative_concept not in prompt.casefold()
    assert prompt.isascii()


def test_server_audio_gate_cannot_be_reenabled_by_a_stale_workbench_client():
    params = _apply_audio_delivery_gate(
        {"audio_enabled": True, "width": 480, "height": 864},
        {"dialogue_delivery": {"visual_audio_enabled": False}},
    )
    assert params["audio_enabled"] is False
    assert params["width"] == 480
    assert _apply_audio_delivery_gate(
        {"audio_enabled": True}, {"dialogue_delivery": {}}
    )["audio_enabled"] is True


def test_project_mp4_duration_probe_falls_back_without_ffprobe(tmp_path, monkeypatch):
    from app.projects import service as project_service

    def box(kind, payload):
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    mvhd = (
        b"\x00\x00\x00\x00"
        + (0).to_bytes(4, "big") * 2
        + (1000).to_bytes(4, "big")
        + (5167).to_bytes(4, "big")
    )
    video = tmp_path / "reference.mp4"
    video.write_bytes(box(b"moov", box(b"mvhd", mvhd)))
    monkeypatch.setattr(project_service.shutil, "which", lambda _name: None)

    probe = project_service._probe_project_media_file(video, "video/mp4")

    assert probe["status"] == "ok"
    assert probe["source"] == "server_mp4_parser"
    assert probe["duration_seconds"] == 5.167


def test_h3_explicit_abstract_brief_stays_text_to_video():
    plan = {
        "h3_mode": "text_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "深蓝丝绸", "camera_command": "[Push in]"}],
        "reference_roles": [{"role": "first_frame", "purpose": "模型误建议的产品图"}],
        "brand_guardrails": {"require_reference_image": False},
        "warnings": [],
    }
    compiled = compile_h3_plan(
        plan,
        brief={"request": "抽象光影，无品牌、无包装、无 Logo、无文字"},
    )
    assert compiled["h3_mode"] == "text_to_video"
    assert compiled["reference_roles"] == []
    assert "<Picture" not in compiled["integrated_multimodal_description"]


def test_h3_compiler_strengthens_abstract_nonhuman_shape_and_shot_boundaries():
    plan = {
        "h3_mode": "text_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [
            {"start_seconds": 0, "end_seconds": 2, "subject": "深蓝液态丝绸", "camera_command": "[Static shot]"},
            {"start_seconds": 2, "end_seconds": 5, "subject": "金色光带与抽象流体", "camera_command": "[Pull out]"},
        ],
        "negative_constraints": ["不得出现品牌、包装、文字、人物或身体部位"],
        "reference_roles": [],
        "brand_guardrails": {"require_reference_image": False},
        "warnings": [],
    }

    compiled = compile_h3_plan(plan, brief={"request": "不要品牌、Logo、文字、人物或身体部位的抽象蓝金丝绸光影"})

    assert compiled["prompt_policy_version"] == H3_PROMPT_POLICY_VERSION
    assert any("抽象主体必须保持非人体、非生物、非解剖形态" in item for item in compiled["negative_constraints"])
    assert "At 2.000s, establish a clearly visible shot boundary" in compiled["integrated_multimodal_description"]
    assert any("非人体" in warning for warning in compiled["warnings"])


def test_h3_compiler_preserves_explicit_continuous_take_without_hard_cut():
    plan = {
        "h3_mode": "reference_replay",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [
            {
                "start_seconds": 0,
                "end_seconds": 2.5,
                "subject": "同一对成年男女坐在暖色客厅沙发上",
                "action": "女子转向男子开始对话",
                "scene": "同一间暖色客厅",
                "camera_command": "[Push in]",
            },
            {
                "start_seconds": 2.5,
                "end_seconds": 5,
                "subject": "同一对成年男女和桌上的商品包装",
                "action": "镜头连续跟随女子的手移动到桌面商品",
                "scene": "同一间暖色客厅",
                "camera_command": "[Tracking shot]",
            },
        ],
        "reference_roles": [
            {"role": "reference_image", "purpose": "成年人物与客厅场景"},
            {"role": "reference_image", "purpose": "商品包装外观"},
        ],
        "negative_constraints": [],
        "warnings": [],
    }

    compiled = compile_h3_plan(
        plan,
        brief={"requirement": "整个 5 秒保持同一场景连续镜头，不得硬切，人物身份和光线连续。"},
    )

    prompt = compiled["integrated_multimodal_description"]
    assert compiled["continuous_take"] is True
    assert "At 2.500s, continue the same uncut take" in prompt
    assert "establish a clearly visible shot boundary" not in prompt
    assert "no hard cut, teleport, identity change, or lighting reset" in prompt


def test_h3_compiler_defaults_short_people_i2v_to_one_continuous_take():
    plan = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [
            {
                "start_seconds": 0,
                "end_seconds": 2.5,
                "subject": "同一对成年男女",
                "action": "男子试探靠近，女子冷淡后退",
                "scene": "同一间咖啡馆",
                "camera_command": "[Static shot]",
            },
            {
                "start_seconds": 2.5,
                "end_seconds": 5,
                "subject": "同一名成年女子",
                "action": "镜头平滑推近她的冷淡表情",
                "scene": "同一间咖啡馆",
                "camera_command": "[Push in]",
            },
        ],
        "reference_roles": [{"role": "first_frame", "purpose": "成年双人首帧"}],
        "negative_constraints": [],
        "warnings": [],
    }

    compiled = compile_h3_plan(
        plan,
        brief={"contains_person": True, "requirement": "5秒竖屏双人对话钩子，人物身份和场景稳定"},
    )

    prompt = compiled["integrated_multimodal_description"]
    assert compiled["continuous_take"] is True
    assert "At 2.500s, continue the same uncut take" in prompt
    assert "establish a clearly visible shot boundary" not in prompt
    assert any("人物首帧的 5 秒多段分镜" in warning for warning in compiled["warnings"])


def test_h3_compiler_keeps_requested_people_i2v_hard_cut():
    plan = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [
            {"start_seconds": 0, "end_seconds": 2.5, "subject": "成年情侣", "camera_command": "[Static shot]"},
            {"start_seconds": 2.5, "end_seconds": 5, "subject": "成年女子", "camera_command": "[Push in]"},
        ],
        "reference_roles": [{"role": "first_frame", "purpose": "成年双人首帧"}],
        "negative_constraints": [],
        "warnings": [],
    }

    compiled = compile_h3_plan(
        plan,
        brief={"contains_person": True, "requirement": "2.5秒明确硬切到单人近景"},
    )

    assert compiled["continuous_take"] is False
    assert "At 2.500s, establish a clearly visible shot boundary" in compiled["integrated_multimodal_description"]


def test_h3_batch_variants_are_unique_and_compiled_into_prompt():
    variants = [production_variant_for_candidate(index, "mvc-test") for index in range(1, 601)]
    assert len({item["variant_key"] for item in variants}) == 600
    plan = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "真实商品包装", "camera_command": "[Push in]"}],
        "reference_roles": [{"role": "first_frame", "purpose": "保持产品包装与 Logo 真实"}],
        "brand_guardrails": {"require_reference_image": True},
        "production_variant": variants[37],
        "warnings": [],
    }

    compiled = compile_h3_plan(
        plan,
        brief={
            "request": "使用真实商品图生成投流素材",
            "source_roles": [{"role": "product_packshot", "technical_role": "first_frame"}],
        },
    )

    prompt = compiled["integrated_multimodal_description"]
    assert f"Campaign variation {variants[37]['variant_key']}" in prompt
    assert "must be visibly different from adjacent candidates" in prompt
    assert "PRODUCT REFERENCE LOCK" in prompt
    assert "preserve the approved first-frame package geometry, scale, position" in prompt


def test_h3_campaign_variant_replaces_conflicting_legacy_abstract_shots():
    variant = production_variant_for_candidate(240, "mvc-legacy")
    plan = {
        "h3_mode": "text_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "creative_goal": "延续深蓝液态丝绸与金色光带形成的流动 S 形视觉",
        "shots": [
            {
                "start_seconds": 0,
                "end_seconds": 2.5,
                "subject": "深蓝色液态丝绸与暖金色光带",
                "action": "丝绸形成连续 S 形曲线",
                "scene": "纯色深蓝背景",
                "lighting": "暖金色轮廓光",
                "camera_command": "[Push in]",
            },
            {
                "start_seconds": 2.5,
                "end_seconds": 5,
                "subject": "同一蓝金丝绸主体",
                "action": "光带继续缠绕",
                "scene": "深蓝背景",
                "lighting": "金色逆光",
                "camera_command": "[Pull out]",
            },
        ],
        "negative_constraints": [
            "连续生产硬约束：不得沿用旧蓝金丝绸、金色光带、液态流体或单一 S 形轮廓",
            "不得出现人物或文字",
        ],
        "brand_guardrails": {"require_reference_image": False},
        "production_variant": variant,
        "warnings": [],
    }

    compiled = compile_h3_plan(plan, brief={"request": "无品牌、无产品、无人物的抽象视觉"})

    assert compiled["production_variant_application"]["mode"] == "replace_legacy_abstract_visual"
    assert compiled["production_variant_application"]["replaced_shot_count"] == 2
    assert "丝绸" not in compiled["creative_goal"]
    assert variant["layout_contract"] in compiled["creative_goal"]
    assert all("丝绸" not in shot["subject"] for shot in compiled["shots"])
    assert all(variant["palette"] == shot["lighting"] for shot in compiled["shots"])
    assert all(variant["composition"] in shot["scene"] for shot in compiled["shots"])
    prompt = compiled["integrated_multimodal_description"]
    assert "HARD EXECUTION OVERRIDE" in prompt
    assert "深蓝色液态丝绸" not in prompt
    assert "金色光带" not in prompt
    assert "S-shaped" not in prompt
    assert "不得出现人物或文字" in prompt
    assert "positive layout contract=" in prompt
    assert "Do not arrange dots or bars along curves" in prompt
    assert any("替换旧顶层创意目标" in warning for warning in compiled["warnings"])


def test_bridge_releases_cached_media_memory_only_when_queue_is_idle(monkeypatch):
    from bridge import skillforgebridge

    calls = []

    def fake_media_http_json(method, path, payload=None, timeout=0):
        calls.append((method, path, payload))
        if path == "/queue":
            return {"queue_running": [], "queue_pending": []}
        return {}

    monkeypatch.setattr(skillforgebridge, "_media_http_json", fake_media_http_json)
    assert skillforgebridge._media_release_idle_memory() is True
    assert calls[-1] == (
        "POST",
        "/free",
        {"unload_models": True, "free_memory": True},
    )

    calls.clear()

    def busy_media_http_json(method, path, payload=None, timeout=0):
        calls.append((method, path, payload))
        return {"queue_running": [[0, "prompt-1"]], "queue_pending": []}

    monkeypatch.setattr(skillforgebridge, "_media_http_json", busy_media_http_json)
    assert skillforgebridge._media_release_idle_memory() is False
    assert calls == [("GET", "/queue", None)]


def test_bridge_capability_heartbeat_rate_limits_idle_media_release(monkeypatch):
    from bridge import skillforgebridge

    releases = []
    monotonic_values = iter((100.0, 120.0, 161.0))
    monkeypatch.setattr(skillforgebridge.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        skillforgebridge,
        "_media_release_idle_memory",
        lambda: releases.append("release") or True,
    )
    monkeypatch.setattr(skillforgebridge, "_MEDIA_IDLE_RELEASE_NEXT_ALLOWED_MONOTONIC", 0.0)
    gpu = [{"vram_used_mb": skillforgebridge.MEDIA_IDLE_RELEASE_VRAM_THRESHOLD_MB + 1}]

    assert skillforgebridge._media_maybe_release_idle_memory(0, gpu) is True
    assert skillforgebridge._media_maybe_release_idle_memory(0, gpu) is False
    assert skillforgebridge._media_maybe_release_idle_memory(0, gpu) is True
    assert releases == ["release", "release"]


def test_bridge_idle_media_release_skips_busy_or_low_vram_nodes(monkeypatch):
    from bridge import skillforgebridge

    monkeypatch.setattr(
        skillforgebridge,
        "_media_release_idle_memory",
        lambda: pytest.fail("idle release must not run for busy or low-VRAM nodes"),
    )
    monkeypatch.setattr(skillforgebridge, "_MEDIA_IDLE_RELEASE_NEXT_ALLOWED_MONOTONIC", 0.0)

    assert skillforgebridge._media_maybe_release_idle_memory(1, [{"vram_used_mb": 15000}]) is False
    assert skillforgebridge._media_maybe_release_idle_memory(0, [{"vram_used_mb": 500}]) is False


@pytest.mark.asyncio
async def test_reopened_project_run_can_review_earlier_project_job():
    from app.media import service

    job = _job("awaiting_review")
    job.project_run_id = "older-run"

    class FakeDb:
        async def get(self, _model, _job_id):
            return job

    reopened_run = SimpleNamespace(id="new-run", project_id="samplebrand-material-workbench")
    assert await service._get_job_for_run(FakeDb(), reopened_run, job.id) is job

    with pytest.raises(AppError) as exc:
        await service._get_job_for_run(
            FakeDb(),
            SimpleNamespace(id="other-run", project_id="other-project"),
            job.id,
        )
    assert exc.value.code == "MEDIA_JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_continuous_batch_builds_deterministic_serial_jobs(monkeypatch):
    from app.media import service

    submitted = []

    async def fake_submit(_db, _user, _project, _run, payload):
        submitted.append(payload)
        index = len(submitted)
        return {
            "deduped": False,
            "job": {"id": f"job-{index}", "status": "assigned" if index == 1 else "queued"},
        }

    monkeypatch.setattr(service, "_submit_job", fake_submit)
    target = (service.now_bjt() + timedelta(hours=1)).isoformat()
    result = await service._batch_submit_jobs(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1"),
        SimpleNamespace(id="run-1"),
        {
            "candidate_count": 3,
            "target_end_at": target,
            "mode": "text_to_video",
            "final_plan": {"h3_mode": "text_to_video"},
            "params": {"seed": 100},
        },
    )

    assert result["candidate_count"] == 3
    assert result["assigned_count"] == 1
    assert result["queued_count"] == 2
    assert [item["params"]["seed"] for item in submitted] == [100, 101, 102]
    assert len({item["idempotency_key"] for item in submitted}) == 3
    assert all(item["params"]["batch_count"] == 1 for item in submitted)
    assert all(item["final_plan"]["production_campaign"]["target_end_at"] == target for item in submitted)
    assert [item["final_plan"]["production_variant"]["candidate_index"] for item in submitted] == [1, 2, 3]
    assert len({item["final_plan"]["production_variant"]["variant_key"] for item in submitted}) == 3


@pytest.mark.asyncio
async def test_continuous_batch_rejects_unbounded_candidate_count():
    from app.media import service

    with pytest.raises(AppError) as exc:
        await service._batch_submit_jobs(
            SimpleNamespace(),
            SimpleNamespace(id="user-1"),
            SimpleNamespace(id="project-1"),
            SimpleNamespace(id="run-1"),
            {"candidate_count": 601},
        )
    assert exc.value.code == "MEDIA_BATCH_INVALID"


@pytest.mark.asyncio
async def test_continuous_batch_cancel_stops_selected_campaigns_in_one_call(monkeypatch):
    from app.media import service

    selected_queued = _job("queued")
    selected_queued.id = "job-selected-queued"
    selected_queued.prompt_json = {"production_campaign": {"id": "mvc-selected"}}
    selected_running = _job("running")
    selected_running.id = "job-selected-running"
    selected_running.prompt_json = {"production_campaign": {"id": "mvc-selected"}}
    other_campaign = _job("queued")
    other_campaign.id = "job-other"
    other_campaign.prompt_json = {"production_campaign": {"id": "mvc-other"}}
    ordinary_job = _job("queued")
    ordinary_job.id = "job-ordinary"
    ordinary_job.prompt_json = {}

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [selected_queued, selected_running, other_campaign, ordinary_job]

    class FakeDb:
        async def execute(self, _query):
            return Result()

    cancelled = []

    async def fake_cancel(_db, row):
        cancelled.append(row.id)
        transition_media_job(row, "cancelled")
        return {"bridge_cancelled": row.id.endswith("running"), "bridge_error": ""}

    async def fake_latest_attempt(_db, _job_id):
        return None

    monkeypatch.setattr(service, "_cancel_job_row", fake_cancel)
    monkeypatch.setattr(service, "_latest_attempt", fake_latest_attempt)

    result = await service._batch_cancel_jobs(
        FakeDb(),
        SimpleNamespace(project_id="samplebrand-material-workbench"),
        {"campaign_ids": ["mvc-selected"]},
    )

    assert cancelled == ["job-selected-queued", "job-selected-running"]
    assert result["campaign_ids"] == ["mvc-selected"]
    assert result["selected_count"] == 2
    assert result["cancelled_count"] == 2
    assert result["bridge_cancelled_count"] == 1
    assert other_campaign.status == "queued"
    assert ordinary_job.status == "queued"


@pytest.mark.asyncio
async def test_continuous_batch_cancel_requires_explicit_scope():
    from app.media import service

    with pytest.raises(AppError) as exc:
        await service._batch_cancel_jobs(
            SimpleNamespace(),
            SimpleNamespace(project_id="samplebrand-material-workbench"),
            {},
        )
    assert exc.value.code == "MEDIA_BATCH_CANCEL_INVALID"


def test_legacy_queued_campaign_prompt_is_upgraded_before_gpu_dispatch():
    from app.media import service

    job = _job("queued")
    job.prompt_json = {
        "h3_mode": "text_to_video",
        "duration_seconds": 5,
        "ratio": "9:16",
        "prompt_policy_version": "minimax-h3-context-ir-v3",
        "shots": [
            {
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "特写",
                "camera_command": "[Static shot]",
                "subject": "多个非生物几何块面",
                "action": "分层移动并形成清晰负空间",
                "scene": "深色摄影棚",
                "lighting": "冷白轮廓光",
                "mood": "高级克制",
                "audio": "低频节奏",
            }
        ],
        "negative_constraints": ["不得出现人物或身体部位"],
        "production_campaign": {
            "id": "mvc-legacy",
            "candidate_index": 38,
            "candidate_count": 600,
            "target_end_at": "2026-08-13T09:00:00+08:00",
        },
    }

    assert service._upgrade_queued_campaign_prompt(job) is True
    assert job.prompt_json["prompt_policy_version"] == H3_PROMPT_POLICY_VERSION
    assert job.prompt_json["production_variant"]["candidate_index"] == 38
    assert job.prompt_json["production_variant"]["variant_key"] == "c2-p2-t2-m1"
    assert "Campaign variation c2-p2-t2-m1" in job.prompt_json["integrated_multimodal_description"]
    assert job.result_json["prompt_upgrade"]["from"] == "minimax-h3-context-ir-v3"
    assert job.result_json["prompt_upgrade"]["to"] == H3_PROMPT_POLICY_VERSION
    assert service._upgrade_queued_campaign_prompt(job) is False


def test_seed_only_campaign_prompt_is_not_polluted_by_layout_variant():
    from app.media import service

    job = _job("queued")
    job.prompt_json = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "ratio": "9:16",
        "prompt_policy_version": H3_PROMPT_POLICY_VERSION,
        "shots": [
            {
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "中景",
                "camera_command": "[Static shot]",
                "subject": "同一对明确成年人",
                "action": "自然对视后女方看向镜头说一句口播",
                "scene": "暖色客厅，桌面小包装保持为背景静物",
                "lighting": "柔和暖光",
                "mood": "轻松自然",
                "audio": "人物对话清晰",
            }
        ],
        "production_campaign": {
            "id": "mpb-people",
            "candidate_index": 1,
            "candidate_count": 2,
            "variation_policy": "seed_only",
        },
    }

    original = dict(job.prompt_json)
    assert service._upgrade_queued_campaign_prompt(job) is False
    assert job.prompt_json == original
    assert "production_variant" not in job.prompt_json


def test_continuous_campaign_deadline_is_detected_without_affecting_ordinary_jobs():
    from app.media import service

    cutoff = service.now_bjt()
    queued = _job("queued")
    queued.prompt_json = {
        "production_campaign": {
            "id": "mvc-cutoff",
            "target_end_at": (cutoff - timedelta(seconds=1)).isoformat(),
        }
    }
    ordinary = _job("queued")

    assert service._continuous_campaign_expired(queued, current_time=cutoff) is True
    assert service._continuous_campaign_expired(ordinary, current_time=cutoff) is False


@pytest.mark.asyncio
async def test_scheduler_cancels_expired_queued_campaign_before_dispatch(monkeypatch):
    from app.media import service

    queued = _job("queued")
    queued.prompt_json = {
        "production_campaign": {
            "target_end_at": (service.now_bjt() - timedelta(seconds=1)).isoformat(),
        }
    }

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [queued]

    class FakeDb:
        commits = 0

        async def execute(self, _query):
            return Result()

        async def commit(self):
            self.commits += 1

        async def get(self, *_args):
            raise AssertionError("expired queued jobs must stop before lookup or dispatch")

    db = FakeDb()
    stats = await service.process_media_jobs_once(db)

    assert stats == {"scanned": 1, "advanced": 1, "failed": 0}
    assert queued.status == "cancelled"
    assert queued.error == "连续生产截止时间已到，未开始任务已自动取消。"
    assert db.commits == 1


def test_continuous_campaign_cutoff_normalizes_timezone_to_beijing():
    from app.media import service

    job = _job("queued")
    job.prompt_json = {
        "production_campaign": {
            "target_end_at": "2026-08-12T01:00:00Z",
        }
    }
    assert service._continuous_campaign_deadline(job).isoformat() == "2026-08-12T09:00:00"
    assert service._continuous_campaign_expired(
        job,
        current_time=service.parse_bjt_datetime("2026-08-12T09:00:01+08:00"),
    ) is True


def test_h3_compiler_repairs_overlapping_timeline_without_zero_length_shots():
    plan = {
        "h3_mode": "text_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [
            {"start_seconds": 0, "end_seconds": 5, "subject": "A"},
            {"start_seconds": 4, "end_seconds": 5, "subject": "B"},
        ],
    }
    compiled = compile_h3_plan(plan)
    assert compiled["shots"][0]["start_seconds"] == 0
    assert compiled["shots"][0]["end_seconds"] == 2.5
    assert compiled["shots"][1]["start_seconds"] == 2.5
    assert compiled["shots"][1]["end_seconds"] == 5
    assert all(item["end_seconds"] > item["start_seconds"] for item in compiled["shots"])


def test_h3_compiler_enforces_local_reference_mode_contract():
    base = {
        "h3_mode": "image_to_video",
        "duration_seconds": 5,
        "recommended_params": MEDIA_DEFAULT_PRESET,
        "shots": [{"start_seconds": 0, "end_seconds": 5, "subject": "产品"}],
        "reference_roles": [
            {"role": "first_frame", "purpose": "场景构图"},
            {"role": "reference_image", "purpose": "真实产品包装"},
        ],
        "warnings": [],
    }
    image_plan = compile_h3_plan(
        base,
        brief={
            "request": "包装必须真实",
            "source_roles": [{"role": "product_packshot", "technical_role": "first_frame"}],
        },
    )
    assert image_plan["reference_roles"] == [{"role": "first_frame", "purpose": "真实产品包装"}]
    assert image_plan["integrated_multimodal_description"].count("<Picture") == 1
    assert any("只接受一张首帧" in item for item in image_plan["warnings"])

    text_plan = compile_h3_plan(
        {**base, "h3_mode": "text_to_video"},
        brief={"request": "只生成抽象亲密氛围"},
    )
    assert text_plan["reference_roles"] == []
    assert "<Picture" not in text_plan["integrated_multimodal_description"]


@pytest.mark.asyncio
async def test_h3_reference_roles_sizes_durations_and_mode_are_server_validated():
    from app.media.service import _normalize_reference_requests, _validated_reference_requests

    assets = {
        "img": SimpleNamespace(
            id="img", project_run_id="run", mime_type="image/png", byte_size=1024,
            metadata_json={},
        ),
        "vid": SimpleNamespace(
            id="vid", project_run_id="run", mime_type="video/mp4", byte_size=1024,
            metadata_json={"media_probe": {"status": "ok", "source": "server_ffprobe", "duration_seconds": 5}},
        ),
    }

    class FakeDb:
        async def get(self, _model, asset_id):
            return assets.get(asset_id)

    run = SimpleNamespace(id="run")
    image = _normalize_reference_requests([{"asset_id": "img", "role": "first_frame"}], mode="image_to_video")
    validated = await _validated_reference_requests(FakeDb(), run, image, mode="image_to_video")
    assert validated[0]["role"] == "first_frame"

    replay = _normalize_reference_requests([{"asset_id": "vid", "role": "reference_video"}], mode="reference_replay")
    validated = await _validated_reference_requests(FakeDb(), run, replay, mode="reference_replay")
    assert validated[0]["duration_seconds"] == 5

    with pytest.raises(AppError) as exc:
        await _validated_reference_requests(FakeDb(), run, replay, mode="image_to_video")
    assert exc.value.code == "MEDIA_REFERENCE_INVALID"


@pytest.mark.asyncio
async def test_text_to_video_accepts_only_governed_product_overlay_reference():
    from app.media.service import _normalize_reference_requests, _validated_reference_requests

    asset = SimpleNamespace(
        id="packshot",
        project_run_id="run",
        mime_type="image/png",
        byte_size=2048,
        sha256="verified-packshot-sha",
        metadata_json={
            "business_role": "product_packshot",
            "product_overlay_transparency_probe": {
                "policy_version": "product-overlay-alpha-v1",
                "sha256": "verified-packshot-sha",
                "passed": True,
                "alpha_min": 0,
                "alpha_max": 255,
                "detail": "verified_non_empty_alpha_plane",
            },
        },
    )

    class FakeDb:
        async def get(self, _model, asset_id):
            return asset if asset_id == "packshot" else None

    run = SimpleNamespace(id="run")
    normalized = _normalize_reference_requests(
        [{"asset_id": "packshot", "role": "overlay_image", "business_role": "product_packshot"}],
        mode="text_to_video",
    )
    validated = await _validated_reference_requests(FakeDb(), run, normalized, mode="text_to_video")
    assert validated[0]["role"] == "overlay_image"
    assert validated[0]["business_role"] == "product_packshot"

    invalid = _normalize_reference_requests(
        [{"asset_id": "packshot", "role": "overlay_image", "business_role": "visual_reference"}],
        mode="text_to_video",
    )
    with pytest.raises(AppError) as exc:
        await _validated_reference_requests(FakeDb(), run, invalid, mode="text_to_video")
    assert exc.value.code == "MEDIA_REFERENCE_ROLE_INVALID"


@pytest.mark.asyncio
async def test_governed_product_overlay_accepts_packshot_detail_pair_and_rejects_ambiguous_pair():
    from app.media.service import _normalize_reference_requests, _validated_reference_requests

    def product_asset(asset_id, business_role):
        sha = (asset_id[-1] * 64)[:64]
        return SimpleNamespace(
            id=asset_id,
            project_run_id="run",
            mime_type="image/png",
            byte_size=2048,
            sha256=sha,
            metadata_json={
                "business_role": business_role,
                "product_overlay_transparency_probe": {
                    "policy_version": "product-overlay-alpha-v1",
                    "sha256": sha,
                    "passed": True,
                    "alpha_min": 0,
                    "alpha_max": 255,
                    "detail": "verified_non_empty_alpha_plane",
                },
            },
        )

    assets = {
        "pack-a": product_asset("pack-a", "product_packshot"),
        "pack-b": product_asset("pack-b", "product_packshot"),
        "detail-c": product_asset("detail-c", "product_detail"),
    }

    class FakeDb:
        async def get(self, _model, asset_id):
            return assets.get(asset_id)

    run = SimpleNamespace(id="run")
    governed_pair = _normalize_reference_requests(
        [
            {"asset_id": "pack-a", "role": "overlay_image", "business_role": "product_packshot"},
            {"asset_id": "detail-c", "role": "overlay_image", "business_role": "product_detail"},
        ],
        mode="text_to_video",
    )
    validated = await _validated_reference_requests(FakeDb(), run, governed_pair, mode="text_to_video")
    assert [item["business_role"] for item in validated] == ["product_packshot", "product_detail"]

    ambiguous_pair = _normalize_reference_requests(
        [
            {"asset_id": "pack-a", "role": "overlay_image", "business_role": "product_packshot"},
            {"asset_id": "pack-b", "role": "overlay_image", "business_role": "product_packshot"},
        ],
        mode="text_to_video",
    )
    with pytest.raises(AppError) as exc:
        await _validated_reference_requests(FakeDb(), run, ambiguous_pair, mode="text_to_video")
    assert exc.value.code == "MEDIA_REFERENCE_ROLE_INVALID"
    assert "1 张包装图和 1 张商品细节图" in exc.value.detail["detail"]


def test_product_overlay_alpha_probe_rejects_opaque_png_before_queue(monkeypatch, tmp_path):
    from app.media import service

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Requested planes not available"

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(service, "_media_quality_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(service.subprocess, "run", fake_run)
    path = tmp_path / "opaque-product.png"
    path.write_bytes(b"opaque")

    probe = service._probe_product_overlay_transparency(path)

    assert probe == {
        "policy_version": "product-overlay-alpha-v1",
        "passed": False,
        "alpha_min": None,
        "alpha_max": None,
        "detail": "alpha_plane_unavailable",
    }
    assert "alphaextract,signalstats" in calls[0][0][calls[0][0].index("-vf") + 1]


def test_bridge_detects_visible_product_bounds_from_alpha_plane(monkeypatch, tmp_path):
    from bridge import skillforgebridge as bridge

    product = tmp_path / "wide-transparent-canvas.png"
    product.write_bytes(b"transparent-product")
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = (
            "[Parsed_cropdetect_1] crop=1336:2356:576:72\n"
            "[Parsed_cropdetect_1] crop=1338:2358:576:72\n"
        )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    crop = bridge._media_detect_product_alpha_crop(product, ffmpeg="/usr/bin/ffmpeg")

    assert crop == {"width": 1338, "height": 2358, "x": 576, "y": 72}
    command = calls[0][0]
    assert command[command.index("-vf") + 1] == "alphaextract,cropdetect=limit=0.01:round=2:reset=0"
    assert command[command.index("-frames:v") + 1] == "3"


@pytest.mark.asyncio
async def test_overlay_reference_with_cached_opaque_probe_is_rejected_actionably():
    from app.media.service import _normalize_reference_requests, _validated_reference_requests

    asset = SimpleNamespace(
        id="opaque-packshot",
        project_run_id="run",
        mime_type="image/png",
        byte_size=2048,
        sha256="opaque-sha",
        metadata_json={
            "product_overlay_transparency_probe": {
                "policy_version": "product-overlay-alpha-v1",
                "sha256": "opaque-sha",
                "passed": False,
                "detail": "alpha_plane_unavailable",
            }
        },
    )

    class FakeDb:
        async def get(self, _model, asset_id):
            return asset if asset_id == asset.id else None

    normalized = _normalize_reference_requests(
        [{"asset_id": asset.id, "role": "overlay_image", "business_role": "product_packshot"}],
        mode="text_to_video",
    )
    with pytest.raises(AppError) as exc:
        await _validated_reference_requests(FakeDb(), SimpleNamespace(id="run"), normalized, mode="text_to_video")

    assert exc.value.code == "MEDIA_PRODUCT_OVERLAY_TRANSPARENCY_REQUIRED"
    assert "透明 PNG/WebP" in exc.value.detail["detail"]


def test_scheduler_prefers_5080_for_standard_and_pro6000_for_reference(monkeypatch):
    from app.media import service

    monkeypatch.setattr(service.bridge_registry, "is_online", lambda _instance_id: True)

    def instance(instance_id, gpu_name, free_mb, queue_depth=0):
        caps = {
            "ops": ["media.submit_job"],
            "workload_roles": ["video_generation"],
            "gpu": [{"name": gpu_name, "vram_free_mb": free_mb}],
            "media": {"supported_modes": ["text_to_video", "image_to_video", "reference_replay", "video_enhance"], "queue_depth": queue_depth},
        }
        return SimpleNamespace(
            id=instance_id,
            name=gpu_name,
            is_active=True,
            is_platform_default=True,
            agent_purpose="media" if "5080" in gpu_name else "mixed",
            bridge_capabilities_json=json.dumps(caps),
        )

    rtx5080 = instance("media-5080", "NVIDIA GeForce RTX 5080", 15000)
    pro6000 = instance("data-primary", "NVIDIA RTX PRO 6000 Blackwell", 56000)
    standard = normalize_media_params({})
    reference = normalize_media_params({"frames": 192, "batch_count": 2})

    assert _score_media_instance(rtx5080, mode="text_to_video", params=standard)[0] > _score_media_instance(pro6000, mode="text_to_video", params=standard)[0]
    assert _score_media_instance(rtx5080, mode="text_to_video", params=standard, active_jobs=1) is None
    assert _score_media_instance(pro6000, mode="reference_replay", params=reference)[0] > 0
    assert _score_media_instance(rtx5080, mode="reference_replay", params=reference) is None
    enhance = {"width": 1080, "height": 1920, "frames": 192, "fps": 24, "steps": 4, "batch_count": 1}
    assert _score_media_instance(rtx5080, mode="video_enhance", params=enhance)[0] > _score_media_instance(pro6000, mode="video_enhance", params=enhance)[0]
    rtx5080.bridge_capabilities_json = json.dumps({
        "ops": ["media.submit_job"],
        "workload_roles": ["video_generation"],
        "media": {"configured": True, "supported_modes": []},
        "gpu": [{"name": "NVIDIA GeForce RTX 5080", "vram_free_mb": 15000}],
    })
    assert _score_media_instance(rtx5080, mode="text_to_video", params=standard) is None


def test_scheduler_requires_real_product_overlay_runtime(monkeypatch):
    from app.media import service

    monkeypatch.setattr(service.bridge_registry, "is_online", lambda _instance_id: True)
    caps = {
        "ops": ["media.submit_job"],
        "workload_roles": ["video_generation"],
        "gpu": [{"name": "NVIDIA RTX PRO 6000 Blackwell", "vram_free_mb": 56000}],
        "media": {
            "supported_modes": ["text_to_video"],
            "queue_depth": 0,
            "postprocess": {"governed_product_overlay": False},
        },
    }
    node = SimpleNamespace(
        id="data-primary",
        name="NVIDIA RTX PRO 6000 Blackwell",
        is_active=True,
        is_platform_default=False,
        agent_purpose="mixed",
        bridge_capabilities_json=json.dumps(caps),
    )

    assert _score_media_instance(
        node,
        mode="text_to_video",
        params=normalize_media_params({}),
        requires_product_overlay=True,
    ) is None
    caps["media"]["postprocess"]["governed_product_overlay"] = True
    node.bridge_capabilities_json = json.dumps(caps)
    assert _score_media_instance(
        node,
        mode="text_to_video",
        params=normalize_media_params({}),
        requires_product_overlay=True,
    ) is not None


def test_system_classifies_business_content_and_routes_quality_first(monkeypatch):
    from app.media import service
    from app.media.workbench_v2 import _infer_brief_content_flags

    monkeypatch.setattr(service.bridge_registry, "is_online", lambda _instance_id: True)

    def instance(instance_id, gpu_name, free_mb, purpose):
        return SimpleNamespace(
            id=instance_id,
            name=gpu_name,
            is_active=True,
            is_platform_default=purpose == "media",
            agent_purpose=purpose,
            bridge_capabilities_json=json.dumps({
                "ops": ["media.submit_job"],
                "workload_roles": ["video_generation"],
                "gpu": [{"name": gpu_name, "vram_free_mb": free_mb}],
                "media": {"supported_modes": ["text_to_video", "image_to_video"], "queue_depth": 0},
            }),
        )

    brief = {
        "product": "示例品牌 AIR",
        "script": "女：开了吗\n男：开了",
        "request": "成年情侣固定镜头自然对话",
    }
    assert classify_production_intent(brief, [], mode="text_to_video") == "people_dialogue"
    policy = production_policy_for(brief, [], mode="text_to_video")
    assert policy["routing_policy"] == "quality_first"
    pro = instance("data-primary", "NVIDIA RTX PRO 6000 Blackwell", 56000, "mixed")
    gpu5080 = instance("platform-media-5080", "NVIDIA GeForce RTX 5080", 15000, "media")
    pro_score = _score_media_instance(
        pro,
        mode="image_to_video",
        params=normalize_media_params({}),
        production_policy=policy,
        quality_profile={"sample_count": 5, "average_quality": 0.9, "success_rate": 1.0},
    )[0]
    gpu5080_score = _score_media_instance(
        gpu5080,
        mode="image_to_video",
        params=normalize_media_params({}),
        production_policy=policy,
        quality_profile={"sample_count": 5, "average_quality": 0.55, "success_rate": 1.0},
    )[0]
    assert gpu5080_score > pro_score
    assert _score_media_instance(
        gpu5080,
        mode="text_to_video",
        params=normalize_media_params({}),
        production_policy=policy,
    )[1]["routing"]["resource_preference"] == "standard_short_5080_primary"
    assert _score_media_instance(
        pro,
        mode="text_to_video",
        params=normalize_media_params({"frames": 192}),
        production_policy=policy,
    )[1]["routing"]["resource_preference"] == "pro6000_reference_long_batch_primary"
    assert classify_production_intent({}, [], mode="text_to_video") == "abstract_broll"
    assert classify_production_intent(
        {},
        [{"role": "first_frame", "business_role": "character_first_frame"}],
        mode="image_to_video",
    ) == "abstract_broll"
    assert classify_production_intent(
        {},
        [{"role": "first_frame", "business_role": "product_packshot"}],
        mode="image_to_video",
    ) == "product_packshot"
    assert classify_production_intent(
        {
            "request": "不新增人物、台词或配音，只换产品",
            "source_replication_contract": {
                "enabled": True,
                "source_people_presence": "partial_hands",
                "forbid_invented_people": True,
            },
        },
        [{"role": "reference_video", "business_role": "motion_reference"}],
        mode="reference_replay",
    ) == "reference_replay"
    assert classify_production_intent(
        {
            "product": "示例品牌超快感",
            "request": "使用透明商品图生成高级氛围 B-roll，不要人物、字幕、角标或额外文字。",
            "contains_person": True,
        },
        [{"role": "first_frame", "business_role": "product_packshot"}],
        mode="image_to_video",
    ) == "product_packshot"
    assert classify_production_intent(
        {"request": "无人物抽象 B-roll，不要商品元素、包装、Logo 或文字。"},
        [],
        mode="text_to_video",
    ) == "abstract_broll"
    assert classify_production_intent(
        {"request": "中国年轻情侣自然对话，不要字幕或角标。"},
        [],
        mode="text_to_video",
    ) == "people_dialogue"
    assert classify_production_intent(
        {"request": "自然室内生活镜头", "contains_person": True},
        [],
        mode="text_to_video",
    ) == "people_lifestyle"
    normalized_brief, applied_flags = _infer_brief_content_flags(
        {
            "request": "生成高级商品氛围 B-roll，不要人物、字幕、角标或额外文字。",
            "contains_person": True,
        },
        [{"role": "product_packshot", "technical_role": "first_frame"}],
    )
    assert normalized_brief["contains_person"] is False
    assert "contains_person_cleared_by_explicit_constraint" in applied_flags


def test_content_aware_quality_signal_and_review_repair_packet():
    analysis = {
        "scores": {
            "prompt_alignment": 2,
            "visual_continuity": 1,
            "hook_strength": 3,
            "shot_boundary_clarity": 2,
            "commercial_readiness": 1,
        },
        "overall_score": 0.4,
        "issues": ["人物身份在第 3 秒漂移"],
        "recommendation": {
            "decision": "retry_recommended",
            "reason": "人物连续性不足",
            "prompt_changes": ["保持同一人物身份和服装，不得硬切。"],
        },
    }
    assert quality_signal_for_intent(analysis, "people_dialogue") < 0.5
    job = _job("awaiting_review")
    job.output_preset_id = "quick_preview"
    job.result_json = {"production_policy": {"production_intent": "people_dialogue"}}
    repair = _build_media_repair_plan(
        job,
        [{"status": "completed", "analysis": analysis}],
    )
    assert repair["status"] == "retry_recommended"
    assert repair["automatic_submit"] is False
    assert repair["production_intent"] == "people_dialogue"
    assert "不得硬切" in repair["constraints"][0]


@pytest.mark.asyncio
async def test_transient_media_bridge_disconnect_waits_before_requeue(monkeypatch):
    from app.media import service

    job = _job("running")
    job.assigned_instance_id = "media-5080"
    attempt = SimpleNamespace(
        instance_id="media-5080",
        status="running",
        error=None,
        completed_at=None,
        metrics_json={},
    )
    current_time = datetime(2026, 8, 12, 0, 0, 0)

    async def latest_attempt(_db, _job_id):
        return attempt

    dispatches = []

    async def dispatch(*args):
        dispatches.append(args)

    monkeypatch.setattr(service, "_latest_attempt", latest_attempt)
    monkeypatch.setattr(service, "_dispatch_job", dispatch)
    monkeypatch.setattr(service.bridge_registry, "is_online", lambda _instance_id: False)
    monkeypatch.setattr(service, "now_bjt", lambda: current_time)

    await service._refresh_job(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), job)
    assert job.status == "running"
    assert job.error == service.MEDIA_BRIDGE_RECONNECT_WAIT_MESSAGE
    assert attempt.status == "running"
    assert attempt.metrics_json["bridge_disconnect_observed_at"].startswith("2026-08-12T00:00:00")
    assert dispatches == []

    monkeypatch.setattr(
        service,
        "now_bjt",
        lambda: current_time + timedelta(seconds=service.MEDIA_BRIDGE_DISCONNECT_GRACE_SECONDS - 1),
    )
    await service._refresh_job(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), job)
    assert job.status == "running"
    assert dispatches == []

    monkeypatch.setattr(
        service,
        "now_bjt",
        lambda: current_time + timedelta(seconds=service.MEDIA_BRIDGE_DISCONNECT_GRACE_SECONDS),
    )
    await service._refresh_job(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), job)
    assert job.status == "queued"
    assert attempt.status == "disconnected"
    assert attempt.completed_at == current_time + timedelta(seconds=service.MEDIA_BRIDGE_DISCONNECT_GRACE_SECONDS)
    assert len(dispatches) == 1


def test_bridge_marks_prompt_missing_from_queue_and_history_as_orphaned(monkeypatch):
    from bridge import skillforgebridge as bridge

    observed_at = "2026-08-14T05:22:20Z"
    record = {
        "job_id": "mvj-orphaned",
        "idempotency_key": "idem-orphaned",
        "attempt_no": 1,
        "prompt_id": "prompt-lost",
        "prompt_ids": ["prompt-lost"],
        "status": "running",
        "metrics": {},
        "prompt_missing_observed_at": observed_at,
    }
    writes = []

    def fake_http(_method, path, _payload=None, timeout=0):
        if path == "/queue":
            return {"queue_running": [], "queue_pending": []}
        if path == "/history/prompt-lost":
            return {}
        raise AssertionError(path)

    observed_epoch = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
    monkeypatch.setattr(bridge, "_load_media_job_record", lambda _job_id: dict(record))
    monkeypatch.setattr(bridge, "_write_media_job_record", lambda job_id, value: writes.append((job_id, value.copy())))
    monkeypatch.setattr(bridge, "_media_gpu_metrics", lambda: {"gpu_util_pct": 0})
    monkeypatch.setattr(bridge, "_media_http_json", fake_http)
    monkeypatch.setattr(bridge.time, "time", lambda: observed_epoch + bridge.MEDIA_PROMPT_ORPHAN_GRACE_SECONDS)

    result = bridge._media_get_job({"job_id": "mvj-orphaned"})

    assert result["status"] == "orphaned"
    assert result["error_code"] == bridge.MEDIA_PROMPT_ORPHAN_ERROR_CODE
    assert result["recovery"]["missing_prompt_ids"] == ["prompt-lost"]
    assert writes[-1][0] == "mvj-orphaned"


def test_bridge_resubmits_only_a_new_attempt_for_an_orphaned_prompt(monkeypatch):
    from bridge import skillforgebridge as bridge

    existing = {
        "job_id": "mvj-recover",
        "idempotency_key": "idem-recover",
        "attempt_no": 1,
        "prompt_ids": ["prompt-old"],
        "status": "orphaned",
        "error_code": bridge.MEDIA_PROMPT_ORPHAN_ERROR_CODE,
        "error": "prompt disappeared",
        "submitted_at": "2026-08-14T05:22:15Z",
    }
    submitted = []
    writes = []
    monkeypatch.setattr(bridge, "_load_media_job_record", lambda _job_id: dict(existing))
    monkeypatch.setattr(bridge, "_load_media_template", lambda _template_id: {
        "version": "realesrgan-x4-1080p-v1",
        "model_version": "RealESRGAN-x4plus",
    })
    monkeypatch.setattr(bridge, "_media_download_reference", lambda _job_id, item: {
        "file_name": "source.mp4",
        "comfy_path": "skillforge/mvj-recover/source.mp4",
        "path": "/tmp/source.mp4",
        "mime_type": item["mime_type"],
        "role": item["role"],
        "business_role": "enhancement_source",
        "sha256": "a" * 64,
    })
    monkeypatch.setattr(bridge, "_media_prepare_workflow", lambda *_args: {"safe": "workflow"})
    monkeypatch.setattr(bridge, "_media_model_manifest", lambda include_hashes=False: {"model_sha256": "b" * 64})
    monkeypatch.setattr(bridge, "_media_gpu_metrics", lambda: {"gpu_util_pct": 0})
    monkeypatch.setattr(bridge, "_write_media_job_record", lambda job_id, value: writes.append((job_id, value.copy())))

    def fake_http(method, path, payload=None, timeout=0):
        assert (method, path) == ("POST", "/prompt")
        submitted.append(payload)
        return {"prompt_id": "prompt-new"}

    monkeypatch.setattr(bridge, "_media_http_json", fake_http)
    payload = {
        "job_id": "mvj-recover",
        "idempotency_key": "idem-recover",
        "attempt_no": 2,
        "template_id": "video_upscale_realesrgan_v1",
        "mode": "video_enhance",
        "params": {"width": 1080, "height": 1920, "frames": 362, "fps": 24, "steps": 4},
        "references": [{
            "asset_id": "source-asset",
            "file_name": "source.mp4",
            "mime_type": "video/mp4",
            "byte_size": 1024,
            "role": "reference_video",
        }],
    }

    result, error = bridge._media_submit_job(payload)

    assert error is None
    assert result["attempt_no"] == 2
    assert result["prompt_ids"] == ["prompt-new"]
    assert result["recovery_history"][0]["prompt_ids"] == ["prompt-old"]
    assert len(submitted) == 1
    assert writes[-1][1]["status"] == "queued"


@pytest.mark.asyncio
async def test_control_plane_requeues_bridge_orphan_with_same_job_id(monkeypatch):
    from app.media import service

    job = _job("running")
    job.assigned_instance_id = "media-5080"
    job.current_attempt_no = 1
    attempt = SimpleNamespace(
        attempt_no=1,
        instance_id="media-5080",
        status="running",
        error=None,
        completed_at=None,
        metrics_json={},
        log_tail=None,
    )

    async def latest_attempt(_db, _job_id):
        return attempt

    dispatches = []

    async def dispatch(_db, _run, dispatched_job):
        dispatches.append((dispatched_job.id, dispatched_job.idempotency_key))

    class Client:
        def __init__(self, _instance_id):
            pass

        async def get_media_job(self, _payload, timeout=0):
            return {
                "status": "orphaned",
                "error_code": service.MEDIA_PROMPT_ORPHAN_ERROR_CODE,
                "error": "ComfyUI prompt disappeared",
                "updated_at": "2026-08-14T06:30:00Z",
                "recovery": {"status": "orphaned"},
            }

    monkeypatch.setattr(service, "_latest_attempt", latest_attempt)
    monkeypatch.setattr(service, "_dispatch_job", dispatch)
    monkeypatch.setattr(service.bridge_registry, "is_online", lambda _instance_id: True)
    monkeypatch.setattr(service, "AIClawClient", Client)

    await service._refresh_job(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), job)

    assert attempt.status == "disconnected"
    assert job.status == "queued"
    assert job.assigned_instance_id is None
    assert dispatches == [(job.id, job.idempotency_key)]
    assert attempt.metrics_json["remote_status"] == "orphaned"


def test_material_workbench_manifest_and_page_declare_real_gateway_contract():
    root = Path(__file__).parents[1] / "demo-projects" / "material-workbench"
    manifest = (root / "projectforge.yaml").read_text(encoding="utf-8")
    parsed_manifest = yaml.safe_load(manifest)
    page = _workbench_web_source()

    assert parsed_manifest["metadata"]["prompt_policy"]["version"] == "material-fde-h3-v1"
    assert parsed_manifest["metadata"]["prompt_policy"]["new_tasks_use_legacy_semantic_compiler"] is False
    assert 'department_id: "931765248"' in manifest
    assert "visibility: department" in manifest
    from app.media.fde_v4 import FDE_MEDIA_CAPABILITIES

    for capability in sorted(FDE_MEDIA_CAPABILITIES):
        assert capability in manifest
        assert capability in page
    assert "deepseek-v4-flash" in manifest
    assert "gateway.capability" in page
    assert "PROJECT_CAPABILITY_CLIENT_TIMEOUT" in page
    assert "Promise.race([request, watchdog])" in page
    assert "window.SkillForgeProject || window.SFProjectGateway || window.PlatformProjectGateway" in page
    assert "window.SkillForgeProjectGateway" not in page
    assert parsed_manifest["version"] == "4.1.0"
    assert "video.prompt.optimize" in parsed_manifest["capabilities"]
    assert 'id="optimizePromptButton"' in page
    assert 'id="promptOptimizationPanel"' in page
    assert "是否使用优化后的提示词" in page
    assert "采用优化稿" in page
    assert "api.optimizePrompt" in page
    assert "prompt_optimization" in page
    assert "MEDIA_PRODUCT_OVERLAY_TRANSPARENCY_REQUIRED" in page
    assert "asset-validation" in page
    assert "setQueueOpen(false)" in page
    assert 'data-workspace="production">生产<' in page
    assert 'data-workspace="review">审片 ' in page
    assert "material.candidate.list" in page
    assert "material.decision.submit" in page
    assert "material.delivery.submit" in page
    assert "material.weekly_report.get" in page
    assert "material.decision.batch_select" not in page
    assert "gateway.media.subscribe" in page
    assert "正在读取 AI 视觉质检" in page
    assert "正在读取问题标注" in page
    assert "scriptTimingHint" in page
    assert "当前镜头时长保持不变" in page
    assert "系统推荐" in page
    assert "production_policy" in page
    assert "repair_plan" in page
    assert "const shotLines" not in page
    assert "$('script').value = String(originalBrief.script || '')" in page
    assert "typeof result.normalized_brief?.contains_person === 'boolean'" in page
    assert "setInterval(refreshJobs" not in page
    assert "'采样中'" not in page
    assert "1080p 高清处理中" in page
    assert "等待高清实测" in page
    assert "status_counts" in page
    assert "shot_boundary_clarity" in page
    assert "batch_diversity" in page
    assert "product_packshot" in page
    assert "continuity_anchor" in page
    assert "上传后先确认用途，确认即参与生成" in page
    assert "技术检查" in page
    assert "九维质量评分" in page or "九维质量" in page
    assert "video.quality.analyze" in page
    assert "qualityAnalyze: jobId => capability('video.quality.analyze', { job_id: jobId, force: true })" in page
    assert "部门素材库" in page
    assert "api.libraryUpsert" in page
    assert "data-use-library" in page
    assert "AI 质量结果仅作建议" in page
    assert 'id="qualityRuntime"' in page
    assert "平台基础视觉模型 · 非项目微调" in page
    assert "服务来源未记录" in page
    assert "rightsPayload" in page
    assert "reviewPayload" in page
    assert "document.hidden" in page
    assert "今晚空闲时" in page
    assert "自动续写" in page
    assert "直接 H3 提示词" in page
    assert "direct_h3_prompt" in page
    assert "未调用 DeepSeek" in page
    assert "$('submitButton').onclick = handleProductionSubmit" in page
    assert "state.submissionLocked ||" in page
    assert "A/B 对比" in page
    assert "compareSelectionJobId" in page
    assert "$('compareJobSelect').onchange" in page
    assert "document.createElement('video')" in page
    wall_source = page[page.index("function renderMaterialWall") : page.index("async function loadReviews")]
    assert "<video" not in wall_source
    assert "}, 300)" in page


@pytest.mark.asyncio
async def test_busy_media_queue_wait_reason_is_written_only_once(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    from app.media import service

    async def no_available_instance(*_args, **_kwargs):
        return None, {}

    monkeypatch.setattr(service, "_select_media_instance", no_available_instance)
    job = SimpleNamespace(
        mode="text_to_video",
        params_json={},
        error=None,
        updated_at=datetime(2026, 8, 11, 21, 0, 0),
    )

    await service._dispatch_job(None, None, job)
    first_update = job.updated_at
    assert job.error == service.MEDIA_QUEUE_WAIT_MESSAGE

    await service._dispatch_job(None, None, job)
    assert job.updated_at == first_update


def test_bridge_rejects_arbitrary_workflow_and_binds_only_existing_paths(monkeypatch):
    from bridge import skillforgebridge as bridge

    normalized, error = bridge._normalize_media_job_payload(
        {
            "job_id": "job-1",
            "template_id": "h3_t2v_v1",
            "mode": "text_to_video",
            "workflow": {"evil": True},
            "params": MEDIA_DEFAULT_PRESET,
        }
    )
    assert normalized is None
    assert "arbitrary workflow" in error

    template = {
        "workflow": {"6": {"inputs": {"text": "old"}}},
        "bindings": {"positive_prompt": [["6", "inputs", "text"]]},
    }
    result = bridge._media_apply_bindings(template, {"positive_prompt": "new"})
    assert result["6"]["inputs"]["text"] == "new"
    assert template["workflow"]["6"]["inputs"]["text"] == "old"

    with pytest.raises(RuntimeError):
        bridge._media_apply_bindings(
            {"workflow": {"6": {"inputs": {}}}, "bindings": {"positive_prompt": [["6", "inputs", "missing"]]}},
            {"positive_prompt": "new"},
        )


def test_bridge_maintained_h3_templates_are_valid_and_reference_media_is_injected(monkeypatch):
    import base64
    import zlib

    from bridge import skillforgebridge as bridge

    template_dir = Path(__file__).parents[1] / "bridge" / "media_templates"
    monkeypatch.setattr(bridge, "MEDIA_TEMPLATE_DIR", template_dir)
    for template_id in sorted(bridge.MEDIA_TEMPLATE_IDS):
        template = bridge._load_media_template(template_id)
        if template_id == "video_upscale_realesrgan_v1":
            assert template["workflow"]["3"]["inputs"]["model_name"] == "RealESRGAN_x4plus.pth"
            assert template["workflow"]["5"]["inputs"]["width"] == 1080
            assert template["workflow"]["5"]["inputs"]["height"] == 1920
            assert template["workflow"]["7"]["class_type"] == "SaveVideo"
        else:
            assert template["upstream"].startswith("https://github.com/Comfy-Org/workflow_templates/")
            assert template["workflow"]["14"]["class_type"] == "SaveVideo"
            assert template["workflow"]["14"]["inputs"]["codec"] == "auto"
            embedded = zlib.decompress(base64.b64decode(bridge.MEDIA_EMBEDDED_TEMPLATES[template_id]))
            assert embedded.replace(b"\r\n", b"\n") == (template_dir / f"{template_id}.json").read_bytes().replace(b"\r\n", b"\n")

    normalized, error = bridge._normalize_media_job_payload({
        "job_id": "upscale-1", "template_id": "video_upscale_realesrgan_v1", "mode": "video_enhance",
        "params": {"width": 1080, "height": 1920, "frames": 192, "fps": 24, "batch_count": 1},
        "references": [{
            "asset_id": "asset-1", "file_name": "source.mp4", "mime_type": "video/mp4",
            "byte_size": 1024, "role": "reference_video",
        }],
    })
    assert error is None
    assert normalized["mode"] == "video_enhance"

    template = bridge._load_media_template("h3_r2v_v1")
    values = {
        "positive_prompt": "Use <Picture 1>, <Video 1> and <Audio 2>.",
        "width": 480,
        "height": 864,
        "frames": 124,
        "fps": 24,
        "steps": 20,
        "seed": 42,
        "output_prefix": "skillforge/job-1-01",
        "audio_enabled": False,
    }
    refs = [
        {"mime_type": "image/png", "comfy_path": "skillforge/job-1/a.png"},
        {"mime_type": "video/mp4", "comfy_path": "skillforge/job-1/b.mp4"},
        {"mime_type": "audio/wav", "comfy_path": "skillforge/job-1/c.wav"},
    ]
    workflow = bridge._media_prepare_workflow(template, values, refs, "reference_replay")
    ref_inputs = workflow["5"]["inputs"]
    assert ref_inputs["ref_images.ref_image_0"] == ["sf_ref_image_0", 0]
    assert ref_inputs["ref_videos.ref_video_0"] == ["sf_ref_video_components_0", 0]
    assert ref_inputs["ref_video_audios.ref_video_audio_0"] == ["sf_ref_video_components_0", 1]
    assert ref_inputs["ref_audios.ref_audio_0"] == ["sf_ref_audio_0", 0]
    assert "audio" not in workflow["13"]["inputs"]


def test_bridge_h3_prompt_combines_visual_audio_and_negative_constraints():
    from bridge import skillforgebridge as bridge

    prompt = bridge._media_compose_prompt(
        {
            "video_prompt": "Vertical product close-up",
            "audio_prompt": "soft click",
            "negative_constraints": ["no logo distortion", "no medical claim"],
        }
    )
    assert "Vertical product close-up" in prompt
    assert "Audio requirements: soft click" in prompt
    assert "Avoid: no logo distortion, no medical claim" in prompt
    integrated = bridge._media_compose_prompt({
        "integrated_multimodal_description": "integrated prompt",
        "video_prompt": "legacy",
        "audio_prompt": "must not be duplicated",
    })
    assert integrated == "integrated prompt"


def test_bridge_accepts_governed_product_overlay_without_passing_it_to_h3():
    from bridge import skillforgebridge as bridge

    normalized, error = bridge._normalize_media_job_payload({
        "job_id": "mvj-overlay-1",
        "template_id": "h3_t2v_v1",
        "mode": "text_to_video",
        "prompt": {
            "integrated_multimodal_description": "Generate an adult couple in a clean scene plate.",
            "product_overlay": {"enabled": True},
        },
        "params": MEDIA_DEFAULT_PRESET,
        "references": [{
            "asset_id": "packshot-1",
            "file_name": "verified-product.png",
            "mime_type": "image/png",
            "byte_size": 2048,
            "role": "overlay_image",
            "business_role": "product_packshot",
        }],
    })

    assert error is None
    assert normalized["mode"] == "text_to_video"
    assert normalized["references"][0]["role"] == "overlay_image"
    assert normalized["postprocess"]["product_overlay"] == {
        "enabled": True,
        "role": "overlay_image",
        "anchor": "bottom_right",
        "width_ratio": 0.22,
        "safe_margin_ratio": 0.05,
        "motion_profile": "static_verified_product",
        "content_crop_policy": "alpha_bbox_v1",
    }

    invalid, error = bridge._normalize_media_job_payload({
        "job_id": "mvj-overlay-2",
        "template_id": "h3_t2v_v1",
        "mode": "text_to_video",
        "prompt": {
            "integrated_multimodal_description": "Generate a clean scene plate.",
            "product_overlay": {"enabled": True},
        },
        "params": MEDIA_DEFAULT_PRESET,
        "references": [{
            "asset_id": "not-a-product",
            "file_name": "reference.png",
            "mime_type": "image/png",
            "byte_size": 2048,
            "role": "overlay_image",
            "business_role": "visual_reference",
        }],
    })
    assert invalid is None
    assert error == "overlay_image is restricted to governed product assets"

    centered, error = bridge._normalize_media_job_payload({
        "job_id": "mvj-overlay-3",
        "template_id": "h3_t2v_v1",
        "mode": "text_to_video",
        "prompt": {
            "integrated_multimodal_description": "Generate a dynamic clean commercial background plate.",
            "product_overlay": {
                "enabled": True,
                "anchor": "center",
                "width_ratio": 0.99,
                "safe_margin_ratio": 0,
                "motion_profile": "arbitrary-command",
            },
        },
        "params": MEDIA_DEFAULT_PRESET,
        "references": [{
            "asset_id": "packshot-2",
            "file_name": "verified-product.png",
            "mime_type": "image/png",
            "byte_size": 2048,
            "role": "overlay_image",
            "business_role": "product_packshot",
        }],
    })
    assert error is None
    assert centered["postprocess"]["product_overlay"] == {
        "enabled": True,
        "role": "overlay_image",
        "anchor": "center",
        "width_ratio": 0.38,
        "safe_margin_ratio": 0.08,
        "motion_profile": "static_verified_product_dynamic_background",
        "content_crop_policy": "alpha_bbox_v1",
    }

    duo, error = bridge._normalize_media_job_payload({
        "job_id": "mvj-overlay-4",
        "template_id": "h3_t2v_v1",
        "mode": "text_to_video",
        "prompt": {
            "integrated_multimodal_description": "Generate a dynamic clean commercial background plate.",
            "product_overlay": {"enabled": True, "anchor": "center", "asset_count": 2},
        },
        "params": MEDIA_DEFAULT_PRESET,
        "references": [
            {
                "asset_id": "packshot-3",
                "file_name": "verified-pack.png",
                "mime_type": "image/png",
                "byte_size": 2048,
                "role": "overlay_image",
                "business_role": "product_packshot",
            },
            {
                "asset_id": "detail-1",
                "file_name": "verified-unit.png",
                "mime_type": "image/png",
                "byte_size": 2048,
                "role": "overlay_image",
                "business_role": "product_detail",
            },
        ],
    })
    assert error is None
    assert duo["postprocess"]["product_overlay"] == {
        "enabled": True,
        "role": "overlay_image",
        "anchor": "center",
        "width_ratio": 0.38,
        "safe_margin_ratio": 0.08,
        "motion_profile": "static_verified_product_dynamic_background",
        "content_crop_policy": "alpha_bbox_v1",
        "layout": "packshot_detail_duo_v1",
        "asset_count": 2,
        "primary_width_ratio": 0.30,
        "secondary_width_ratio": 0.16,
        "group_gap_ratio": 0.02,
    }

    duplicate_roles, error = bridge._normalize_media_job_payload({
        "job_id": "mvj-overlay-5",
        "template_id": "h3_t2v_v1",
        "mode": "text_to_video",
        "prompt": {
            "integrated_multimodal_description": "Generate a clean commercial plate.",
            "product_overlay": {"enabled": True, "asset_count": 2},
        },
        "params": MEDIA_DEFAULT_PRESET,
        "references": [
            {
                "asset_id": "packshot-4",
                "file_name": "verified-pack-a.png",
                "mime_type": "image/png",
                "byte_size": 2048,
                "role": "overlay_image",
                "business_role": "product_packshot",
            },
            {
                "asset_id": "packshot-5",
                "file_name": "verified-pack-b.png",
                "mime_type": "image/png",
                "byte_size": 2048,
                "role": "overlay_image",
                "business_role": "product_packshot",
            },
        ],
    })
    assert duplicate_roles is None
    assert error == "two governed product overlays require one product_packshot and one product_detail"


def test_bridge_rechecks_product_overlay_alpha_after_signed_download(monkeypatch, tmp_path):
    from bridge import skillforgebridge as bridge

    class TransparentResult:
        returncode = 0
        stdout = (
            "lavfi.signalstats.YMIN=0\n"
            "lavfi.signalstats.YAVG=104.5\n"
            "lavfi.signalstats.YMAX=255\n"
        )
        stderr = ""

    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(bridge.subprocess, "run", lambda *args, **kwargs: TransparentResult())
    product = tmp_path / "verified-product.png"
    product.write_bytes(b"transparent-product")

    probe = bridge._media_probe_product_overlay_transparency(product)

    assert probe == {
        "policy_version": "product-overlay-alpha-v1",
        "passed": True,
        "alpha_min": 0.0,
        "alpha_max": 255.0,
        "detail": "verified_non_empty_alpha_plane",
    }


def test_bridge_media_probe_and_collect_result_cache_are_bounded(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    output = tmp_path / "result.mp4"
    output.write_bytes(b"governed-video")

    class ProbeResult:
        returncode = 0
        stderr = ""
        stdout = json.dumps({
            "format": {"duration": "5.167", "format_name": "mov,mp4", "bit_rate": "550213"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 480,
                    "height": 864,
                    "avg_frame_rate": "24/1",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "32000",
                    "channels": 2,
                },
            ],
        })

    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr(bridge.subprocess, "run", lambda *args, **kwargs: ProbeResult())
    probe = bridge._media_probe_output(output)
    assert probe["status"] == "ok"
    assert probe["duration_seconds"] == 5.167
    assert probe["streams"][0]["width"] == 480
    assert probe["streams"][1]["codec_name"] == "aac"

    record = {
        "job_id": "mvj-probe",
        "status": "completed",
        "outputs": [{"path": str(output)}],
        "model_version": "MiniMax-H3",
        "metrics": {"total_seconds": 70.5, "peak_vram_used_mb": 61440},
    }
    writes = []
    probe_calls = []
    monkeypatch.setattr(bridge, "_media_get_job", lambda payload: record)
    monkeypatch.setattr(bridge, "_media_effective_output_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_write_media_job_record", lambda job_id, value: writes.append((job_id, value.copy())))
    monkeypatch.setattr(
        bridge,
        "_media_probe_output",
        lambda path: probe_calls.append(path) or probe,
    )

    first = bridge._media_collect_result({"job_id": "mvj-probe", "result_index": 0})
    second = bridge._media_collect_result({"job_id": "mvj-probe", "result_index": 0})
    assert first["media_probe"] == probe
    assert second["media_probe"] == probe
    assert first["metrics"]["peak_vram_used_mb"] == 61440
    assert len(probe_calls) == 1
    assert len(writes) == 1


def test_bridge_media_delivery_removes_audio_when_disabled(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    source = tmp_path / "result.mp4"
    source.write_bytes(b"generated-video-with-audio")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"video-only")
        return Result()

    video_only_probe = {
        "status": "ok",
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 480, "height": 864}],
    }
    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "_media_probe_output", lambda path: video_only_probe)
    record = {"params": {"audio_enabled": False}}

    delivered, probe, changed = bridge._media_prepare_delivery_output(record, source, 0)

    assert changed is True
    assert delivered.name == "result-video-only.mp4"
    assert delivered.read_bytes() == b"video-only"
    assert probe == video_only_probe
    assert "-an" in calls[0]
    assert record["audio_stripped_for_delivery"] is True
    assert record["delivery_outputs"]["0"]["source_path"] == str(source)

    delivered_again, _, changed_again = bridge._media_prepare_delivery_output(record, source, 0)
    assert delivered_again == delivered
    assert changed_again is False
    assert len(calls) == 1


def test_bridge_media_delivery_composites_one_verified_product_png_idempotently(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    source = tmp_path / "result.mp4"
    product = tmp_path / "verified-product.png"
    source.write_bytes(b"generated-clean-plate")
    product.write_bytes(b"verified-transparent-product")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"composited-video")
        return Result()

    probe = {
        "status": "ok",
        "duration_seconds": 5.167,
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 480, "height": 864},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    monkeypatch.setattr(bridge, "_media_effective_input_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "_media_probe_output", lambda path: probe)
    record = {
        "params": {"width": 480, "height": 864, "audio_enabled": True},
        "postprocess": {"product_overlay": {"enabled": True}},
        "references": [{
            "role": "overlay_image",
            "business_role": "product_packshot",
            "path": str(product),
            "sha256": "verified-product-sha",
        }],
    }

    delivered, delivered_probe, changed = bridge._media_prepare_delivery_output(record, source, 0)

    assert changed is True
    assert delivered.name == "result-product-overlay.mp4"
    assert delivered.read_bytes() == b"composited-video"
    assert delivered_probe == probe
    command = calls[0]
    assert command[command.index("-i") + 1] == str(source)
    assert str(product.resolve()) in command
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "scale=106:-1" in filter_graph
    assert "overlay=x=W-w-24:y=H-h-43" in filter_graph
    assert record["product_overlay_applied_for_delivery"] is True
    assert record["delivery_outputs"]["0"]["product_overlay_sha256"] == "verified-product-sha"

    delivered_again, _, changed_again = bridge._media_prepare_delivery_output(record, source, 0)
    assert delivered_again == delivered
    assert changed_again is False
    assert len(calls) == 1


def test_bridge_media_delivery_centers_verified_product_over_dynamic_plate(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    source = tmp_path / "dynamic-plate.mp4"
    product = tmp_path / "verified-product.png"
    source.write_bytes(b"dynamic-clean-plate")
    product.write_bytes(b"verified-transparent-product")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"center-composited-video")
        return Result()

    probe = {
        "status": "ok",
        "duration_seconds": 5.167,
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 480, "height": 864}],
    }
    monkeypatch.setattr(bridge, "_media_effective_input_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "_media_probe_output", lambda path: probe)
    record = {
        "params": {"width": 480, "height": 864, "audio_enabled": False},
        "postprocess": {"product_overlay": {
            "enabled": True,
            "anchor": "center",
            "width_ratio": 0.38,
            "safe_margin_ratio": 0.08,
            "motion_profile": "static_verified_product_dynamic_background",
        }},
        "references": [{
            "role": "overlay_image",
            "business_role": "product_packshot",
            "path": str(product),
            "sha256": "verified-product-sha",
        }],
    }

    delivered, _, changed = bridge._media_prepare_delivery_output(record, source, 0)

    assert changed is True
    assert delivered.read_bytes() == b"center-composited-video"
    filter_graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "scale=182:-1" in filter_graph
    assert "overlay=x=(W-w)/2:y=(H-h)/2" in filter_graph
    assert record["delivery_outputs"]["0"]["product_overlay_config"]["anchor"] == "center"
    assert len(record["delivery_outputs"]["0"]["product_overlay_config_sha256"]) == 64


def test_bridge_media_delivery_composes_verified_packshot_and_detail_as_duo(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    source = tmp_path / "dynamic-plate.mp4"
    packshot = tmp_path / "verified-pack.png"
    detail = tmp_path / "verified-detail.png"
    source.write_bytes(b"dynamic-clean-plate")
    packshot.write_bytes(b"verified-transparent-pack")
    detail.write_bytes(b"verified-transparent-detail")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"duo-composited-video")
        return Result()

    probe = {
        "status": "ok",
        "duration_seconds": 5.167,
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 480, "height": 864}],
    }
    monkeypatch.setattr(bridge, "_media_effective_input_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        bridge,
        "_media_detect_product_alpha_crop",
        lambda path, **_kwargs: (
            {"width": 1200, "height": 2300, "x": 600, "y": 80}
            if Path(path) == packshot
            else {"width": 1000, "height": 2200, "x": 700, "y": 100}
        ),
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "_media_probe_output", lambda path: probe)
    record = {
        "params": {"width": 480, "height": 864, "audio_enabled": False},
        "postprocess": {"product_overlay": {
            "enabled": True,
            "anchor": "center",
            "layout": "packshot_detail_duo_v1",
            "asset_count": 2,
            "content_crop_policy": "alpha_bbox_v1",
        }},
        # Reverse input order to prove business roles, not browser order,
        # deterministically own the primary/secondary layout.
        "references": [
            {
                "asset_id": "detail-1",
                "role": "overlay_image",
                "business_role": "product_detail",
                "path": str(detail),
                "sha256": "detail-sha",
            },
            {
                "asset_id": "pack-1",
                "role": "overlay_image",
                "business_role": "product_packshot",
                "path": str(packshot),
                "sha256": "pack-sha",
            },
        ],
    }

    delivered, _, changed = bridge._media_prepare_delivery_output(record, source, 0)

    assert changed is True
    assert delivered.read_bytes() == b"duo-composited-video"
    command = calls[0]
    assert command.index(str(packshot.resolve())) < command.index(str(detail.resolve()))
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[1:v]crop=1200:2300:600:80,scale=144:-1" in filter_graph
    assert "[2:v]crop=1000:2200:700:100,scale=77:-1" in filter_graph
    assert "overlay=x=(W-231)/2:y=(H-h)/2" in filter_graph
    assert "overlay=x=(W-231)/2+154:y=(H-h)/2" in filter_graph
    output = record["delivery_outputs"]["0"]
    assert output["product_overlay_sha256s"] == ["pack-sha", "detail-sha"]
    assert output["product_overlay_roles"] == ["product_packshot", "product_detail"]
    assert output["product_overlay_runtime"]["content_crop_policy"] == "alpha_bbox_v1"
    assert output["product_overlay_runtime"]["alpha_content_crops"][0]["bbox"] == {
        "width": 1200,
        "height": 2300,
        "x": 600,
        "y": 80,
    }

    delivered_again, _, changed_again = bridge._media_prepare_delivery_output(record, source, 0)
    assert delivered_again == delivered
    assert changed_again is False
    assert len(calls) == 1


def test_bridge_media_delivery_trims_three_second_hook(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    source = tmp_path / "result.mp4"
    source.write_bytes(b"generated-four-second-video")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"trimmed-three-second-video")
        return Result()

    trimmed_probe = {
        "status": "ok",
        "duration_seconds": 3.0,
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 480, "height": 864},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "_media_probe_output", lambda path: trimmed_probe)
    record = {"params": {"audio_enabled": True, "delivery_duration_seconds": 3}}

    delivered, probe, changed = bridge._media_prepare_delivery_output(record, source, 0)

    assert changed is True
    assert delivered.name == "result-3s.mp4"
    assert delivered.read_bytes() == b"trimmed-three-second-video"
    assert probe == trimmed_probe
    assert calls[0][calls[0].index("-t") + 1] == "3"
    assert "-an" not in calls[0]
    assert record["duration_trimmed_for_delivery"] == 3
    assert record["delivery_outputs"]["0"]["duration_trimmed"] is True


def test_bridge_media_ffmpeg_uses_bootstrap_venv_bundle(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from bridge import skillforgebridge as bridge

    venv = tmp_path / "media" / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    bundled = venv / "lib" / "imageio_ffmpeg" / "ffmpeg"
    python.parent.mkdir(parents=True)
    bundled.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    bundled.write_bytes(b"ffmpeg")

    class Result:
        returncode = 0
        stdout = str(bundled) + "\n"
        stderr = ""

    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_VENV_DIR", venv)
    monkeypatch.setattr(bridge.subprocess, "run", lambda *args, **kwargs: Result())
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", SimpleNamespace(get_ffmpeg_exe=lambda: (_ for _ in ()).throw(RuntimeError("missing"))))

    assert bridge._media_ffmpeg_executable() == str(bundled.resolve())


def test_bridge_media_bootstrap_installs_governed_ffmpeg_runtime(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / "requirements.txt").write_text("", encoding="utf-8")
    venv = tmp_path / "venv"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    calls = []

    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_COMFY_DIR", comfy)
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_VENV_DIR", venv)
    monkeypatch.setattr(bridge, "_media_bootstrap_run", lambda command, **kwargs: calls.append((command, kwargs)))

    bridge._media_bootstrap_install_comfy()

    runtime_command, runtime_kwargs = calls[-1]
    assert runtime_command == [
        str(python), "-m", "pip", "install", "--only-binary=:all:", "imageio-ffmpeg==0.6.0",
    ]
    assert runtime_kwargs["step"] == "install_governed_media_runtime"


def test_bridge_media_delivery_falls_back_to_bootstrap_pyav(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    venv = tmp_path / "media" / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    source = tmp_path / "source.mp4"
    target = tmp_path / "target.mp4"
    source.write_bytes(b"generated-with-audio")
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"video-only")
        return Result()

    monkeypatch.setattr(bridge, "_media_ffmpeg_executable", lambda: None)
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_VENV_DIR", venv)
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    result = bridge._media_strip_audio_track(source, target)

    assert result.returncode == 0
    assert target.read_bytes() == b"video-only"
    assert calls[0][0] == str(python)
    assert calls[0][1] == "-c"
    assert "import av,sys" in calls[0][2]
    assert calls[0][-2:] == [str(source), str(target)]


def test_bridge_media_probe_parses_mp4_streams_without_ffprobe(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    def box(kind, payload):
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    def media_header(timescale, duration):
        return (
            b"\x00\x00\x00\x00"
            + (0).to_bytes(4, "big") * 2
            + timescale.to_bytes(4, "big")
            + duration.to_bytes(4, "big")
        )

    def handler(kind):
        return b"\x00\x00\x00\x00" + (0).to_bytes(4, "big") + kind

    video_sample = b"\x00" * 24 + (480).to_bytes(2, "big") + (864).to_bytes(2, "big") + b"\x00" * 50
    audio_sample = (
        b"\x00" * 16
        + (2).to_bytes(2, "big")
        + (16).to_bytes(2, "big")
        + b"\x00" * 4
        + (32000 << 16).to_bytes(4, "big")
        + b"\x00" * 20
    )
    video_stbl = box(
        b"stbl",
        box(b"stsd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + box(b"avc1", video_sample))
        + box(
            b"stts",
            b"\x00\x00\x00\x00"
            + (1).to_bytes(4, "big")
            + (124).to_bytes(4, "big")
            + (1).to_bytes(4, "big"),
        ),
    )
    audio_stbl = box(
        b"stbl",
        box(b"stsd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + box(b"mp4a", audio_sample)),
    )
    video_tkhd = b"\x00" * 76 + (480 << 16).to_bytes(4, "big") + (864 << 16).to_bytes(4, "big")
    audio_tkhd = b"\x00" * 76 + b"\x00" * 8
    video_trak = box(
        b"trak",
        box(b"tkhd", video_tkhd)
        + box(
            b"mdia",
            box(b"mdhd", media_header(24, 124))
            + box(b"hdlr", handler(b"vide"))
            + box(b"minf", video_stbl),
        ),
    )
    audio_trak = box(
        b"trak",
        box(b"tkhd", audio_tkhd)
        + box(
            b"mdia",
            box(b"mdhd", media_header(32000, 165344))
            + box(b"hdlr", handler(b"soun"))
            + box(b"minf", audio_stbl),
        ),
    )
    output = tmp_path / "result.mp4"
    output.write_bytes(
        box(b"ftyp", b"isom\x00\x00\x02\x00isom")
        + box(b"moov", box(b"mvhd", media_header(1000, 5167)) + video_trak + audio_trak)
    )
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)

    probe = bridge._media_probe_output(output)

    assert probe["status"] == "ok"
    assert probe["source"] == "bridge_mp4_parser"
    assert probe["duration_seconds"] == 5.167
    assert probe["bit_rate"] > 0
    assert probe["streams"][0] == {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 480,
        "height": 864,
        "avg_frame_rate": "24/1",
        "sample_rate": 0,
        "channels": 0,
        "duration_seconds": 5.167,
    }
    assert probe["streams"][1]["codec_type"] == "audio"
    assert probe["streams"][1]["codec_name"] == "aac"
    assert probe["streams"][1]["sample_rate"] == 32000
    assert probe["streams"][1]["channels"] == 2


def test_bridge_h3_bootstrap_accepts_only_fixed_signed_profile(monkeypatch):
    from bridge import skillforgebridge as bridge

    monkeypatch.setattr(bridge, "_media_bootstrap_preflight", lambda: {
        "gpu": ["NVIDIA RTX PRO 6000"],
        "free_disk_bytes": 100_000_000_000,
        "required_disk_bytes": 80_000_000_000,
    })

    normalized, error = bridge._normalize_media_bootstrap_payload({
        "profile": "h3_all_modes_v1",
        "bandwidth_limit_mbps": 3,
        "dry_run": True,
    })
    assert error is None
    plan = bridge._start_media_bootstrap(normalized)
    assert plan["status"] == "planned"
    assert plan["comfyui_tag"] == "v0.31.0"
    assert plan["preflight"]["gpu"] == ["NVIDIA RTX PRO 6000"]
    assert len(plan["manifest"]) == 5
    assert all(len(item["sha256"]) == 64 for item in plan["manifest"])

    normalized, error = bridge._normalize_media_bootstrap_payload({
        "profile": "h3_all_modes_v1",
        "bandwidth_limit_mbps": 3,
        "dry_run": True,
        "url": "https://attacker.invalid/model",
    })
    assert normalized is None
    assert "arbitrary URL" in error


def test_bridge_h3_bootstrap_resumes_after_short_http_stream(tmp_path, monkeypatch):
    import hashlib

    from bridge import skillforgebridge as bridge

    content = b"abcdefgh"
    item = {
        "path": "diffusion_models/test-model.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_MODEL_DIR", tmp_path)
    updates = []
    monkeypatch.setattr(bridge, "_media_bootstrap_update", lambda **changes: updates.append(changes) or changes)
    bridge._MEDIA_BOOTSTRAP_CANCEL.clear()
    part = tmp_path / "diffusion_models" / "test-model.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(content[:3])
    requests = []

    class Response:
        status = 206

        def __init__(self, start, payload):
            self.headers = {"Content-Range": f"bytes {start}-{len(content) - 1}/{len(content)}"}
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

        def read(self, size):
            if not self.payload:
                return b""
            chunk, self.payload = self.payload[:size], self.payload[size:]
            return chunk

    def fake_urlopen(request, timeout):
        assert timeout == 120
        start = int(str(request.get_header("Range")).removeprefix("bytes=").removesuffix("-"))
        requests.append(start)
        # The first resumed response ends early after two bytes. The downloader
        # must issue another Range request instead of failing the whole install.
        return Response(start, content[start:5] if len(requests) == 1 else content[start:])

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    downloaded = bridge._media_bootstrap_download_model(item, 0, len(content), 3)

    assert downloaded == len(content)
    assert requests == [3, 5]
    assert (tmp_path / item["path"]).read_bytes() == content
    assert any(update.get("current_step") == "download_models_retrying" for update in updates)
    assert any(update.get("retry_count") == 1 for update in updates)


def test_bridge_h3_bootstrap_rejects_mismatched_content_range(tmp_path, monkeypatch):
    import hashlib

    from bridge import skillforgebridge as bridge

    content = b"abcdefgh"
    item = {
        "path": "diffusion_models/test-model.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_MODEL_DIR", tmp_path)
    monkeypatch.setattr(bridge, "_media_bootstrap_update", lambda **_changes: {})
    monkeypatch.setattr(bridge, "_MEDIA_BOOTSTRAP_CANCEL", type("Cancel", (), {
        "clear": lambda self: None,
        "is_set": lambda self: False,
        "wait": lambda self, _delay: False,
    })())
    part = tmp_path / "diffusion_models" / "test-model.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(content[:3])
    attempts = 0

    class Response:
        status = 206
        headers = {"Content-Range": f"bytes 0-{len(content) - 1}/{len(content)}"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

    def fake_urlopen(_request, timeout):
        nonlocal attempts
        assert timeout == 120
        attempts += 1
        return Response()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="download retry exhausted"):
        bridge._media_bootstrap_download_model(item, 0, len(content), 3)

    assert attempts == 8
    assert part.read_bytes() == content[:3]


def test_bridge_h3_bootstrap_parallel_ranges_respect_manifest_and_merge(tmp_path, monkeypatch):
    import hashlib
    import threading
    import time

    from bridge import skillforgebridge as bridge

    size = 17 * 1024 * 1024 + 13
    pattern = b"skillforge-h3-range-"
    content = (pattern * (size // len(pattern) + 1))[:size]
    item = {
        "path": "diffusion_models/parallel-model.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_MODEL_DIR", tmp_path)
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_PARALLEL_THRESHOLD_BYTES", 1)
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_RANGE_CHUNK_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_PARALLELISM", 3)
    updates = []
    monkeypatch.setattr(bridge, "_media_bootstrap_update", lambda **changes: updates.append(changes) or changes)
    bridge._MEDIA_BOOTSTRAP_CANCEL.clear()
    part = tmp_path / "diffusion_models" / "parallel-model.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(content[:4])
    lock = threading.Lock()
    active = 0
    max_active = 0
    requests = []

    class Response:
        status = 206

        def __init__(self, start, end):
            self.headers = {"Content-Range": f"bytes {start}-{end}/{len(content)}"}
            self.payload = content[start : end + 1]

        def __enter__(self):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            return self

        def __exit__(self, *_args):
            nonlocal active
            with lock:
                active -= 1
            return False

        def getcode(self):
            return self.status

        def read(self, requested):
            chunk, self.payload = self.payload[:requested], self.payload[requested:]
            return chunk

    def fake_urlopen(request, timeout):
        assert timeout == 120
        start, end = [int(value) for value in request.get_header("Range").removeprefix("bytes=").split("-")]
        with lock:
            requests.append((start, end))
        return Response(start, end)

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    downloaded = bridge._media_bootstrap_download_model(item, 0, len(content), 1024)

    assert downloaded == len(content)
    assert (tmp_path / item["path"]).read_bytes() == content
    assert len(requests) == 3
    assert max_active >= 2
    assert not list(part.parent.glob("*.range-*"))
    assert any(update.get("current_step") == "download_models_parallel" for update in updates)
    assert any(update.get("current_step") == "merge_model_ranges" for update in updates)


def test_bridge_h3_bootstrap_counts_resumable_range_fragments_without_overlap(tmp_path, monkeypatch):
    from bridge import skillforgebridge as bridge

    item = {
        "path": "diffusion_models/resumable-model.bin",
        "size": 20,
        "sha256": "0" * 64,
    }
    monkeypatch.setattr(bridge, "MEDIA_BOOTSTRAP_MODEL_DIR", tmp_path)
    part = tmp_path / "diffusion_models" / "resumable-model.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"1234")
    part.with_name(part.name + ".range-4-10").write_bytes(b"567")
    part.with_name(part.name + ".range-6-12").write_bytes(b"7890")
    part.with_name(part.name + ".range-99-120").write_bytes(b"ignored")

    assert bridge._media_bootstrap_existing_model_bytes(item) == 10


def test_bridge_h3_bootstrap_auto_resumes_active_state_once_and_skips_terminal(monkeypatch):
    from bridge import skillforgebridge as bridge

    state = {"status": "downloading"}
    starts = []

    class AliveThread:
        def is_alive(self):
            return True

    monkeypatch.setattr(bridge, "_MEDIA_BOOTSTRAP_THREAD", None)
    monkeypatch.setattr(bridge, "_load_media_bootstrap_status", lambda: dict(state))

    def fake_start(normalized):
        starts.append(normalized)
        monkeypatch.setattr(bridge, "_MEDIA_BOOTSTRAP_THREAD", AliveThread())
        return {"status": "downloading"}

    monkeypatch.setattr(bridge, "_start_media_bootstrap", fake_start)
    assert bridge._resume_media_bootstrap_if_needed() is True
    assert bridge._resume_media_bootstrap_if_needed() is False
    assert starts == [{
        "profile": "h3_all_modes_v1",
        "bandwidth_limit_mbps": 3,
        "dry_run": False,
        "accept_license": True,
        "force": False,
        "_resume_after_bridge_restart": True,
    }]

    monkeypatch.setattr(bridge, "_MEDIA_BOOTSTRAP_THREAD", None)
    for terminal_status in ("not_started", "succeeded", "failed", "cancelled", "cancelling"):
        state["status"] = terminal_status
        assert bridge._resume_media_bootstrap_if_needed() is False
    assert len(starts) == 1


def test_bridge_protocol_preserves_sanitized_media_capability():
    from app.aiclaw.bridge_protocol import BridgeCapabilitiesFrame
    from app.aiclaw.bridge_router import _sanitize_media_capability, _sanitize_workload_roles

    frame = BridgeCapabilitiesFrame.model_validate({
        "type": "bridge_capabilities",
        "workload_roles": ["video_generation", "arbitrary_role"],
        "media": {
            "configured": True,
            "online": True,
            "comfyui_version": "0.31.0",
            "template_ids": ["h3_t2v_v1", "arbitrary_workflow"],
            "supported_modes": ["text_to_video", "shell"],
            "workload_roles": ["video_generation", "arbitrary_role"],
            "queue_depth": 2,
            "model_files": [{
                "name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "path": "/secret/model/path",
                "size_bytes": 123,
                "sha256": "a" * 64,
                "complete": True,
            }],
            "model_sha256": "b" * 64,
            "hashes_complete": True,
            "postprocess": {
                "governed_product_overlay": True,
                "overlay_roles": ["product_packshot", "evil"],
                "anchor": "top_left",
                "anchors": ["center", "top_left", "bottom_right"],
                "motion_profiles": [
                    "static_verified_product_dynamic_background",
                    "arbitrary_motion",
                ],
                "content_crop_policies": ["alpha_bbox_v1", "arbitrary_crop"],
                "command": "arbitrary-command",
            },
        },
    })

    assert _sanitize_workload_roles(frame.workload_roles) == ["video_generation"]
    media = _sanitize_media_capability(frame.media)
    assert media["template_ids"] == ["h3_t2v_v1"]
    assert media["supported_modes"] == ["text_to_video"]
    assert media["workload_roles"] == ["video_generation"]
    assert media["model_files"][0]["name"].endswith(".safetensors")
    assert "path" not in media["model_files"][0]
    assert media["postprocess"] == {
        "governed_product_overlay": True,
        "overlay_roles": ["product_packshot", "product_detail"],
        "anchor": "bottom_right",
        "anchors": ["center", "bottom_right"],
        "motion_profiles": [
            "static_verified_product_dynamic_background",
            "static_verified_product",
        ],
        "content_crop_policies": ["alpha_bbox_v1"],
    }


@pytest.mark.asyncio
async def test_media_purpose_is_excluded_from_skill_runtime_targets():
    from app.execution.execution_service import _is_skill_runtime_agent
    from app.execution.sync_service import _agent_purpose_reject_reason

    media = SimpleNamespace(agent_purpose="media")
    mixed = SimpleNamespace(agent_purpose="mixed")

    assert _is_skill_runtime_agent(media) is False
    assert "media" in _agent_purpose_reject_reason(media)
    assert _is_skill_runtime_agent(mixed) is True
