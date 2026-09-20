import asyncio
import inspect
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.common.exceptions import AppError
from app.media.fde_v4 import (
    FDE_MEDIA_CAPABILITIES,
    MATERIAL_FDE_PROMPT_POLICY,
    MATERIAL_FDE_VERSION,
    _batch_decision,
    _decode_cursor,
    _decode_request_cursor,
    _decode_value_cursor,
    _encode_cursor,
    _encode_request_cursor,
    _encode_value_cursor,
    _request_values,
    _technical_state,
    _delivery_status_from_outbox,
    _can_make_editorial_decision,
    normalize_material_performance_fact,
)
from app.media.models import MediaGenerationJob
from app.media.product_assets import _view_intent, classify_product_asset_name
from app.media.workbench_v3 import _included_business_context, compose_exact_h3_prompt


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = ROOT / "demo-projects" / "material-workbench"


def _job(status: str = "queued") -> MediaGenerationJob:
    return MediaGenerationJob(
        id="mvj-fde-test",
        project_id="samplebrand-material-workbench",
        project_run_id="prun-fde-test",
        idempotency_key="fde-test",
        mode="text_to_video",
        status=status,
        prompt_json={},
        params_json={},
        reference_assets_json=[],
        workflow_template_id="h3_t2v_v1",
        result_json={},
    )


def test_fde_request_defaults_keep_business_fields_out_of_generation():
    values = _request_values({
        "title": "真实业务需求",
        "business": {
            "product": "产品A",
            "sku": "SKU-001",
            "platform": "抖音",
            "selling_points": "卖点",
            "promotion": "活动",
        },
        "visual_prompt": "固定机位拍摄一段生活化双人对话。",
    })

    assert values["business_json"]["product"] == "产品A"
    assert values["generation_participation_json"] == {
        "product": False,
        "sku": False,
        "platform": False,
        "selling_points": False,
        "promotion": False,
    }
    assert _included_business_context({
        "business_info": values["business_json"],
        "business_participation": {
            key: "management_only" for key in values["generation_participation_json"]
        },
    }) == {}


def test_fde_business_context_requires_explicit_ai_strategy_participation():
    result = _included_business_context({
        "business_info": {
            "product": "产品A",
            "sku": "SKU-001",
            "platform": "抖音",
            "selling_points": "轻薄",
            "promotion": "两位数到手",
        },
        "business_participation": {
            "product": "ai_strategy",
            "sku": "management_only",
            "platform": "management_only",
            "selling_points": "ai_strategy",
            "promotion": "management_only",
        },
    })

    assert result == {"product": "产品A", "selling_points": "轻薄"}


def test_exact_prompt_assembly_is_visible_and_hash_stable():
    assembled = compose_exact_h3_prompt(
        "地铁车厢自拍视角，双人闺蜜近景。",
        "左女：你是说放冰箱会更好用对吗？\n右女：对啊。",
        [
            {"asset_id": "pra-image", "technical_role": "reference_image", "purpose": "人物妆造参考"},
            {"asset_id": "pra-video", "technical_role": "reference_video", "purpose": "动作节奏参考"},
        ],
    )

    assert assembled["prompt"].startswith("地铁车厢自拍视角")
    assert "台词（原文，逐字保持）" in assembled["prompt"]
    assert "<Picture 1>：人物妆造参考" in assembled["prompt"]
    assert "<Video 1>：动作节奏参考" in assembled["prompt"]
    assert len(assembled["sha256"]) == 64
    assert assembled == compose_exact_h3_prompt(
        "地铁车厢自拍视角，双人闺蜜近景。",
        "左女：你是说放冰箱会更好用对吗？\n右女：对啊。",
        [
            {"asset_id": "pra-image", "technical_role": "reference_image", "purpose": "人物妆造参考"},
            {"asset_id": "pra-video", "technical_role": "reference_video", "purpose": "动作节奏参考"},
        ],
    )


def test_candidate_technical_gate_does_not_invent_editorial_approval():
    job = _job("awaiting_review")
    job.result_asset_id = "pra-result"
    job.result_json = {"review_manifest": {"status": "ready"}}

    technical_status, evidence = _technical_state(job)

    assert technical_status == "passed"
    assert evidence["job_status"] == "awaiting_review"
    assert "editorial" not in evidence


