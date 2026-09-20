import re
import subprocess
import wave
from array import array
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.common.exceptions import AppError
from app.media.ad_material_parser import (
    adapt_source_roles_for_ad_contract,
    apply_ad_material_contract_to_plan,
    build_operator_brief_plan,
    normalize_frontdesk_script,
    parse_ad_material_brief,
)
from app.media.h3_prompt_policy import compile_h3_plan
from app.media.h3_prompt_policy import build_character_first_frame_prompt
from app.media.models import (
    MediaContinuationChain,
    MediaContinuationSegment,
    MediaGenerationJob,
    MediaWorkbenchStreamEvent,
)
from app.media.service import MEDIA_CAPABILITIES, _job_business_title
from app.media.speech_delivery import (
    DialogueLine,
    SPEECH_DELIVERY_MODEL,
    SPEECH_DELIVERY_POLICY_VERSION,
    SPEECH_TRANSCRIPTION_MODEL,
    SpeechDeliveryError,
    _assemble_and_mux,
    _ffmpeg_executable,
    _speech_input_text,
    _transcribe_dialogue_bytes,
    _validated_voice,
    _wave_info,
    evaluate_dialogue_transcription,
    load_speech_delivery_config,
    parse_dialogue_lines,
)
from app.media.workbench_v2 import (
    BUSINESS_ASSET_ROLES,
    NEW_MEDIA_CAPABILITIES,
    _apply_script_timing_to_plan,
    _apply_replication_facts_to_brief,
    _default_request_for_mode,
    _estimate_script_seconds,
    _infer_brief_content_flags,
    _apply_source_understanding_to_plan,
    _normalize_source_understanding,
    _normalize_cloud_reference_item,
    _product_from_material_hints,
    _resolve_audio_brief_conflicts,
    _recommended_dialogue_duration,
    _script_timing_for_clip,
    _sha256_bound_bridge_probe,
    _serialize_review_asset,
    _review_manifest_delivery_entry,
    _decode_review_cursor,
    _encode_review_cursor,
    _review_sort_time,
    _source_understanding_complete,
    _character_first_frame_quality_gate,
    cloud_reference_search,
    cloud_reference_import,
    compile_production_plan,
    _bind_source_mentions,
    _normalize_source_roles,
    _reconcile_source_roles_with_understanding,
    _source_replication_contract,
    _workflow_mode,
    _workbench_media_node_online,
    continuation_segment_idempotency_key,
    continuation_segment_durations,
    continuation_timeline_handoff,
    hard_review_gate,
    dialogue_delivery_gate,
    infer_production_mode,
    preset_benchmark_gate,
    prepare_production,
    generate_character_first_frame,
    production_duration_contract,
    production_frames_for_duration,
    normalize_output_ratio,
    output_params_for_ratio,
)
from app.projects.models import ProjectRunAsset
from app.projects.service import PROJECT_MEDIA_CAPABILITIES


def test_v2_media_capability_registry_stays_in_sync():
    assert NEW_MEDIA_CAPABILITIES <= MEDIA_CAPABILITIES
    assert MEDIA_CAPABILITIES == PROJECT_MEDIA_CAPABILITIES
    assert {
        "video.workbench.snapshot",
        "video.production.prepare",
        "video.production.compile",
        "video.production.submit",
        "video.first_frame.generate",
        "video.theme.diverge",
        "video.production_batch.create",
        "video.continuation.plan",
        "video.review.manifest",
        "video.review.list",
        "video.cloud_reference.search",
        "video.cloud_reference.import",
        "video.review.annotation.save",
        "video.review.batch_reject",
        "video.review.batch_update",
        "video.enhance.submit",
        "video.dialogue_delivery.retry",
    } <= NEW_MEDIA_CAPABILITIES


def test_dialogue_without_people_reference_recommends_governed_first_frame_anchor():
    contract = parse_ad_material_brief(
        {
            "script": "女：开了吗？\n男：开了。",
            "request": "15秒中国年轻成年情侣居家自然对话，无商品、无字幕。",
            "platform": "douyin",
            "duration_seconds": 15,
        },
        [],
        None,
        inferred_mode="text_to_video",
    )

    anchor = contract["first_frame_anchor"]
    assert anchor["recommended"] is True
    assert anchor["capability"] == "video.first_frame.generate"
    assert anchor["business_role"] == "character_first_frame"
    assert anchor["next_h3_mode"] == "image_to_video"
    assert anchor["resolution"] == "2K"


def test_dialogue_with_people_anchor_never_generates_a_duplicate_first_frame():
    contract = parse_ad_material_brief(
        {
            "script": "女：开了吗？\n男：开了。",
            "request": "中国年轻成年情侣居家自然对话。",
            "platform": "douyin",
        },
        [{"asset_id": "people", "role": "character_first_frame", "technical_role": "first_frame"}],
        None,
        inferred_mode="image_to_video",
    )

    assert contract["first_frame_anchor"]["recommended"] is False


def test_product_references_stay_governed_overlays_after_character_anchor_is_added():
    roles = [
        {"asset_id": "people", "role": "character_first_frame", "technical_role": "first_frame"},
        {"asset_id": "pack", "role": "product_packshot", "technical_role": "first_frame"},
        {"asset_id": "detail", "role": "product_detail", "technical_role": "reference_image"},
    ]
    contract = parse_ad_material_brief(
        {
            "script": "第一次约会，我以为他是男大体育生。\n结果我发现是用对套了。",
            "request": "中国大陆年轻成年女性居家自然口播，后期植入真实商品图。",
            "platform": "douyin",
            "contains_person": True,
        },
        roles,
        None,
        inferred_mode="reference_replay",
    )
    adapted = adapt_source_roles_for_ad_contract(roles, contract)

    assert contract["requested_cast_profile"] == "single_woman"
    assert contract["recommended_h3_mode"] == "image_to_video"
    assert contract["product_only_reference_with_people"] is True
    assert [item["technical_role"] for item in adapted] == ["first_frame", "overlay_image", "overlay_image"]
    assert infer_production_mode(_normalize_source_roles(adapted)) == "image_to_video"


def test_governed_character_first_frame_prompt_locks_medium_two_shot_and_distinct_cast():
    contract = parse_ad_material_brief(
        {
            "script": "女：开了吗？\n男：开了。",
            "request": "15秒中国年轻成年情侣居家自然对话，无商品、无字幕。",
            "platform": "douyin",
            "duration_seconds": 15,
        },
        [],
        None,
        inferred_mode="text_to_video",
    )
    prompt = build_character_first_frame_prompt({
        "ad_material_contract": contract,
        "shots": [{"subject": "一位中国大陆成年女性与一位中国大陆成年男性", "scene": "居家客厅"}],
    })

    assert len(prompt) <= 4000
    assert "waist-up medium two-shot" in prompt
    assert "both elbows and waist support are fully visible" in prompt
    assert "x=32 percent" in prompt and "x=66 percent" in prompt
    assert "muted sage" in prompt and "charcoal-blue" in prompt
    assert "Exactly two adults" in prompt
    assert "no product, package" in prompt
    assert "VISIBLE COMMERCIAL APPEARANCE" in prompt
    assert "synthetic AI face" in prompt
    assert "mainland Chinese adult" not in prompt


def test_character_first_frame_gate_rejects_tight_or_ambiguous_two_person_image():
    result = _character_first_frame_quality_gate(
        {
            "model": "vision-model",
            "assets": [{
                "asset_id": "pra-anchor",
                "evidence_status": "verified",
                "semantic_role": "person_scene",
                "people_presence": "multiple_people",
                "people_count": 2,
                "framing": "close_up",
                "both_shoulder_lines_visible": True,
                "both_elbows_visible": False,
                "waist_support_visible": False,
                "inward_partner_gaze": True,
                "faces_visibly_distinct": True,
                "contains_product": False,
                "contains_source_text": False,
            }],
        },
        asset_id="pra-anchor",
        contract={"content_format": "dialogue", "actor_count": 2},
    )

    assert result["passed"] is False
    assert any("腰部以上中景" in issue for issue in result["issues"])
    assert any("肘部" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_generate_character_first_frame_uses_governed_image_capability_and_visual_gate(monkeypatch):
    from app.hall import direct_capability_service
    from app.media import workbench_v2

    contract = parse_ad_material_brief(
        {
            "script": "女：开了吗？\n男：开了。",
            "request": "15秒中国年轻成年情侣居家自然对话，无商品、无字幕。",
            "platform": "douyin",
        },
        [],
        None,
        inferred_mode="text_to_video",
    )
    plan = {
        "ad_material_contract": contract,
        "shots": [{"subject": "一位中国大陆成年女性与一位中国大陆成年男性", "scene": "居家客厅"}],
    }
    captured = {}
    asset_row = SimpleNamespace(id="pra-anchor", metadata_json={})

    async def no_existing(*_args, **_kwargs):
        return None

    async def fake_run(_capability_id, body, **_kwargs):
        captured["body"] = body
        return {"result": {"status": "completed", "model": "gpt-image-2", "image_urls": ["https://images.example/anchor.png"]}}

    async def fake_download(*_args, **_kwargs):
        return SimpleNamespace(body=b"\x89PNG\r\n\x1a\nfirst-frame", media_type="image/png")

    async def fake_upload(_db, _user, _run_id, **kwargs):
        captured["upload"] = kwargs
        asset_row.metadata_json = dict(kwargs["metadata"])
        return {"asset": {"id": "pra-anchor", "file_name": kwargs["file_name"], "mime_type": kwargs["mime_type"]}}

    async def fake_understanding(*_args, **_kwargs):
        return {
            "model": "Qwen/Qwen3.6-35B-A3B",
            "assets": [{
                "asset_id": "pra-anchor",
                "evidence_status": "verified",
                "semantic_role": "person_scene",
                "people_presence": "multiple_people",
                "people_count": 2,
                "framing": "waist_up_two_shot",
                "both_shoulder_lines_visible": True,
                "both_elbows_visible": True,
                "waist_support_visible": True,
                "inward_partner_gaze": True,
                "faces_visibly_distinct": True,
                "contains_product": False,
                "contains_source_text": False,
            }],
        }

    class FakeDb:
        async def get(self, _model, asset_id):
            return asset_row if asset_id == "pra-anchor" else None

        async def flush(self):
            return None

    monkeypatch.setattr(workbench_v2, "_existing_character_first_frame", no_existing)
    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)
    monkeypatch.setattr(direct_capability_service, "download_direct_capability_image", fake_download)
    monkeypatch.setattr(workbench_v2.project_service, "upload_project_run_asset", fake_upload)
    monkeypatch.setattr(workbench_v2.project_service, "serialize_run_asset", lambda asset, **_kwargs: {"id": asset.id, "metadata": asset.metadata_json})
    monkeypatch.setattr(workbench_v2, "_understand_source_assets", fake_understanding)

    result = await generate_character_first_frame(
        FakeDb(),
        SimpleNamespace(id="user-1", role="member"),
        SimpleNamespace(id="project-1"),
        SimpleNamespace(id="run-1"),
        {"prepared_plan": plan, "brief": {"ad_material_contract": contract}, "idempotency_key": "anchor-idempotency-1"},  # gitleaks:allow -- enum/config key or deliberate synthetic test fixture; not a credential
    )

    assert result["status"] == "completed"
    assert result["quality_gate"]["passed"] is True
    assert result["source_role"]["role"] == "character_first_frame"
    assert result["next_h3_mode"] == "image_to_video"
    params = captured["body"]["params"]
    assert params["resolution"] == "2K" and params["size"] == "9:16" and params["wait"] is True
    assert captured["upload"]["metadata"]["generation_capability"] == "gpt-imagegen"
    assert captured["upload"]["metadata"]["default_role"] == "character_first_frame"


@pytest.mark.asyncio
async def test_character_first_frame_idempotency_resumes_visual_gate_without_regeneration(monkeypatch):
    from app.hall import direct_capability_service
    from app.media import workbench_v2

    contract = parse_ad_material_brief(
        {"script": "女：开了吗？\n男：开了。", "platform": "douyin"},
        [],
        None,
        inferred_mode="text_to_video",
    )
    existing = SimpleNamespace(
        id="pra-existing",
        metadata_json={
            "generation_prompt_sha256": "a" * 64,
            "quality_gate": {
                "passed": False,
                "policy_version": "material-source-understanding-v2",
            },
        },
    )

    async def find_existing(*_args, **_kwargs):
        return existing

    async def forbidden_generation(*_args, **_kwargs):
        raise AssertionError("idempotent resume must not spend another image generation")

    async def fake_understanding(*_args, **_kwargs):
        return {
            "model": "vision-model",
            "assets": [{
                "asset_id": "pra-existing", "evidence_status": "verified", "semantic_role": "person_scene",
                "people_presence": "full_person", "people_count": 2, "framing": "medium_two_shot",
                "both_shoulder_lines_visible": True, "both_elbows_visible": True,
                "waist_support_visible": True, "inward_partner_gaze": True,
                "faces_visibly_distinct": True, "contains_product": False, "contains_source_text": False,
            }],
        }

    class FakeDb:
        async def flush(self):
            return None

    monkeypatch.setattr(workbench_v2, "_existing_character_first_frame", find_existing)
    monkeypatch.setattr(workbench_v2, "_understand_source_assets", fake_understanding)
    monkeypatch.setattr(direct_capability_service, "run_direct_capability", forbidden_generation)
    monkeypatch.setattr(workbench_v2.project_service, "serialize_run_asset", lambda asset, **_kwargs: {"id": asset.id})

    result = await generate_character_first_frame(
        FakeDb(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1"),
        SimpleNamespace(id="run-1"),
        {
            "prepared_plan": {"ad_material_contract": contract, "shots": []},
            "idempotency_key": "anchor-idempotency-resume",
        },
    )

    assert result["deduped"] is True
    assert result["status"] == "completed"
    assert existing.metadata_json["quality_gate"]["passed"] is True


@pytest.mark.asyncio
async def test_operator_can_edit_storyboard_and_recompile_governed_h3_prompt():
    result = await compile_production_plan({
        "brief": {
            "duration_seconds": 5,
            "ratio": "9:16",
            "audio_enabled": True,
            "script": "女：你看这个。\n男：好。",
        },
        "final_plan": {
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "ratio": "9:16",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "medium two shot",
                "camera_command": "[Push in]",
                "subject": "一对中国大陆年轻成年情侣",
                "action": "先对视，再错开动作自然交谈",
                "scene": "真实中国城市公寓客厅",
                "lighting": "柔和窗光",
                "mood": "轻松自然",
                "audio": "女：你看这个。男：好。",
            }],
        },
        "shots": [{
            "start_seconds": 0,
            "end_seconds": 5,
            "framing": "waist-up two shot",
            "camera_command": "[Tracking shot]",
            "subject": "一对中国大陆年轻成年情侣",
            "action": "女方先抬眉开口，男方延迟0.4秒转头回应，双方不得同时启动动作",
            "scene": "真实中国城市公寓客厅",
            "lighting": "柔和窗光",
            "mood": "松弛且有好奇感",
            "audio": "女：你看这个。男：好。",
        }],
    })
    plan = result["prepared_plan"]
    assert plan["shots"][0]["action"].startswith("女方先抬眉")
    assert plan["shots"][0]["camera_command"] == "[Tracking shot]"
    assert plan["user_shot_override"]["applied"] is True
    assert plan["user_shot_override"]["edited_shot_count"] == 1
    assert plan["user_shot_override"]["edited_action_indices"] == [1]
    assert len(plan["user_shot_override"]["compiled_prompt_sha256"]) == 64
    assert plan["integrated_multimodal_description"]
    assert "OPERATOR-EDITED VISUAL MOTION" in plan["integrated_multimodal_description"]
    assert "exact 0.4-second delay" in plan["integrated_multimodal_description"]
    assert "moving in sync" in plan["integrated_multimodal_description"]
    assert "你看这个" not in plan["integrated_multimodal_description"]


@pytest.mark.asyncio
async def test_storyboard_recompile_rejects_missing_shots():
    with pytest.raises(AppError) as captured:
        await compile_production_plan({
            "final_plan": {"h3_mode": "text_to_video", "duration_seconds": 5, "shots": []},
            "shots": [],
        })
    assert captured.value.code == "MEDIA_SHOTS_REQUIRED"


def test_direct_h3_prompt_keeps_operator_source_and_compiles_silent_plate_for_governed_voiceover():
    direct_prompt = (
        "Exactly one mainland Chinese adult woman in a real home. "
        "Natural pores, slight facial asymmetry, relaxed shoulders. No visible text."
    )
    compiled = compile_h3_plan(
        {
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "ratio": "9:16",
            "shots": [{"start_seconds": 0, "end_seconds": 5, "camera_command": "[Static shot]"}],
            "direct_h3_prompt": direct_prompt,
        },
        brief={
            "script": "女生：后来才发现，是用对套了。",
            "audio_enabled": True,
            "script_timing": {"shot_script": "女生：后来才发现，是用对套了。"},
        },
    )

    execution_prompt = compiled["integrated_multimodal_description"]
    assert execution_prompt != direct_prompt
    assert execution_prompt == compiled["video_prompt"]
    assert execution_prompt.startswith("SILENT CLEAN LIVE-ACTION VISUAL PLATE.")
    assert "No visible text" not in execution_prompt
    assert compiled["operator_direct_h3_prompt"] == direct_prompt
    assert compiled["direct_prompt_transform"]["version"] == "direct-clean-plate-v1"
    assert compiled["h3_execution_profile"] == "direct_clean_people_plate_silent_v1"
    assert compiled["planning_mode"] == "direct_h3_prompt"
    assert compiled["planning_model"] is None
    assert compiled["prompt_source"] == "operator_direct_deterministic_clean_plate"
    assert compiled["direct_prompt_validated"] is True
    assert compiled["requested_audio_prompt"] == "女生：后来才发现，是用对套了。"
    assert compiled["dialogue_delivery"]["status"] == "voiceover_renderer_required"
    assert compiled["dialogue_delivery"]["visual_strategy"] == "silent_reaction_plate_v2"
    assert compiled["dialogue_delivery"]["native_lip_sync"] is False
    assert compiled["recommended_params"]["audio_enabled"] is False
    recompiled_at_submit = compile_h3_plan(
        compiled,
        brief={
            "script": "女生：后来才发现，是用对套了。",
            "audio_enabled": True,
            "script_timing": {"shot_script": "女生：后来才发现，是用对套了。"},
        },
    )
    assert recompiled_at_submit["integrated_multimodal_description"] == execution_prompt
    assert recompiled_at_submit["operator_direct_h3_prompt"] == direct_prompt


