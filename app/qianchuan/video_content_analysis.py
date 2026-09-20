"""Helpers for Qianchuan single-video content analysis MCP tools."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any


QIANCHUAN_PAGE_URL_TEMPLATE = (
    "https://qianchuan.jinritemai.example.test/dataV2/roi2-material-analysis"
    "?aavid={aavid}&tab=content-analyze&mar-goal=2#"
)
QIANCHUAN_API_BASE = "https://qianchuan.jinritemai.example.test/ad/api/data/v1"
QIANCHUAN_GF_VERSION = "1.0.0.5718"
QIANCHUAN_REFER = "ecp,7406240954379714587,7408427016095039514"
QIANCHUAN_MATERIAL_LIST_REFER = "ecp,7406240954379714587,7406240954379698203"

INTERACTION_METRICS: tuple[tuple[str, str, str], ...] = (
    ("click", "live_watch_count_for_roi2_v2", "整体点击次数"),
    ("lose", "video_lose_count_for_roi2", "整体流失数"),
    ("like", "video_like_count_for_roi2", "整体点赞次数"),
    ("comment", "video_comment_count_for_roi2_v2", "整体评论次数"),
    ("share", "video_share_count_for_roi2", "整体转发次数"),
    ("follow", "video_follow_count_for_roi2", "整体新增粉丝数"),
)

MATERIAL_LIST_DIMENSIONS: tuple[str, ...] = (
    "material_type",
    "material_name_v2",
    "material_id",
    "material_suggest_v2",
    "material_content_v2",
    "material_image_mode_v2",
    "material_suggest_reason_v2",
    "material_duration_v2",
    "material_create_time_v2",
    "material_source_v2",
    "material_tag_list",
)

MATERIAL_LIST_METRICS: tuple[str, ...] = (
    "stat_cost_for_roi2",
    "basic_stat_cost_for_roi2_v2",
    "total_prepay_and_pay_order_roi2",
    "total_pay_order_gmv_include_coupon_for_roi2",
    "total_pay_order_gmv_for_roi2",
    "total_pay_order_count_for_roi2",
    "total_cost_per_pay_order_for_roi2",
    "product_show_count_for_roi2",
    "product_click_count_for_roi2",
    "product_cvr_rate_for_roi2",
    "product_convert_rate_for_roi2",
    "live_show_count_for_roi2_v2",
    "live_watch_count_for_roi2_v2",
    "live_cvr_rate_for_roi2_v2",
    "live_convert_rate_for_roi2_v2",
    "video_play_count_for_roi2_v2",
    "video_play_finish_rate_for_roi2_v2",
    "video_play_duration_3s_rate_for_roi2",
    "video_like_count_for_roi2",
    "video_comment_count_for_roi2_v2",
    "video_follow_count_for_roi2",
    "material_related_ad_cnt",
    "material_related_product_cnt",
)

PHASES: tuple[tuple[str, int, int | None], ...] = (
    ("0-2s", 0, 2),
    ("3-5s", 3, 5),
    ("6-10s", 6, 10),
    ("11-16s", 11, 16),
    ("17-22s", 17, 22),
    ("23s+", 23, None),
)


class QianchuanAnalysisError(ValueError):
    """Raised when required Qianchuan analysis input or response is invalid."""


def qianchuan_video_content_analysis_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "aavid": {
                "type": "string",
                "description": "千川账户 aavid / advertiser_id，例如 1855723231649801",
            },
            "material_id": {
                "type": "string",
                "description": "视频素材长 material_id，例如 7639640154815758378；不传时可用 cloud_video_id / material_name 自动解析",
            },
            "cloud_video_id": {
                "type": "string",
                "description": "云视频短 ID / 千川素材名前缀，例如 100329907；用于先在千川视频素材列表中解析长 material_id",
            },
            "short_video_id": {
                "type": "string",
                "description": "cloud_video_id 的兼容别名",
            },
            "material_name": {
                "type": "string",
                "description": "可选，云视频完整视频名或千川素材名；用于辅助短 ID 命中视频素材",
            },
            "vid": {
                "type": "string",
                "description": "可选，视频 vid；提供后会额外读取脚本文本和内容公式",
            },
            "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "marketing_goal": {
                "type": "string",
                "description": "千川营销目标；视频素材推商品使用 1，默认 1",
                "default": "1",
            },
            "material_search_marketing_goal": {
                "type": "string",
                "description": "素材列表搜索使用的营销目标；不传先用 marketing_goal，短 ID 未命中时会尝试 2",
            },
            "p_date": {
                "type": "string",
                "description": "内容分析快照日期 YYYYMMDD；默认使用 end_date",
            },
            "page_url": {
                "type": "string",
                "description": "可选，先导航的千川页面 URL；默认自动拼 content-analyze 页面",
            },
            "include_top_videos": {
                "type": "boolean",
                "description": "是否额外读取同页 top video 参考；默认 true，失败只进入 warnings",
                "default": True,
            },
            "include_material_candidates": {
                "type": "boolean",
                "description": "是否返回用于短 ID 解析的千川素材候选；默认 true",
                "default": True,
            },
            "material_search_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "短 ID 解析时读取的千川视频素材列表条数，默认 100",
                "default": 100,
            },
            "material_type": {
                "type": "string",
                "description": "千川素材类型；视频素材为 3，默认 3",
                "default": "3",
            },
            "include_raw": {
                "type": "boolean",
                "description": "是否返回接口原始 business JSON；默认 false",
                "default": False,
            },
        },
        "required": ["aavid", "start_date", "end_date"],
    }


def normalize_qianchuan_video_content_args(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise QianchuanAnalysisError("参数必须是对象")
    aavid = _required_text(args.get("aavid"), "aavid")
    material_id = _optional_text(args.get("material_id"))
    cloud_video_id = _optional_text(args.get("cloud_video_id") or args.get("short_video_id") or args.get("video_id"))
    material_name = _optional_text(args.get("material_name") or args.get("video_name"))
    if not material_id and not (cloud_video_id or material_name):
        raise QianchuanAnalysisError("缺少 material_id；或提供 cloud_video_id / material_name 用于自动解析")
    start_date = _normalize_date(args.get("start_date"), "start_date")
    end_date = _normalize_date(args.get("end_date"), "end_date")
    if start_date > end_date:
        raise QianchuanAnalysisError("start_date 不能晚于 end_date")
    p_date = str(args.get("p_date") or _compact_date(end_date)).strip()
    if len(p_date) != 8 or not p_date.isdigit():
        raise QianchuanAnalysisError("p_date 必须是 YYYYMMDD")
    page_url = str(args.get("page_url") or QIANCHUAN_PAGE_URL_TEMPLATE.format(aavid=aavid)).strip()
    return {
        "aavid": aavid,
        "advertiser_id": aavid,
        "material_id": material_id,
        "cloud_video_id": cloud_video_id,
        "material_name": material_name,
        "vid": _optional_text(args.get("vid")),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_time": f"{start_date.isoformat()} 00:00:00",
        "end_time": f"{end_date.isoformat()} 23:59:59",
        "marketing_goal": str(args.get("marketing_goal") or "1").strip(),
        "material_search_marketing_goal": _optional_text(args.get("material_search_marketing_goal")),
        "p_date": p_date,
        "page_url": page_url,
        "include_top_videos": args.get("include_top_videos") is not False,
        "include_material_candidates": args.get("include_material_candidates") is not False,
        "material_search_limit": _bounded_int(args.get("material_search_limit"), default=100, minimum=1, maximum=200),
        "material_type": str(args.get("material_type") or "3").strip() or "3",
        "include_raw": args.get("include_raw") is True,
    }


def collect_qianchuan_video_content_analysis(
    args: dict[str, Any],
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Collect and normalize a single Qianchuan video's content analysis."""

    try:
        context = normalize_qianchuan_video_content_args(args)
    except QianchuanAnalysisError as exc:
        return {"ok": False, "error_code": "PARAM_INVALID", "error": str(exc)}

    responses: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if not _resolve_material_if_needed(context, fetcher, responses, warnings):
        return _material_resolution_failed_result(context, responses, warnings)

    requests = _build_requests(context)
    for name, spec in requests.items():
        if name == "script" and not context.get("vid"):
            warnings.append({
                "endpoint": name,
                "message": "未提供 vid，已跳过脚本文本和内容公式读取",
            })
            continue
        if name == "top_videos" and not context.get("include_top_videos"):
            continue
        try:
            responses[name] = fetcher(spec)
        except Exception as exc:  # noqa: BLE001
            warnings.append({"endpoint": name, "message": f"fetch failed: {exc}"})
    return _assemble_result(context, responses, warnings)