def test_candidate_technical_gate_routes_invalid_media_to_repair():
    job = _job("awaiting_review")
    job.result_asset_id = "pra-result"
    job.result_json = {"technical_validation": {"passed": False, "reason": "decode_failed"}}

    technical_status, evidence = _technical_state(job)

    assert technical_status == "repair_required"
    assert evidence["technical_validation"]["reason"] == "decode_failed"


def test_fde_cursors_round_trip_without_losing_sort_keys():
    created_at = datetime(2026, 8, 18, 10, 30, 15)

    decoded_at, decoded_id = _decode_cursor(_encode_cursor(created_at, "mca-1"))
    assert decoded_at == created_at
    assert decoded_id == "mca-1"

    priority, decoded_at, decoded_id = _decode_request_cursor(
        _encode_request_cursor(300, created_at, "mmr-1")
    )
    assert (priority, decoded_at, decoded_id) == (300, created_at, "mmr-1")

    value, decoded_id = _decode_value_cursor(_encode_value_cursor(123.45, "mca-2"))
    assert value == pytest.approx(123.45)
    assert decoded_id == "mca-2"


def test_fde_facade_has_no_batch_select_capability():
    assert "material.decision.batch_select" not in FDE_MEDIA_CAPABILITIES
    assert {"material.decision.submit", "material.decision.batch_hold", "material.decision.batch_reject"} <= FDE_MEDIA_CAPABILITIES


def test_only_platform_editorial_roles_can_make_business_decisions():
    assert _can_make_editorial_decision(SimpleNamespace(role="aibp")) is True
    assert _can_make_editorial_decision(SimpleNamespace(role="dept_admin")) is True
    assert _can_make_editorial_decision(SimpleNamespace(role="observer")) is False


def test_fde_batch_select_is_rejected_before_database_mutation():
    with pytest.raises(AppError) as exc_info:
        asyncio.run(_batch_decision(
            SimpleNamespace(),
            SimpleNamespace(id="user-1"),
            SimpleNamespace(id="samplebrand-material-workbench"),
            SimpleNamespace(id="prun-1", project_id="samplebrand-material-workbench", department_id="931765248"),
            {"candidate_ids": ["mca-1"]},
            "selected",
        ))
    assert exc_info.value.code == "MEDIA_EDITORIAL_BATCH_SELECT_FORBIDDEN"


def test_project_contract_is_v4_and_declares_only_material_batch_hold_reject():
    manifest = yaml.safe_load((PROJECT_DIR / "projectforge.yaml").read_text(encoding="utf-8"))

    assert manifest["version"] == MATERIAL_FDE_VERSION
    assert manifest["metadata"]["prompt_policy"]["version"] == MATERIAL_FDE_PROMPT_POLICY
    assert "material.decision.batch_select" not in manifest["capabilities"]
    assert "material.decision.batch_hold" in manifest["capabilities"]
    assert "material.decision.batch_reject" in manifest["capabilities"]


def test_production_metric_matches_gpu_queue_and_hides_unavailable_spend_card():
    html = (PROJECT_DIR / "web" / "index.html").read_text(encoding="utf-8")
    app_source = (PROJECT_DIR / "web" / "js" / "app.js").read_text(encoding="utf-8")
    service_source = (ROOT / "app" / "media" / "service.py").read_text(encoding="utf-8")

    assert "本周真实消耗" not in html
    assert "metricWeeklySpend" not in app_source
    assert "const activeStatuses = new Set(['queued', 'assigned', 'running', 'collecting'])" in app_source
    assert '"active": {"queued", "assigned", "running", "collecting"}' in service_source
    assert "state.jobActiveTotal" in app_source


def test_material_wall_uses_poster_images_before_hover_video():
    html = (PROJECT_DIR / "web" / "index.html").read_text(encoding="utf-8")
    app_source = (PROJECT_DIR / "web" / "js" / "app.js").read_text(encoding="utf-8")
    css_source = (PROJECT_DIR / "web" / "fde-v4.css").read_text(encoding="utf-8")
    render_source = inspect.cleandoc(
        app_source[app_source.index("function renderMaterialWall") : app_source.index("async function loadReviews")]
    )
    hover_source = inspect.cleandoc(
        app_source[app_source.index("function startWallPreview") : app_source.index("function renderMaterialWall")]
    )

    assert "<img loading=" in render_source
    assert "<video" not in render_source
    assert "document.createElement('video')" in hover_source
    assert "预览加载中" in hover_source
    assert "video.addEventListener('playing'" in hover_source
    assert "预览加载失败，点击打开" in hover_source
    assert "}, 300)" in hover_source
    assert ".material-wall-cover > video" in css_source
    assert "position: absolute" in css_source
    assert 'id="closeReviewDialog"' in html
    assert "$('closeReviewDialog').onclick = showMaterialWall" in app_source
    assert ".review-dialog-close" in css_source


