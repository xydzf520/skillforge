from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.common.exceptions import AppError
from app.media.workbench_v3 import (
    AGENT_MODEL_PROFILE,
    EXACT_PROMPT_POLICY_VERSION,
    PROMPT_OPTIMIZATION_POLICY_VERSION,
    _default_agent_prompt,
    _agent_runtime,
    _normalize_explicit_sources,
    _local_replace_instruction,
    _normalize_local_replacement_items,
    _normalize_directions,
    _normalize_replay_agent_segments,
    _node_supports_local_replace,
    _replay_preview_sources,
    _validate_reference_tokens,
    _replay_product_sources,
    _prompt_optimize,
    compose_exact_h3_prompt,
    normalize_prompt_optimization,
    plan_replay_segments,
)


def test_workbench_agent_defaults_are_real_h3_instructions():
    prompt_agent = _default_agent_prompt("video_prompt_h3")
    replay_agent = _default_agent_prompt("video_replay_h3")

    assert "视频提示词 Agent" in prompt_agent
    assert "MiniMax H3" in prompt_agent
    assert "confirmed_source_bindings" in prompt_agent
    assert "禁止虚构“参考图1/2/3”" in prompt_agent
    assert "asset_roles: []" in prompt_agent
    assert "视频复刻 Agent" in replay_agent
    assert "subject_definitions" in replay_agent
    assert AGENT_MODEL_PROFILE == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_agent_runtime_rolls_shipped_default_forward_without_overwriting_custom_prompt(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    old_default = "old shipped default"
    row = SimpleNamespace(
        system_prompt=old_default,
        default_prompt_sha256=hashlib.sha256(old_default.encode("utf-8")).hexdigest(),
        version=4,
        updated_by="u1",
        updated_at=None,
    )

    async def fake_profile(*_args, **_kwargs):
        return row

    monkeypatch.setattr("app.media.workbench_v3._agent_profile_row", fake_profile)
    db = FakeDb()
    runtime = await _agent_runtime(db, SimpleNamespace(id="u1"), SimpleNamespace(id="project"), "video_prompt_h3")
    assert runtime["system_prompt"] == _default_agent_prompt("video_prompt_h3")
    assert runtime["is_custom"] is False
    assert runtime["version"] == 5
    assert db.flushes == 1

    custom = "my deliberate custom instructions"
    row.system_prompt = custom
    row.default_prompt_sha256 = hashlib.sha256(old_default.encode("utf-8")).hexdigest()
    db.flushes = 0
    runtime = await _agent_runtime(db, SimpleNamespace(id="u1"), SimpleNamespace(id="project"), "video_prompt_h3")
    assert runtime["system_prompt"] == custom
    assert runtime["is_custom"] is True
    assert db.flushes == 0


def test_replay_agent_must_cover_service_owned_segments_and_cannot_forge_bindings():
    ranges = [
        {"index": 0, "start_seconds": 0, "end_seconds": 8, "duration_seconds": 8},
        {"index": 1, "start_seconds": 8, "end_seconds": 16, "duration_seconds": 8},
    ]
    normalized = _normalize_replay_agent_segments(
        {"segments": [
            {"index": 0, "start_seconds": 99, "end_seconds": 100, "visual_prompt": "保持开场节拍。"},
            {"index": 1, "start_seconds": 100, "end_seconds": 101, "visual_prompt": "承接上一段人物与动作。"},
        ]},
        ranges,
        required=True,
    )
    assert [(item["start_seconds"], item["end_seconds"]) for item in normalized] == [(0.0, 8.0), (8.0, 16.0)]

    with pytest.raises(AppError) as missing:
        _normalize_replay_agent_segments({"segments": normalized[:1]}, ranges, required=True)
    assert missing.value.code == "MEDIA_REPLAY_AGENT_OUTPUT_INVALID"

    with pytest.raises(AppError) as forged:
        _normalize_replay_agent_segments(
            {"segments": [
                {"index": 0, "visual_prompt": "使用 <Video 2>"},
                {"index": 1, "visual_prompt": "继续"},
            ]},
            ranges,
            required=True,
        )
    assert forged.value.code == "MEDIA_REPLAY_AGENT_OUTPUT_INVALID"


def test_material_workbench_32_declares_agent_workspace_and_capabilities():
    root = Path(__file__).parents[1] / "demo-projects" / "material-workbench"
    manifest = (root / "projectforge.yaml").read_text(encoding="utf-8")
    page = (root / "web" / "index.html").read_text(encoding="utf-8")
    script = (root / "web" / "js" / "app.js").read_text(encoding="utf-8")

    assert "version: 3.2.0" in manifest
    assert "video.agent.save" in manifest
    assert 'data-workspace="agent"' in page
    assert 'id="agentSystemPrompt"' in page
    assert 'id="generateReplay"' in page
    assert 'id="localReplacementList"' in page
    assert "api.agentInvoke" in script
    assert "generateReplay" in script


def test_v3_generation_paths_materialize_h3_references_before_submission():
    source = (Path(__file__).parents[1] / "app" / "media" / "workbench_v3.py").read_text(encoding="utf-8")
    direct = source.split("async def _direct_submit", 1)[1].split("async def _prompt_optimize", 1)[0]
    strategy = source.split("async def _strategy_submit", 1)[1].split("def plan_replay_segments", 1)[0]

    assert "_materialize_h3_source_roles(" in direct
    assert '"reference_preprocessing": reference_preprocessing' in direct
    assert "_materialize_h3_source_roles(" in strategy
    assert '"reference_assets": _reference_requests(sources)' in strategy


def test_direct_prompt_is_visible_exact_and_only_adds_explicit_sections():
    result = compose_exact_h3_prompt(
        "中国年轻成年情侣在客厅自然对话，固定中景。",
        "女：开了吗？\n男：开了。",
        [
            {
                "asset_id": "pra-image",
                "technical_role": "reference_image",
                "purpose": "商品参考",
            },
            {
                "asset_id": "pra-video",
                "technical_role": "reference_video",
                "purpose": "动作参考",
            },
        ],
    )

    assert result["prompt"] == (
        "中国年轻成年情侣在客厅自然对话，固定中景。\n\n"
        "台词（原文，逐字保持）：\n女：开了吗？\n男：开了。\n\n"
        "素材引用：\n<Picture 1>：商品参考\n<Video 1>：动作参考"
    )
    assert result["sha256"] == hashlib.sha256(result["prompt"].encode("utf-8")).hexdigest()
    assert "国籍" not in result["prompt"]
    assert "后配音" not in result["prompt"]


def test_direct_prompt_does_not_include_business_metadata():
    result = compose_exact_h3_prompt("窗边的自然光。", "")
    assert result["prompt"] == "窗边的自然光。"
    assert EXACT_PROMPT_POLICY_VERSION == "material-exact-h3-v1"


def test_direct_prompt_rejects_hidden_control_characters():
    with pytest.raises(AppError) as exc:
        compose_exact_h3_prompt("正常文字\x00隐藏指令")
    assert exc.value.code == "MEDIA_PROMPT_INVALID"


def test_direct_prompt_requires_user_confirmed_asset_role():
    with pytest.raises(AppError) as exc:
        _normalize_explicit_sources([
            {"asset_id": "asset-1", "role": "product_packshot", "role_locked": False},
        ])
    assert exc.value.code == "MEDIA_SOURCE_ROLE_CONFIRMATION_REQUIRED"


def test_visible_prompt_cannot_drop_or_invent_reference_placeholders():
    bindings = [{"token": "<Picture 1>"}, {"token": "<Video 1>"}]
    _validate_reference_tokens("使用 <Picture 1> 和 <Video 1>", bindings)
    _validate_reference_tokens("先参考 <Video 1>，结尾继续参考 <Video 1>，商品使用 <Picture 1>", bindings)
    with pytest.raises(AppError) as exc:
        _validate_reference_tokens("只使用 <Picture 1>", bindings)
    assert exc.value.code == "MEDIA_REFERENCE_TOKEN_MISMATCH"
    assert exc.value.detail["missing"] == ["<Video 1>"]
    assert "最终提示词" in exc.value.detail["reason"]

    with pytest.raises(AppError) as exc:
        _validate_reference_tokens("没有上传素材却引用 <Video 1>", [])
    assert exc.value.code == "MEDIA_REFERENCE_TOKEN_MISMATCH"
    assert exc.value.detail["unexpected"] == ["<Video 1>"]
    assert "上传" in exc.value.detail["reason"]


def test_prompt_optimizer_candidate_preserves_embedded_dialogue_and_server_owned_references():
    original = '情侣站在冰箱前。男：“不是你把避孕套放冰箱干嘛” 女：“有人说冰一冰体验更好” 冰箱保留。'
    result = normalize_prompt_optimization(
        {
            "optimized_visual_prompt": (
                '5秒居家对话。情侣穿睡衣站在保留的冰箱前。'
                '男自然拿起包装：“不是你把避孕套放冰箱干嘛” '
                '女轻松回应：“有人说冰一冰体验更好”'
            ),
            "preserved_items": ["冰箱", "原台词"],
            "assumptions": ["暖色生活光"],
        },
        original_visual_prompt=original,
        script="",
        sources=[{
            "asset_id": "asset-1",
            "technical_role": "reference_image",
            "role": "visual_reference",
            "purpose": "人物参考",
        }],
    )

    assert result["policy_version"] == PROMPT_OPTIMIZATION_POLICY_VERSION
    assert "不是你把避孕套放冰箱干嘛" in result["optimized_final_prompt"]
    assert "<Picture 1>：人物参考" in result["optimized_final_prompt"]
    assert result["original_final_prompt_sha256"] != result["optimized_final_prompt_sha256"]


def test_prompt_optimizer_rejects_silent_dialogue_rewrite_and_invented_reference_token():
    with pytest.raises(AppError) as dialogue_exc:
        normalize_prompt_optimization(
            {"optimized_visual_prompt": '情侣站在冰箱前。男：“你怎么放这里了”'},
            original_visual_prompt='情侣站在冰箱前。男：“不是你把避孕套放冰箱干嘛”',
            script="",
            sources=[],
        )
    assert dialogue_exc.value.code == "MEDIA_PROMPT_OPTIMIZATION_DIALOGUE_CHANGED"

    with pytest.raises(AppError) as reference_exc:
        normalize_prompt_optimization(
            {"optimized_visual_prompt": "使用 <Picture 1> 作为人物参考"},
            original_visual_prompt="使用人物参考图",
            script="",
            sources=[],
        )
    assert reference_exc.value.code == "MEDIA_PROMPT_OPTIMIZATION_REFERENCE_INVALID"

    with pytest.raises(AppError) as unquoted_dialogue_exc:
        normalize_prompt_optimization(
            {"optimized_visual_prompt": "情侣站在冰箱前。男：你怎么放这里了 女：听说这样更好"},
            original_visual_prompt="情侣站在冰箱前。 男：不是你把避孕套放冰箱干嘛 女：有人说冰一冰体验更好",
            script="",
            sources=[],
        )
    assert unquoted_dialogue_exc.value.code == "MEDIA_PROMPT_OPTIMIZATION_DIALOGUE_CHANGED"

    with pytest.raises(AppError) as preserve_exc:
        normalize_prompt_optimization(
            {"optimized_visual_prompt": "情侣在卧室自然对话。"},
            original_visual_prompt="情侣在厨房自然对话，冰箱保留，场景可以调整。",
            script="",
            sources=[],
        )
    assert preserve_exc.value.code == "MEDIA_PROMPT_OPTIMIZATION_PRESERVE_CHANGED"


@pytest.mark.asyncio
async def test_prompt_optimize_is_preview_only_and_uses_h3_fidelity_contract(monkeypatch):
    captured = {}

    async def fake_strategy_ai(_db, _user, _run, **kwargs):
        captured.update(kwargs)
        return "deepseek-v4-flash", {
            "optimized_visual_prompt": '地铁闺蜜近景。左女：“对吗” 右女：“对啊”',
            "summary": "按时间线整理",
            "modifications": [],
            "preserved_items": ["台词逐字保持"],
            "assumptions": [],
            "warnings": [],
        }

    monkeypatch.setattr("app.media.workbench_v3._strategy_ai", fake_strategy_ai)
    async def fake_agent_runtime(_db, _user, _project, _agent_key):
        return {
            "agent_key": "video_prompt_h3", "display_name": "视频提示词 Agent",
            "description": "test", "model": AGENT_MODEL_PROFILE, "version": 3,
            "system_prompt": "按保留/更换/允许调整等用户要求生成 H3 提示词。",
            "prompt_sha256": "a" * 64, "default_prompt_sha256": "b" * 64,
            "is_custom": True, "updated_at": None,
        }
    monkeypatch.setattr("app.media.workbench_v3._agent_runtime", fake_agent_runtime)
    result = await _prompt_optimize(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1"),
        SimpleNamespace(id="run-1", execution_run_id=None),
        {
            "visual_prompt": '地铁闺蜜自拍。左女：“对吗” 右女：“对啊”',
            "script": "",
            "duration_seconds": 5,
            "ratio": "9:16",
            "source_roles": [],
        },
    )

    assert result["optimization"]["requires_user_confirmation"] is True
    assert result["optimization"]["applied"] is False
    assert result["optimization"]["model"] == "deepseek-v4-flash"
    assert result["optimization"]["agent"]["version"] == 3
    assert captured["feature"] == "prompt_optimize"
    assert captured["policy_version"] == PROMPT_OPTIMIZATION_POLICY_VERSION
    assert captured["temperature"] == 0.2
    assert "候选只供用户比较" in captured["prompt"]
    assert "保留/更换/允许调整" in captured["system"]


@pytest.mark.asyncio
async def test_prompt_optimize_long_inline_dialogue_falls_back_without_unknown_error(monkeypatch):
    original = (
        "给minimax h3 写个提示词，人物服装换成睡衣，人物的脸需要变，更生活化，"
        "女生要好看，冰箱保留，女生要欲拒还迎。台词："
        "男：不是你把避孕套放冰箱干嘛 "
        "女：有人说冰一冰体验更好 "
        "男：你你怎么又买避孕套了 "
        "女：哎这不是普通的，这可是air空气套，air的存在感真的很低，跟空气一样，"
        "你看它贴在手上，薄的就像一层皮，戴了跟没戴一样， "
        "男：嗯 "
        "女：而且它不是那种干薄，里面添加了玻尿酸，润感是够的，"
        "所以我现在都不太看什么0.10.2了，这种东西还是自己身体舒适最重要"
    )
    calls = []

    async def fake_strategy_ai(_db, _user, _run, **kwargs):
        calls.append(kwargs["prompt"])
        return "deepseek-v4-flash", {
            "optimized_visual_prompt": "情侣穿睡衣站在冰箱前。男：怎么放冰箱了 女：听说这样更好",
            "summary": "错误地改写了台词",
        }

    monkeypatch.setattr("app.media.workbench_v3._strategy_ai", fake_strategy_ai)
    async def fake_agent_runtime(_db, _user, _project, _agent_key):
        return {
            "agent_key": "video_prompt_h3", "display_name": "视频提示词 Agent",
            "description": "test", "model": AGENT_MODEL_PROFILE, "version": 1,
            "system_prompt": "保真优化，不改台词。", "prompt_sha256": "a" * 64,
            "default_prompt_sha256": "a" * 64, "is_custom": False, "updated_at": None,
        }
    monkeypatch.setattr("app.media.workbench_v3._agent_runtime", fake_agent_runtime)
    result = await _prompt_optimize(
        SimpleNamespace(),
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="project-1"),
        SimpleNamespace(id="run-1", execution_run_id=None),
        {
            "visual_prompt": original,
            "script": "",
            "duration_seconds": 15,
            "ratio": "9:16",
            "source_roles": [],
        },
    )

    optimization = result["optimization"]
    assert len(calls) == 2
    assert "不要概括、纠错、润色或规范化" in calls[1]
    assert optimization["fidelity_fallback"] is True
    assert optimization["fidelity_failure_code"] == "MEDIA_PROMPT_OPTIMIZATION_DIALOGUE_CHANGED"
    assert original in optimization["optimized_visual_prompt"]
    assert "0.10.2" in optimization["optimized_final_prompt"]
    assert optimization["requires_user_confirmation"] is True