def test_direct_h3_prompt_without_script_remains_exact_operator_prompt():
    direct_prompt = "One macro shot of clear water moving over smooth glass."
    compiled = compile_h3_plan(
        {
            "h3_mode": "text_to_video",
            "duration_seconds": 5,
            "ratio": "9:16",
            "shots": [{"start_seconds": 0, "end_seconds": 5, "camera_command": "[Static shot]"}],
            "direct_h3_prompt": direct_prompt,
        },
        brief={"script": "", "audio_enabled": False},
    )

    assert compiled["integrated_multimodal_description"] == direct_prompt
    assert compiled["operator_direct_h3_prompt"] == direct_prompt
    assert compiled["prompt_source"] == "operator_direct"
    assert compiled["h3_execution_profile"] == "direct_h3_prompt_v1"
    assert "direct_prompt_transform" not in compiled


def test_direct_h3_fifteen_second_real_dialogue_stays_in_voice_lineage_not_visual_prompt():
    script = (
        "女：开了吗？\n"
        "男：开了。\n"
        "女：我长话短说，有手机的赶紧去薅一单示例品牌001隐形套。\n"
        "男：就是很薄的那个吗？\n"
        "女：对，这次两位数到手。"
    )
    timing = _script_timing_for_clip(script, 15)
    direct_prompt = (
        "Two distinct mainland Chinese adults, one woman on the left and one man on the right, "
        "share a believable contemporary home interior in one coherent fifteen-second take. "
        "Natural pores, asymmetric everyday faces, restrained reactions, stable identity and eye-level 50mm framing."
    )
    compiled = compile_h3_plan(
        {
            "h3_mode": "text_to_video",
            "duration_seconds": 15,
            "ratio": "9:16",
            "shots": [{"start_seconds": 0, "end_seconds": 15, "camera_command": "[Static shot]"}],
            "direct_h3_prompt": direct_prompt,
            "script_timing": timing,
        },
        brief={"script": script, "audio_enabled": True, "script_timing": timing},
    )

    assert timing["fits"] is True
    assert timing["shot_script"] == script
    assert compiled["requested_audio_prompt"] == script
    assert "开了吗" not in compiled["integrated_multimodal_description"]
    assert compiled["h3_execution_profile"] == "direct_clean_people_plate_silent_v1"
    assert compiled["recommended_params"]["audio_enabled"] is False


@pytest.mark.asyncio
async def test_prepare_direct_h3_prompt_skips_deepseek_and_keeps_script_and_request(monkeypatch):
    from app.media import workbench_v2

    async def validated_references(*_args, **_kwargs):
        return []

    async def forbidden_plan_compare(*_args, **_kwargs):
        raise AssertionError("DeepSeek planner must not run in direct prompt mode")

    async def fake_presets(*_args, **_kwargs):
        return {"recommended_id": "quick_preview", "items": []}

    monkeypatch.setattr(
        workbench_v2,
        "_core",
        lambda: SimpleNamespace(
            MEDIA_BASELINE_PROFILE="deepseek-v4-flash",
            _validated_reference_requests=validated_references,
            _plan_compare=forbidden_plan_compare,
        ),
    )
    monkeypatch.setattr(workbench_v2, "output_presets", fake_presets)
    direct_prompt = "Exactly one mainland Chinese adult woman. Natural motion. No visible text."

    result = await prepare_production(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1", department_id="931765248"),
        SimpleNamespace(id="run-1", project_id="project-1", department_id="931765248"),
        {
            "creative_option": "direct_prompt",
            "direct_h3_prompt": direct_prompt,
            "brief": {
                "product": "示例品牌 超快感",
                "script": "女生：后来才发现，是用对套了。",
                "request": "国内真实生活场景，无字幕。",
                "platform": "douyin",
                "duration_seconds": 5,
                "audio_enabled": True,
            },
            "source_roles": [],
            "theme_divergence": {"enabled": False},
        },
    )

    assert result["planning_mode"] == "direct_h3_prompt"
    assert result["prompt_source"] == "operator_direct"
    assert result["source_understanding_pipeline"]["planner_model"] is None
    assert result["prepared_plan"]["integrated_multimodal_description"] != direct_prompt
    assert result["prepared_plan"]["operator_direct_h3_prompt"] == direct_prompt
    assert result["prepared_plan"]["h3_execution_profile"] == "direct_clean_people_plate_silent_v1"
    assert result["normalized_brief"]["script"] == "女生：后来才发现，是用对套了。"
    assert result["normalized_brief"]["request"].rstrip("。") == "国内真实生活场景，无字幕"


def test_operator_brief_plan_preserves_real_frontdesk_prompt_without_ai():
    brief = {
        "product": "示例品牌 超快感",
        "script": "女：你今天怎么这么开心？\n男：因为终于选对了。",
        "request": "去字幕，一比一复刻原视频动作，只换成超快感产品。",
        "duration_seconds": 8,
        "ratio": "9:16",
        "audio_enabled": True,
    }
    contract = parse_ad_material_brief(
        brief,
        [],
        None,
        inferred_mode="text_to_video",
    )

    plan = build_operator_brief_plan(brief, contract, h3_mode="text_to_video")

    assert plan["planning_mode"] == "operator_brief"
    assert plan["planning_model"] is None
    assert plan["prompt_source"] == "operator_input"
    assert plan["operator_prompt"] == brief["request"]
    assert plan["operator_prompt_preserved"] is True
    assert plan["shots"][0]["end_seconds"] == 8
    assert contract["subtitle_policy"] == "forbid_burned_in_text"


@pytest.mark.asyncio
async def test_prepare_operator_brief_skips_deepseek_and_keeps_operator_text(monkeypatch):
    from app.media import workbench_v2

    async def validated_references(*_args, **_kwargs):
        return []

    async def forbidden_plan_compare(*_args, **_kwargs):
        raise AssertionError("DeepSeek planner must not run in operator brief mode")

    async def fake_presets(*_args, **_kwargs):
        return {"recommended_id": "quick_preview", "items": []}

    monkeypatch.setattr(
        workbench_v2,
        "_core",
        lambda: SimpleNamespace(
            MEDIA_BASELINE_PROFILE="deepseek-v4-flash",
            _validated_reference_requests=validated_references,
            _plan_compare=forbidden_plan_compare,
        ),
    )
    monkeypatch.setattr(workbench_v2, "output_presets", fake_presets)
    operator_request = "中国年轻成年情侣在真实客厅自然对话，轮流说话，动作松弛，不要字幕。"
    operator_script = (
        "女：你不是说套套零点二就已经很薄了吗？我之前一直这样以为。\n"
        "男：后来用对了才发现，真正自然的感觉不只是看一个数字。"
    )

    result = await prepare_production(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1", department_id="931765248"),
        SimpleNamespace(id="run-1", project_id="project-1", department_id="931765248"),
        {
            "creative_option": "smart",
            "brief": {
                "product": "示例品牌 001 隐形套",
                "script": operator_script,
                "request": operator_request,
                "platform": "douyin",
                "duration_seconds": 5,
                "audio_enabled": True,
                "contains_person": True,
            },
            "source_roles": [],
            "theme_divergence": {"enabled": False},
        },
    )

    assert result["planning_mode"] == "operator_brief"
    assert result["prompt_source"] == "operator_input"
    assert result["source_understanding_pipeline"]["planner_model"] is None
    assert result["normalized_brief"]["request"] == operator_request
    assert result["normalized_brief"]["operator_input"] == {
        "product": "示例品牌 001 隐形套",
        "script": operator_script,
        "request": operator_request,
        "platform": "douyin",
        "duration_seconds": 5,
        "ratio": "9:16",
    }
    assert result["prepared_plan"]["operator_prompt"] == operator_request
    assert result["prepared_plan"]["planning_model"] is None
    assert result["prepared_plan"]["integrated_multimodal_description"]
    assert result["duration_auto_adjusted"] is False
    assert result["duration_contract"]["requested_seconds"] == 5
    assert result["normalized_brief"]["requested_duration_seconds"] == 5
    assert result["duration_recommendation_seconds"] > 5


def test_fitted_first_turn_cannot_collapse_two_person_dialogue_into_single_presenter():
    script = (
        "女：你不是说套套零点二就已经很薄了吗？我之前一直这样以为。\n"
        "男：后来用对了才发现，真正自然的感觉不只是看一个数字。"
    )
    timing = _script_timing_for_clip(script, 5)
    assert normalize_frontdesk_script(timing["shot_script"])["speaker_count"] == 1

    contract = parse_ad_material_brief(
        {
            "product": "示例品牌 001 隐形套",
            "script": script,
            "script_timing": timing,
            "request": "中国年轻成年情侣在真实客厅自然对话，不要字幕。",
            "duration_seconds": 5,
            "audio_enabled": True,
            "contains_person": True,
        },
        [],
        {},
        inferred_mode="text_to_video",
    )

    assert contract["content_format"] == "dialogue"
    assert contract["speaker_count"] == 2
    assert contract["actor_count"] == 2
    assert contract["speakers"] == ["女", "男"]
    assert contract["executable_script"] == timing["shot_script"]
    assert contract["script_normalization"]["executable_speaker_count"] == 1

    guarded = apply_ad_material_contract_to_plan(
        {
            "creative_goal": "通过真实自然的单人口播传达体验",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "subject": "一位年轻女性",
                "action": "面向镜头口播",
            }],
        },
        contract,
    )
    assert "优先保证轮流说话" in guarded["creative_goal"]
    assert "保持需求中明确的性别、人数和人物身份" in guarded["shots"][0]["subject"]
    assert "两人" in guarded["shots"][0]["subject"]


@pytest.mark.asyncio
async def test_prepare_ai_optimize_only_calls_deepseek_when_explicit(monkeypatch):
    from app.media import workbench_v2

    calls = []

    async def validated_references(*_args, **_kwargs):
        return []

    async def plan_compare(*_args, **_kwargs):
        calls.append("deepseek")
        return {
            "plan_comparison": {
                "baseline": {
                    "model": "deepseek-v4-flash",
                    "model_version": "production",
                    "output": {
                        "creative_goal": "优化后的真实生活对话钩子",
                        "duration_seconds": 5,
                        "ratio": "9:16",
                        "shots": [{
                            "start_seconds": 0,
                            "end_seconds": 5,
                            "framing": "腰部以上双人中景",
                            "subject": "两位中国大陆年轻成年人",
                            "action": "轮流自然对话，动作错峰",
                            "scene": "真实客厅",
                            "lighting": "真实室内光",
                            "mood": "自然松弛",
                            "camera_command": "[Static shot]",
                            "audio": "",
                        }],
                    },
                },
                "candidate": None,
            },
        }

    async def fake_presets(*_args, **_kwargs):
        return {"recommended_id": "quick_preview", "items": []}

    monkeypatch.setattr(
        workbench_v2,
        "_core",
        lambda: SimpleNamespace(
            MEDIA_BASELINE_PROFILE="deepseek-v4-flash",
            _validated_reference_requests=validated_references,
            _plan_compare=plan_compare,
        ),
    )
    monkeypatch.setattr(workbench_v2, "output_presets", fake_presets)

    result = await prepare_production(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1", department_id="931765248"),
        SimpleNamespace(id="run-1", project_id="project-1", department_id="931765248"),
        {
            "planning_mode": "ai_optimize",
            "creative_option": "smart",
            "brief": {
                "request": "中国年轻成年情侣在真实客厅自然对话，不要字幕。",
                "duration_seconds": 5,
                "audio_enabled": False,
                "contains_person": True,
            },
            "source_roles": [],
            "theme_divergence": {"enabled": False},
        },
    )

    assert calls == ["deepseek"]
    assert result["planning_mode"] == "ai_planned"
    assert result["prompt_source"] == "planning_model"
    assert result["source_understanding_pipeline"]["planner_model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_operator_brief_theme_divergence_requires_explicit_ai(monkeypatch):
    from app.media import workbench_v2

    async def validated_references(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        workbench_v2,
        "_core",
        lambda: SimpleNamespace(
            MEDIA_BASELINE_PROFILE="deepseek-v4-flash",
            _validated_reference_requests=validated_references,
        ),
    )

    with pytest.raises(AppError) as captured:
        await prepare_production(
            SimpleNamespace(),
            SimpleNamespace(id="user-1"),
            SimpleNamespace(id="project-1", department_id="931765248"),
            SimpleNamespace(id="run-1", project_id="project-1", department_id="931765248"),
            {
                "planning_mode": "operator_brief",
                "creative_option": "smart",
                "brief": {
                    "request": "国内成年人真实生活场景，不要字幕。",
                    "duration_seconds": 5,
                    "audio_enabled": False,
                },
                "source_roles": [],
                "theme_divergence": {"enabled": True, "direction_count": 6},
            },
        )

    assert captured.value.code == "MEDIA_THEME_REQUIRES_AI_OPTIMIZATION"


def test_workbench_node_online_falls_back_to_fresh_persisted_heartbeat(monkeypatch):
    from app.media import workbench_v2

    current = workbench_v2.now_bjt()
    node = SimpleNamespace(
        id="platform-media-5080",
        is_active=True,
        last_heartbeat=current - timedelta(seconds=30),
        bridge_connected_at=None,
    )
    monkeypatch.setattr(workbench_v2, "_core", lambda: SimpleNamespace(
        bridge_registry=SimpleNamespace(is_online=lambda _instance_id: False)
    ))

    assert _workbench_media_node_online(node) is True
    node.last_heartbeat = current - timedelta(seconds=300)
    assert _workbench_media_node_online(node) is False


def test_workbench_node_runtime_exposes_stale_heartbeat_reason(monkeypatch):
    from app.media import workbench_v2

    current = workbench_v2.now_bjt()
    node = SimpleNamespace(
        id="platform-media-5080",
        is_active=True,
        last_heartbeat=current - timedelta(hours=10),
        bridge_connected_at=None,
    )
    monkeypatch.setattr(workbench_v2, "_core", lambda: SimpleNamespace(
        bridge_registry=SimpleNamespace(is_online=lambda _instance_id: False)
    ))

    runtime = workbench_v2._workbench_media_node_runtime(node)

    assert runtime["online"] is False
    assert runtime["online_source"] == "persisted_heartbeat"
    assert runtime["offline_reason"] == "heartbeat_stale"
    assert runtime["heartbeat_age_seconds"] >= 10 * 60 * 60


def test_review_provenance_sidecar_is_sha_bound_and_does_not_claim_c2pa_signature():
    from app.media.workbench_v2 import _media_provenance_sidecar

    now = datetime.now().astimezone()
    row = SimpleNamespace(
        id="mvj-provenance", project_id="samplebrand-material-workbench", department_id="931765248",
        mode="reference_replay", created_at=now, completed_at=now,
        result_sha256="a" * 64,
        prompt_json={
            "prompt_policy_version": "minimax-h3-context-ir-v60",
            "integrated_multimodal_description": "中国成年情侣自然对话，无字幕。",
        },
        result_json={}, params_json={"seed": 42, "width": 480, "height": 864, "frames": 124, "fps": 24, "steps": 20},
        workflow_template_id="h3_r2v_v1", workflow_version="workflow-sha",
        model_version="MiniMax-H3", model_sha256="b" * 64,
        cloned_from_job_id=None, depends_on_job_id=None, continuation_chain_id=None,
        production_batch_id="batch-1",
    )
    output_asset = SimpleNamespace(
        id="asset-output",
        sha256="c" * 64,
        mime_type="video/mp4",
        metadata_json={
            "postprocess_provenance": {
                "product_overlay_applied": True,
                "product_overlay_sha256s": ["e" * 64, "f" * 64],
                "product_overlay_roles": ["product_packshot", "product_detail"],
                "product_overlay_config_sha256": "1" * 64,
            }
        },
    )
    source_asset = SimpleNamespace(id="asset-source", sha256="d" * 64, mime_type="video/mp4")
    attempt = SimpleNamespace(instance_id="data-primary", attempt_no=1, started_at=now, completed_at=now)

    sidecar = _media_provenance_sidecar(
        row,
        output_asset,
        [{"asset_id": source_asset.id, "role": "reference_video", "purpose": "结构参考"}],
        {source_asset.id: source_asset},
        attempt,
    )

    assert sidecar["schema_version"] == "skillforge.media.provenance.v1"
    assert sidecar["intent"] == "edit"
    assert sidecar["output"]["sha256"] == "c" * 64
    assert sidecar["ingredients"][0]["sha256"] == "d" * 64
    assert sidecar["generator"]["seed"] == 42
    assert sidecar["execution"]["instance_id"] == "data-primary"
    assert sidecar["postprocess"] == {
        "product_overlay_applied": True,
        "product_overlay_sha256s": ["e" * 64, "f" * 64],
        "product_overlay_roles": ["product_packshot", "product_detail"],
        "product_overlay_config_sha256": "1" * 64,
    }
    assert sidecar["content_credentials"] == {
        "status": "unsigned_sidecar",
        "embedded": False,
        "reason": "C2PA signer is not configured; verify the SHA256-bound SkillForge audit trail",
    }
    assert re.fullmatch(r"[0-9a-f]{64}", sidecar["fingerprint_sha256"])
    assert sidecar == _media_provenance_sidecar(
        row,
        output_asset,
        [{"asset_id": source_asset.id, "role": "reference_video", "purpose": "结构参考"}],
        {source_asset.id: source_asset},
        attempt,
    )


def test_governed_dialogue_parser_assigns_stable_gendered_voices():
    lines = parse_dialogue_lines(
        "女：宝，今晚早点回来。\n男：好，我也正想和你聊聊。",
        female_voice=f"{SPEECH_DELIVERY_MODEL}:claire",
        male_voice=f"{SPEECH_DELIVERY_MODEL}:alex",
    )
    assert [(line.speaker, line.speaker_role, line.text) for line in lines] == [
        ("女", "female", "宝，今晚早点回来。"),
        ("男", "male", "好，我也正想和你聊聊。"),
    ]
    assert lines[0].voice.endswith(":claire")
    assert lines[1].voice.endswith(":alex")


def test_governed_dialogue_parser_separates_same_gender_actor_voices():
    lines = parse_dialogue_lines(
        "左边女生：你听说了吗？\n右边女生：真的有这种吗？\n左边女生：当然。",
        female_voice=f"{SPEECH_DELIVERY_MODEL}:claire",
        female_voice_secondary=f"{SPEECH_DELIVERY_MODEL}:bella",
        male_voice=f"{SPEECH_DELIVERY_MODEL}:alex",
        male_voice_secondary=f"{SPEECH_DELIVERY_MODEL}:benjamin",
    )
    assert lines[0].voice.endswith(":claire")
    assert lines[1].voice.endswith(":bella")
    assert lines[2].voice == lines[0].voice


def test_governed_dialogue_voice_rejects_arbitrary_provider_value():
    with pytest.raises(SpeechDeliveryError) as exc:
        _validated_voice("speech:external-clone:person", default_name="claire")
    assert exc.value.code == "SPEECH_VOICE_NOT_ALLOWED"


def test_governed_dialogue_uses_compact_benchmarked_cosyvoice_instruction():
    line = DialogueLine("女", "female", "真的，还不易破。", f"{SPEECH_DELIVERY_MODEL}:anna")
    assert _speech_input_text(line) == "自然生活化松弛<|endofprompt|>真的，还不易破。"
    assert "先轻微吸气" not in _speech_input_text(line)


def test_governed_dialogue_strips_stage_directions_and_uses_shared_performance_style():
    lines = parse_dialogue_lines(
        "女：只要两位数（骄傲的语气）\n男：你小点声。",
        female_voice=f"{SPEECH_DELIVERY_MODEL}:claire",
        female_voice_secondary=f"{SPEECH_DELIVERY_MODEL}:bella",
        male_voice=f"{SPEECH_DELIVERY_MODEL}:alex",
        male_voice_secondary=f"{SPEECH_DELIVERY_MODEL}:benjamin",
        performance_profile={"delivery_style": "confidential"},
    )

    assert [item.text for item in lines] == ["只要两位数", "你小点声。"]
    assert lines[0].style_prompt == "自然生活化轻快自信吐字清晰"
    assert lines[1].style_prompt == "自然生活化轻声克制吐字清晰"
    assert _speech_input_text(lines[0]) == "自然生活化轻快自信吐字清晰<|endofprompt|>只要两位数"
    assert "骄傲的语气" not in _speech_input_text(lines[0])


def test_governed_dialogue_wave_info_ignores_streaming_header_frame_sentinel(tmp_path):
    buffer = bytearray()
    wav_path = tmp_path / "streaming-provider.wav"
    with wave.open(str(wav_path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\0\0" * 4410)
    buffer.extend(wav_path.read_bytes())
    data_offset = buffer.find(b"data")
    assert data_offset > 0
    buffer[data_offset + 4:data_offset + 8] = (0x7FFFFFFF).to_bytes(4, "little")
    wav_path.write_bytes(buffer)

    channels, width, rate, frames, duration = _wave_info(wav_path)

    assert (channels, width, rate, frames) == (1, 2, 44100, 4410)
    assert duration == pytest.approx(0.1)


def test_governed_dialogue_mux_adds_real_audio_stream(tmp_path):
    ffmpeg = _ffmpeg_executable()
    video = tmp_path / "clean-plate.mp4"
    created = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x96:r=24:d=1.2",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video),
        ],
        capture_output=True,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr.decode("utf-8", errors="replace")
    wav = tmp_path / "line.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\0\0" * 11025)
    line = DialogueLine("女", "female", "测试", f"{SPEECH_DELIVERY_MODEL}:claire")
    audio_content, video_content, metadata = _assemble_and_mux(
        video,
        [(line, wav.read_bytes())],
        target_duration_seconds=1.2,
        line_gap_seconds=0.18,
    )
    delivered = tmp_path / "delivered.mp4"
    delivered.write_bytes(video_content)
    assert audio_content[:4] == b"RIFF"
    assert metadata["target_duration_seconds"] == 1.2
    assert metadata["verified_streams"] == ["video", "audio"]
    assert metadata["audio_codec"] == "aac"
    audio_probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(delivered), "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True,
        timeout=30,
    )
    assert audio_probe.returncode == 0, audio_probe.stderr.decode("utf-8", errors="replace")