async def collect_qianchuan_video_content_analysis_async(
    args: dict[str, Any],
    fetcher: Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]],
) -> dict[str, Any]:
    """Async variant of collect_qianchuan_video_content_analysis."""

    try:
        context = normalize_qianchuan_video_content_args(args)
    except QianchuanAnalysisError as exc:
        return {"ok": False, "error_code": "PARAM_INVALID", "error": str(exc)}

    responses: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if not await _resolve_material_if_needed_async(context, fetcher, responses, warnings):
        return _material_resolution_failed_result(context, responses, warnings)

    requests = _build_requests(context)
    for name, spec in requests.items():
        if name == "script" and not context.get("vid"):
            warnings.append({
                "endpoint": name,
                "message": "未提供 vid，已跳过脚本文本和内容公式读取",
            })
            continue
        if name == "top_videos" and not context.get("include_top_videos"):
            continue
        try:
            value = fetcher(spec)
            if inspect.isawaitable(value):
                value = await value
            responses[name] = value
        except Exception as exc:  # noqa: BLE001
            warnings.append({"endpoint": name, "message": f"fetch failed: {exc}"})
    return _assemble_result(context, responses, warnings)


def _build_requests(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metric_fields = [field for _key, field, _label in INTERACTION_METRICS]
    filters = {
        "ConditionRelationshipType": 1,
        "Conditions": [
            {"Field": "advertiser_id", "Values": [context["aavid"]], "Operator": 7},
            {"Field": "material_id", "Values": [context["material_id"]], "Operator": 7},
            {"Field": "marketing_goal", "Values": [context["marketing_goal"]], "Operator": 7},
        ],
    }
    common_headers = {"Content-Type": "application/json"}
    material_body = {
        "material_id": context["material_id"],
        "p_date": context["p_date"],
        "period_type": 30,
        "assist_type": 3,
        "assist_video_type": 1,
        "aavid": context["aavid"],
    }
    return {
        "interaction_lifecycle": {
            "url": _api_url(
                "/common/statQuery",
                gfversion=QIANCHUAN_GF_VERSION,
                aavid=context["aavid"],
            ),
            "method": "POST",
            "headers": common_headers,
            "body": {
                "DataSetKey": "roi2_video_material_analysis_insight",
                "Dimensions": ["duration"],
                "Metrics": metric_fields,
                "Filters": filters,
                "StartTime": context["start_time"],
                "EndTime": context["end_time"],
                "refer": QIANCHUAN_REFER,
                "aavid": context["aavid"],
            },
            "page_url": context["page_url"],
            "required": True,
        },
        "script": {
            "url": _api_url(
                "/material-analysis/getContentFormulaAndScript",
                vid=context.get("vid") or "",
                aavid=context["aavid"],
            ),
            "method": "GET",
            "headers": {},
            "body": None,
            "page_url": context["page_url"],
            "required": False,
        },
        "material_analysis": {
            "url": _api_url(
                "/material-analysis/getContentMaterialAnalysisInfo",
                gfversion=QIANCHUAN_GF_VERSION,
                aavid=context["aavid"],
            ),
            "method": "POST",
            "headers": common_headers,
            "body": material_body,
            "page_url": context["page_url"],
            "required": False,
        },
        "top_videos": {
            "url": _api_url(
                "/material-analysis/getContentMaterialTopVideo",
                gfversion=QIANCHUAN_GF_VERSION,
                aavid=context["aavid"],
            ),
            "method": "POST",
            "headers": common_headers,
            "body": material_body,
            "page_url": context["page_url"],
            "required": False,
        },
    }


def _build_material_search_request(context: dict[str, Any], *, marketing_goal: str | None = None) -> dict[str, Any]:
    common_headers = {"Content-Type": "application/json"}
    filters = {
        "ConditionRelationshipType": 1,
        "Conditions": [
            {"Field": "advertiser_id", "Values": [context["aavid"]], "Operator": 7},
            {"Field": "marketing_goal", "Values": [str(marketing_goal or context["marketing_goal"])], "Operator": 7},
            {"Field": "adlab_mode_fork", "Values": ["1"], "Operator": 7},
            {"Field": "material_type", "Values": [context["material_type"]], "Operator": 7},
        ],
    }
    if context.get("material_id"):
        filters["Conditions"].append({"Field": "material_id", "Values": [context["material_id"]], "Operator": 7})
    search_text = str(context.get("cloud_video_id") or context.get("material_name") or "").strip()
    if search_text:
        filters["Conditions"].append({"Field": "material_name_v2", "Values": [search_text], "Operator": 7})
    return {
        "url": _api_url(
            "/common/statQuery",
            reqFrom="roi2_material_list",
            gfversion=QIANCHUAN_GF_VERSION,
            aavid=context["aavid"],
        ),
        "method": "POST",
        "headers": common_headers,
        "body": {
            "StartTime": context["start_time"],
            "EndTime": context["end_time"],
            "PageParams": {"Offset": 0, "Limit": context["material_search_limit"]},
            "DataSetKey": "roi2_video_material_analysis",
            "refer": QIANCHUAN_MATERIAL_LIST_REFER,
            "Dimensions": list(MATERIAL_LIST_DIMENSIONS),
            "Metrics": list(MATERIAL_LIST_METRICS),
            "Filters": filters,
            "OrderBy": [{"Type": 2, "Field": "stat_cost_for_roi2"}],
            "aavid": context["aavid"],
        },
        "page_url": context["page_url"],
        "required": not bool(context.get("material_id")),
    }


def _resolve_material_if_needed(
    context: dict[str, Any],
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
    responses: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> bool:
    if context.get("material_id") and not (context.get("cloud_video_id") or context.get("material_name")):
        return True
    search_errors: list[dict[str, Any]] = []
    for index, goal in enumerate(_material_search_goals(context)):
        response_key = "material_search" if index == 0 else f"material_search_goal_{goal}"
        spec = _build_material_search_request(context, marketing_goal=goal)
        try:
            responses[response_key] = fetcher(spec)
        except Exception as exc:  # noqa: BLE001
            search_errors.append({"endpoint": response_key, "message": f"fetch failed: {exc}"})
            continue
        if _apply_material_resolution(context, responses.get(response_key), warnings, emit_warning=False):
            context["material_search_marketing_goal_used"] = goal
            return True
    warnings.extend(search_errors)
    _apply_material_resolution(context, responses.get("material_search"), warnings, emit_warning=True)
    return bool(context.get("material_id"))


async def _resolve_material_if_needed_async(
    context: dict[str, Any],
    fetcher: Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]],
    responses: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> bool:
    if context.get("material_id") and not (context.get("cloud_video_id") or context.get("material_name")):
        return True
    search_errors: list[dict[str, Any]] = []
    for index, goal in enumerate(_material_search_goals(context)):
        response_key = "material_search" if index == 0 else f"material_search_goal_{goal}"
        spec = _build_material_search_request(context, marketing_goal=goal)
        try:
            value = fetcher(spec)
            if inspect.isawaitable(value):
                value = await value
            responses[response_key] = value
        except Exception as exc:  # noqa: BLE001
            search_errors.append({"endpoint": response_key, "message": f"fetch failed: {exc}"})
            continue
        if _apply_material_resolution(context, responses.get(response_key), warnings, emit_warning=False):
            context["material_search_marketing_goal_used"] = goal
            return True
    warnings.extend(search_errors)
    _apply_material_resolution(context, responses.get("material_search"), warnings, emit_warning=True)
    return bool(context.get("material_id"))


def _apply_material_resolution(
    context: dict[str, Any],
    fetch_result: dict[str, Any] | None,
    warnings: list[dict[str, Any]],
    *,
    emit_warning: bool = True,
) -> bool:
    business, warning, proof = _unwrap_business(fetch_result, endpoint="material_search", required=not bool(context.get("material_id")))
    context["material_search_proof"] = proof
    if business is None:
        if warning and emit_warning:
            warnings.append(warning)
        return bool(context.get("material_id"))
    candidates = _parse_material_candidates(business)
    context["material_candidates"] = candidates
    resolved = _pick_material_candidate(
        candidates,
        material_id=context.get("material_id"),
        cloud_video_id=context.get("cloud_video_id"),
        material_name=context.get("material_name"),
    )
    if not resolved:
        if emit_warning:
            warnings.append({
                "endpoint": "material_search",
                "message": "未在千川视频素材列表中命中短 ID / 视频名",
                "cloud_video_id": context.get("cloud_video_id"),
                "material_name": context.get("material_name"),
                "candidate_count": len(candidates),
            })
        return bool(context.get("material_id"))
    context["resolved_material"] = resolved
    context["material_id"] = str(resolved.get("material_id") or context.get("material_id") or "").strip()
    if not context.get("vid") and resolved.get("vid"):
        context["vid"] = str(resolved["vid"]).strip()
    return bool(context.get("material_id"))


def _material_search_goals(context: dict[str, Any]) -> list[str]:
    goals: list[str] = []
    for value in (
        context.get("material_search_marketing_goal"),
        context.get("marketing_goal"),
        "2" if not context.get("material_id") else None,
        "1" if not context.get("material_id") else None,
    ):
        text = str(value or "").strip()
        if text and text not in goals:
            goals.append(text)
    return goals or ["1"]


def _material_resolution_failed_result(
    context: dict[str, Any],
    responses: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    proof: dict[str, Any] = {}
    material_proof = context.get("material_search_proof")
    if isinstance(material_proof, dict):
        proof["material_search"] = material_proof
    elif responses.get("material_search"):
        proof["material_search"] = _proof_from_fetch(responses["material_search"], "material_search")
    return {
        "ok": False,
        "error_code": "QIANCHUAN_MATERIAL_RESOLVE_FAILED",
        "input": _public_context(context),
        "summary": {
            "cloud_video_id": context.get("cloud_video_id"),
            "material_name": context.get("material_name"),
            "candidate_count": len(context.get("material_candidates") or []),
        },
        "resolved_material": context.get("resolved_material") or None,
        "material_candidates": context.get("material_candidates") or [],
        "warnings": warnings,
        "proof": proof,
    }


def _api_url(path: str, **query: Any) -> str:
    query_text = "&".join(f"{key}={value}" for key, value in query.items() if value is not None and value != "")
    return f"{QIANCHUAN_API_BASE}{path}" + (f"?{query_text}" if query_text else "")


def _assemble_result(
    context: dict[str, Any],
    responses: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    proof: dict[str, Any] = {}
    raw: dict[str, Any] = {}

    if responses.get("material_search"):
        material_search_business, material_search_warning, material_search_proof = _unwrap_business(
            responses.get("material_search"),
            endpoint="material_search",
            required=False,
        )
        proof["material_search"] = context.get("material_search_proof") or material_search_proof
        if context.get("include_raw") and material_search_business is not None:
            raw["material_search"] = material_search_business
        elif material_search_warning:
            warnings.append(material_search_warning)

    interaction_business, interaction_warning, interaction_proof = _unwrap_business(
        responses.get("interaction_lifecycle"),
        endpoint="interaction_lifecycle",
        required=True,
    )
    proof["interaction_lifecycle"] = interaction_proof
    if interaction_business is None:
        warnings.append(interaction_warning or {
            "endpoint": "interaction_lifecycle",
            "message": "互动时序分析接口未返回有效数据",
        })
        return {
            "ok": False,
            "error_code": "QIANCHUAN_INTERACTION_FETCH_FAILED",
            "input": _public_context(context),
            "warnings": warnings,
            "proof": proof,
        }
    if context.get("include_raw"):
        raw["interaction_lifecycle"] = interaction_business

    interaction = _parse_interaction_lifecycle(interaction_business)

    script = {"available": False, "text": "", "formula": [], "segments": []}
    script_business, script_warning, script_proof = _unwrap_business(
        responses.get("script"),
        endpoint="script",
        required=False,
    )
    proof["script"] = script_proof
    if script_business is not None:
        script = _parse_script(script_business)
        if context.get("include_raw"):
            raw["script"] = script_business
    elif script_warning:
        warnings.append(script_warning)

    material = {"available": False, "video": {}, "creative_gap": {}}
    material_business, material_warning, material_proof = _unwrap_business(
        responses.get("material_analysis"),
        endpoint="material_analysis",
        required=False,
    )
    proof["material_analysis"] = material_proof
    if material_business is not None:
        material = _parse_material_analysis(material_business)
        if context.get("include_raw"):
            raw["material_analysis"] = material_business
    elif material_warning:
        warnings.append(material_warning)

    top_videos: dict[str, Any] = {"available": False, "items": []}
    top_business, top_warning, top_proof = _unwrap_business(
        responses.get("top_videos"),
        endpoint="top_videos",
        required=False,
    )
    proof["top_videos"] = top_proof
    if top_business is not None:
        top_videos = _parse_top_videos(top_business)
        if context.get("include_raw"):
            raw["top_videos"] = top_business
    elif top_warning:
        warnings.append(top_warning)

    click_total = interaction["totals"].get("click", {}).get("value", 0)
    peak_click = interaction["peaks"].get("click")
    zero_second = next((row for row in interaction["series"] if row.get("duration") == 0), None)
    result: dict[str, Any] = {
        "ok": True,
        "source": "qianchuan.video_content_analysis",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": _public_context(context),
        "resolved_material": context.get("resolved_material") or None,
        "summary": {
            "material_id": context["material_id"],
            "cloud_video_id": context.get("cloud_video_id"),
            "date_range": f"{context['start_date']}~{context['end_date']}",
            "click_total": click_total,
            "click_peak": peak_click,
            "zero_second_click": (zero_second or {}).get("click", 0),
            "series_points": len(interaction["series"]),
            "has_script": bool(script.get("available")),
            "has_material_analysis": bool(material.get("available")),
            "top_videos_available": bool(top_videos.get("available")),
        },
        "interaction_lifecycle": interaction,
        "content_analysis": {
            "video": material.get("video") or {},
            "script": script,
            "creative_gap": material.get("creative_gap") or {},
            "top_videos": top_videos,
        },
        "warnings": warnings,
        "proof": proof,
    }
    if context.get("include_material_candidates") and context.get("material_candidates"):
        result["material_candidates"] = context["material_candidates"]
    if raw:
        result["raw"] = raw
    return result


def _unwrap_business(
    fetch_result: dict[str, Any] | None,
    *,
    endpoint: str,
    required: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    if not fetch_result:
        if required:
            return None, {"endpoint": endpoint, "message": "未执行接口请求"}, {}
        return None, None, {}
    proof = _proof_from_fetch(fetch_result, endpoint)
    if not isinstance(fetch_result, dict):
        return None, {"endpoint": endpoint, "message": "fetch_result 不是对象"}, proof
    if not fetch_result.get("success"):
        return None, {
            "endpoint": endpoint,
            "message": str(fetch_result.get("error") or "fetch failed"),
        }, proof

    envelope = fetch_result.get("data")
    http_status = envelope.get("status") if isinstance(envelope, dict) else None
    if isinstance(http_status, int) and http_status >= 400:
        return None, {"endpoint": endpoint, "message": f"HTTP {http_status}"}, proof
    business = envelope.get("data") if isinstance(envelope, dict) and "data" in envelope else envelope
    if isinstance(business, str):
        try:
            business = json.loads(business)
        except json.JSONDecodeError:
            return None, {"endpoint": endpoint, "message": "接口返回非 JSON 字符串"}, proof
    if not isinstance(business, dict):
        return None, {"endpoint": endpoint, "message": "接口返回非对象 JSON"}, proof

    status_code = business.get("status_code", business.get("code"))
    if status_code not in (None, 0, "0", 200, "200"):
        message = business.get("message") or business.get("msg") or business.get("error") or "business status not ok"
        return None, {
            "endpoint": endpoint,
            "status_code": status_code,
            "message": str(message),
        }, proof
    return business, None, proof


def _parse_interaction_lifecycle(business: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_data(business)
    rows = _find_rows(payload)
    series = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        duration = _duration_from_row(row)
        if duration is None:
            continue
        metrics = row.get("Metrics") or row.get("metrics") or row
        item: dict[str, Any] = {
            "duration": duration,
            "duration_label": f"{duration:02d}s",
        }
        for key, field, _label in INTERACTION_METRICS:
            item[key] = _to_number(_metric_value(metrics, field))
        series.append(item)
    series.sort(key=lambda item: int(item["duration"]))
    totals = _parse_totals(payload, series)
    peaks = _parse_peaks(series)
    return {
        "metrics": [
            {"key": key, "field": field, "label": label}
            for key, field, label in INTERACTION_METRICS
        ],
        "totals": totals,
        "peaks": peaks,
        "phases": _phase_summary(series, total_click=totals.get("click", {}).get("value", 0)),
        "series": series,
    }


def _parse_script(business: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_data(business)
    text = _first_string_by_keys(payload, ("script", "script_text", "text", "copywriting", "content"))
    formula = _extract_formula(payload)
    segments = _extract_segments(payload)
    return {
        "available": bool(text or formula or segments),
        "text": text,
        "formula": formula,
        "segments": segments,
    }


def _parse_material_analysis(business: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_data(business)
    if not isinstance(payload, dict):
        return {"available": False, "video": {}, "creative_gap": {}}
    video = {
        key: payload.get(key)
        for key in (
            "material_id",
            "material_uri",
            "title",
            "cost",
            "cost_rank",
            "ctr",
            "ctr_rank",
            "play_over_rate",
            "status_lifetime",
            "status_identity",
            "material_create_time",
            "video_duration",
            "industry_id_list",
            "high_quality_material_effect_cost",
            "high_quality_material_effect_cost_rate",
            "gmv",
            "roi",
            "bench_tag_from",
        )
        if key in payload
    }
    my_tags = _normalize_tag_entries(payload.get("my_tag_entry"))
    benchmark_tags = _normalize_tag_entries(payload.get("bench_tag_entry"))
    creative_gap = _build_creative_gap(my_tags, benchmark_tags)
    return {
        "available": bool(video or my_tags or benchmark_tags),
        "video": video,
        "creative_gap": creative_gap,
    }


def _parse_top_videos(business: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_data(business)
    rows = _find_rows(payload)
    if isinstance(payload, dict) and not rows:
        for key in ("top_videos", "topVideoList", "material_list", "list", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
    items = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        items.append({
            key: row.get(key)
            for key in (
                "material_id",
                "material_uri",
                "title",
                "cost",
                "ctr",
                "play_over_rate",
                "gmv",
                "roi",
                "video_duration",
            )
            if key in row
        } or row)
    return {"available": bool(items), "items": items, "total": len(rows)}


def _parse_material_candidates(business: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _payload_data(business)
    rows = _find_rows(payload)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        flat = _flatten_material_row(row)
        material_id = _first_text_by_names(flat, "material_id", "materialId")
        material_name = _first_text_by_names(flat, "material_name_v2", "material_name", "materialName", "title", "name")
        content = _first_value_by_names(flat, "material_content_v2", "material_content", "materialContent")
        content_obj = _json_object(content)
        vid = (
            _first_text_by_names(content_obj, "vid", "video_id", "videoId", "material_uri", "materialUri")
            or _first_string_by_keys(content_obj, ("vid", "video_id", "videoId", "material_uri", "materialUri"))
            or _first_text_by_names(flat, "vid", "material_uri", "materialUri")
        )
        if not material_id and not material_name:
            continue
        metrics = _extract_known_metrics(flat)
        candidates.append({
            "material_id": material_id,
            "material_name": material_name,
            "vid": vid,
            "material_type": _first_text_by_names(flat, "material_type", "materialType"),
            "duration": _to_number(_first_value_by_names(flat, "material_duration_v2", "duration")),
            "create_time": _first_text_by_names(flat, "material_create_time_v2", "material_create_time", "create_time"),
            "cost": metrics.get("stat_cost_for_roi2", 0),
            "roi": metrics.get("total_prepay_and_pay_order_roi2", 0),
            "gmv": metrics.get("total_pay_order_gmv_include_coupon_for_roi2") or metrics.get("total_pay_order_gmv_for_roi2", 0),
            "product_click": metrics.get("product_click_count_for_roi2", 0),
            "live_watch": metrics.get("live_watch_count_for_roi2_v2", 0),
            "video_play": metrics.get("video_play_count_for_roi2_v2", 0),
            "metrics": metrics,
            "material_content": _compact_material_content(content_obj) if content_obj else {},
        })
    candidates.sort(key=lambda item: (_to_number(item.get("cost")), _to_number(item.get("gmv"))), reverse=True)
    return candidates


def _compact_material_content(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    video_info = value.get("video_info") if isinstance(value.get("video_info"), dict) else {}
    return {
        "material_type": value.get("material_type"),
        "video_info": {
            key: video_info.get(key)
            for key in ("video_id", "video_name", "width", "height", "ratio", "duration", "duration_str")
            if key in video_info
        },
    }


def _pick_material_candidate(
    candidates: list[dict[str, Any]],
    *,
    material_id: str | None,
    cloud_video_id: str | None,
    material_name: str | None,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    if material_id:
        for item in candidates:
            if str(item.get("material_id") or "") == str(material_id):
                return item
    short_id = str(cloud_video_id or "").strip()
    if short_id:
        normalized_short_id = _normalize_match_text(short_id)
        short_id_matches = []
        for item in candidates:
            name = str(item.get("material_name") or "")
            normalized_name = _normalize_match_text(name)
            if (
                normalized_name.startswith(normalized_short_id)
                or short_id in name
                or str(item.get("material_id") or "") == short_id
            ):
                short_id_matches.append(item)
        if short_id_matches:
            return max(
                short_id_matches,
                key=lambda item: (
                    _to_number(item.get("cost")),
                    _to_number(item.get("gmv")),
                    _to_number(item.get("product_click")),
                    _to_number(item.get("video_play")),
                ),
            )

    scored = []
    name_hint = _normalize_match_text(material_name)
    for item in candidates:
        name = str(item.get("material_name") or "")
        normalized_name = _normalize_match_text(name)
        score = 0
        if name_hint:
            if normalized_name == name_hint:
                score += 50
            elif name_hint in normalized_name or normalized_name in name_hint:
                score += 25
        if score:
            scored.append((score, _to_number(item.get("cost")), item))
    if scored:
        scored.sort(key=lambda value: (value[0], value[1]), reverse=True)
        return scored[0][2]
    if not short_id and name_hint and candidates:
        return candidates[0]
    return None


def _flatten_material_row(row: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"Dimensions", "dimensions", "Metrics", "metrics"}:
            continue
        flat[key] = _unwrap_cell_value(value)
    dimensions = row.get("Dimensions") or row.get("dimensions")
    if isinstance(dimensions, dict):
        for key, value in dimensions.items():
            flat[str(key)] = _unwrap_cell_value(value)
    metrics = row.get("Metrics") or row.get("metrics")
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            flat[str(key)] = _to_number(_unwrap_cell_value(value))
    return flat


def _unwrap_cell_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("Value", "value", "ValueStr", "value_str", "Name", "name", "Text", "text"):
            if key in value:
                return value[key]
    return value


def _first_value_by_names(mapping: Any, *names: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    lowered = {str(key).lower(): key for key in mapping.keys()}
    for name in names:
        real = lowered.get(name.lower())
        if real is not None and mapping.get(real) not in (None, ""):
            return mapping[real]
    return None


def _first_text_by_names(mapping: Any, *names: str) -> str:
    value = _first_value_by_names(mapping, *names)
    if isinstance(value, (dict, list)):
        nested = _first_string_by_keys(value, tuple(names) + ("uri", "id", "url"))
        return nested
    return str(value or "").strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _extract_known_metrics(flat: dict[str, Any]) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {}
    for name in MATERIAL_LIST_METRICS:
        if name in flat:
            metrics[name] = _to_number(flat.get(name))
    return metrics


def _normalize_match_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if not ch.isspace())


def _parse_totals(payload: Any, series: list[dict[str, Any]]) -> dict[str, Any]:
    totals_container = None
    if isinstance(payload, dict):
        raw_totals = payload.get("Totals") or payload.get("totals") or payload.get("Total") or payload.get("total")
        if isinstance(raw_totals, list) and raw_totals:
            totals_container = raw_totals[0].get("Metrics") if isinstance(raw_totals[0], dict) else raw_totals[0]
        elif isinstance(raw_totals, dict):
            totals_container = raw_totals.get("Metrics") or raw_totals.get("metrics") or raw_totals
    totals = {}
    for key, field, label in INTERACTION_METRICS:
        value = _to_number(_metric_value(totals_container or {}, field))
        if value == 0:
            value = sum(_to_number(item.get(key)) for item in series)
        totals[key] = {"field": field, "label": label, "value": value, "value_str": _format_number(value)}
    return totals


def _parse_peaks(series: list[dict[str, Any]]) -> dict[str, Any]:
    peaks: dict[str, Any] = {}
    for key, _field, label in INTERACTION_METRICS:
        if not series:
            peaks[key] = None
            continue
        row = max(series, key=lambda item: _to_number(item.get(key)))
        value = _to_number(row.get(key))
        peaks[key] = {
            "label": label,
            "duration": row.get("duration"),
            "duration_label": row.get("duration_label"),
            "value": value,
            "value_str": _format_number(value),
        }
    return peaks


def _phase_summary(series: list[dict[str, Any]], *, total_click: float | int) -> list[dict[str, Any]]:
    total = _to_number(total_click)
    phases = []
    for label, start, end in PHASES:
        rows = [
            row for row in series
            if int(row.get("duration", -1)) >= start
            and (end is None or int(row.get("duration", -1)) <= end)
        ]
        click = sum(_to_number(row.get("click")) for row in rows)
        phases.append({
            "phase": label,
            "start_second": start,
            "end_second": end,
            "points": len(rows),
            "click": click,
            "click_str": _format_number(click),
            "click_share": round(click / total, 6) if total else 0,
            "avg_click_per_second": round(click / len(rows), 2) if rows else 0,
        })
    return phases


def _normalize_tag_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    entries = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = (
            item.get("tag_name")
            or item.get("tag_label")
            or item.get("label")
            or item.get("name")
            or item.get("type")
            or item.get("tag_type")
            or item.get("key")
        )
        tags = _tag_names(
            item.get("tag_list")
            or item.get("tag_name_list")
            or item.get("tags")
            or item.get("tag_entry")
            or item.get("children")
            or item.get("value")
        )
        if label or tags:
            entries.append({"label": str(label or ""), "tags": tags})
    return entries


def _tag_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        for key in ("name", "label", "tag_name", "tag_value", "value"):
            if isinstance(value.get(key), str) and value.get(key):
                return [value[key]]
        if isinstance(value.get("text"), str) and value.get("text"):
            return [value["text"]]
        return [
            text
            for item in value.values()
            for text in _tag_names(item)
        ]
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_tag_names(item))
        return _dedupe_text(names)
    return []


def _build_creative_gap(
    my_tags: list[dict[str, Any]],
    benchmark_tags: list[dict[str, Any]],
) -> dict[str, Any]:
    my_by_label = {item.get("label"): set(item.get("tags") or []) for item in my_tags}
    bench_by_label = {item.get("label"): set(item.get("tags") or []) for item in benchmark_tags}
    missing_labels = [
        label for label, tags in bench_by_label.items()
        if tags and not my_by_label.get(label)
    ]
    benchmark_only = []
    for label, bench_tags in bench_by_label.items():
        missing_tags = sorted(bench_tags - my_by_label.get(label, set()))
        if missing_tags:
            benchmark_only.append({"label": label, "tags": missing_tags})
    return {
        "my_tags": my_tags,
        "benchmark_tags": benchmark_tags,
        "missing_tag_labels": missing_labels,
        "benchmark_only_tags": benchmark_only,
        "summary": _creative_gap_summary(missing_labels, benchmark_only),
    }


def _creative_gap_summary(missing_labels: list[str], benchmark_only: list[dict[str, Any]]) -> str:
    if not missing_labels and not benchmark_only:
        return "当前素材标签与 benchmark 暂无明显缺口。"
    parts = []
    if missing_labels:
        parts.append("缺少维度：" + "、".join(str(item) for item in missing_labels[:8]))
    high_signal = [
        f"{item.get('label')}({ '、'.join(str(tag) for tag in (item.get('tags') or [])[:4]) })"
        for item in benchmark_only[:5]
        if item.get("tags")
    ]
    if high_signal:
        parts.append("benchmark 独有标签：" + "；".join(high_signal))
    return "；".join(parts)


def _find_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("Rows", "rows", "list", "items", "data_list", "dataList"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for value in payload.values():
        if isinstance(value, dict):
            rows = _find_rows(value)
            if rows:
                return rows
    return []


def _payload_data(business: dict[str, Any]) -> Any:
    payload = business.get("data", business.get("Data", business))
    if isinstance(payload, dict) and len(payload) == 1 and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


def _duration_from_row(row: dict[str, Any]) -> int | None:
    candidates = [
        row.get("duration"),
        (row.get("Dimensions") or {}).get("duration") if isinstance(row.get("Dimensions"), dict) else None,
        (row.get("dimensions") or {}).get("duration") if isinstance(row.get("dimensions"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("Value", candidate.get("value"))
        try:
            return int(float(candidate))
        except (TypeError, ValueError):
            continue
    return None


def _metric_value(metrics: Any, field: str) -> Any:
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(field)
    if value is None:
        value = metrics.get(field.lower()) or metrics.get(field.upper())
    if isinstance(value, dict):
        for key in ("Value", "value", "value_str", "ValueStr"):
            if key in value:
                return value[key]
    return value


def _first_string_by_keys(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        lowered = {str(k).lower(): k for k in value.keys()}
        for key in keys:
            real_key = lowered.get(key.lower())
            if real_key is not None and isinstance(value.get(real_key), str) and value[real_key].strip():
                return value[real_key].strip()
        for item in value.values():
            text = _first_string_by_keys(item, keys)
            if text:
                return text
    if isinstance(value, list):
        for item in value:
            text = _first_string_by_keys(item, keys)
            if text:
                return text
    return ""


def _extract_formula(value: Any) -> list[Any]:
    matches = _extract_by_key_contains(value, ("formula", "结构", "公式"))
    return _compact_extracted(matches, limit=20)


def _extract_segments(value: Any) -> list[Any]:
    matches = _extract_by_key_contains(value, ("segment", "detail", "section", "段落"))
    return _compact_extracted(matches, limit=20)


def _extract_by_key_contains(value: Any, markers: tuple[str, ...]) -> list[Any]:
    matches: list[Any] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker.lower() in key_text for marker in markers):
                matches.append(item)
            matches.extend(_extract_by_key_contains(item, markers))
    elif isinstance(value, list):
        for item in value:
            matches.extend(_extract_by_key_contains(item, markers))
    return matches


def _compact_extracted(values: list[Any], *, limit: int) -> list[Any]:
    output = []
    for value in values:
        if isinstance(value, list):
            output.extend(value[:limit])
        elif isinstance(value, dict):
            output.append(value)
        elif isinstance(value, str) and value.strip():
            output.append(value.strip())
        if len(output) >= limit:
            break
    return output[:limit]


def _proof_from_fetch(fetch_result: dict[str, Any], endpoint: str) -> dict[str, Any]:
    if not isinstance(fetch_result, dict):
        return {"endpoint": endpoint}
    proof = dict(fetch_result.get("proof") or {})
    data = fetch_result.get("data") if isinstance(fetch_result.get("data"), dict) else {}
    business = data.get("data") if isinstance(data, dict) else None
    proof.update({
        "endpoint": endpoint,
        "requested_url": proof.get("requested_url") or data.get("url"),
        "http_status": data.get("status"),
        "business_status_code": business.get("status_code", business.get("code")) if isinstance(business, dict) else None,
    })
    if not proof.get("response_hash") and business is not None:
        proof["response_hash"] = _stable_hash(business)
    return {key: value for key, value in proof.items() if value is not None}


def _public_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: context[key]
        for key in (
            "aavid",
            "material_id",
            "cloud_video_id",
            "material_name",
            "vid",
            "start_date",
            "end_date",
            "marketing_goal",
            "p_date",
            "page_url",
        )
        if context.get(key) is not None
    }


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _to_number(value: Any) -> float | int:
    if value in (None, ""):
        return 0
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0
    if number.is_integer():
        return int(number)
    return number


def _format_number(value: Any) -> str:
    number = _to_number(value)
    if isinstance(number, int):
        return f"{number:,}"
    return f"{number:,.2f}"


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise QianchuanAnalysisError(f"缺少 {field}")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _normalize_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise QianchuanAnalysisError(f"{field} 必须是 YYYY-MM-DD") from exc


def _compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _dedupe_text(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output