def test_strategy_a_b_keep_script_and_c_exposes_line_diff_without_applying_it():
    original = "女：开了吗？\n男：开了。"
    directions = _normalize_directions(
        {
            "directions": [
                {"title": "A", "suggested_script": "不应采用"},
                {"title": "B", "suggested_script": "也不应采用"},
                {"title": "C", "suggested_script": "女：准备好了吗？\n男：好了。"},
            ]
        },
        original,
    )
    assert directions[0]["suggested_script"] == original
    assert directions[1]["suggested_script"] == original
    assert directions[2]["script_change_accepted"] is False
    assert [line["changed"] for line in directions[2]["script_diff"]] == [True, True]


def test_replay_product_image_is_only_injected_for_explicit_replacement():
    assert _replay_product_sources({"product_mode": "keep", "product_asset_ids": ["pra-1"]}) == []
    assert _replay_product_sources({"product_mode": "replace", "product_asset_ids": ["pra-1"]}) == [
        {
            "asset_id": "pra-1",
            "technical_role": "reference_image",
            "role": "product_packshot",
            "purpose": "用户确认用于替换的正确商品素材",
        }
    ]
    assert "所选正确商品素材" in _local_replace_instruction({"product_mode": "replace"})
    assert "原片局部替换" in _local_replace_instruction({"product_mode": "replace"})