def test_governed_dialogue_mux_aligns_each_voice_to_performance_timeline(tmp_path):
    ffmpeg = _ffmpeg_executable()
    video = tmp_path / "timeline-clean-plate.mp4"
    created = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x96:r=24:d=1.8",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video),
        ],
        capture_output=True,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr.decode("utf-8", errors="replace")

    rendered = []
    for index, value in enumerate((9000, -9000)):
        wav_path = tmp_path / f"timeline-line-{index}.wav"
        with wave.open(str(wav_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(array("h", [value] * 8820).tobytes())
        rendered.append((
            DialogueLine("女" if index == 0 else "男", "female" if index == 0 else "male", "测试", f"{SPEECH_DELIVERY_MODEL}:anna"),
            wav_path.read_bytes(),
        ))

    audio_content, _video_content, metadata = _assemble_and_mux(
        video,
        rendered,
        target_duration_seconds=1.8,
        line_gap_seconds=0.1,
        performance_timeline=[
            {"start_seconds": 0.1, "end_seconds": 0.55},
            {"start_seconds": 1.0, "end_seconds": 1.5},
        ],
    )

    with wave.open(BytesIO(audio_content), "rb") as stream:
        rate = stream.getframerate()
        samples = array("h")
        samples.frombytes(stream.readframes(stream.getnframes()))

    def level(start: float, end: float) -> float:
        window = samples[int(start * rate):int(end * rate)]
        return sum(abs(value) for value in window) / max(len(window), 1)

    assert level(0.12, 0.24) > 1000
    assert level(0.65, 0.85) < 10
    assert level(1.05, 1.18) > 1000
    assert metadata["timeline_alignment"]["mode"] == "performance_timeline_v1"
    assert metadata["timeline_alignment"]["line_count"] == 2
    assert metadata["timeline_alignment"]["planned_end_seconds"] == 1.5
    assert metadata["aligned_audio_duration_seconds"] == pytest.approx(1.8, abs=0.01)


def test_governed_dialogue_mux_rebalances_short_ai_slots_from_real_audio(tmp_path):
    ffmpeg = _ffmpeg_executable()
    video = tmp_path / "rebalanced-timeline-clean-plate.mp4"
    created = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x96:r=24:d=6.1",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video),
        ],
        capture_output=True,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr.decode("utf-8", errors="replace")

    rendered = []
    for index, (seconds, value) in enumerate(((1.2, 7000), (0.8, -7000), (2.2, 9000), (1.0, -9000))):
        wav_path = tmp_path / f"rebalanced-line-{index}.wav"
        with wave.open(str(wav_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(array("h", [value] * int(44100 * seconds)).tobytes())
        rendered.append((
            DialogueLine("女" if index % 2 == 0 else "男", "female" if index % 2 == 0 else "male", "测试", f"{SPEECH_DELIVERY_MODEL}:anna"),
            wav_path.read_bytes(),
        ))

    audio_content, _video_content, metadata = _assemble_and_mux(
        video,
        rendered,
        target_duration_seconds=6.0,
        line_gap_seconds=0.1,
        performance_timeline=[
            {"start_seconds": 0.0, "end_seconds": 0.8},
            {"start_seconds": 0.8, "end_seconds": 1.4},
            {"start_seconds": 1.4, "end_seconds": 5.0},
            {"start_seconds": 5.0, "end_seconds": 6.0},
        ],
    )

    alignment = metadata["timeline_alignment"]
    assert alignment["mode"] == "performance_timeline_v1"
    assert alignment["audio_rebalanced"] is True
    assert alignment["line_count"] == 4
    assert alignment["planned_end_seconds"] == pytest.approx(5.825, abs=0.03)
    assert max(item["speed_ratio"] for item in alignment["lines"]) <= 1.35
    assert alignment["lines"][1]["start_seconds"] > alignment["lines"][0]["end_seconds"]
    with wave.open(BytesIO(audio_content), "rb") as stream:
        assert stream.getnframes() / stream.getframerate() == pytest.approx(6.0, abs=0.01)


@pytest.mark.asyncio
async def test_governed_dialogue_config_requires_dedicated_secret(monkeypatch):
    async def fake_config(**_kwargs):
        return {
            "ai.api_key": "shared-text-key-must-not-be-reused",
            "ai.speech.enabled": True,
            "ai.speech.api_base": "https://api.siliconflow.cn/v1",
            "ai.speech.model": SPEECH_DELIVERY_MODEL,
        }

    monkeypatch.setattr("app.media.speech_delivery.get_ai_config", fake_config)
    with pytest.raises(SpeechDeliveryError) as exc:
        await load_speech_delivery_config()
    assert exc.value.code == "SPEECH_CONFIG_MISSING"


@pytest.mark.asyncio
async def test_governed_dialogue_config_can_inherit_allowlisted_siliconflow_vision_profile(monkeypatch):
    async def fake_config(**_kwargs):
        return {
            "ai.api_base": "https://api.deepseek.com",
            "ai.api_key": "deepseek-key-must-not-be-reused",
            "ai.vision.api_base": "https://api.siliconflow.cn/v1",
            "ai.vision.api_key": "existing-siliconflow-account",
            "ai.speech.model": SPEECH_DELIVERY_MODEL,
        }

    monkeypatch.setattr("app.media.speech_delivery.get_ai_config", fake_config)
    config = await load_speech_delivery_config()
    assert config["api_base"] == "https://api.siliconflow.cn/v1"
    assert config["api_key"] == "existing-siliconflow-account"
    assert config["credential_source"] == "vision_siliconflow_profile"
    assert config["female_voice"] == f"{SPEECH_DELIVERY_MODEL}:anna"


@pytest.mark.asyncio
async def test_governed_dialogue_config_honors_explicit_disable(monkeypatch):
    async def fake_config(**_kwargs):
        return {
            "ai.speech.enabled": False,
            "ai.vision.api_base": "https://api.siliconflow.cn/v1",
            "ai.vision.api_key": "existing-siliconflow-account",
            "ai.speech.model": SPEECH_DELIVERY_MODEL,
        }

    monkeypatch.setattr("app.media.speech_delivery.get_ai_config", fake_config)
    with pytest.raises(SpeechDeliveryError) as exc:
        await load_speech_delivery_config()
    assert exc.value.code == "SPEECH_RENDERER_DISABLED"


@pytest.mark.asyncio
async def test_governed_dialogue_config_rejects_arbitrary_endpoint(monkeypatch):
    async def fake_config(**_kwargs):
        return {
            "ai.speech.enabled": True,
            "ai.speech.api_base": "https://attacker.example/v1",
            "ai.speech.api_key": "dedicated-speech-key",
            "ai.speech.model": SPEECH_DELIVERY_MODEL,
        }

    monkeypatch.setattr("app.media.speech_delivery.get_ai_config", fake_config)
    with pytest.raises(SpeechDeliveryError) as exc:
        await load_speech_delivery_config()
    assert exc.value.code == "SPEECH_ENDPOINT_NOT_ALLOWED"


def test_dialogue_transcription_ignores_speaker_labels_and_provider_tags():
    result = evaluate_dialogue_transcription(
        "<|zh|><|NEUTRAL|>开了吗。开了！长话短说，赶紧去薅示例品牌001隐形套。",
        "女：开了吗\n男：开了\n女：长话短说，赶紧去薅示例品牌001隐形套。",
    )

    assert result["passed"] is True
    assert result["coverage"] == 1.0
    assert result["similarity"] == 1.0


@pytest.mark.asyncio
async def test_dialogue_transcription_uses_allowlisted_model_and_reports_missing_lines():
    class FakeResponse:
        status_code = 200
        text = '{"text":"开了吗开了"}'
        headers = {"x-siliconcloud-trace-id": "trace-asr-1"}

        @staticmethod
        def json():
            return {"text": "开了吗开了"}

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse()

    client = FakeClient()
    result = await _transcribe_dialogue_bytes(
        client,
        {
            "api_base": "https://api.siliconflow.cn/v1",
            "api_key": "server-only-key",
            "transcription_enabled": True,
            "transcription_model": SPEECH_TRANSCRIPTION_MODEL,
            "transcription_min_similarity": 0.82,
            "transcription_min_coverage": 0.88,
        },
        audio_content=b"RIFF" + b"0" * 100,
        expected_script="女：开了吗\n男：开了\n女：长话短说，赶紧去薅示例品牌001隐形套。",
    )

    assert result["passed"] is False
    assert result["error_code"] == "SPEECH_TRANSCRIPTION_MISMATCH"
    assert result["coverage"] < 0.88
    assert client.calls[0][0] == "https://api.siliconflow.cn/v1/audio/transcriptions"
    assert client.calls[0][1]["data"] == {"model": SPEECH_TRANSCRIPTION_MODEL}


@pytest.mark.parametrize(
    ("requested", "generation", "delivery", "target", "continuation"),
    [
        (3, 4, 3, None, False),
        *[(seconds, seconds, None, None, False) for seconds in range(5, 16)],
        (30, 15, None, 30, True),
    ],
)
def test_production_duration_contract_keeps_h3_execution_truthful(
    requested, generation, delivery, target, continuation
):
    contract = production_duration_contract(requested)
    assert contract == {
        "requested_seconds": requested,
        "generation_seconds": generation,
        "delivery_seconds": delivery,
        "target_seconds": target,
        "continuation": continuation,
    }
    assert production_frames_for_duration(generation) == (124 if generation == 5 else generation * 24)


@pytest.mark.parametrize("requested", [2, 4, 16, 29, 31, "bad"])
def test_production_duration_contract_rejects_unpublished_slots(requested):
    with pytest.raises(AppError) as raised:
        production_duration_contract(requested)
    assert raised.value.code == "MEDIA_DURATION_INVALID"


def test_frontdesk_dialogue_contract_uses_product_png_as_reference_not_people_first_frame():
    roles = [{"asset_id": "asset-product", "role": "product_packshot", "technical_role": "first_frame"}]
    understanding = {
        "assets": [{
            "asset_id": "asset-product",
            "semantic_role": "product_package",
            "contains_product": True,
            "people_presence": "none",
        }]
    }
    brief = {
        "product": "示例品牌 超快感",
        "script": "女：宝，来抱一下。\n男：不要，没心情。",
        "duration_seconds": 5,
        "audio_enabled": True,
    }
    contract = parse_ad_material_brief(brief, roles, understanding, inferred_mode="image_to_video")
    adapted = adapt_source_roles_for_ad_contract(roles, contract)
    assert contract["content_format"] == "dialogue"
    assert contract["recommended_h3_mode"] == "text_to_video"
    assert contract["product_only_reference_with_people"] is True
    assert adapted[0]["role"] == "product_packshot"
    assert adapted[0]["technical_role"] == "overlay_image"
    assert _normalize_source_roles(adapted)[0]["technical_role"] == "overlay_image"
    assert infer_production_mode(_normalize_source_roles(adapted)) == "text_to_video"
    assert len(contract["performance_beats"]) == 2
    assert contract["opening_visual_hook"]["pattern"] == "expectant_eye_line_reveal"
    assert contract["opening_visual_hook"]["end_seconds"] == 1.5
    assert any("不冻结" in rule for rule in contract["performance_rules"])

    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "image_to_video",
            "creative_goal": "生成自然对话",
            "shots": [{"start_seconds": 0, "end_seconds": 5, "framing": "medium shot"}],
            "negative_constraints": [],
        },
        contract,
    )
    assert planned["h3_mode"] == "text_to_video"
    assert planned["performance_timeline"][0]["speaker"] == "女"
    assert "前1.5秒视觉钩子" in planned["shots"][0]["action"]
    assert "非说话人冻结" in " ".join(planned["negative_constraints"])


def test_product_only_packshot_uses_dynamic_clean_plate_and_deterministic_center_overlay():
    roles = [{"asset_id": "asset-product", "role": "product_packshot", "technical_role": "first_frame"}]
    understanding = {
        "assets": [{
            "asset_id": "asset-product",
            "semantic_role": "product_package",
            "contains_product": True,
            "people_presence": "none",
        }]
    }
    brief = {
        "product": "示例品牌 超快感",
        "request": "生成有前五秒吸引力的商品氛围 B-roll，使用已审核透明包装图，不要人物。",
        "duration_seconds": 5,
        "audio_enabled": True,
    }

    contract = parse_ad_material_brief(brief, roles, understanding, inferred_mode="image_to_video")
    adapted = adapt_source_roles_for_ad_contract(roles, contract)
    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "image_to_video",
            "creative_goal": "商品氛围展示",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "商品中景",
                "camera_command": "[Static shot]",
                "subject": "示例品牌超快感包装",
                "action": "轻微光影变化",
                "scene": "暖色棚拍背景",
            }],
            "negative_constraints": [],
            "recommended_params": {"width": 480, "height": 864, "audio_enabled": True},
        },
        contract,
    )
    compiled = compile_h3_plan(planned, brief={**brief, "source_roles": adapted})

    assert contract["content_format"] == "product_demo"
    assert contract["product_only_reference_without_people"] is True
    assert contract["recommended_h3_mode"] == "text_to_video"
    assert contract["assembly_plan"]["commercial_preference"] == "dynamic_clean_plate_then_deterministic_product_overlay"
    assert adapted[0]["technical_role"] == "overlay_image"
    assert planned["product_overlay"] == {
        "enabled": True,
        "source_role": "overlay_image",
        "business_roles": ["product_packshot", "product_detail"],
        "anchor": "center",
        "width_ratio": 0.38,
        "safe_margin_ratio": 0.08,
        "motion_profile": "static_verified_product_dynamic_background",
        "asset_count": 1,
        "layout": "single_verified_product_v1",
    }
    assert compiled["h3_mode"] == "text_to_video"
    assert compiled["h3_execution_profile"] == "clean_dynamic_product_background_plate_v1"
    assert compiled["recommended_params"]["audio_enabled"] is False
    prompt = compiled["integrated_multimodal_description"]
    assert "CLEAN DYNAMIC COMMERCIAL BACKGROUND PLATE" in prompt
    assert "broad diffused highlight travels once" in prompt
    assert "central 38 percent" in prompt
    assert "示例品牌" not in prompt
    assert "package" not in prompt.casefold()
    assert "product" not in prompt.casefold()
    assert prompt.isascii()


