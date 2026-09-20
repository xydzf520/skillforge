"""Deterministic production decisions shared by planning, routing and review.

The LLM describes the creative.  This policy keeps the operational choices
stable and auditable so operators do not need to understand H3 prompts or GPU
differences to get a sensible default.
"""

from __future__ import annotations

import re
from typing import Any


PRODUCTION_POLICY_VERSION = "material-production-policy-v6"

_INTENT_CONFIG: dict[str, dict[str, str]] = {
    "people_dialogue": {
        "label": "人物对话",
        "routing_policy": "quality_first",
        "routing_label": "人物一致性优先",
        "reason": "对话镜头优先选择同类任务中人物连续性、提示词一致性和投流可用度更高的节点。",
    },
    "people_lifestyle": {
        "label": "人物生活场景",
        "routing_policy": "quality_first",
        "routing_label": "人物稳定性优先",
        "reason": "人物镜头优先参考同类成片的人物身份、动作和构图稳定性，再考虑速度。",
    },
    "product_packshot": {
        "label": "商品展示",
        "routing_policy": "fidelity_first",
        "routing_label": "商品保真优先",
        "reason": "商品镜头优先参考同类成片的包装保真、连续性和商业可用度。",
    },
    "reference_replay": {
        "label": "参考复刻",
        "routing_policy": "continuity_first",
        "routing_label": "参考一致性优先",
        "reason": "复刻任务优先选择同类任务中参考一致性和镜头连续性更高的高显存节点。",
    },
    "continuation": {
        "label": "视频续写",
        "routing_policy": "continuity_first",
        "routing_label": "接缝连续性优先",
        "reason": "续写任务优先保证主体、运动方向和画面接缝连续，再考虑吞吐。",
    },
    "abstract_broll": {
        "label": "抽象 B-roll",
        "routing_policy": "throughput_balanced",
        "routing_label": "稳定吞吐优先",
        "reason": "无人物、无真实商品保真要求的镜头优先使用速度更快的空闲节点。",
    },
}