def test_local_replacement_normalizes_time_scope_and_preservation_contract():
    items = _normalize_local_replacement_items([
        {
            "scope": "visual_audio",
            "original_text": "已抢购 3000 单",
            "replacement_text": "已抢购 5000 单",
            "start_seconds": 1.2,
            "end_seconds": 4.8,
            "region_hint": "画面下方商品堆头",
        },
        {
            "scope": "audio_only",
            "original_text": "今天三千单",
            "replacement_text": "今天五千单",
            "start_seconds": 4.8,
        },
    ], 7.08)

    assert items[0]["start_seconds"] == 1.2
    assert items[0]["end_seconds"] == 4.8
    assert items[1]["end_seconds"] == 7.08
    instruction = _local_replace_instruction({"product_mode": "keep", "replacement_items": items})
    assert "同步替换画面文字和对应口播" in instruction
    assert "只替换对应口播" in instruction
    assert "已抢购 3000 单" in instruction
    assert "已抢购 5000 单" in instruction
    assert "沿用原说话人的声线、语气、语速、节奏和环境声" in instruction


@pytest.mark.parametrize("items,code", [
    ([{"scope": "visual_only", "original_text": "旧值"}], "MEDIA_LOCAL_REPLACEMENT_INCOMPLETE"),
    ([{"scope": "visual_only", "original_text": "旧值", "replacement_text": "新值", "start_seconds": 6, "end_seconds": 8}], "MEDIA_LOCAL_REPLACEMENT_RANGE_INVALID"),
    ([{"scope": "unknown", "original_text": "旧值", "replacement_text": "新值"}], "MEDIA_LOCAL_REPLACEMENT_SCOPE_INVALID"),
])
def test_local_replacement_rejects_ambiguous_targets(items, code):
    with pytest.raises(AppError) as exc:
        _normalize_local_replacement_items(items, 7.08)
    assert exc.value.code == code