def test_packshot_and_detail_compile_as_governed_duo_with_a_larger_clean_safe_zone():
    roles = [
        {"asset_id": "asset-pack", "role": "product_packshot", "technical_role": "first_frame"},
        {"asset_id": "asset-detail", "role": "product_detail", "technical_role": "reference_image"},
    ]
    understanding = {
        "assets": [
            {
                "asset_id": "asset-pack",
                "semantic_role": "product_package",
                "contains_product": True,
                "people_presence": "none",
            },
            {
                "asset_id": "asset-detail",
                "semantic_role": "product_detail",
                "contains_product": True,
                "people_presence": "none",
            },
        ]
    }
    brief = {
        "product": "示例品牌 超快感",
        "request": "包装和单片一起展示，不要人物，商品图必须保持真实。",
        "duration_seconds": 5,
        "audio_enabled": False,
    }

    contract = parse_ad_material_brief(brief, roles, understanding, inferred_mode="image_to_video")
    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "image_to_video",
            "creative_goal": "商品组合展示",
            "shots": [{"start_seconds": 0, "end_seconds": 5, "camera_command": "[Static shot]"}],
            "negative_constraints": [],
        },
        contract,
    )
    compiled = compile_h3_plan(
        planned,
        brief={**brief, "source_roles": adapt_source_roles_for_ad_contract(roles, contract)},
    )

    assert contract["assembly_plan"]["overlay_asset_count"] == 2
    assert contract["assembly_plan"]["overlay_layout"] == "packshot_detail_duo_v1"
    assert planned["product_overlay"]["asset_count"] == 2
    assert planned["product_overlay"]["layout"] == "packshot_detail_duo_v1"
    assert all(item["technical_role"] == "overlay_image" for item in adapt_source_roles_for_ad_contract(roles, contract))
    assert "central 52 percent" in compiled["integrated_multimodal_description"]


@pytest.mark.parametrize(
    ("script", "expected_speakers", "expected_turns"),
    [
        (
            "女：开了吗 男：开了 女：ok我长话短说啊，赶紧去薅一单 男：就是很薄的那个吗 女：对的",
            ["女", "男"],
            5,
        ),
        (
            "1：为什么不带套 2：我带了啊，没骗你 1：好吧，原来是这样",
            ["1", "2"],
            3,
        ),
        (
            "魔力玻玻： 女：开了吗 男：开了 女：这次只要两位数到手16只",
            ["女", "男"],
            3,
        ),
    ],
)
def test_frontdesk_inline_dialogue_from_real_workbook_preserves_turns(
    script, expected_speakers, expected_turns
):
    contract = parse_ad_material_brief(
        {
            "product": "示例品牌",
            "script": script,
            "duration_seconds": 15,
            "audio_enabled": True,
        },
        [],
        None,
        inferred_mode="text_to_video",
    )

    assert contract["policy_version"] == "frontdesk-ad-material-v21"
    assert contract["content_format"] == "dialogue"
    assert contract["speakers"] == expected_speakers
    assert contract["speaker_count"] == 2
    assert contract["actor_count"] == 2
    assert contract["dialogue_continuity_lock"]["checkpoints_percent"] == [40, 60, 80, 100]
    assert len(contract["performance_beats"]) == expected_turns
    assert contract["performance_beats"][0]["start_seconds"] == 0
    assert contract["performance_beats"][-1]["end_seconds"] == 15


def test_frontdesk_multiline_single_presenter_stays_talking_head():
    contract = parse_ad_material_brief(
        {
            "product": "示例品牌 超快感",
            "script": (
                "女生：第一次约会，我以为他是男大体育生。\n"
                "女生：结果我发现是用对套了。\n"
                "女生：真的不能小看这个快感套。"
            ),
            "duration_seconds": 14,
            "audio_enabled": True,
        },
        [],
        None,
        inferred_mode="text_to_video",
    )

    assert contract["content_format"] == "talking_head"
    assert contract["speaker_count"] == 1
    assert contract["speakers"] == ["女生"]
    assert contract["actor_count"] == 1
    assert contract["cast_market"] == "mainland_china"
    assert contract["face_style"] == "authentic_live_action"
    assert not any("说话人标签不完整" in item for item in contract["warnings"])

    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "text_to_video",
            "creative_goal": "单人自然口播投流钩子",
            "duration_seconds": 14,
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 14,
                "framing": "medium shot",
                "subject": "一名成年女性",
                "action": "自然回忆后给出恍然反应",
                "camera_command": "[Static shot]",
            }],
            "negative_constraints": [],
            "warnings": [
                "实测 H3 联合生成中文对白会烧入可见字幕；当前先生成无字静音人物底片，"
                "原台词已保留，底片完成后由平台受控配音器生成对应语音并合成最终成片；"
                "只有实际检测到视频流和音频流后才进入完整审片。"
            ],
        },
        contract,
    )
    compiled = compile_h3_plan(
        planned,
        brief={
            "product": "示例品牌 超快感",
            "script": contract["executable_script"],
            "request": "一名成年女性自然口播；无字幕、无角标。",
            "duration_seconds": 14,
            "audio_enabled": True,
            "contains_person": True,
        },
    )
    assert any("生成单角色语音" in item for item in compiled["warnings"])
    assert not any("生成双角色语音" in item for item in compiled["warnings"])
    assert sum(item.startswith("实测 H3 联合生成中文对白") for item in compiled["warnings"]) == 1
    prompt = compiled["integrated_multimodal_description"]
    assert "CLEAN ONE-PERSON LIVE-ACTION SCENE PLATE" in prompt
    assert "exactly one adult woman" in prompt
    assert "VISIBLE COMMERCIAL APPEARANCE" in prompt
    assert "real pores" in prompt
    assert "mainland Chinese adult" not in prompt
    assert "preserve the same single adult identity" in prompt
    assert "same two adult identities" not in prompt
    assert "while the other listens" not in prompt
    assert "listener reactions" not in prompt
    assert "another person" not in " ".join(shot["action"] for shot in planned["shots"]).casefold()
    assert all("另一人" not in shot["action"] for shot in planned["shots"])


def test_unlabelled_workbook_monologue_keeps_explicit_female_cast_from_requirement():
    brief = {
        "product": "示例品牌 超快感",
        "request": "中国大陆25至30岁年轻女性，在真实居家客厅中自然口播。",
        "script": (
            "第一次约会，我以为他是男大体育生\n"
            "结果我发现是用对套了\n"
            "真的不能小看这个快感套"
        ),
        "platform": "douyin",
        "duration_seconds": 15,
        "contains_person": True,
    }
    contract = parse_ad_material_brief(brief, [], None, inferred_mode="text_to_video")
    planned = apply_ad_material_contract_to_plan(
        build_operator_brief_plan(brief, contract, h3_mode="text_to_video"),
        contract,
    )
    compiled = compile_h3_plan(planned, brief=brief)

    assert contract["content_format"] == "talking_head"
    assert contract["requested_cast_profile"] == "single_woman"
    assert "同一位中国大陆成年女性真实人物" in planned["shots"][0]["subject"]
    assert "同一位中国大陆成年女性真实人物" in compiled["integrated_multimodal_description"]
    assert "adult presenter" not in compiled["integrated_multimodal_description"]


def test_real_fifteen_second_inline_dialogue_keeps_mid_and_final_orientation_locks():
    script = (
        "女：开了吗 男：开了 女：ok我长话短说啊，你们如果有手机的话，"
        "赶紧去藅一单这个示例品牌001隐形套 男：就是很薄的那个吗 "
        "女：对的之前没有做活动，但这次只要两位数到手"
    )
    brief = {
        "product": "示例品牌 001 隐形套",
        "script": script,
        "request": "15秒中国年轻成年情侣居家自然交流底片，无商品、无包装、无字幕。",
        "duration_seconds": 15,
        "audio_enabled": True,
    }
    contract = parse_ad_material_brief(brief, [], None, inferred_mode="text_to_video")
    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "text_to_video",
            "creative_goal": "情侣居家自然交流底片",
            "duration_seconds": 15,
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 15,
                "framing": "腰部以上双人中景",
                "subject": "一位成年女性和一位成年男性",
                "action": "两人轮流表达并给出延迟反应",
                "camera_command": "[Push in]",
            }],
            "reference_roles": [],
            "negative_constraints": [],
        },
        contract,
    )
    compiled = compile_h3_plan(planned, brief=brief)
    prompt = compiled["integrated_multimodal_description"]

    assert compiled["prompt_policy_version"] == "minimax-h3-context-ir-v60"
    assert compiled["h3_execution_profile"] == "clean_dialogue_plate_silent_v1"
    assert contract["dialogue_continuity_lock"]["checkpoints_percent"] == [40, 60, 80, 100]
    assert "MID-TO-END ORIENTATION LOCK" in prompt
    assert "FINAL-FRAME SETTLE" in prompt
    assert "FRAMING LOCK" in prompt
    assert "each face stays below twenty-four percent" in prompt
    assert "fixed waist-up medium two-shot [Static shot]" in prompt
    assert "[Push in]" not in prompt
    assert re.search(r"T1 0\.00-[0-9.]+s: left adult", prompt)
    assert re.search(r"T2 [0-9.]+-[0-9.]+s: right adult", prompt)
    assert re.search(r"T3 [0-9.]+-[0-9.]+s: P1 [0-9.]+-[0-9.]+s left adult", prompt)
    assert re.search(r"T4 [0-9.]+-[0-9.]+s: .*?right adult", prompt)
    assert re.search(r"T5 [0-9.]+-[0-9.]+s: P1 [0-9.]+-[0-9.]+s left adult", prompt)
    assert "left adult" in prompt
    assert "right adult" in prompt
    assert "P1 " in prompt
    assert "P2 " in prompt
    assert "P3 " in prompt
    assert "open-palm arc inside the lower third" in prompt
    assert "returns to waist support" in prompt
    assert "two adults with stable contrasting identities" in prompt
    assert "without inventing ages, face shapes, hair styles or skin tones" in prompt
    assert "mainland Chinese adults" not in prompt
    assert "about 29" not in prompt and "about 34" not in prompt
    assert "instead of holding one repeated smile" in prompt
    assert len(prompt) <= 7000
    for unsafe_speech_cue in ("speaking", "spoken", "question", "dialogue", "conversation", "listens"):
        assert unsafe_speech_cue not in prompt.casefold()


def test_frontdesk_close_friend_whisper_feedback_drives_visual_and_voice_profile():
    brief = {
        "product": "示例品牌 AIR",
        "script": "女1：你小点声，真的有那么薄吗？\n女2：我也是刚知道。",
        "request": "真实闺蜜局，两个人坐在沙发边吃东西，用悄悄话的感觉；神态自然，不要一直低头，不要字幕。",
        "duration_seconds": 8,
        "audio_enabled": True,
    }
    contract = parse_ad_material_brief(brief, [], None, inferred_mode="text_to_video")

    assert contract["performance_profile"] == {
        "version": "frontdesk-performance-profile-v1",
        "social_context": "close_friends",
        "delivery_style": "confidential",
        "camera_pace": "locked_or_single_slow_move",
        "max_primary_actions_per_beat": 1,
        "listener_reaction_delay_seconds": 0.3,
        "max_continuous_downward_gaze_seconds": 0.6,
        "return_to_partner_or_camera_gaze": True,
        "posture_reset_between_beats": True,
        "avoid_repeated_smile_or_nod": True,
    }
    assert any("低头" in item and "0.6秒" in item for item in contract["performance_rules"])

    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "text_to_video",
            "creative_goal": "真实闺蜜局",
            "duration_seconds": 8,
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 8,
                "framing": "腰部以上双人中景",
                "subject": "两位成年女性朋友",
                "action": "自然交流",
                "camera_command": "[Static shot]",
            }],
            "negative_constraints": [],
        },
        contract,
    )
    compiled = compile_h3_plan(planned, brief=brief)
    prompt = compiled["integrated_multimodal_description"]

    assert compiled["prompt_policy_version"] == "minimax-h3-context-ir-v60"
    assert "SOCIAL DYNAMICS - two familiar adult friends" in prompt
    assert "PERFORMANCE RHYTHM - compact private motion" in prompt
    assert "GAZE RECOVERY - any glance below the partner's face lasts no longer than 0.6 seconds" in prompt
    assert len(prompt) <= 7000


def test_frontdesk_prompt_library_direction_labels_are_shared_by_timing_and_voice():
    script = (
        "左边：哎，你用过专为女性设计的套套吗？"
        "右边：真的有这种套套吗？"
        "左边：不是0.1也不是0.2，是更轻薄的日常选择。"
    )
    normalized = normalize_frontdesk_script(script)
    assert normalized["changed"] is True
    assert normalized["turn_count"] == 3
    assert normalized["speaker_count"] == 2
    assert normalized["normalized_script"].splitlines() == [
        "左边：哎，你用过专为女性设计的套套吗？",
        "右边：真的有这种套套吗？",
        "左边：不是0.1也不是0.2，是更轻薄的日常选择。",
    ]

    timing = _script_timing_for_clip(script, 5)
    assert timing["fits"] is False
    assert timing["original_line_count"] == 3
    assert timing["shot_line_count"] < timing["original_line_count"]

    rendered = parse_dialogue_lines(
        script,
        female_voice=f"{SPEECH_DELIVERY_MODEL}:anna",
        male_voice=f"{SPEECH_DELIVERY_MODEL}:alex",
        female_voice_secondary=f"{SPEECH_DELIVERY_MODEL}:bella",
        male_voice_secondary=f"{SPEECH_DELIVERY_MODEL}:benjamin",
    )
    assert [(item.speaker, item.text) for item in rendered] == [
        ("左边", "哎，你用过专为女性设计的套套吗？"),
        ("右边", "真的有这种套套吗？"),
        ("左边", "不是0.1也不是0.2，是更轻薄的日常选择。"),
    ]
    assert rendered[0].voice == rendered[2].voice
    assert rendered[0].voice != rendered[1].voice
    assert [item.speaker_role for item in rendered] == ["female", "female", "female"]


def test_frontdesk_product_heading_is_not_counted_as_a_speaker():
    normalized = normalize_frontdesk_script("魔力玻玻：润薄通透，画面保持干净。")
    assert normalized["turn_count"] == 0
    contract = parse_ad_material_brief(
        {"product": "示例品牌 魔力玻玻", "script": "魔力玻玻：润薄通透，画面保持干净。"},
        [],
        None,
        inferred_mode="text_to_video",
    )
    assert contract["speaker_count"] == 0
    assert contract["speakers"] == []


def test_frontdesk_answer_suffix_is_a_real_second_turn():
    normalized = normalize_frontdesk_script("画外音：怕痒的人能用吗？女生回答：可以先从轻柔体感开始。")
    assert normalized["turn_count"] == 2
    assert normalized["speakers"] == ["画外音", "女生"]


def test_frontdesk_real_workbook_letter_and_named_roles_are_not_merged():
    lettered = normalize_frontdesk_script(
        "A:猜这个多少钱？\nB:99块9。\nA:59块9。\nB:这么划算，在哪买？"
    )
    assert lettered["turn_count"] == 4
    assert lettered["speaker_count"] == 2
    assert lettered["speakers"] == ["A", "B"]

    retail = normalize_frontdesk_script(
        "女：买。男：不买。店员：两位好，想看哪款？女：我想看这一款。"
    )
    assert retail["turn_count"] == 4
    assert retail["speaker_count"] == 3
    assert retail["speakers"] == ["女", "男", "店员"]

    street = normalize_frontdesk_script(
        "女生：每秒五毛和一个亿，你选哪个？路人甲：我选一个亿。路人乙：我选每秒五毛。"
    )
    assert street["turn_count"] == 3
    assert street["speaker_count"] == 3


def test_frontdesk_product_variant_heading_does_not_join_previous_turn():
    normalized = normalize_frontdesk_script(
        "魔力玻玻：\n女：开了吗\n男：开了\n001：\n女：开了吗\n男：开了"
    )
    assert normalized["turn_count"] == 4
    assert all(not item["line"].endswith("001：") for item in normalized["turns"])


def test_very_long_default_dialogue_uses_longest_native_clip_before_turn_fitting():
    script = "\n".join(f"女：这是第{index}段需要自然说完的完整投流台词。" for index in range(1, 13))
    assert _recommended_dialogue_duration(script, 5) == 15