_DIALOGUE_LABEL_RE = re.compile(r"(?:^|\n)\s*[^\n：:]{1,8}[：:]", re.MULTILINE)
_DIALOGUE_TERMS = ("对话", "口播", "问答", "男女", "情侣", "闺蜜", "台词", "说：", "说\"")
_PEOPLE_TERMS = ("人物", "男生", "女生", "男人", "女人", "成人", "成年人", "情侣", "伴侣", "闺蜜", "模特")
_PRODUCT_TERMS = ("商品", "产品", "包装", "packshot", "logo", "品牌", "片装", "盒装", "sku", "货品")
_NEGATED_TERM_PREFIX_RE = re.compile(
    r"(?:无|不要|无需|没有|禁止|避免|不得|去掉|移除|删除|不(?:需要|要|含|包含|出现|展示|新增|增加|生成|保留))"
    r"(?:任何|额外|新增|真实|真人|相关|的|\s|、|，|和|或){0,12}$"
)
_NEGATED_LIST_PREFIX_RE = re.compile(
    r"(?:无|不要|无需|没有|禁止|避免|不得|去掉|移除|删除|不(?:需要|要|含|包含|出现|展示|新增|增加|生成|保留))"
    r"(?:(?![。！？；;\n，,]).){0,48}(?:、|或|和|及)(?:(?![。！？；;\n，,]).){0,16}$"
)
_NEGATED_TERM_SUFFIX_RE = re.compile(
    r"^(?:相关)?(?:不得|不能|不应|不允许|不要|无需|不需要|不出现|不展示|不生成|去掉|移除|删除)"
    r"(?:再|被)?(?:出现|展示|生成|保留)?"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _term_is_negated(value: str, start: int, end: int) -> bool:
    prefix = value[max(0, start - 24):start]
    suffix = value[end:end + 18]
    return bool(
        _NEGATED_TERM_PREFIX_RE.search(prefix)
        or _NEGATED_LIST_PREFIX_RE.search(prefix)
        or _NEGATED_TERM_SUFFIX_RE.search(suffix)
    )


def _contains_non_negated_term(value: str, terms: tuple[str, ...]) -> bool:
    """Return true only when a business term is requested positively.

    Natural-language production briefs frequently contain constraints such as
    ``不要人物`` or ``无商品元素``.  A plain substring check turns those
    constraints into the exact opposite production intent, which then changes
    routing, quality scoring and the operator-facing recommendation.
    """

    for term in terms:
        for match in re.finditer(re.escape(term), value):
            if _term_is_negated(value, match.start(), match.end()):
                continue
            return True
    return False


def _contains_negated_term(value: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        for match in re.finditer(re.escape(term), value):
            if _term_is_negated(value, match.start(), match.end()):
                return True
    return False


def brief_explicitly_forbids_people(brief: dict[str, Any] | None) -> bool:
    """Detect an explicit no-people contract without treating it as intent."""

    data = brief if isinstance(brief, dict) else {}
    combined = " ".join(
        _text(data.get(key)).casefold()
        for key in ("product", "script", "request", "requirement", "creative_angle", "hook")
    )
    return (
        _contains_negated_term(combined, _PEOPLE_TERMS)
        and not _contains_non_negated_term(combined, _PEOPLE_TERMS)
        and not _contains_non_negated_term(combined, _DIALOGUE_TERMS)
        and not bool(_DIALOGUE_LABEL_RE.search(_text(data.get("script"))))
    )


def classify_production_intent(
    brief: dict[str, Any] | None,
    source_roles: list[dict[str, Any]] | None,
    *,
    mode: str,
) -> str:
    """Classify the business shot without asking the operator for model jargon."""

    data = brief if isinstance(brief, dict) else {}
    roles = {
        _text(item.get("business_role") or item.get("role") or item.get("technical_role"))
        for item in (source_roles or [])
        if isinstance(item, dict)
    }
    script = _text(data.get("script"))
    combined = " ".join(
        _text(data.get(key)).casefold()
        for key in ("product", "script", "request", "requirement", "creative_angle", "hook")
    )
    if mode == "continuation":
        return "continuation"
    replication_contract = data.get("source_replication_contract")
    replication_contract = replication_contract if isinstance(replication_contract, dict) else {}
    if (
        mode == "reference_replay"
        and replication_contract.get("enabled") is True
        and (
            replication_contract.get("source_people_presence") in {"none", "partial_hands"}
            or replication_contract.get("forbid_invented_people") is True
        )
    ):
        # Negative requirements such as "do not add people or dialogue" must
        # not be misclassified as a positive people-dialogue request.
        return "reference_replay"
    has_dialogue = bool(_DIALOGUE_LABEL_RE.search(script)) or _contains_non_negated_term(combined, _DIALOGUE_TERMS)
    has_people_term = _contains_non_negated_term(combined, _PEOPLE_TERMS)
    stale_people_flag = bool(data.get("contains_person")) and _contains_negated_term(combined, _PEOPLE_TERMS)
    has_people = has_dialogue or has_people_term or (bool(data.get("contains_person")) and not stale_people_flag)
    # ``first_frame`` is a transport role and may contain people or a scene.
    # Only the business role ``product_packshot`` is verified product evidence.
    has_product = bool(roles & {"product_packshot", "product_detail"}) or _contains_non_negated_term(
        combined,
        _PRODUCT_TERMS,
    )
    if has_dialogue:
        return "people_dialogue"
    if has_people:
        return "people_lifestyle"
    if mode == "reference_replay":
        return "reference_replay"
    if has_product:
        return "product_packshot"
    return "abstract_broll"


def production_policy_for(
    brief: dict[str, Any] | None,
    source_roles: list[dict[str, Any]] | None,
    *,
    mode: str,
) -> dict[str, Any]:
    intent = classify_production_intent(brief, source_roles, mode=mode)
    config = _INTENT_CONFIG[intent]
    return {
        "version": PRODUCTION_POLICY_VERSION,
        "production_intent": intent,
        "intent_label": config["label"],
        "routing_policy": config["routing_policy"],
        "routing_label": config["routing_label"],
        "reason": config["reason"],
        "minimum_quality_samples": 3,
    }


def quality_signal_for_intent(quality: dict[str, Any] | None, intent: str) -> float | None:
    """Return a 0..1 content-aware signal from one persisted quality result."""

    data = quality if isinstance(quality, dict) else {}
    analyses: list[dict[str, Any]] = []
    if isinstance(data.get("scores"), dict):
        analyses.append(data)
    for item in data.get("assets") or []:
        if isinstance(item, dict) and item.get("status") == "completed" and isinstance(item.get("analysis"), dict):
            analyses.append(item["analysis"])
    weights = {
        "people_dialogue": {
            "prompt_alignment": 0.25,
            "visual_continuity": 0.35,
            "hook_strength": 0.15,
            "shot_boundary_clarity": 0.10,
            "commercial_readiness": 0.15,
        },
        "people_lifestyle": {
            "prompt_alignment": 0.20,
            "visual_continuity": 0.40,
            "shot_boundary_clarity": 0.15,
            "commercial_readiness": 0.15,
            "silhouette_safety": 0.10,
        },
        "product_packshot": {
            "prompt_alignment": 0.15,
            "visual_continuity": 0.20,
            "commercial_readiness": 0.15,
            "product_fidelity": 0.35,
            "selling_point_coverage": 0.15,
        },
        "reference_replay": {
            "prompt_alignment": 0.30,
            "visual_continuity": 0.35,
            "shot_boundary_clarity": 0.15,
            "commercial_readiness": 0.10,
            "silhouette_safety": 0.10,
        },
        "continuation": {
            "prompt_alignment": 0.20,
            "visual_continuity": 0.45,
            "shot_boundary_clarity": 0.20,
            "commercial_readiness": 0.15,
        },
        "abstract_broll": {
            "prompt_alignment": 0.25,
            "visual_continuity": 0.25,
            "hook_strength": 0.25,
            "shot_boundary_clarity": 0.10,
            "commercial_readiness": 0.15,
        },
    }.get(intent, {})
    values: list[float] = []
    for analysis in analyses:
        scores = analysis.get("scores") if isinstance(analysis.get("scores"), dict) else {}
        weighted = 0.0
        total = 0.0
        for key, weight in weights.items():
            try:
                score = float(scores.get(key))
            except (TypeError, ValueError):
                continue
            if 1 <= score <= 5:
                weighted += (score / 5.0) * weight
                total += weight
        if total:
            signal = weighted / total
        else:
            try:
                signal = float(analysis.get("overall_score"))
            except (TypeError, ValueError):
                continue
        recommendation = analysis.get("recommendation") if isinstance(analysis.get("recommendation"), dict) else {}
        if recommendation.get("decision") in {"retry_recommended", "reject_recommended"}:
            signal = min(signal, 0.45)
        values.append(max(0.0, min(1.0, signal)))
    if not values:
        try:
            overall = float(data.get("overall_score"))
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, overall)) if overall else None
    return round(sum(values) / len(values), 4)