def test_local_replace_capability_reads_bridge_report_and_fails_closed():
    capable = SimpleNamespace(bridge_capabilities_json=json.dumps({
        "media": {"profiles": ["video_local_edit_sam2_propainter_data_patch_v2"]},
    }))
    missing = SimpleNamespace(bridge_capabilities_json=json.dumps({
        "media": {"profiles": ["h3_all_modes_v1"]},
    }))
    malformed = SimpleNamespace(bridge_capabilities_json="not-json")

    assert _node_supports_local_replace(capable) is True
    assert _node_supports_local_replace(missing) is False
    assert _node_supports_local_replace(malformed) is False


@pytest.mark.parametrize("duration", [40, 50, 60])
def test_full_replay_segments_cover_entire_source_without_over_15_seconds(duration):
    segments = plan_replay_segments(duration)
    assert segments[0]["start_seconds"] == 0
    assert segments[-1]["end_seconds"] == duration
    assert all(item["duration_seconds"] <= 15 for item in segments)
    assert all(item["duration_seconds"] + item["overlap_seconds"] <= 15 for item in segments)
    assert all(item["duration_seconds"] >= 4 for item in segments)
    assert all(segments[index]["end_seconds"] == segments[index + 1]["start_seconds"] for index in range(len(segments) - 1))
    assert sum(item["duration_seconds"] for item in segments) == pytest.approx(duration)
    assert all(item["overlap_seconds"] == pytest.approx(0.5) for item in segments[1:])