def test_product_dialogue_compiles_clean_plate_without_visible_dialogue_and_keeps_audio():
    roles = [{"asset_id": "asset-product", "role": "product_packshot", "technical_role": "first_frame"}]
    understanding = {
        "assets": [{
            "asset_id": "asset-product",
            "semantic_role": "product_package",
            "contains_product": True,
            "people_presence": "none",
        }]
    }
    dialogue = "女：宝，来抱一下。\n男：不要，没心情。"
    contract = parse_ad_material_brief(
        {
            "product": "示例品牌 超快感",
            "script": dialogue,
            "request": "双人自然对话；不要字幕、文字或水印。",
            "duration_seconds": 5,
            "audio_enabled": True,
        },
        roles,
        understanding,
        inferred_mode="image_to_video",
    )
    planned = apply_ad_material_contract_to_plan(
        {
            "h3_mode": "image_to_video",
            "creative_goal": "一对成年人居家自然对话",
            "shots": [{
                "start_seconds": 0,
                "end_seconds": 5,
                "framing": "medium shot",
                "subject": "一对成年男女",
                "action": "轮流对话并自然回应",
                "camera_command": "[Static shot]",
            }],
            "reference_roles": [{
                "asset_id": "asset-product",
                "role": "overlay_image",
                "business_role": "product_packshot",
                "purpose": "由 Bridge 确定性植入真实商品透明图",
            }],
            "negative_constraints": [],
        },
        contract,
    )
    compiled = compile_h3_plan(
        planned,
        brief={
            "product": "示例品牌 超快感",
            "request": "双人自然对话；不要字幕、文字或水印。",
            "source_roles": adapt_source_roles_for_ad_contract(roles, contract),
        },
    )

    prompt = compiled["integrated_multimodal_description"]
    assert compiled["h3_mode"] == "text_to_video"
    assert compiled["reference_roles"] == []
    assert compiled["brand_guardrails"]["product_render_policy"] == "clean_plate_then_verified_overlay"
    assert compiled["product_overlay"] == {
        "enabled": True,
        "source_role": "overlay_image",
        "business_roles": ["product_packshot", "product_detail"],
        "anchor": "bottom_right",
        "width_ratio": 0.22,
        "safe_margin_ratio": 0.05,
        "motion_profile": "static_verified_product",
        "asset_count": 1,
        "layout": "single_verified_product_v1",
    }
    assert compiled["h3_execution_profile"] == "clean_people_plate_silent_v4"
    assert "CLEAN TWO-PERSON LIVE-ACTION SCENE PLATE" in prompt
    assert "bao lai bao yi xia" not in prompt
    assert "bu yao mei xin qing" not in prompt
    assert "示例品牌" not in prompt
    assert "商品" not in prompt
    assert "包装" not in prompt
    assert "subtitle" not in prompt.casefold()
    assert "caption" not in prompt.casefold()
    assert prompt.isascii()
    assert compiled["audio_prompt"] == ""
    assert compiled["requested_audio_prompt"] == dialogue
    assert compiled["recommended_params"]["audio_enabled"] is False
    assert compiled["dialogue_delivery"]["status"] == "voiceover_renderer_required"
    assert compiled["dialogue_delivery"]["visual_strategy"] == "silent_reaction_plate_v2"
    assert compiled["dialogue_delivery"]["native_lip_sync"] is False
    for forbidden in ("speak", "spoken", "question", "dialogue", "conversation", "voiceover", "mouth"):
        assert forbidden not in prompt.casefold()
    assert not any("overlay_image" in item and "不支持" in item for item in compiled["warnings"])
    assert compiled["warnings"].count(
        "人物镜头将先生成无商品干净底片，再由 Bridge 使用已审核透明 PNG 固定植入；"
        "真实包装不会交给 H3 重绘，成片仍需检查遮挡、比例和安全区。"
    ) == 1


def test_handheld_product_request_is_not_misrepresented_as_verified_interaction():
    roles = [{"asset_id": "asset-product", "role": "product_packshot", "technical_role": "first_frame"}]
    understanding = {
        "assets": [{
            "asset_id": "asset-product",
            "semantic_role": "product_package",
            "contains_product": True,
            "people_presence": "none",
        }]
    }
    contract = parse_ad_material_brief(
        {
            "product": "示例品牌 超快感",
            "script": "女：这个真的很薄吗？\n男：你拿起来看看。",
            "request": "中国年轻成年情侣自然对话，女方手上拿着真实商品包装展示，包装不能变形。",
            "contains_person": True,
            "platform": "douyin",
        },
        roles,
        understanding,
        inferred_mode="image_to_video",
    )

    assert contract["requested_product_interaction"] == "handheld"
    assert contract["assembly_plan"]["product_interaction_allowed"] is False
    assert contract["assembly_plan"]["execution_product_interaction"] == "static_verified_overlay"
    assert any("不会伪装成真实手持" in item for item in contract["warnings"])


def test_output_ratio_orients_same_benchmarked_pixel_class():
    vertical = {"width": 480, "height": 864, "steps": 20}
    assert output_params_for_ratio(vertical, "9:16") == {**vertical, "ratio": "9:16"}
    assert output_params_for_ratio(vertical, "16:9") == {
        "width": 864, "height": 480, "steps": 20, "ratio": "16:9"
    }
    with pytest.raises(AppError) as raised:
        normalize_output_ratio("1:1")
    assert raised.value.code == "MEDIA_OUTPUT_RATIO_INVALID"


def test_1080p_enhancement_recovers_only_sha256_bound_bridge_probe():
    probe = {
        "status": "ok",
        "duration_seconds": 8.0,
        "streams": [
            {"codec_type": "video", "width": 480, "height": 864, "avg_frame_rate": "24/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    result = {
        "sha256": "source-sha",
        "media_probe": probe,
        "results": [{"sha256": "other-sha", "media_probe": {**probe, "duration_seconds": 99}}],
    }

    assert _sha256_bound_bridge_probe(result, "source-sha") == probe
    assert _sha256_bound_bridge_probe(result, "wrong-sha") == {}
    assert _sha256_bound_bridge_probe(
        {"sha256": "source-sha", "media_probe": {"status": "ok", "duration_seconds": 8, "streams": []}},
        "source-sha",
    ) == {}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("MEDIA_PLAN_NOT_FOUND", "重新生成分镜"),
        ("MEDIA_OUTPUT_PRESET_NOT_READY", "双节点稳定性基准"),
        ("MEDIA_REFERENCE_PREPROCESS_FAILED", "自动处理失败"),
        ("MEDIA_REVIEW_HARD_GATE_BLOCKED", "硬合规门禁"),
    ],
)
def test_media_errors_are_actionable_instead_of_unknown(code, expected):
    error = AppError(code, 422)
    assert error.message != "未知错误"
    assert expected in error.message


def test_workflow_modes_can_only_resolve_to_allowlisted_bridge_templates():
    allowed = {"h3_t2v_v1", "h3_i2v_v1", "h3_r2v_v1"}
    assert _workflow_mode("text_to_video", {}) == ("text_to_video", "h3_t2v_v1")
    assert _workflow_mode("image_to_video", {}) == ("image_to_video", "h3_i2v_v1")
    assert _workflow_mode("reference_replay", {}) == ("reference_replay", "h3_r2v_v1")
    assert _workflow_mode("specified_asset", {"h3_mode": "image_to_video"})[1] in allowed
    assert _workflow_mode("specified_asset", {"h3_mode": "reference_replay"})[1] in allowed
    with pytest.raises(AppError):
        _workflow_mode("specified_asset", {"h3_mode": "arbitrary_comfyui"})
    with pytest.raises(AppError):
        _workflow_mode("remote_shell", {})


def test_business_asset_roles_have_deterministic_h3_role_mapping():
    assert BUSINESS_ASSET_ROLES == {
        "character_first_frame": ("image", "first_frame"),
        "product_packshot": ("image", "first_frame"),
        "product_detail": ("image", "reference_image"),
        "visual_reference": ("image", "reference_image"),
        "motion_reference": ("video", "reference_video"),
        "audio_reference": ("audio", "reference_audio"),
        "continuity_anchor": ("video", "reference_video"),
    }


def test_business_asset_mentions_bind_human_name_to_governed_asset_id():
    roles = _normalize_source_roles([
        {
            "asset_id": "asset-pack",
            "role": "product_packshot",
            "purpose": "真实商品包装",
            "mention_token": "@超快感-盒1",
        },
        {
            "asset_id": "asset-unit",
            "role": "product_detail",
            "purpose": "真实商品单片",
            "mention_token": "@超快感-单片",
        },
    ])
    brief, bound = _bind_source_mentions(
        {"request": "保持原视频构图，把商品换成 @超快感-单片 ，去掉全部文字"}, roles
    )
    assert brief["source_mentions"] == [{
        "mention_token": "@超快感-单片",
        "asset_id": "asset-unit",
        "business_role": "product_detail",
        "technical_role": "reference_image",
    }]
    assert bound[0]["mentioned"] is False
    assert bound[1]["mentioned"] is True
    assert bound[1]["purpose"].startswith("用户在需求中以 @超快感-单片 明确指定")
    # Prepare -> submit rebinding is idempotent and does not duplicate the marker.
    _, rebound = _bind_source_mentions(brief, _normalize_source_roles(bound))
    assert rebound[1]["purpose"].count("明确指定") == 1


def test_business_asset_mentions_reject_ambiguous_or_unsafe_tokens():
    with pytest.raises(AppError) as duplicate:
        _bind_source_mentions(
            {"request": "使用 @同名"},
            _normalize_source_roles([
                {"asset_id": "asset-a", "role": "product_packshot", "mention_token": "@同名"},
                {"asset_id": "asset-b", "role": "product_detail", "mention_token": "@同名"},
            ]),
        )
    assert duplicate.value.code == "MEDIA_REFERENCE_MENTION_DUPLICATE"
    with pytest.raises(AppError) as unsafe:
        _normalize_source_roles([
            {"asset_id": "asset-a", "role": "product_packshot", "mention_token": "@商品<script>"},
        ])
    assert unsafe.value.code == "MEDIA_REFERENCE_MENTION_INVALID"


def test_qwen_source_understanding_corrects_product_images_without_inventing_people():
    roles = _normalize_source_roles([
        {"asset_id": "video", "role": "motion_reference"},
        {"asset_id": "box", "role": "character_first_frame"},
        {"asset_id": "unit", "role": "visual_reference"},
    ])
    understanding = _normalize_source_understanding(
        {
            "summary": "源片为商品散片展示，仅出现局部手部。",
            "assets": [
                {
                    "asset_id": "video", "semantic_role": "source_video", "people_presence": "partial_hands",
                    "people_count": 0, "contains_product": True, "contains_source_text": True,
                    "visual_summary": "局部手部拿起多片商品散片，无完整人物。",
                    "shots": [{
                        "start_seconds": 0, "end_seconds": 4, "framing": "近景", "subject": "多片商品散片与局部手部",
                        "action": "手部将散片扇形展开", "scene": "桌面", "camera_command": "[Static shot]",
                        "product_slot": "散片", "source_text": "字幕和底部声明",
                    }],
                },
                {
                    "asset_id": "box", "semantic_role": "product_package", "people_presence": "none",
                    "contains_product": True, "visual_summary": "示例品牌超快感盒装包装",
                },
                {
                    "asset_id": "unit", "semantic_role": "product_unit", "people_presence": "none",
                    "contains_product": True, "visual_summary": "示例品牌超快感独立单片",
                },
            ],
        },
        roles,
    )
    corrected, changes = _reconcile_source_roles_with_understanding(roles, understanding)
    assert [item["role"] for item in corrected] == ["motion_reference", "product_packshot", "product_detail"]
    assert [item["technical_role"] for item in corrected] == ["reference_video", "reference_image", "reference_image"]
    assert changes == [
        {"asset_id": "box", "from": "character_first_frame", "to": "product_packshot"},
        {"asset_id": "unit", "from": "visual_reference", "to": "product_detail"},
    ]

    brief = {"request": "去掉文字，一比一复刻，只换产品", "strip_reference_text": True}
    contract = _source_replication_contract(brief, corrected, understanding)
    assert contract["enabled"] is True
    assert contract["mode"] == "structure_replay_product_replace"
    assert contract["source_people_presence"] == "partial_hands"
    assert contract["forbid_invented_people"] is True
    planned = _apply_source_understanding_to_plan(
        {"creative_goal": "生成一对男女", "shots": [], "negative_constraints": []},
        {**brief, "source_replication_contract": contract},
    )
    assert "一对男女" not in planned["creative_goal"]
    assert planned["shots"][0]["subject"] == "多片商品散片与局部手部"
    assert "只替换源视频中的商品槽位" in planned["shots"][0]["action"]
    assert any("不得新增源视频中不存在的人物" in item for item in planned["negative_constraints"])


def test_locked_manual_asset_role_is_not_overridden_by_qwen_suggestion():
    roles = _normalize_source_roles([
        {"asset_id": "image", "role": "visual_reference", "role_locked": True},
    ])
    understanding = _normalize_source_understanding(
        {"assets": [{"asset_id": "image", "semantic_role": "product_package", "people_presence": "none"}]},
        roles,
    )
    corrected, changes = _reconcile_source_roles_with_understanding(roles, understanding)
    assert corrected[0]["role"] == "visual_reference"
    assert corrected[0]["role_locked"] is True
    assert changes == []


def test_source_understanding_requires_evidence_for_every_visual_asset():
    roles = _normalize_source_roles([
        {"asset_id": "video", "role": "motion_reference"},
        {"asset_id": "box", "role": "product_packshot"},
    ])
    incomplete = _normalize_source_understanding(
        {
            "assets": [{
                "asset_id": "video",
                "semantic_role": "source_video",
                "people_presence": "partial_hands",
                "shots": [{"start_seconds": 0, "end_seconds": 4, "subject": "局部手部与商品"}],
            }],
        },
        roles,
    )
    assert incomplete["assets"][1]["evidence_status"] == "missing"
    assert _source_understanding_complete(incomplete, roles) is False

    complete = _normalize_source_understanding(
        {
            "assets": [
                {
                    "asset_id": "video",
                    "semantic_role": "source_video",
                    "people_presence": "partial_hands",
                    "shots": [{"start_seconds": 0, "end_seconds": 4, "subject": "局部手部与商品"}],
                },
                {
                    "asset_id": "box",
                    "semantic_role": "product_package",
                    "people_presence": "none",
                    "visual_summary": "真实盒装商品",
                },
            ],
        },
        roles,
    )
    assert all(item["evidence_status"] == "verified" for item in complete["assets"])
    assert _source_understanding_complete(complete, roles) is True


def test_source_understanding_rejects_video_without_shot_evidence():
    roles = _normalize_source_roles([{"asset_id": "video", "role": "motion_reference"}])
    understanding = _normalize_source_understanding(
        {
            "assets": [{
                "asset_id": "video",
                "semantic_role": "source_video",
                "people_presence": "none",
                "shots": [],
            }],
        },
        roles,
    )
    assert _source_understanding_complete(understanding, roles) is False


def test_verified_partial_hand_source_clears_stale_people_checkbox():
    brief = _apply_replication_facts_to_brief(
        {"contains_person": True, "request": "不新增人物，只换产品"},
        {
            "enabled": True,
            "source_people_presence": "partial_hands",
            "forbid_invented_people": True,
        },
    )
    assert brief["contains_person"] is False
    assert brief["source_people_presence"] == "partial_hands"


def test_person_and_voice_flags_are_inferred_before_planning():
    brief, applied = _infer_brief_content_flags(
        {
            "script": "女：你听到了吗\n男：听到了",
            "audio_enabled": True,
            "contains_person": False,
            "contains_voice": False,
        },
        [{"role": "character_first_frame", "technical_role": "first_frame"}],
    )

    assert brief["contains_person"] is True
    assert brief["contains_voice"] is True
    assert applied == ["contains_person", "contains_voice"]

    unchanged, applied = _infer_brief_content_flags(
        {"script": "只作为画外文字", "audio_enabled": False},
        [{"role": "product_packshot", "technical_role": "first_frame"}],
    )
    assert unchanged.get("contains_person") is not True
    assert unchanged.get("contains_voice") is not True
    assert applied == []


def test_production_mode_inference_covers_optional_source_combinations():
    assert infer_production_mode([]) == "text_to_video"
    assert infer_production_mode([{"role": "character_first_frame"}]) == "image_to_video"
    assert infer_production_mode([{"role": "product_packshot"}]) == "image_to_video"
    assert infer_production_mode([{"role": "motion_reference"}]) == "reference_replay"
    assert infer_production_mode([{"role": "audio_reference"}]) == "reference_replay"
    assert infer_production_mode([], continuation=True) == "continuation"


def test_character_and_product_first_frames_keep_distinct_business_roles():
    people = _normalize_source_roles([{"asset_id": "asset-people", "role": "character_first_frame"}])
    product = _normalize_source_roles([{"asset_id": "asset-product", "role": "product_packshot"}])
    assert people == [{
        "asset_id": "asset-people", "role": "character_first_frame",
        "technical_role": "first_frame", "purpose": "",
    }]
    assert product == [{
        "asset_id": "asset-product", "role": "product_packshot",
        "technical_role": "first_frame", "purpose": "",
    }]