@pytest.mark.parametrize(
    ("file_name", "product_key", "view_type", "package_count", "eligible"),
    [
        ("超快噶-正面 (2).png", "超快感", "front", None, True),
        ("超快感-侧1.png", "超快感", "side", None, True),
        ("超快感-单片.png", "超快感", "unit", None, True),
        ("001 5只-正.png", "001", "front", 5, True),
        ("铂金三合一-背.png", "铂金三合一", "back", None, True),
        ("air隐薄-撕开.png", "AIR隐薄", "open_pack", None, True),
        ("魔力玻玻16只.png", "魔力玻玻", "front", 16, True),
        ("裸片.png", None, "unit", None, False),
        ("图像合成_2026.png", None, "composite", None, False),
    ],
)
def test_product_image_file_names_are_classified_without_silent_unknown_binding(
    file_name, product_key, view_type, package_count, eligible
):
    result = classify_product_asset_name(file_name)

    assert result["product_key"] == product_key
    assert result["view_type"] == view_type
    assert result["package_count"] == package_count
    assert result["auto_reference_eligible"] is eligible


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("包装正面展示，正对镜头", "front"),
        ("把超快感包装侧放在桌面，展示侧面", "side"),
        ("拿出散片贴在手背展示薄度", "unit"),
        ("撕开包装并展示开盖状态", "open_pack"),
        ("展示背面说明", "back"),
    ],
)
def test_product_asset_view_intent_uses_explicit_camera_and_action_terms(prompt, expected):
    view_type, matched = _view_intent(prompt)

    assert view_type == expected
    assert matched


def test_product_asset_view_intent_does_not_match_single_ambiguous_character():
    assert _view_intent("人物正在侧头看向镜头，正常说话") == (None, [])


def test_v4_page_exposes_only_two_primary_workspaces():
    html = (PROJECT_DIR / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count('class="workspace-tab') == 2
    assert 'data-workspace="production"' in html
    assert 'data-workspace="review"' in html
    assert 'data-workspace="agent"' not in html


def test_delivery_outbox_does_not_claim_success_without_remote_id():
    pending = SimpleNamespace(status="completed", remote_video_id=None)
    delivered = SimpleNamespace(status="completed", remote_video_id="cloud-video-1")
    blocked = SimpleNamespace(status="blocked_connector_pending", remote_video_id=None)

    assert _delivery_status_from_outbox(pending) == "pending"
    assert _delivery_status_from_outbox(delivered) == "delivered"
    assert _delivery_status_from_outbox(blocked) == "pending"


def test_performance_fact_requires_both_exact_join_keys():
    with pytest.raises(AppError) as exc_info:
        normalize_material_performance_fact(
            {"video_id": "cloud-video-1", "date": "2026-08-18", "metrics": {"statCost": 12.5}},
            source_version="cloud-video-2026-08-18",
        )
    assert exc_info.value.code == "MEDIA_PERFORMANCE_EXACT_LINK_REQUIRED"


def test_performance_fact_normalizes_governed_cloud_and_ad_metrics():
    fact = normalize_material_performance_fact(
        {
            "video_id": "cloud-video-1",
            "material_id": "ad-material-9",
            "date": "2026-08-18",
            "metrics": {
                "statCost": "125.50",
                "payOrderAmountAndPayOrderCouponAmount": "251.00",
                "showCnt": 1000,
                "clickCnt": 50,
                "convertCnt": 4,
            },
        },
        source_version="cloud-video-2026-08-18",
    )

    assert fact["remote_video_id"] == "cloud-video-1"
    assert fact["ad_asset_id"] == "ad-material-9"
    assert fact["spend"] == Decimal("125.50")
    assert fact["gmv"] == Decimal("251.00")
    assert fact["roi"] == Decimal("2")
    assert fact["impressions"] == 1000