def test_full_replay_uses_valid_shot_boundaries_when_available():
    segments = plan_replay_segments(
        40,
        [
            {"end_seconds": 9},
            {"end_seconds": 21},
            {"end_seconds": 34},
            {"end_seconds": 40},
        ],
    )
    assert [(item["start_seconds"], item["end_seconds"]) for item in segments] == [
        (0.0, 9.0),
        (9.0, 21.0),
        (21.0, 34.0),
        (34.0, 40.0),
    ]
    assert all(item["overlap_seconds"] == 0 for item in segments)


def test_full_replay_continuity_uses_still_anchor_not_second_reference_video():
    segment = SimpleNamespace(
        segment_index=1,
        start_seconds=14.0,
        end_seconds=28.0,
        source_clip_asset_id=None,
    )
    sources = _replay_preview_sources(segment, {})
    current_clip_and_anchor = compose_exact_h3_prompt(
        "保持当前源片段结构。",
        "",
        sources,
    )

    assert "<Video 1>：源视频 14.00–28.00 秒完整片段" in current_clip_and_anchor["prompt"]
    assert "<Picture 1>：上一生成片段的尾帧锚点" in current_clip_and_anchor["prompt"]
    assert "<Video 2>" not in current_clip_and_anchor["prompt"]


@pytest.mark.parametrize("duration", [1.9, 60.1])
def test_full_replay_rejects_duration_outside_contract(duration):
    with pytest.raises(AppError) as exc:
        plan_replay_segments(duration)
    assert exc.value.code == "MEDIA_REPLAY_DURATION_INVALID"