def test_long_dialogue_is_deterministically_fitted_to_single_h3_clip():
    script = (
        "女：开了吗？\n"
        "男：开了。\n"
        "女：OK我长话短说，有手机的话看看这个日常轻薄款。\n"
        "男：就是很薄的那个吗？\n"
        "女：对，具体商品和活动信息以真实页面为准。"
    )
    timing = _script_timing_for_clip(script, 5)

    assert _estimate_script_seconds(script) > 10
    assert timing["fits"] is False
    assert timing["shot_script"] == "女：开了吗？\n男：开了。"
    assert timing["recommended_action"] == "split_or_continuation"

    adapted = _apply_script_timing_to_plan(
        {
            "audio_prompt": script,
            "warnings": [],
            "negative_constraints": [],
            "shots": [
                {
                    "action": "女方说“开了吗？”，男方回答“开了。”，女方接着说“OK我长话短说”",
                    "audio": script,
                },
                {"action": "继续说完整口播", "audio": "继续完整口播"},
            ],
        },
        timing,
    )
    assert adapted["audio_prompt"].endswith("女：开了吗？\n男：开了。")
    assert adapted["shots"][0]["audio"] == timing["shot_script"]
    assert "开了吗" in adapted["shots"][0]["action"]
    assert "开了" in adapted["shots"][0]["action"]
    assert "长话短说" not in adapted["shots"][0]["action"]
    assert "本镜头只表演以下口播" in adapted["shots"][0]["action"]
    assert adapted["shots"][1]["audio"] == "保持同一环境声，不新增台词"
    assert "被省略内容" in adapted["shots"][1]["action"]
    assert any("5 秒镜头的自然语速安全预算约 4.5 秒" in item for item in adapted["warnings"])
    assert any("不得为了容纳" in item for item in adapted["negative_constraints"])


def test_default_five_second_dialogue_auto_selects_shortest_safe_native_duration():
    assert _recommended_dialogue_duration(
        "左边女生：哎，你用过专为女性设计的套套吗？\n右边女生：真的有这种套套吗？",
        5,
    ) == 9
    assert _recommended_dialogue_duration("女：开了吗？\n男：开了。", 5) == 5
    # Explicit operator choices are preserved and use the excerpt contract.
    assert _recommended_dialogue_duration("女：很长的一段完整台词需要更长时间自然说完。", 8) == 8


def test_short_dialogue_keeps_original_audio_direction():
    timing = _script_timing_for_clip("女：开了吗？\n男：开了。", 5)
    original = {"audio_prompt": "女：开了吗？\n男：开了。", "shots": [{"audio": "自然对话"}]}
    adapted = _apply_script_timing_to_plan(original, timing)

    assert timing["fits"] is True
    assert adapted["audio_prompt"] == original["audio_prompt"]
    assert adapted["shots"] == original["shots"]


def test_cloud_reference_contract_normalizes_live_rows_without_fake_roi():
    item = _normalize_cloud_reference_item({
        "id": "1001",
        "title": "示例品牌 001 轻薄素材",
        "category_name": "示例品牌/001",
        "duration_seconds": 23,
        "state_label": "正常",
        "can_play": True,
        "media": {"has_video": True, "has_cover": True},
        "metrics": {"views": 218, "downloads": 4},
    }, allow_import=True)
    assert item == {
        "video_id": "1001",
        "title": "示例品牌 001 轻薄素材",
        "category_name": "示例品牌/001",
        "duration_seconds": 23,
        "size_bytes": None,
        "created_at": None,
        "state_label": "正常",
        "has_cover": True,
        "has_video": True,
        "can_play": True,
        "metrics": {"views": 218, "downloads": 4},
        "selectable": True,
        "import_status": "ready",
    }
    assert "roi" not in item["metrics"]


def test_cloud_reference_cache_rows_remain_metadata_only():
    item = _normalize_cloud_reference_item({
        "video_id": "1002", "title": "缓存素材", "can_play": True, "has_video": True,
        "metrics": {"roi": 1.82, "statCost": 31.4},
    }, allow_import=False)
    assert item["selectable"] is False
    assert item["metrics"] == {"roi": 1.82, "stat_cost": 31.4}


@pytest.mark.asyncio
async def test_cloud_reference_search_scans_beyond_pending_first_page_and_prioritizes_playable(monkeypatch):
    from app.codex import cloud_video as cloud_video_service

    captured = {}

    async def fake_videos(args):
        captured.update(args)
        return {
            "total": 3766,
            "items": [
                {
                    "id": "pending-1",
                    "title": "最新待转码素材",
                    "can_play": False,
                    "media": {"has_video": True},
                },
                {
                    "id": "ready-1",
                    "title": "较早但可导入素材",
                    "can_play": True,
                    "media": {"has_video": True},
                },
                {
                    "id": "missing-1",
                    "title": "无源文件素材",
                    "can_play": True,
                    "media": {"has_video": False},
                },
            ],
        }

    monkeypatch.setattr(cloud_video_service, "builtin_cloud_video_videos", fake_videos)
    result = await cloud_reference_search(None, SimpleNamespace(id="user-1"), SimpleNamespace(id="run-1"), {
        "search": "超快感",
        "limit": 2,
    })

    assert captured == {
        "search": "超快感",
        "page_size": 60,
        "auto_page": True,
        "max_pages": 5,
        "max_items": 300,
    }
    assert [item["video_id"] for item in result["items"]] == ["ready-1", "pending-1"]
    assert result["scanned_count"] == 3
    assert result["importable_count"] == 1
    assert "可安全导入" in result["notice"]


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("开了吗-001.mp4", "示例品牌 001 隐形系列"),
        ("商品榜单-持久.mp4", "示例品牌 持久"),
        ("AIR 空气套参考", "示例品牌 AIR / 铂金"),
        ("魔力玻玻水感", "示例品牌 魔力玻玻"),
        ("超快感酥麻", "示例品牌 超快感"),
    ],
)
def test_product_defaults_follow_material_hints(hint, expected):
    assert _product_from_material_hints([hint]) == expected


def test_blank_brief_defaults_are_safe_for_each_inferred_mode():
    text_default = _default_request_for_mode("text_to_video", has_assets=False)
    assert "无品牌抽象 B-roll" in text_default
    assert "无包装、Logo、文字" in text_default
    assert "正确首帧" in _default_request_for_mode("image_to_video", has_assets=True)
    assert "只采用素材中可验证的信息" in _default_request_for_mode("reference_replay", has_assets=True)


@pytest.mark.asyncio
async def test_cloud_reference_import_keeps_upstream_url_server_side(monkeypatch):
    import base64

    from app.codex import cloud_video as cloud_video_service
    from app.media import workbench_v2

    content = b"controlled-cloud-video"

    async def fake_raw(video_id):
        assert video_id == "1003"
        return {"videoId": "1003", "name": "示例品牌 001 参考", "canPlay": 1, "videoUrl": "https://private.example/video.mp4"}

    async def fake_upload(_db, _user, run_id, **kwargs):
        assert run_id == "prun-test"
        assert kwargs["content"] == content
        assert kwargs["metadata"]["upstream_url_stored"] is False
        assert "private.example" not in str(kwargs["metadata"])
        return {"asset": {"id": "pra-imported", "file_name": kwargs["file_name"], "mime_type": kwargs["mime_type"]}}

    monkeypatch.setattr(cloud_video_service, "_cloud_video_raw_by_id", fake_raw)
    monkeypatch.setattr(
        cloud_video_service,
        "_download_media_data_url",
        lambda *_args, **_kwargs: (f"data:video/mp4;base64,{base64.b64encode(content).decode('ascii')}", len(content), "video/mp4"),
    )
    monkeypatch.setattr(workbench_v2.project_service, "upload_project_run_asset", fake_upload)
    result = await cloud_reference_import(
        None,
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="samplebrand-material-workbench", department_id="931765248"),
        SimpleNamespace(id="prun-test", department_id="931765248"),
        {"video_id": "1003", "role": "motion_reference"},
    )
    assert result["asset"]["id"] == "pra-imported"
    assert result["raw_url_returned"] is False
    assert result["credential_location"] == "platform_only"


def test_product_packshot_becomes_reference_image_when_other_references_exist():
    normalized = _normalize_source_roles([
        {"asset_id": "asset-product", "role": "product_packshot"},
        {"asset_id": "asset-motion", "role": "motion_reference"},
    ])
    assert [item["technical_role"] for item in normalized] == ["reference_image", "reference_video"]
    with pytest.raises(AppError):
        _normalize_source_roles([
            {"asset_id": "asset-first", "role": "first_frame"},
            {"asset_id": "asset-motion", "role": "reference_video"},
        ])


def test_output_preset_gate_keeps_benchmark_evidence_without_locking_valid_dimensions():
    good = [{"status": "completed", "duration_seconds": 100 + index} for index in range(5)]
    quick = [{"status": "completed", "duration_seconds": 80 + index} for index in range(5)]
    passed = preset_benchmark_gate(
        "balanced_vertical",
        {"rtx_5080": good, "rtx_pro_6000": good},
        {"rtx_5080": quick, "rtx_pro_6000": quick},
    )
    assert passed["enabled"] is True
    assert passed["benchmark_passed"] is True
    failed = preset_benchmark_gate(
        "hd_vertical",
        {"rtx_5080": good[:4], "rtx_pro_6000": good},
        {"rtx_5080": quick, "rtx_pro_6000": quick},
    )
    assert failed["enabled"] is True
    assert failed["benchmark_passed"] is False
    assert "已开放手动选择" in failed["reason"]


def test_continuation_segments_are_exactly_bounded_and_do_not_hide_overshoot():
    durations = continuation_segment_durations(5.0, 20, 8)
    assert sum(durations) == 15
    assert all(4 <= item <= 15 for item in durations)
    assert continuation_segment_durations(0, 30, 15) == [15, 15]
    long_durations = continuation_segment_durations(2.0, 60, 4)
    assert len(long_durations) <= 12 and sum(long_durations) == 58
    with pytest.raises(AppError):
        continuation_segment_durations(5.0, 8, 5)


def test_dialogue_default_is_content_aware_and_never_falls_back_to_abstract_broll():
    value = _default_request_for_mode(
        "image_to_video",
        has_assets=True,
        script="女：开了吗\n男：开了",
        contains_person=True,
    )
    assert "完整语义回合" in value
    assert "同一对明确成年人物" in value
    assert "保持所选首帧的人物身份" in value
    assert "没有正确商品图时不生成包装、Logo 或文字" in value


def test_audio_switch_resolves_stale_silent_prose_and_keeps_business_copy():
    brief, resolutions = _resolve_audio_brief_conflicts({
        "script": "女：开了吗\n男：开了",
        "audio_enabled": True,
        "request": "保持固定机位；5秒无声；不得有音频内容；人物自然对视。",
    })
    assert resolutions == ["audio_requirement_conflict_resolved"]
    assert "无声" not in brief["request"]
    assert "不得有音频" not in brief["request"]
    assert "保持固定机位" in brief["request"]
    assert "人物自然对视" in brief["request"]


def test_business_title_is_generated_from_product_goal_and_friendly_option_label():
    title = _job_business_title(
        {"creative_option": "smart", "variant_index": 2},
        {"product": "示例品牌 AIR / 铂金"},
        {"creative_goal": "冰箱话题人物钩子"},
    )
    assert title == "示例品牌 AIR / 铂金 · 冰箱话题人物钩子 · 智能生成 · v02"


def test_continuation_retry_uses_a_new_auditable_idempotency_revision():
    assert continuation_segment_idempotency_key("mcc-1", 2, {}) == "continuation:mcc-1:2"
    assert continuation_segment_idempotency_key("mcc-1", 2, {"retry_revision": 1}) == "continuation:mcc-1:2:r1"
    assert continuation_segment_idempotency_key("mcc-1", 2, {"retry_revision": 3}) == "continuation:mcc-1:2:r3"
    assert continuation_segment_idempotency_key("mcc-1", 2, {"retry_revision": "bad"}) == "continuation:mcc-1:2"


def test_continuation_timeline_handoff_keeps_exact_ranges_hashes_and_truthful_interchange():
    chain = MediaContinuationChain(
        id="mcc-edit-1", project_id="project-1", project_run_id="run-1", department_id="dept-1",
        source_asset_id="source-1", status="awaiting_review", target_duration_seconds=20,
        config_json={
            "source_duration_seconds": 5.0,
            "preview_includes_source": True,
            "audio_continuity": True,
            "locks": {"identity": True, "scene": True},
        },
    )
    segments = [
        MediaContinuationSegment(
            id="segment-1", chain_id=chain.id, segment_index=1, status="awaiting_review",
            duration_seconds=8, media_job_id="job-1", anchor_asset_id="source-1",
            prompt_json={"prompt_policy_version": "policy-1"}, locks_json={},
            seam_analysis_json={"status": "passed", "human_audio_review_required": True},
        ),
        MediaContinuationSegment(
            id="segment-2", chain_id=chain.id, segment_index=2, status="awaiting_review",
            duration_seconds=7, media_job_id="job-2", anchor_asset_id="result-1",
            prompt_json={"prompt_policy_version": "policy-1"}, locks_json={},
            seam_analysis_json={"status": "passed"},
        ),
    ]
    jobs = {
        "job-1": MediaGenerationJob(
            id="job-1", project_id="project-1", project_run_id="run-1", idempotency_key="job-1",
            mode="reference_replay", status="awaiting_review", prompt_json={"prompt_policy_version": "policy-1"},
            params_json={"fps": 24, "frames": 192, "seed": 11}, reference_assets_json=[],
            workflow_template_id="h3_r2v_v1", workflow_version="workflow-1", model_version="h3-1",
            result_asset_id="result-1",
        ),
        "job-2": MediaGenerationJob(
            id="job-2", project_id="project-1", project_run_id="run-1", idempotency_key="job-2",
            mode="reference_replay", status="awaiting_review", prompt_json={"prompt_policy_version": "policy-1"},
            params_json={"fps": 24, "frames": 168, "seed": 12}, reference_assets_json=[],
            workflow_template_id="h3_r2v_v1", workflow_version="workflow-1", model_version="h3-1",
            result_asset_id="result-2",
        ),
    }

    def asset(asset_id: str, digest: str) -> ProjectRunAsset:
        return ProjectRunAsset(
            id=asset_id, project_id="project-1", project_run_id="run-1", source_kind="file",
            file_name=f"{asset_id}.mp4", mime_type="video/mp4", byte_size=1234,
            sha256=digest, storage_backend="local", storage_path=f"run-1/{asset_id}.mp4",
        )

    source = asset("source-1", "a" * 64)
    result_1 = asset("result-1", "b" * 64)
    result_2 = asset("result-2", "c" * 64)
    preview = asset("preview-1", "d" * 64)
    assets = {item.id: item for item in (source, result_1, result_2, preview)}

    handoff = continuation_timeline_handoff(chain, source, segments, jobs, assets, preview)

    assert handoff["schema_version"] == "skillforge.media.timeline_handoff.v1"
    assert handoff["timeline_duration_seconds"] == 20.0
    assert [clip["timeline_range"] for clip in handoff["tracks"][0]["clips"]] == [
        {"start_seconds": 0.0, "duration_seconds": 5.0},
        {"start_seconds": 5.0, "duration_seconds": 8.0},
        {"start_seconds": 13.0, "duration_seconds": 7.0},
    ]
    assert handoff["tracks"][0]["clips"][1]["asset"]["sha256"] == "b" * 64
    assert handoff["tracks"][0]["clips"][2]["generator"]["seed"] == 12
    assert handoff["continuity"]["human_audio_review_required"] is True
    assert handoff["interchange"]["opentimelineio_export"]["status"] == "not_exposed"
    assert len(handoff["fingerprint_sha256"]) == 64

    changed = continuation_timeline_handoff(
        chain, source, segments, jobs, {**assets, "result-2": asset("result-2", "e" * 64)}, preview,
    )
    assert changed["fingerprint_sha256"] != handoff["fingerprint_sha256"]


def test_workbench_snapshot_bulk_serializes_continuations_without_chain_n_plus_one():
    source = (Path(__file__).parents[1] / "app" / "media" / "workbench_v2.py").read_text(encoding="utf-8")
    assert "async def serialize_continuations(" in source
    assert "serialized_continuations = await serialize_continuations(db, continuations)" in source
    assert "[await serialize_continuation(db, chain) for chain in continuations]" not in source


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _GateDb:
    def __init__(self, critical=0):
        self.critical = critical

    async def execute(self, _statement):
        return _ScalarResult(self.critical)


def _review_job():
    return MediaGenerationJob(
        id="mvj-v2",
        project_id="samplebrand-material-workbench",
        project_run_id="prun-v2",
        idempotency_key="v2",
        mode="text_to_video",
        status="awaiting_review",
        prompt_json={"creative_goal": "无价格声明的抽象投流素材"},
        params_json={"audio_enabled": True},
        reference_assets_json=[],
        workflow_template_id="h3_t2v_v1",
        result_json={"technical_validation": {"status": "passed"}},
        rights_json={
            "copyright_authorized": True,
            "contains_person": False,
            "contains_voice": False,
            "malware_scan": "passed",
            "sensitive_data_scan": "passed",
            "redaction": "passed",
        },
    )


def test_review_cursor_and_sort_time_use_review_completion_not_request_creation():
    job = _review_job()
    job.id = "mvj-review-ready"
    job.created_at = datetime(2026, 8, 14, 9, 0, 0)
    job.completed_at = datetime(2026, 8, 14, 15, 30, 0)

    cursor = _encode_review_cursor(job)

    assert _review_sort_time(job) == job.completed_at
    assert _decode_review_cursor(cursor) == (job.completed_at, job.id)


@pytest.mark.asyncio
async def test_hard_review_gate_blocks_missing_adult_declaration_and_critical_annotations():
    job = _review_job()
    passed, missing = await hard_review_gate(_GateDb(critical=1), job, {"rights": {}, "compliance": {}})
    assert passed is False
    assert "compliance.adult_audience_confirmed" in missing
    assert "review_annotations.critical_resolved" in missing


@pytest.mark.asyncio
async def test_hard_review_gate_allows_clean_no_person_no_voice_asset():
    job = _review_job()
    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": False}},
    )
    assert passed is True
    assert missing == []


@pytest.mark.asyncio
async def test_hard_review_gate_blocks_clean_plate_until_visual_gate_finishes():
    job = _review_job()
    job.prompt_json = {**job.prompt_json, "h3_execution_profile": "clean_dialogue_plate_silent_v1"}

    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": False}},
    )

    assert passed is False
    assert "visual_policy_gate.completed" in missing


@pytest.mark.asyncio
async def test_hard_review_gate_blocks_evidenced_visible_text_in_clean_plate():
    job = _review_job()
    job.prompt_json = {**job.prompt_json, "h3_execution_profile": "clean_dialogue_plate_silent_v1"}
    job.result_json = {
        **job.result_json,
        "quality_analysis": {
            "status": "completed",
            "assets": [{
                "status": "completed",
                "analysis": {
                    "visual_policy_gate": {
                        "passed": False,
                        "findings": [{"code": "visible_text_detected", "visible_text": "想呢了"}],
                    },
                },
            }],
        },
    }

    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": False}},
    )

    assert passed is False
    assert "visual_policy_gate.visible_text_detected" in missing


@pytest.mark.asyncio
async def test_hard_review_gate_allows_clean_plate_after_visual_gate_passes():
    job = _review_job()
    job.prompt_json = {**job.prompt_json, "h3_execution_profile": "clean_dialogue_plate_silent_v1"}
    job.result_json = {
        **job.result_json,
        "quality_analysis": {
            "status": "completed",
            "assets": [{"status": "completed", "analysis": {"visual_policy_gate": {"passed": True, "findings": []}}}],
        },
    }

    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": False}},
    )

    assert passed is True
    assert missing == []


@pytest.mark.asyncio
async def test_hard_review_gate_blocks_requested_dialogue_until_governed_voiceover_is_in_final_asset():
    job = _review_job()
    job.prompt_json = {
        **job.prompt_json,
        "dialogue_delivery": {
            "requested": True,
            "status": "voiceover_renderer_required",
            "renderer": None,
        },
    }
    job.result_json = {
        **job.result_json,
        "clean_plate_asset": {"id": "pra_v20_clean_plate"},
    }
    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": False}},
    )
    assert passed is False
    assert "dialogue_delivery.completed" in missing
    assert dialogue_delivery_gate(job) == {
        "required": True,
        "passed": False,
        "status": "voiceover_renderer_required",
        "label": "对白待独立配音",
        "renderer": None,
        "artifact_id": None,
        "has_audio_stream": False,
        "transcription": {
            "passed": False,
            "status": "not_run",
            "provider": None,
            "model": None,
            "similarity": None,
            "coverage": None,
            "transcript": None,
            "trace_id": None,
        },
        "retryable": True,
        "reason": "对白任务必须由受控配音执行器完成，并在最终成片中验证到音频流。",
        "visual_policy_gate": {"required": False, "passed": True, "status": "not_required", "findings": []},
    }


def test_dialogue_delivery_does_not_reuse_direct_plate_that_failed_visible_text_gate():
    job = _review_job()
    job.prompt_json = {
        **job.prompt_json,
        "h3_execution_profile": "direct_clean_people_plate_silent_v1",
        "dialogue_delivery": {"requested": True, "status": "voiceover_renderer_required"},
    }
    job.result_json = {
        **job.result_json,
        "clean_plate_asset": {"id": "pra_direct_bad_plate"},
        "quality_analysis": {
            "status": "completed",
            "assets": [{
                "status": "completed",
                "analysis": {
                    "visual_policy_gate": {
                        "passed": False,
                        "findings": [{"code": "visible_text_detected", "visible_text": "乱码"}],
                    },
                },
            }],
        },
    }

    gate = dialogue_delivery_gate(job)

    assert gate["retryable"] is False
    assert gate["visual_policy_gate"]["status"] == "failed"
    assert "禁止在坏底片上继续配音" in gate["reason"]


def test_dialogue_delivery_auto_mode_hides_manual_action_after_visual_gate():
    job = _review_job()
    job.prompt_json = {
        **job.prompt_json,
        "h3_execution_profile": "clean_dialogue_plate_silent_v1",
        "requested_audio_prompt": "女：今晚早点回来。",
        "dialogue_delivery": {
            "requested": True,
            "status": "voiceover_renderer_required",
            "delivery_mode": "auto_after_visual_gate",
            "auto_finalize": True,
        },
    }
    job.result_json = {
        **job.result_json,
        "clean_plate_asset": {"id": "pra_auto_plate"},
        "dialogue_delivery": {"status": "awaiting_visual_gate"},
        "quality_analysis": {
            "status": "completed",
            "assets": [{
                "status": "completed",
                "analysis": {"visual_policy_gate": {"passed": True, "findings": []}},
            }],
        },
    }

    gate = dialogue_delivery_gate(job)

    assert gate["label"] == "对白自动处理中"
    assert gate["retryable"] is False
    assert "系统将自动生成受控配音" in gate["reason"]


def test_explicit_silent_dialogue_contract_overrides_legacy_script_inference():
    job = _review_job()
    job.prompt_json = {
        **job.prompt_json,
        "script_timing": {"shot_script": "女：今晚早点回来。"},
        "dialogue_delivery": {
            "requested": False,
            "status": "silent_output_requested",
            "delivery_mode": "silent_output",
            "auto_finalize": False,
        },
    }

    assert dialogue_delivery_gate(job) == {
        "required": False,
        "passed": True,
        "status": "not_required",
        "label": "无需对白交付",
    }


@pytest.mark.asyncio
async def test_hard_review_gate_infers_legacy_fitted_dialogue_instead_of_trusting_h3_aac():
    job = _review_job()
    job.prompt_json = {
        **job.prompt_json,
        "script_timing": {"shot_script": "女：宝，今晚早点回来。"},
    }
    job.result_json = {
        "technical_validation": {"status": "passed", "audio_codec": "aac"},
    }

    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True}},
    )

    assert passed is False
    assert "dialogue_delivery.completed" in missing
    legacy_gate = dialogue_delivery_gate(job)
    assert legacy_gate["label"] == "对白待独立配音"
    assert legacy_gate["retryable"] is False
    assert "必须返回生产重新生成" in legacy_gate["reason"]


@pytest.mark.asyncio
async def test_hard_review_gate_allows_proven_governed_dialogue_delivery():
    job = _review_job()
    job.prompt_json = {**job.prompt_json, "dialogue_delivery": {"requested": True}}
    job.result_json = {
        "technical_validation": {"status": "passed", "audio_codec": "aac"},
        "dialogue_delivery": {
            "status": "completed",
            "policy_version": SPEECH_DELIVERY_POLICY_VERSION,
            "renderer": "platform-voice-renderer-v1",
            "artifact_id": "pra_voice_final",
            "transcription": {
                "status": "passed",
                "passed": True,
                "provider": "SiliconFlow",
                "model": SPEECH_TRANSCRIPTION_MODEL,
                "similarity": 0.98,
                "coverage": 1.0,
                "transcript": "宝今晚早点回来",
            },
        },
    }
    passed, missing = await hard_review_gate(
        _GateDb(), job,
        {"rights": {}, "compliance": {"adult_audience_confirmed": True, "commercial_claim_verified": False}},
    )
    assert passed is True
    assert missing == []
    assert dialogue_delivery_gate(job)["passed"] is True


def test_v2_project_package_is_modular_and_does_not_poll_capabilities():
    root = Path(__file__).parents[1] / "demo-projects" / "material-workbench" / "web"
    index = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "js" / "app.js").read_text(encoding="utf-8")
    api = (root / "js" / "api.js").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    interaction_fixes = (root / "interaction-fixes.css").read_text(encoding="utf-8")
    manifest = (root.parent / "projectforge.yaml").read_text(encoding="utf-8")
    manifest_version = re.search(r"(?m)^version:\s*([^\s]+)", manifest).group(1)
    assert '<script type="module" src="./js/app.js"></script>' in index
    assert 'rel="stylesheet" href="./styles.css"' in index
    assert 'rel="stylesheet" href="./interaction-fixes.css"' in index
    assert "setInterval(refresh" not in app
    assert "subscribeMedia" in app
    assert "subscribeMedia((eventName, payload)" in app
    assert "gateway.media.subscribe" in api
    assert "renderSnapshot(await api.snapshot())" in app
    assert app.count("renderSnapshot(await api.snapshot())") == 2
    assert "snapshot: () => capability('material.workbench.snapshot', { include_recent_jobs: true, limit: 60 }, 30000)" in api
    assert "if (Array.isArray(snapshot.jobs))" in app
    assert "function applySnapshotJobs(items)" in app
    assert "state.jobListLoaded = true" in app
    assert "renderQueueLoadError(error)" in app
    assert "resyncEventStream" in app
    assert "sessionStorage.setItem(reviewListStorageKey" in app
    assert "state.reviewListLoaded = true" in app
    assert "sf-material-workbench-v21101-reviews" in app
    assert "最新进入选片池" in index
    assert "function sortReviewsNewestFirst(items = [])" in app
    assert "state.reviews = sortReviewsNewestFirst(cached.items)" in app
    assert "state.reviewListLoaded = false" in app
    assert "state.candidates = [...new Map(combined.map(item => [item.id, item])).values()]" in app
    assert "function reviewReadyAt(item = {})" in app
    assert "reviewCreatedLabel(readyAt)" in app
    assert "index < 0 && !state.reviewListLoaded" in app
    assert "oldestVisibleReviewAt" in app
    assert "signedExpiry > Math.floor(Date.now() / 1000) + 60" in app
    assert "previous?.poster ? { poster: previous.poster }" in app
    assert "!$('reviewWorkspace').classList.contains('hidden') && !state.reviewListLoaded" in app
    assert "封面待加载" in app
    assert ">无封面<" not in app
    assert "sf-material-workbench-v2150-images" in app
    assert "role_repaired_by: 'material_workbench_v2150'" in app
    assert "/__skillforge_media_review_cache__/" in app
    assert "manifest.primary.poster?.download_url" in app
    assert "renderReviewList()\n    persistReviewList()" in app
    assert "productHints" in app
    assert "reviewManifestCacheTtlMs = 10 * 60 * 1000" in app
    assert "job.poster_asset_id && job.project_run_id" not in app
    assert 'id="runRecovery"' in index
    assert '>生产<' in index and '>审片 ' in index
    assert 'id="presetCards"' in index
    assert "全部档位均可选择" in index
    assert "const benchmarkPending = item.benchmark_required && item.gate?.benchmark_passed === false" in app
    assert "if (event.target.id === 'ratio') renderPresets()" in app
    assert 'id="filmstrip"' in index
    assert 'id="reviewAudioStatus"' in index
    assert 'id="reviewSoundToggle"' in index
    assert 'id="reviewPlayToggle"' in index
    assert "function reviewAudioState(primary = {})" in app
    assert "function dialogueDeliveryButtonLabel(gate = {})" in app
    assert "校验台词完整性" in app
    assert "台词待校验" in app
    assert "dialogue_delivery_gate: dialogueGate" in app
    assert "const awaitingTranscription = dialogueGate.status === 'transcription_required'" in app
    assert "status: awaitingTranscription" in app
    assert "function updateReviewSoundToggle(primary = {})" in app
    assert "function renderDialogueDeliveryState(primary = {})" in app
    dialogue_renderer = app.split("function renderDialogueDeliveryState(primary = {})", 1)[1].split(
        "function updateReviewPlayToggle", 1
    )[0]
    assert "const audioState = reviewAudioState(primary)" in dialogue_renderer
    assert "renderDialogueDeliveryState(primary)" not in dialogue_renderer
    assert "const authoritativeGate = result.dialogue_delivery_gate || {}" in app
    assert "state.reviewManifest.primary.dialogue_delivery_gate = authoritativeGate" in app
    assert "renderDialogueDeliveryState(state.reviewManifest.primary)" in app
    assert "$('reviewSoundToggle').onclick" in app
    assert "$('reviewPlayToggle').onclick = () => toggleReviewPlayback()" in app
    assert "async function toggleReviewPlayback()" in app
    assert "$('reviewVideoA').onplay = updateReviewPlayToggle" in app
    assert "$('reviewVideoB').muted = true" in app
    assert "当前视频文件没有音频流，因此浏览器会禁用声音按钮" in app
    assert "无声底片 · 配音失败" in app
    assert "含音轨" in app
    assert ".audio-status.ready" in styles and ".audio-status.failed" in styles
    assert '.sound-toggle[aria-pressed="true"]' in styles
    assert '.play-toggle[aria-pressed="true"]' in styles
    assert 'id="reviewSelectAll"' in index
    assert "batchReviewUpdate" in api
    assert "limit: 30" in app
    assert "loading=\"lazy\"" in app
    assert "shot.start_seconds" in app and "shot.end_seconds" in app
    assert ">批量通过<" not in index
    assert "setInterval(" not in app
    assert "$('productionForm').onsubmit = handleProductionSubmit" in app
    assert "$('submitButton').onclick = handleProductionSubmit" in app
    assert "submissionLocked: false" in app
    assert "state.submissionLocked = true" in app
    assert "state.submissionLocked ||" in app
    assert "reconnectDelays = [1000, 2000, 5000, 10000, 30000]" in app
    assert "eventGeneration: 0, eventConnecting: false" in app
    assert "function stopEventStream({ resetCursor = false } = {})" in app
    assert "if (generation !== state.eventGeneration" in app
    assert "state.reconnectTimer = null" in app
    assert "reviewImageObjectUrlLimit = 96" in app
    assert "reviewImageDiskEntryLimit = 240" in app
    assert "URL.revokeObjectURL(oldestUrl)" in app
    assert "cacheKeys.length - reviewImageDiskEntryLimit" in app
    assert "function releaseVideo(video)" in app
    assert "function reviewCardLevel(job = {}, quality = {})" in app
    assert "function materialStageFor(job = {}, quality = {})" in app
    assert "当前只评价底片本身" in app
    assert 'id="materialStageNotice"' in index
    assert ".material-stage-notice" in styles
    assert "if (job.status === 'rejected') return 'low'" in app
    assert "已人工驳回，不计为可用素材" in app
    assert "video.removeAttribute('src')" in app
    assert "video.removeAttribute('poster')" in app
    assert "reviewRequestToken: 0" in app
    assert "const requestToken = ++state.reviewRequestToken" in app
    assert "requestToken !== state.reviewRequestToken" in app
    assert "$('viewerShell').classList.add('loading')" in app
    assert "$('viewerShell').classList.remove('loading')" in app
    assert ".viewer-shell.loading video{visibility:hidden!important}" in styles
    assert "if (!url) { releaseVideo(video); return }" in app
    assert 'id="downloadProvenance"' in index
    assert "function downloadProvenance()" in app
    assert "primary.provenance" in app
    assert ".review-workspace" in styles and ".production-workspace" in styles
    assert "html.review-mode" in interaction_fixes
    assert "height: calc(100dvh - 72px)" in interaction_fixes
    assert "grid-template-rows: 66px minmax(0, 1fr)" in interaction_fixes
    assert "overscroll-behavior: contain" in interaction_fixes
    assert "document.documentElement.classList.toggle('review-mode'" in app
    assert "document.body.classList.toggle('review-mode'" in app
    assert "function resetReviewViewportScroll()" in app
    assert "state.productionScrollY" in app
    assert "requestAnimationFrame(resetReviewViewportScroll)" in app
    assert "position: fixed" in interaction_fixes
    assert "contain: size layout paint" in interaction_fixes
    assert "width: 1px !important" in interaction_fixes
    assert "padding-right: 286px" in interaction_fixes
    assert "position: absolute" in interaction_fixes
    assert "rightsConfirmed" not in index
    assert "rightsConfirmed" not in app
    assert "部门自有素材" in index
    assert "音频已关闭；本镜头不会使用口播台词" in app
    assert "script: $('script').value.trim()" in app
    assert "audio_enabled: audioEnabled" in app
    assert 'id="product" placeholder="可不填；不会自动写进提示词"' in index
    assert 'id="requirement" rows="7"' in index
    assert 'id="product" required' not in index
    assert 'id="requirement" required' not in index
    assert "applySafeDefaults()" in app
    assert 'id="assetMentionMenu"' in index
    assert "function assetMentionToken(item = {}, index = 0)" in app
    assert "function showAssetMentionMenu()" in app
    assert "mention_token: assetMentionToken(item, index)" in app
    assert "data-append-mention" in app
    assert "已绑定 ${item.mention_token}" in app
    assert "输入 <strong>@</strong>" in index
    assert ".asset-mention-menu" in styles
    assert "window.prompt" not in app
    assert 'id="textInputDialog"' in index
    assert "requestText({" in app
    assert "cloudReferenceImport" in api
    assert "video.cloud_reference.import" in api
    assert "item.title || item.video_name" in app
    assert "item.metrics?.roi" in app
    assert "ROI ${esc(item.roi ?? '—')}" not in app
    assert "人物 / 场景首帧" in app
    assert "真实商品首帧" in app
    role_handler = app.split("[data-asset-role]", 1)[1].split("[data-remove-asset]", 1)[0]
    assert "updateFormSummary()" in role_handler
    assert "assetDrop').classList.contains('uploading')" in app
    assert "function selectedAssetFingerprints(item)" in app
    assert "function localFileFingerprint(file = {})" in app
    assert "function findSelectedAssetIndex(candidate)" in app
    assert "function dedupeSelectedAssets(items)" in app
    assert "state.assets = dedupeSelectedAssets(state.assets)" in app
    assert "localFingerprint, role: defaultRole(file)" in app
    assert "function revealExistingAsset(index, fileName)" in app
    assert "已在下方素材列表中，本次未重复添加" in app
    assert "系统已自动去重" not in app
    assert 'id="assetUploadStatus" role="status" aria-live="polite"' in index
    assert ".asset-row.duplicate-highlight" in styles
    assert 'id="assetSelectionSummary" class="asset-selection-summary"' in index
    assert 'id="submitBlockReason" class="submit-block-reason"' in index
    assert "确认并参与" in app
    assert "插入 @素材名" in app
    assert "focusUnconfirmedAsset()" in app
    assert "assets: state.assets.filter(item => item.asset?.id).map" in app
    assert "if (Array.isArray(draft.assets))" in app
    assert "|| state.assets.some(item => !item.roleLocked)" not in app
    assert index.count('class="workbench-version"') == 3
    assert index.count(f"FDE 素材供给系统 <strong>v{manifest_version}</strong>") == 3
    assert ".workbench-version" in styles
    assert "setAttribute('aria-busy', 'true')" in app
    assert "setAttribute('aria-busy', 'false')" in app
    assert ".submit-bar" in interaction_fixes and "bottom: 72px" in interaction_fixes
    assert "素材上传中" in interaction_fixes
    assert "user_compiled_prompt" in app
    assert "已手工修改 · 提交时经服务端校验后生效" in app
    assert "data-shot-field=\"action\"" in app
    assert "recompileShotPlan" in app
    assert "video.production.compile" in api
    assert "setQueueOpen" in app
    assert "queue-open" in styles
    assert "translate(-50%,-50%)" in styles
    assert "queue-obscured" in styles
    assert "job.assigned_instance_id" in app
    assert 'id="prepareError"' in index
    assert "AI 优化失败：" in app
    assert "AI 策略" in index
    assert "放弃 AI 优化，按原文生成" in index
    assert "planningMode = 'operator_brief'" in app
    assert "planningMode: 'operator_brief'" in app
    assert "直接生成" in index
    assert index.index('id="requirement"') < index.index('id="product"')
    assert "视频复刻" in index
    assert "资源与调度" in index
    assert "function hasProductionInput()" in app
    assert "planning_mode: isDirectPromptMode() ? 'direct_h3_prompt' : planningMode" in app
    assert "planningMode: isDirectPromptMode() ? 'direct_h3_prompt' : requestedSubmitPlanningMode()" in app
    assert "state.submissionLocked || state.submitting" in app
    assert "function strategySubmitButtonLabel()" in app
    assert "最终提示词已生成，但还没有提交生产" in app
    assert "state.submissionLocked ? ''" in app
    assert "提交 ${count} 个方向生成" in app
    strategy_submit = app.split("async function submitStrategyPlans()", 1)[1].split("function replaySourceAsset", 1)[0]
    assert "if (state.submitting || state.submissionLocked) return" in strategy_submit
    assert "selectedCompiledStrategyDirections()" in strategy_submit
    assert "state.submissionLocked = true" in strategy_submit
    assert "任务 ${ids.map(esc).join('、')}" in strategy_submit
    assert "提交失败：${errorText(error)}" in strategy_submit
    assert "setQueueOpen(true)" in strategy_submit
    assert 'id="strategyActionFeedback"' in index
    assert "async function runStrategyAction(" in app
    assert "上一项 AI 操作仍在处理中" in app
    assert "正在确认所选方向并生成最终 H3 提示词" in app
    assert "strategyDirectionTitle(item)" in app
    assert "strategy-card-select" in app
    assert "strategy-card-footer" in app
    assert "position: static" in interaction_fixes
    assert "grid-template-rows: auto auto 1fr auto" in interaction_fixes
    assert "jobProgressLabel(job)" in app
    assert "GPU 生成中" in app
    assert "job.progress_percent ??" not in app
    assert "qualityForDisplay(primary)" in app
    assert "aggregate.assets || []" in app
    assert "applicable_overall_score" in app
    assert "dimension_applicability" in app
    assert "当前镜头不评价此维度" in app
    assert ">不适用</b>" in app
    assert "模型未返回完整九维分数" in app
    assert "returnReviewToProduction(reason)" in app
    assert "已驳回；素材、分镜和修改要求已返回生产" in app
    reject_source = app.split("async function rejectReview()", 1)[1].split("function mergeReviewJob", 1)[0]
    assert "api.cloneJob" not in reject_source
    assert "primary.sources" in app
    assert "engineRoleToBusinessRole" in app
    assert 'id="enhance1080Button"' in index
    assert "video.enhance.submit" in api
    assert "enhanceCurrent1080p" in app

    backend = (Path(__file__).parents[1] / "app" / "media" / "workbench_v2.py").read_text(encoding="utf-8")
    assert 'not media.get("configured")' in backend
    assert '"video_generation" not in roles' in backend
    assert '"authorization_basis": "department_owned_material_policy"' in backend
    assert "allow_locked_benchmark=benchmark_override" in backend