def test_bridge_local_edit_contract_rejects_arbitrary_workflow_and_accepts_fixed_profile():
    from bridge import skillforgebridge as bridge

    payload = {
        "job_id": "local-edit-1",
        "template_id": bridge.MEDIA_LOCAL_EDIT_TEMPLATE_ID,
        "mode": "video_local_edit",
        "prompt": {
            "integrated_multimodal_description": "仅修改遮罩区域，其他像素保持不变。",
            "local_edit_config": {
                "product_mode": "replace",
                "remove_text": False,
                "replacement_items": [{
                    "scope": "visual_audio",
                    "original_text": "已抢购 3000 单",
                    "replacement_text": "已抢购 5000 单",
                    "start_seconds": 1,
                    "end_seconds": 4,
                }],
            },
        },
        "params": {"width": 480, "height": 864, "frames": 124, "fps": 24, "steps": 20, "batch_count": 1},
        "references": [
            {"asset_id": "video", "file_name": "source.mp4", "mime_type": "video/mp4", "byte_size": 1024, "role": "reference_video"},
            {"asset_id": "image", "file_name": "product.png", "mime_type": "image/png", "byte_size": 512, "role": "reference_image"},
        ],
    }
    normalized, error = bridge._normalize_media_job_payload(payload)
    assert error is None
    assert normalized["mode"] == "video_local_edit"

    rejected, error = bridge._normalize_media_job_payload({**payload, "workflow": {"arbitrary": True}})
    assert rejected is None
    assert "arbitrary workflow" in error
    _default_agent_prompt,
    _normalize_replay_agent_segments,