def test_review_assets_use_short_lived_signed_delivery_without_widening_run_access():
    asset = ProjectRunAsset(
        id="pra-review-1",
        project_id="samplebrand-material-workbench",
        project_run_id="prun-owned-by-producer",
        owner_user_id="producer",
        department_id="931765248",
        source_kind="visual_frame",
        file_name="poster.jpg",
        mime_type="image/jpeg",
        byte_size=128,
        sha256="a" * 64,
        storage_backend="local",
        storage_path="samplebrand-material-workbench/poster.jpg",
        metadata_json={},
    )
    delivered = _serialize_review_asset(asset)
    assert delivered["delivery"] == "signed_project_review"
    assert "/public-download?expires=" in delivered["download_url"]
    assert "&token=" in delivered["download_url"]

    historical = {
        "asset_id": asset.id,
        "download_url": f"/api/projects/runs/{asset.project_run_id}/assets/{asset.id}/download",
        "time_seconds": 0.2,
    }
    hydrated = _review_manifest_delivery_entry(
        historical, {asset.id: asset}, project_id=asset.project_id
    )
    assert hydrated["delivery"] == "signed_project_review"
    assert "/public-download?expires=" in hydrated["download_url"]
    assert hydrated["time_seconds"] == 0.2


def test_workbench_keeps_final_audio_intent_when_h3_clean_plate_is_silent():
    app = (Path(__file__).parents[1] / "demo-projects" / "material-workbench" / "web" / "js" / "app.js").read_text(encoding="utf-8")
    clean_plate_branch = app.split("if (plan.dialogue_delivery?.visual_audio_enabled === false)", 1)[1].split("state.selectedPreset", 1)[0]
    assert "$('audioEnabled').checked = true" in clean_plate_branch
    assert "$('containsVoice').checked = true" in clean_plate_branch
    assert "$('audioEnabled').checked = false" not in clean_plate_branch
    assert "job.prompt?.dialogue_delivery?.requested === true || job.params?.audio_enabled !== false" in app


def test_review_visual_gate_updates_when_async_quality_event_arrives():
    app = (Path(__file__).parents[1] / "demo-projects" / "material-workbench" / "web" / "js" / "app.js").read_text(encoding="utf-8")
    merge_quality = app.split("function mergeSelectedQualityFromJob(job = {})", 1)[1].split(
        "function qualityIssueText", 1
    )[0]
    assert "renderQuality(state.reviewManifest.primary)" in merge_quality
    assert "renderGates(state.reviewManifest.primary)" in merge_quality
    assert "visualGate.passed === true" in merge_quality
    assert "retryable: true" in merge_quality
    assert "renderDialogueDeliveryState(state.reviewManifest.primary)" in merge_quality


@pytest.mark.asyncio
async def test_long_business_video_is_materialized_as_h3_safe_reference(monkeypatch):
    from app.media import workbench_v2

    source = SimpleNamespace(
        id="source-video", project_run_id="source-run", project_id="project-1", department_id="dept-1",
        file_name="48-second-source.mp4", mime_type="video/mp4", sha256="source-sha",
    )
    derived = SimpleNamespace(
        id="derived-video", project_run_id="run-1", project_id="project-1", department_id="dept-1",
        file_name="h3-reference.mp4", mime_type="video/mp4", sha256="derived-sha",
    )

    class FakeDb:
        async def get(self, _model, asset_id):
            return {source.id: source, derived.id: derived}.get(asset_id)

    async def fake_duration(_db, asset):
        return 48.622 if asset.id == source.id else 14.0

    async def fake_derive(*_args, **_kwargs):
        return derived

    monkeypatch.setattr(workbench_v2, "_reference_asset_duration", fake_duration)
    monkeypatch.setattr(workbench_v2, "_ensure_h3_reference_clip", fake_derive)

    roles, preprocessing = await workbench_v2._materialize_h3_source_roles(
        FakeDb(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="run-1", project_id="project-1", department_id="dept-1"),
        [{
            "asset_id": source.id,
            "role": "motion_reference",
            "technical_role": "reference_video",
            "purpose": "原视频动作参考",
        }],
    )

    assert roles[0]["asset_id"] == derived.id
    assert roles[0]["original_asset_id"] == source.id
    assert preprocessing == [{
        "source_asset_id": source.id,
        "derived_asset_id": derived.id,
        "file_name": source.file_name,
        "source_duration_seconds": 48.622,
        "clip_start_seconds": 0,
        "clip_duration_seconds": 14.0,
        "strategy": "opening_clip",
    }]


@pytest.mark.asyncio
async def test_reference_text_suppression_materializes_even_a_short_video(monkeypatch):
    from app.media import workbench_v2

    source = SimpleNamespace(
        id="source-video", project_run_id="run-1", project_id="project-1", department_id="dept-1",
        file_name="source.mp4", mime_type="video/mp4", sha256="source-sha",
    )
    derived = SimpleNamespace(
        id="derived-video", project_run_id="run-1", project_id="project-1", department_id="dept-1",
        file_name="motion-only.mp4", mime_type="video/mp4", sha256="derived-sha",
    )

    class FakeDb:
        async def get(self, _model, asset_id):
            return {source.id: source, derived.id: derived}.get(asset_id)

    async def fake_duration(_db, _asset):
        return 8.0

    async def fake_derive(*_args, **kwargs):
        assert kwargs["suppress_text"] is True
        return derived

    monkeypatch.setattr(workbench_v2, "_reference_asset_duration", fake_duration)
    monkeypatch.setattr(workbench_v2, "_ensure_h3_reference_clip", fake_derive)
    roles, preprocessing = await workbench_v2._materialize_h3_source_roles(
        FakeDb(), SimpleNamespace(id="user-1"),
        SimpleNamespace(id="run-1", project_id="project-1", department_id="dept-1"),
        [{"asset_id": source.id, "role": "motion_reference", "technical_role": "reference_video", "purpose": "动作参考"}],
        suppress_reference_text=True, reference_identity_policy="replace_actor",
    )
    assert roles[0]["asset_id"] == derived.id
    assert roles[0]["preprocessing"] == "opening_clip_motion_only_no_text"
    assert preprocessing[0]["strip_reference_text"] is True
    assert preprocessing[0]["reference_identity_policy"] == "replace_actor"


@pytest.mark.asyncio
async def test_continuity_anchor_materializes_from_source_tail(monkeypatch):
    from app.media import workbench_v2

    source = SimpleNamespace(
        id="source-video", project_run_id="run-1", project_id="project-1", department_id="dept-1",
        file_name="nominal-15-second-source.mp4", mime_type="video/mp4", sha256="source-sha",
    )
    derived = SimpleNamespace(
        id="derived-video", project_run_id="run-1", project_id="project-1", department_id="dept-1",
        file_name="h3-reference-tail.mp4", mime_type="video/mp4", sha256="derived-sha",
    )
    captured = {}

    class FakeDb:
        async def get(self, _model, asset_id):
            return {source.id: source, derived.id: derived}.get(asset_id)

    async def fake_duration(_db, asset):
        return 15.083333 if asset.id == source.id else 14.0

    async def fake_derive(*_args, **kwargs):
        captured.update(kwargs)
        return derived

    monkeypatch.setattr(workbench_v2, "_reference_asset_duration", fake_duration)
    monkeypatch.setattr(workbench_v2, "_ensure_h3_reference_clip", fake_derive)

    roles, preprocessing = await workbench_v2._materialize_h3_source_roles(
        FakeDb(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="run-1", project_id="project-1", department_id="dept-1"),
        [{
            "asset_id": source.id,
            "role": "continuity_anchor",
            "technical_role": "reference_video",
            "purpose": "上一段视频续写锚点",
        }],
    )

    assert captured["clip_strategy"] == "tail"
    assert roles[0]["asset_id"] == derived.id
    assert roles[0]["preprocessing"] == "tail_clip"
    assert preprocessing[0]["clip_start_seconds"] == pytest.approx(1.083)
    assert preprocessing[0]["strategy"] == "tail_clip"


def test_v2_job_has_schedule_workflow_batch_and_continuation_lineage():
    columns = MediaGenerationJob.__table__.columns
    for name in (
        "production_batch_id", "workflow_definition_id", "workflow_definition_version",
        "continuation_chain_id", "depends_on_job_id", "sequence_index", "not_before_at", "deadline_at",
        "review_assignee_id", "review_tags_json", "business_title", "output_preset_id",
        "poster_asset_id", "source_roles_json", "creative_option", "job_group_id",
    ):
        assert name in columns


def test_clone_keeps_compiled_plan_but_does_not_cross_run_plan_comparison_scope():
    source = (Path(__file__).parents[1] / "app" / "media" / "service.py").read_text(encoding="utf-8")
    assert 'source.plan_comparison_id if source.project_run_id == run.id else None' in source
    assert '"reference_assets": payload.get("reference_assets")' in source
    assert '"workflow_definition_id": payload.get("workflow_definition_id") or source.workflow_definition_id' in source


def test_failed_continuation_seam_creates_diagnostic_preview_without_review_job():
    source = (Path(__file__).parents[1] / "app" / "media" / "workbench_v2.py").read_text(encoding="utf-8")
    assert '"reason": "continuation_seam_hard_gate_failed"' in source
    assert '"formal_asset": False' in source
    assert 'diagnostic_preview = await _create_continuation_preview' in source


def test_event_stream_cursor_is_database_backed_and_monotonic():
    columns = MediaWorkbenchStreamEvent.__table__.columns
    assert "previous_cursor" in columns
    assert "fingerprint" in columns
    assert "changed_events_json" in columns
    assert MediaWorkbenchStreamEvent.__table__.c.id.autoincrement is True

    router_source = (Path(__file__).parents[1] / "app" / "projects" / "router.py").read_text(encoding="utf-8")
    assert 'request.headers.get("last-event-id")' in router_source
    assert "MediaWorkbenchStreamEvent.id > cursor" in router_source
    assert "capability_quota" not in router_source


@pytest.mark.asyncio
async def test_standard_multi_output_submission_uses_seed_only_variants(monkeypatch):
    from app.media import workbench_v2

    captured = {}

    async def fake_resolve(*_args, **_kwargs):
        return "fast_vertical", {"width": 480, "height": 864, "fps": 24, "steps": 20}, {"id": "fast_vertical"}

    async def fake_create(_db, _user, _project, _run, payload):
        captured.update(payload)
        return {"batch": {"id": "mpb-safe"}, "jobs": []}

    monkeypatch.setattr(workbench_v2, "_resolve_output_preset", fake_resolve)
    monkeypatch.setattr(workbench_v2, "create_production_batch", fake_create)

    await workbench_v2.submit_production(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1", department_id=931765248),
        SimpleNamespace(id="run-1", department_id=931765248),
        {
            "idempotency_key": "safe-batch-123",
            "brief": {"product": "超快感", "request": "成年人客厅口播"},
            "final_plan": {"creative_goal": "保持人物和首帧构图", "h3_mode": "image_to_video"},
            "source_roles": [],
            "output_preset_id": "fast_vertical",
            "duration_seconds": 5,
            "audio_enabled": False,
            "quantity": 2,
        },
    )

    assert captured["variation_policy"] == "seed_only"
    assert captured["directions"][0]["final_plan"]["creative_goal"] == "保持人物和首帧构图"
    assert captured["params"]["audio_enabled"] is False


@pytest.mark.asyncio
async def test_seed_only_batch_preserves_people_plan_and_removes_stale_layout_variant(monkeypatch):
    from app.media import workbench_v2

    submitted = []

    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    class FakeDb:
        async def execute(self, _query):
            return EmptyResult()

        def add(self, _row):
            return None

        async def flush(self):
            return None

    async def fake_submit(_db, _user, _project, _run, payload):
        submitted.append(payload)
        return {"job": {"id": f"job-{len(submitted)}"}}

    async def fake_counts(_db, _batch_id):
        return {"total": 2, "queued": 2}

    async def fake_serialize(_db, batch):
        return {"id": batch.id}

    monkeypatch.setattr(
        workbench_v2,
        "_core",
        lambda: SimpleNamespace(_submit_job=fake_submit, _new_id=lambda prefix: f"{prefix}-test"),
    )
    monkeypatch.setattr(workbench_v2, "_batch_counts", fake_counts)
    monkeypatch.setattr(workbench_v2, "_serialize_batch", fake_serialize)

    await workbench_v2.create_production_batch(
        FakeDb(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1", department_id=931765248),
        SimpleNamespace(id="run-1", department_id=931765248),
        {
            "idempotency_key": "seed-only-123",
            "title": "人物口播双版本",
            "variation_policy": "seed_only",
            "directions": [
                {
                    "id": "direction-1",
                    "variant_count": 2,
                    "final_plan": {
                        "creative_goal": "同一对成年人在暖色客厅自然口播",
                        "h3_mode": "image_to_video",
                        "production_variant": {"variant_key": "stale-layout"},
                    },
                }
            ],
            "seed": 100,
            "params": {"seed": 100},
        },
    )

    assert len(submitted) == 2
    assert [item["params"]["seed"] for item in submitted] == [101, 102]
    assert all("production_variant" not in item["final_plan"] for item in submitted)
    assert all(item["final_plan"]["creative_goal"] == "同一对成年人在暖色客厅自然口播" for item in submitted)
    assert all(item["final_plan"]["production_campaign"]["variation_policy"] == "seed_only" for item in submitted)
