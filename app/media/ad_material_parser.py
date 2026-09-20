"""Deterministic ad-material brief parser and human performance compiler.

The planning model is allowed to be creative, but it must not guess how an
uploaded packshot, a source clip and a dialogue script relate to each other.
This module turns the recurring front-desk production sheet patterns into a
versioned contract before H3 prompt compilation.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


AD_MATERIAL_POLICY_VERSION = "frontdesk-ad-material-v21"
PERFORMANCE_PROFILE_VERSION = "frontdesk-performance-profile-v1"

_MAINLAND_CHINA_PLATFORMS = {
    "douyin",
    "抖音",
    "巨量引擎",
    "巨量千川",
    "qianchuan",
    "oceanengine",
    "xiaohongshu",
    "小红书",
    "kuaishou",
    "快手",
}

_SPEAKER_LINE = re.compile(r"^\s*([^：:\n]{1,8})[：:]\s*(.+?)\s*$")
_SECTION_HEADING_LINE = re.compile(r"^\s*[^：:\n]{1,16}[：:]\s*$")
_INLINE_ROLE_SPEAKER_MARKER = re.compile(
    r"(?P<speaker>"
    r"(?:(?:左边|右边|左侧|右侧|左|右)\s*)?"
    r"(?:女(?:生|士)?|男(?:生|士)?)(?:\s*[一二三四五六七八九\d])?(?:\s*回答)?"
    r"|(?:左边|右边|左侧|右侧|左|右)(?:\s*[一二三四五六七八九\d])?"
    r"|旁白|画外音|主持人|采访者|老板|员工|顾客|医生|店员|销售|客服|厂长|路人(?:甲|乙|丙|[1-9])?"
    r"|[AaBb]"
    r")\s*[：:]\s*"
)
_INLINE_NUMBERED_SPEAKER_MARKER = re.compile(
    r"(?:^|(?<=[\s，,；;。！？!?]))"
    r"(?P<speaker>[1-9一二三四五六七八九甲乙丙丁])\s*[：:]\s*"
)
_QUESTION_TERMS = ("吗", "呢", "怎么", "为什么", "真的假的", "是不是", "?", "？")
_REFUSAL_TERMS = ("不要", "不想", "没心情", "不行", "别", "拒绝", "算了")
_SURPRISE_TERMS = ("真的", "原来", "居然", "这么", "啊", "呀")
_CTA_TERMS = ("活动", "到手", "下单", "拍", "囤", "赶紧", "现在", "链接", "手机")
_STRICT_REPLAY_TERMS = ("一比一", "1:1", "复刻", "只换产品", "替换产品", "更换产品", "换产品")
_NO_GENERATED_TEXT_RE = re.compile(
    r"(?:不要|不含|不带|不展示|不出现|不生成|去掉|去除|去|移除|禁止|不得|without|\bno\b)"
    r"[^。；;.!?\n]{0,48}(?:字幕|文字|水印|角标|贴纸|caption|subtitle|text|watermark)",
    re.IGNORECASE,
)
_PRODUCT_HANDHELD_RE = re.compile(
    r"(?:手持|手拿|手上拿|拿着|拿起|举着|捏住|握住|递出|递给|展示在手中)",
    re.IGNORECASE,
)
_PRODUCT_HANDHELD_NEGATED_RE = re.compile(
    r"(?:不要|不得|禁止|避免|无需|不需要|取消|去掉)[^。；;.!?\n]{0,18}"
    r"(?:手持|手拿|手上拿|拿着|拿起|举着|捏住|握住|递出|递给|展示在手中)",
    re.IGNORECASE,
)
_EXPLICIT_FEMALE_CAST_RE = re.compile(
    r"(?:中国大陆|中国|国内|本地|年轻|成年|真实|自然|素人|普通人|演员|人物|主角|主持人|口播者|医生|主播)*"
    r"(?:女性|女生|女人|女主|女演员|女主持|女医生|女主播|\bwoman\b|\bfemale\b|\bactress\b)",
    re.IGNORECASE,
)
_EXPLICIT_MALE_CAST_RE = re.compile(
    r"(?:中国大陆|中国|国内|本地|年轻|成年|真实|自然|素人|普通人|演员|人物|主角|主持人|口播者|医生|主播)*"
    r"(?:男性|男生|男人|男主|男演员|男主持|男医生|男主播|\bman\b|\bmale\b|\bactor\b)",
    re.IGNORECASE,
)
_EXPLICIT_MIXED_CAST_RE = re.compile(
    r"(?:一男一女|男女|情侣|夫妻|伴侣|couple|one\s+woman\s+and\s+one\s+man)",
    re.IGNORECASE,
)
_KNOWN_SPEAKER_LABEL_RE = re.compile(
    r"^(?:"
    r"(?:(?:左边|右边|左侧|右侧|左|右)\s*)?(?:女(?:生|士)?|男(?:生|士)?)(?:\s*[一二三四五六七八九\d])?(?:\s*回答)?"
    r"|(?:左边|右边|左侧|右侧|左|右)(?:\s*[一二三四五六七八九\d])?"
    r"|旁白|画外音|主持人|采访者|老板|员工|顾客|医生|店员|销售|客服|厂长|路人(?:甲|乙|丙|[1-9])?"
    r"|[AaBb]"
    r"|[1-9一二三四五六七八九甲乙丙丁]"
    r")$"
)
_NON_SPEAKER_HEADINGS = {
    "产品", "商品", "文案", "描述", "需求", "要求", "镜头", "画面", "场景", "主题",
    "魔力玻玻", "超快感", "持久", "铂金", "air", "001",
}

_CLOSE_FRIEND_TERMS = ("闺蜜", "姐妹", "好友", "朋友局", "朋友聊天")
_COUPLE_TERMS = ("情侣", "对象", "伴侣", "夫妻", "男朋友", "女朋友", "两口子")
_PROFESSIONAL_TERMS = ("医生", "店员", "客服", "销售", "采访", "主持人", "厂长", "老板", "员工")
_CONFIDENTIAL_DELIVERY_TERMS = ("悄悄话", "小点声", "小声", "轻声", "压低声音", "耳语")
_URGENT_DELIVERY_TERMS = ("快快快", "赶紧", "长话短说", "加急", "急用", "抢", "薅")
_SURPRISED_DELIVERY_TERMS = ("吃惊", "惊讶", "真的假的", "天呐", "居然", "没想到")
_UPBEAT_DELIVERY_TERMS = ("骄傲", "开心", "兴奋", "得意", "轻快")
_TENSE_DELIVERY_TERMS = ("生气", "不耐烦", "冷战", "争吵", "拒绝")


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    lowered = value.casefold()
    return any(term.casefold() in lowered for term in terms)


def build_performance_profile(request_text: Any, content_format: str) -> dict[str, Any]:
    """Translate recurring front-desk wording into one shared performance contract.

    The worksheet repeatedly asks for a close-friend atmosphere, confidential
    delivery, delayed surprise, or less rigid body language.  Those phrases
    must drive both the H3 clean plate and the governed voice renderer; leaving
    them as prose makes the two stages disagree and produces visually natural
    footage with the wrong vocal rhythm (or vice versa).
    """

    text = _text(request_text, 12000)
    if _contains_any(text, _CLOSE_FRIEND_TERMS):
        social_context = "close_friends"
    elif _contains_any(text, _COUPLE_TERMS):
        social_context = "couple"
    elif _contains_any(text, _PROFESSIONAL_TERMS):
        social_context = "professional"
    else:
        social_context = "everyday"

    if _contains_any(text, _CONFIDENTIAL_DELIVERY_TERMS):
        delivery_style = "confidential"
    elif _contains_any(text, _URGENT_DELIVERY_TERMS):
        delivery_style = "urgent_clear"
    elif _contains_any(text, _SURPRISED_DELIVERY_TERMS):
        delivery_style = "restrained_surprise"
    elif _contains_any(text, _UPBEAT_DELIVERY_TERMS):
        delivery_style = "upbeat_confident"
    elif _contains_any(text, _TENSE_DELIVERY_TERMS):
        delivery_style = "restrained_tension"
    elif social_context == "professional":
        delivery_style = "calm_informative"
    else:
        delivery_style = "relaxed_natural"

    people_performance = content_format in {"dialogue", "talking_head"}
    return {
        "version": PERFORMANCE_PROFILE_VERSION,
        "social_context": social_context,
        "delivery_style": delivery_style,
        "camera_pace": "locked_or_single_slow_move" if people_performance else "business_default",
        "max_primary_actions_per_beat": 1 if people_performance else 0,
        "listener_reaction_delay_seconds": 0.3 if content_format == "dialogue" else 0,
        "max_continuous_downward_gaze_seconds": 0.6 if people_performance else 0,
        "return_to_partner_or_camera_gaze": people_performance,
        "posture_reset_between_beats": people_performance,
        "avoid_repeated_smile_or_nod": people_performance,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _normalize_speaker_label(value: Any) -> str:
    label = re.sub(r"\s+", "", _text(value, 24))
    return re.sub(r"回答$", "", label)


def _looks_like_speaker(value: Any) -> bool:
    label = _normalize_speaker_label(value)
    if not label or label.casefold() in _NON_SPEAKER_HEADINGS:
        return False
    return bool(_KNOWN_SPEAKER_LABEL_RE.fullmatch(label))


def _speaker_identity_key(value: Any) -> str:
    label = _normalize_speaker_label(value).casefold()
    direction = next(
        (
            key
            for key, terms in (("left", ("左边", "左侧", "左")), ("right", ("右边", "右侧", "右")))
            if any(term in label for term in terms)
        ),
        "",
    )
    number_match = re.search(r"([1-9一二三四五六七八九])$", label)
    number = number_match.group(1) if number_match else ""
    gender = "female" if "女" in label else "male" if "男" in label else ""
    if direction:
        return "-".join(item for item in (direction, gender, number) if item)
    if gender:
        return "-".join(item for item in (gender, number) if item)
    if label in {"旁白", "画外音"}:
        return "narrator"
    return label


def parse_dialogue_turns(value: Any) -> list[dict[str, str]]:
    """Parse explicit front-desk speaker turns without inventing dialogue."""

    turns: list[dict[str, str]] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        markers = [
            *list(_INLINE_ROLE_SPEAKER_MARKER.finditer(line)),
            *list(_INLINE_NUMBERED_SPEAKER_MARKER.finditer(line)),
        ]
        markers.sort(key=lambda item: item.start("speaker"))
        deduped_markers = []
        last_end = -1
        for marker in markers:
            if marker.start("speaker") < last_end:
                continue
            deduped_markers.append(marker)
            last_end = marker.end()
        if deduped_markers:
            for index, marker in enumerate(deduped_markers):
                end = deduped_markers[index + 1].start("speaker") if index + 1 < len(deduped_markers) else len(line)
                spoken = line[marker.end():end].strip(" \t，,；;")
                if spoken:
                    turns.append({"speaker": _normalize_speaker_label(marker.group("speaker")), "line": spoken})
            continue
        match = _SPEAKER_LINE.match(line)
        if match and _looks_like_speaker(match.group(1)):
            turns.append({"speaker": _normalize_speaker_label(match.group(1)), "line": match.group(2).strip()})
        elif match or _SECTION_HEADING_LINE.match(line):
            # Product/SKU section headings such as ``001：`` separate two
            # requested variants.  They are neither dialogue nor a continuation
            # of the preceding actor's line.
            continue
        elif turns:
            turns[-1]["line"] = f"{turns[-1]['line']} {line}".strip()
    return turns


def normalize_frontdesk_script(value: Any) -> dict[str, Any]:
    """Normalize spreadsheet-style inline dialogue once for every downstream stage."""

    raw = _text(value, 6000)
    turns = parse_dialogue_turns(raw)
    normalized = "\n".join(f"{item['speaker']}：{item['line']}" for item in turns) if turns else raw
    identities = list(dict.fromkeys(_speaker_identity_key(item["speaker"]) for item in turns))
    speakers_by_identity: dict[str, str] = {}
    for item in turns:
        speakers_by_identity.setdefault(_speaker_identity_key(item["speaker"]), item["speaker"])
    comparable_raw = re.sub(r"[ \t]+", " ", raw.replace("\r\n", "\n").replace("\r", "\n")).strip()
    comparable_normalized = re.sub(r"[ \t]+", " ", normalized).strip()
    return {
        "normalized_script": normalized,
        "turns": turns,
        "turn_count": len(turns),
        "speaker_count": len(identities),
        "speakers": [speakers_by_identity[key] for key in identities],
        "changed": bool(turns and comparable_normalized != comparable_raw),
        "policy_version": AD_MATERIAL_POLICY_VERSION,
    }


def _cast_description_from_speakers(speakers: list[Any], cast_market: str = "") -> str:
    labels = [_text(item, 40).casefold() for item in speakers if _text(item, 40)]
    female_count = sum(1 for label in labels if any(term in label for term in ("女", "woman", "female", "girl")))
    male_count = sum(1 for label in labels if any(term in label for term in ("男", "man", "male", "boy")))
    mainland = cast_market == "mainland_china"
    if female_count >= 2 and male_count == 0:
        return "两位明确不同的成年女性，均为中国大陆本地真实人物" if mainland else "两位明确不同的成年女性"
    if female_count >= 1 and male_count >= 1:
        return "一位中国大陆成年女性与一位中国大陆成年男性" if mainland else "一位成年女性与一位成年男性"
    if female_count == 1 and male_count == 0:
        return "同一位中国大陆成年女性真实人物" if mainland else "同一位明确成年女性"
    if male_count == 1 and female_count == 0:
        return "同一位中国大陆成年男性真实人物" if mainland else "同一位明确成年男性"
    return ""


def _requested_cast_profile(value: Any, *, content_format: str) -> str:
    """Keep explicit operator casting facts even when the script has no labels.

    Front-desk rows often contain a plain three-line monologue with the actor
    specified only in the generation requirement.  Looking at the script body
    would be unsafe because a female presenter can mention a man in her copy.
    Callers therefore pass only operator-owned requirement fields here.
    """

    source = _text(value, 6000)
    if not source:
        return "unspecified"
    if _EXPLICIT_MIXED_CAST_RE.search(source):
        return "mixed_pair"
    female = bool(_EXPLICIT_FEMALE_CAST_RE.search(source))
    male = bool(_EXPLICIT_MALE_CAST_RE.search(source))
    if content_format == "talking_head":
        if female and not male:
            return "single_woman"
        if male and not female:
            return "single_man"
    if female and male:
        return "mixed_pair"
    if female:
        return "single_woman"
    if male:
        return "single_man"
    return "unspecified"


def _turn_intent(line: str) -> str:
    if any(term in line for term in _REFUSAL_TERMS):
        return "resistance"
    if any(term in line for term in _QUESTION_TERMS):
        return "question"
    if any(term in line for term in _CTA_TERMS):
        return "call_to_action"
    if any(term in line for term in _SURPRISE_TERMS):
        return "surprise"
    return "statement"


def _performance_for_intent(intent: str) -> tuple[str, str]:
    if intent == "question":
        return (
            "保持原有坐姿或站姿支撑，先看向对方并停半拍，再只做一次2至4厘米的自然前倾；说完缓慢回落",
            "保持呼吸和原有支撑，约0.25秒后只把眼睛和头部小幅转向说话人；不抢话、不机械点头",
        )
    if intent == "resistance":
        return (
            "短促呼气后只做一次小幅重心后移，视线短暂移开再回到对方；双手保持生活化支撑",
            "先维持原姿态半拍，再轻微收住表情并放松肩膀；继续看着对方，不冻结、不夸张失落",
        )
    if intent == "surprise":
        return (
            "眼神先聚焦再轻微睁大，保持身体支撑，只让眉眼完成一次自然变化后回落；不追加手势",
            "身体保持原有支撑，约0.25秒后用嘴角和眼神给出一次小幅回应，不同步模仿",
        )
    if intent == "call_to_action":
        return (
            "眼神自然回到对方或镜头，只做一次有支撑的小幅指向动作；不临时拿取未指定道具",
            "听完后再用眼神和一次轻微前倾表示被说服，不与说话人的主动作同时发生",
        )
    return (
        "保留自然呼吸和身体支撑，只用一次小幅手势或一次重心变化配合语义；说完回到松弛姿态",
        "持续看向说话人，保留眨眼、呼吸和细小表情变化，不僵住、不提前表演下一句",
    )


def build_performance_beats(script: Any, duration_seconds: Any) -> list[dict[str, Any]]:
    """Build a compact turn-taking timeline for H3 and later evaluation."""

    turns = parse_dialogue_turns(script)
    if not turns:
        return []
    try:
        duration = max(1.0, float(duration_seconds or 5))
    except (TypeError, ValueError):
        duration = 5.0
    turns = turns[:8]
    weights = [max(2, len(re.sub(r"\s+", "", item["line"]))) for item in turns]
    total_weight = max(1, sum(weights))
    cursor = 0.0
    beats: list[dict[str, Any]] = []
    for index, (turn, weight) in enumerate(zip(turns, weights, strict=True)):
        end = duration if index == len(turns) - 1 else min(duration, cursor + duration * weight / total_weight)
        intent = _turn_intent(turn["line"])
        speaker_action, listener_reaction = _performance_for_intent(intent)
        beats.append({
            "index": index + 1,
            "start_seconds": round(cursor, 2),
            "end_seconds": round(end, 2),
            "speaker": turn["speaker"],
            "line": turn["line"],
            "intent": intent,
            "speaker_action": speaker_action,
            "listener_reaction": listener_reaction,
            "eye_line": "说话开始前看向对方；只有明确口播面向镜头时才短暂看镜头",
            "pause_after_seconds": 0.18 if index < len(turns) - 1 else 0.28,
            "motion_budget": "一个主动作 + 一个细微表情变化；手、头、肩、重心不得同时叠加启动",
        })
        cursor = end
    return beats


def _dialogue_opening_hook(beats: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose a visible first-frame action without inventing props or copy.

    A live production A/B showed that an "expectant expression" and a delayed
    reaction can leave the first two keyframes almost static.  The hook must
    therefore start with an already-moving, physically supported body action;
    facial nuance remains secondary evidence instead of the main event.
    """

    if not beats:
        return {}
    first_intent = _text(beats[0].get("intent"), 40) or "statement"
    pattern = {
        "question": "question_turn_reveal",
        "resistance": "held_reaction_reveal",
        "surprise": "delayed_surprise_reveal",
        "call_to_action": "decisive_eye_line_reveal",
    }.get(first_intent, "expectant_eye_line_reveal")
    return {
        "pattern": pattern,
        "start_seconds": 0.0,
        "end_seconds": 1.5,
        "speaker_action": "首帧说话人保持真实受力支撑，肩线与上身已向对方自然偏转；随后只完成一次3至5厘米的前倾再回落，不能从静止等待开始",
        "listener_reaction": "听者先保持原朝向，约0.3秒后再把头和眼睛转向说话人；两人的动作起点必须错开",
        "camera": "0–1.5秒保持腰部以上双人中景，不切镜、不变焦；两人的完整肩线、上半身和独立座椅支撑必须同时清楚可见，让转身、前倾、重心回落和延迟转头都发生在画面内",
        "forbid": "不得使用过近头像构图，不得两人静坐等待、仅用微笑或眨眼充当钩子、同时看镜头、同步点头、夸张挥手或临时拿取未指定道具",
    }


def _source_facts(source_understanding: Any) -> dict[str, Any]:
    assets = [item for item in _list(_dict(source_understanding).get("assets")) if isinstance(item, dict)]
    product_assets = [
        item for item in assets
        if item.get("semantic_role") in {"product_package", "product_unit"} or item.get("contains_product") is True
    ]
    people_assets = [
        item for item in assets
        if item.get("people_presence") in {"full_person", "multiple_people"}
    ]
    source_videos = [item for item in assets if item.get("semantic_role") == "source_video"]
    return {
        "asset_count": len(assets),
        "product_asset_ids": [_text(item.get("asset_id"), 50) for item in product_assets],
        "people_asset_ids": [_text(item.get("asset_id"), 50) for item in people_assets],
        "source_video_asset_ids": [_text(item.get("asset_id"), 50) for item in source_videos],
        "has_product_reference": bool(product_assets),
        "has_people_reference": bool(people_assets),
        "has_source_video": bool(source_videos),
        "source_contains_text": any(item.get("contains_source_text") is True for item in source_videos),
    }


def parse_ad_material_brief(
    brief: dict[str, Any],
    source_roles: list[dict[str, Any]],
    source_understanding: dict[str, Any] | None,
    *,
    inferred_mode: str | None = None,
) -> dict[str, Any]:
    """Normalize one worksheet-like request into a production contract."""

    data = deepcopy(brief)
    timing = _dict(data.get("script_timing"))
    # The fitted shot script controls what can be spoken in this clip, but it
    # must never redefine the business cast.  A five-second excerpt may contain
    # only the woman's first turn while the source request is still a two-person
    # dialogue.  Keep full-script identities as immutable facts and use the
    # excerpt only for executable speech/performance beats.
    full_script_normalization = normalize_frontdesk_script(data.get("script"))
    executable_script_normalization = normalize_frontdesk_script(
        timing.get("shot_script") or data.get("script")
    )
    executable_script = _text(executable_script_normalization.get("normalized_script"), 6000)
    speakers = _list(full_script_normalization.get("speakers")) or _list(
        executable_script_normalization.get("speakers")
    )
    request_text = " ".join(
        _text(data.get(key), 6000) for key in ("request", "requirement", "creative_angle", "script")
    )
    cast_request_text = " ".join(
        _text(data.get(key), 6000) for key in ("request", "requirement", "creative_angle")
    )
    facts = _source_facts(source_understanding)
    declared_product_ids = [
        _text(item.get("asset_id"), 50)
        for item in source_roles
        if isinstance(item, dict) and item.get("role") in {"product_packshot", "product_detail"}
    ]
    declared_product_count = len(dict.fromkeys(declared_product_ids))
    declared_people_ids = [
        _text(item.get("asset_id"), 50)
        for item in source_roles
        if isinstance(item, dict) and item.get("role") == "character_first_frame"
    ]
    if declared_product_ids:
        facts["has_product_reference"] = True
        facts["product_asset_ids"] = list(dict.fromkeys([*facts["product_asset_ids"], *declared_product_ids]))
    if declared_people_ids:
        facts["has_people_reference"] = True
        facts["people_asset_ids"] = list(dict.fromkeys([*facts["people_asset_ids"], *declared_people_ids]))
    if any(
        isinstance(item, dict) and item.get("role") in {"motion_reference", "continuity_anchor"}
        for item in source_roles
    ):
        facts["has_source_video"] = True
    # Several worksheet rows split one presenter's monologue into multiple
    # labelled lines.  Line count alone must not turn that into a two-person
    # dialogue contract with an invented listener.
    if len(speakers) >= 2:
        content_format = "dialogue"
    elif executable_script:
        content_format = "talking_head"
    elif facts["has_source_video"] and any(term in request_text.casefold() for term in _STRICT_REPLAY_TERMS):
        content_format = "strict_reference_replay"
    elif facts["has_product_reference"]:
        content_format = "product_demo"
    else:
        content_format = "visual_broll"

    people_requested = content_format in {"dialogue", "talking_head"} or data.get("contains_person") is True
    performance_profile = build_performance_profile(request_text, content_format)
    product_only_reference_with_people = bool(
        people_requested and facts["has_product_reference"] and not facts["has_source_video"]
    )
    product_only_reference_without_people = bool(
        not people_requested
        and facts["has_product_reference"]
        and not facts["has_people_reference"]
        and not facts["has_source_video"]
    )
    requested_product_interaction = bool(
        product_only_reference_with_people
        and _PRODUCT_HANDHELD_RE.search(request_text)
        and not _PRODUCT_HANDHELD_NEGATED_RE.search(request_text)
    )
    recommended_mode = inferred_mode or "text_to_video"
    if facts["has_source_video"]:
        recommended_mode = "reference_replay"
    elif product_only_reference_with_people:
        # A product-only PNG cannot be a first frame for an adult dialogue and
        # H3 cannot keep printed packaging truthful while people move around
        # it. Generate a clean people plate, optionally anchored by a governed
        # character first frame, then let Bridge composite the exact PNG.
        recommended_mode = "image_to_video" if facts["has_people_reference"] else "text_to_video"
    elif product_only_reference_without_people:
        recommended_mode = "text_to_video"

    audio_enabled = data.get("audio_enabled") is not False
    platform = _text(data.get("platform"), 80) or "douyin"
    requested_cast_market = _text(data.get("cast_market"), 60).casefold()
    cast_market = requested_cast_market or (
        "mainland_china"
        if people_requested and platform.casefold() in _MAINLAND_CHINA_PLATFORMS
        else "unspecified"
    )
    face_style = _text(data.get("face_style"), 60).casefold() or (
        "authentic_live_action" if people_requested else "not_applicable"
    )
    requested_cast_profile = _requested_cast_profile(
        cast_request_text,
        content_format=content_format,
    )
    no_generated_text_requested = bool(_NO_GENERATED_TEXT_RE.search(request_text))
    text_policy = (
        "remove_source_overlays"
        if data.get("strip_reference_text") is True and facts["has_source_video"]
        else "clean_frame_no_generated_text" if no_generated_text_requested else "allow_requested_text_only"
    )
    beats = build_performance_beats(executable_script, data.get("duration_seconds") or 5)
    opening_hook = _dialogue_opening_hook(beats) if content_format == "dialogue" else {}
    first_frame_anchor_recommended = bool(
        people_requested
        and content_format in {"dialogue", "talking_head"}
        and not facts["has_people_reference"]
        and not facts["has_source_video"]
        and recommended_mode == "text_to_video"
    )
    warnings: list[str] = []
    if product_only_reference_with_people:
        warnings.append(
            "人物镜头将先生成无商品干净底片，再由 Bridge 使用已审核透明 PNG 固定植入；"
            "真实包装不会交给 H3 重绘，成片仍需检查遮挡、比例和安全区。"
        )
        if requested_product_interaction:
            warnings.append(
                "检测到人物手持或拿取商品的要求。当前保真链路不会伪装成真实手持："
                "H3 只生成人物干净底片，Bridge 将真实商品固定植入安全区，不生成手指遮挡。"
                "如必须真实手持，请提供已拍摄的手持商品素材或后期分层蒙版。"
            )
    elif product_only_reference_without_people:
        warnings.append(
            "商品展示将先生成有明确光影运动的无商品背景底片，再由 Bridge 使用已审核透明 PNG 居中确定性植入；"
            "包装像素不会交给 H3 重绘，背景动效与商品真实性分别验收。"
        )

    dialogue_continuity_lock: dict[str, Any] = {}
    if content_format == "dialogue":
        dialogue_continuity_lock = {
            "version": "inward-partner-orientation-v1",
            "checkpoints_percent": [40, 60, 80, 100],
            "left_actor_orientation": "鼻梁、瞳孔和肩线持续朝向画面右侧的对方",
            "right_actor_orientation": "鼻梁、瞳孔和肩线持续朝向画面左侧的对方",
            "asymmetric_pose": "两人头部高度、肩线角度和动作收势保持错开，不形成平行正面站姿",
            "final_twenty_percent": "最后20%时间内每次细微反应后都回到面向对方的斜向姿态",
            "final_frame": "左侧人物视线落在右侧人物眼睛或面颊，右侧人物视线落在左侧人物眼睛或面颊；两人保持独立受力和错开收势",
        }
        performance_rules = [
            "每个节拍只有一个主动作和一个细微表情变化；手、头、肩、重心不得同时叠加启动",
            "对话镜头默认采用腰部以上双人中景，完整肩线、上半身和独立受力支撑必须同时进入画面；不得用过近头像构图隐藏动作",
            "对话镜头首帧必须已经发生可见的肩线转动、上身前倾或重心变化；前1.5秒再出现错开的视线变化与延迟反应，不能从两人静坐等待开始",
            "每个台词节拍只允许一个主动作，动作先于台词启动并在台词结束后自然回落",
            "非说话人持续眨眼、呼吸、视线和重心微调，不冻结也不抢动作",
            "默认对话始终保持人物之间的交流视线；除非需求明确要求面向镜头口播，否则不得两人同时转向镜头、并肩站正或形成合影式姿态",
            "禁止两人同时点头、同时挥手、重复指点、无支撑悬空手势和突然瞬移",
            "人物脚底或坐姿保持受力关系，肩颈、手腕和手指保持自然关节范围",
            "口型只对应当前 executable_script，不补说被时长裁掉的台词",
            "任何一次低头或向下看不得持续超过0.6秒；视线必须自然回到对方，避免中后段一直低头",
        ]
    elif content_format == "talking_head":
        performance_rules = [
            "每句只选择一个主动作，手势、头部、肩线和重心变化不得同时启动；先完成动作再自然回落",
            "单人口播始终只保留同一位成年人物；不得补充听者、搭档、路人、镜中人、背景人脸或第二具人体",
            "首帧即出现与语义一致的轻微呼吸、眼神聚焦、肩线或重心变化，随后只执行一个自然主动作",
            "每句之间保留真实停顿、眨眼、呼吸和表情回落，避免连续念稿、机械点头、重复挥手或僵直站桩",
            "人物面向镜头自然口播时，头部、视线、肩线、手势与重心变化保持连续，不凭空换位或改变身份",
            "人物脚底或坐姿保持受力关系，肩颈、手腕和手指保持自然关节范围",
            "口型只对应当前 executable_script，不补说被时长裁掉的台词",
            "任何一次低头或向下看不得持续超过0.6秒；视线必须自然回到镜头或明确目标，避免中后段一直低头",
        ]
    else:
        performance_rules = []

    return {
        "policy_version": AD_MATERIAL_POLICY_VERSION,
        "content_format": content_format,
        "platform": platform,
        "ratio": _text(data.get("ratio"), 20) or "9:16",
        "product": _text(data.get("product"), 300),
        "executable_script": executable_script,
        "script_normalization": {
            "changed": bool(full_script_normalization.get("changed")),
            "turn_count": int(full_script_normalization.get("turn_count") or 0),
            "speaker_count": len(speakers),
            "speakers": speakers,
            "normalized_script": _text(full_script_normalization.get("normalized_script"), 6000),
            "executable_turn_count": int(executable_script_normalization.get("turn_count") or 0),
            "executable_speaker_count": int(executable_script_normalization.get("speaker_count") or 0),
            "executable_script": executable_script,
        },
        "speaker_count": len(speakers),
        "speakers": speakers,
        "actor_count": len(speakers) if content_format == "dialogue" else 1 if people_requested else 0,
        "cast_market": cast_market,
        "requested_cast_profile": requested_cast_profile,
        "face_style": face_style,
        "people_requested": people_requested,
        "source_facts": facts,
        "product_only_reference_with_people": product_only_reference_with_people,
        "product_only_reference_without_people": product_only_reference_without_people,
        "requested_product_interaction": "handheld" if requested_product_interaction else "none",
        "recommended_h3_mode": recommended_mode,
        "text_policy": text_policy,
        "subtitle_policy": "forbid_burned_in_text" if no_generated_text_requested else "review_generated_text",
        "audio_policy": "synchronized_dialogue" if executable_script and audio_enabled else "ambient_only",
        "performance_profile": performance_profile,
        "performance_beats": beats,
        "opening_visual_hook": opening_hook,
        "first_frame_anchor": {
            "recommended": first_frame_anchor_recommended,
            "capability": "video.first_frame.generate",
            "business_role": "character_first_frame",
            "technical_role": "first_frame",
            "provider_capability": "gpt-imagegen",
            "resolution": "2K",
            "ratio": "9:16",
            "next_h3_mode": "image_to_video",
            "reason": (
                "先生成并视觉校验人物/场景首帧，再由 H3 图生视频锁定中国人物、服装、站位和腰部以上双人中景。"
                if first_frame_anchor_recommended
                else "已提供人物首帧或参考视频，无需重复生成构图锚点。"
            ),
        },
        "dialogue_continuity_lock": dialogue_continuity_lock,
        "performance_rules": performance_rules,
        "product_integration": (
            "deterministic_overlay_not_h3_reference"
            if product_only_reference_with_people or product_only_reference_without_people
            else "preserve_verified_source_role"
        ),
        "assembly_plan": (
            {
                "people_layer": "h3_generated_performance",
                "product_truth_layer": "verified_packshot_reference",
                "commercial_preference": "clean_people_plate_then_deterministic_product_overlay",
                "overlay_anchor": "bottom_right",
                "overlay_width_ratio": 0.22,
                "overlay_safe_margin_ratio": 0.05,
                "overlay_motion_profile": "static_verified_product",
                "overlay_asset_count": min(2, declared_product_count),
                "overlay_layout": "packshot_detail_duo_v1" if declared_product_count > 1 else "single_verified_product_v1",
                "product_interaction_allowed": False,
                "requested_product_interaction": "handheld" if requested_product_interaction else "none",
                "execution_product_interaction": "static_verified_overlay",
            }
            if product_only_reference_with_people
            else {
                "background_layer": "h3_generated_dynamic_clean_plate",
                "product_truth_layer": "verified_packshot_reference",
                "commercial_preference": "dynamic_clean_plate_then_deterministic_product_overlay",
                "overlay_anchor": "center",
                "overlay_width_ratio": 0.38,
                "overlay_safe_margin_ratio": 0.08,
                "overlay_motion_profile": "static_verified_product_dynamic_background",
                "overlay_asset_count": min(2, declared_product_count),
                "overlay_layout": "packshot_detail_duo_v1" if declared_product_count > 1 else "single_verified_product_v1",
                "product_interaction_allowed": False,
            }
            if product_only_reference_without_people
            else {}
        ),
        "warnings": warnings,
    }


def adapt_source_roles_for_ad_contract(
    source_roles: list[dict[str, Any]], contract: dict[str, Any]
) -> list[dict[str, Any]]:
    """Keep business lineage while selecting an executable H3 role."""

    normalized = [dict(item) for item in source_roles]
    assembly = _dict(contract.get("assembly_plan"))
    if assembly.get("commercial_preference") not in {
        "clean_people_plate_then_deterministic_product_overlay",
        "dynamic_clean_plate_then_deterministic_product_overlay",
    }:
        return normalized
    for item in normalized:
        if item.get("role") in {"product_packshot", "product_detail"}:
            item["technical_role"] = "overlay_image"
            item["purpose"] = "已审核真实商品透明图；仅由 Bridge 在生成后确定性植入，不发送给 H3 重绘"
    return normalized


def build_operator_brief_plan(
    brief: dict[str, Any],
    contract: dict[str, Any],
    *,
    h3_mode: str,
) -> dict[str, Any]:
    """Build an executable plan from the operator's own business prompt.

    The front-desk workbook is already a production brief: product, source
    material, copy and generation requirements.  DeepSeek can improve that
    brief, but it must not be required merely to reach H3.  This builder keeps
    the operator text verbatim in lineage and supplies only the minimum
    deterministic structure required by the governed H3 compiler.
    """

    duration_value = brief.get("duration_seconds") or 5
    try:
        duration_seconds = max(1.0, float(duration_value))
    except (TypeError, ValueError):
        duration_seconds = 5.0
    if duration_seconds.is_integer():
        duration_seconds = int(duration_seconds)

    product = _text(brief.get("product"), 300)
    requirement = _text(
        brief.get("request")
        or brief.get("requirement")
        or brief.get("creative_angle"),
        6000,
    )
    executable_script = _text(contract.get("executable_script") or brief.get("script"), 6000)
    operator_prompt = requirement or executable_script or product
    if not operator_prompt:
        operator_prompt = "根据已选素材生成一条结构清晰、动作自然的投流镜头"

    content_format = _text(contract.get("content_format"), 60) or "visual_broll"
    cast = _cast_description_from_speakers(
        _list(contract.get("speakers")), _text(contract.get("cast_market"), 60)
    )
    if not cast and content_format == "talking_head":
        market = "中国大陆" if _text(contract.get("cast_market"), 60) == "mainland_china" else ""
        requested_cast_profile = _text(contract.get("requested_cast_profile"), 40)
        if requested_cast_profile == "single_woman":
            cast = f"同一位{market}成年女性真实人物"
        elif requested_cast_profile == "single_man":
            cast = f"同一位{market}成年男性真实人物"
    if content_format == "dialogue":
        subject = cast or "同一对明确成年人物"
        action = f"按用户填写的台词轮流自然交流；{requirement}" if requirement else "按用户填写的台词轮流自然交流"
        scene = "使用用户要求或已选参考素材中的真实生活场景"
        mood = "自然、松弛、生活化；非说话人保持延迟反应"
    elif content_format == "talking_head":
        subject = cast or "同一位明确成年人物"
        action = f"按用户填写的台词自然口播；{requirement}" if requirement else "按用户填写的台词自然口播"
        scene = "使用用户要求或已选参考素材中的真实生活场景"
        mood = "真实、松弛、克制，保留自然呼吸和停顿"
    elif content_format == "strict_reference_replay":
        subject = "严格沿用已取证参考视频中的主体数量、身份类别和商品槽位"
        action = requirement or "严格复刻参考视频的构图、动作、节奏和镜头结构，只修改用户明确指定的内容"
        scene = "严格沿用参考视频中已取证的场景和构图"
        mood = "沿用参考视频中可验证的情绪和节奏"
    elif content_format == "product_demo":
        subject = product or "用户指定的真实商品"
        action = requirement or "围绕已审核商品素材完成清晰、克制的商品展示"
        scene = "简洁真实的商品展示环境；为已审核商品素材保留安全区"
        mood = "可信、清晰、具有商业质感"
    else:
        subject = product or operator_prompt
        action = requirement or operator_prompt
        scene = "使用用户要求或已选素材能够证明的场景；不额外发明人物、商品或道具"
        mood = "符合用户原始提示词的投流节奏"

    negatives: list[str] = []
    if contract.get("subtitle_policy") == "forbid_burned_in_text":
        negatives.append("字幕、文字、水印、角标、贴纸、字母、数字、伪文字和可读界面")
    if contract.get("people_requested") is True:
        negatives.append("塑胶脸、网红模板脸、过度磨皮、机械点头、动作僵硬、关节异常和人物身份漂移")

    fps = 24
    return {
        "creative_goal": operator_prompt,
        "audience": _text(brief.get("audience"), 200) or "明确成年受众",
        "h3_mode": h3_mode,
        "duration_seconds": duration_seconds,
        "ratio": _text(brief.get("ratio"), 20) or "9:16",
        "shots": [{
            "start_seconds": 0,
            "end_seconds": duration_seconds,
            "framing": "腰部以上中景" if contract.get("people_requested") is True else "主体清晰的投流景别",
            "camera_command": "[Static shot]",
            "subject": subject,
            "action": action,
            "scene": scene,
            "lighting": "真实环境光，肤色、商品颜色和空间关系稳定",
            "mood": mood,
            "audio": executable_script if brief.get("audio_enabled") is not False else "",
        }],
        "audio_prompt": executable_script if brief.get("audio_enabled") is not False else "",
        "negative_constraints": negatives,
        "assumptions": ["未调用创意规划模型；画面意图来自用户原始输入，系统仅做确定性结构化与安全编译。"],
        "warnings": ["本方案未调用 DeepSeek 优化；用户原始提示词已原样留痕并由同一 H3 策略编译。"],
        "recommended_params": {
            "frames": max(1, round(float(duration_seconds) * fps)),
            "fps": fps,
            "steps": 20,
            "seed": -1,
            "audio_enabled": bool(executable_script and brief.get("audio_enabled") is not False),
        },
        "planning_mode": "operator_brief",
        "planning_model": None,
        "prompt_source": "operator_input",
        "operator_prompt": operator_prompt,
        "operator_prompt_preserved": True,
    }


def _compact_beat(
    beat: dict[str, Any], *, include_dialogue: bool = True, has_listener: bool = True
) -> str:
    speaker_action = _text(beat.get("speaker_action"), 90)
    listener = _text(beat.get("listener_reaction"), 82)
    prefix = (
        f"{float(beat.get('start_seconds') or 0):.1f}-{float(beat.get('end_seconds') or 0):.1f}s "
        f"{_text(beat.get('speaker'), 12)}：{speaker_action}"
    )
    spoken = f"，说“{_text(beat.get('line'), 100)}”" if include_dialogue else "，自然开口并保持口型连续"
    return f"{prefix}{spoken}；另一人{listener}" if has_listener else f"{prefix}{spoken}；说完自然停顿、呼吸并回到松弛姿态"


def apply_ad_material_contract_to_plan(
    plan: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    """Apply the deterministic performance timeline before H3 compilation."""

    updated = deepcopy(plan)
    updated["ad_material_policy_version"] = contract.get("policy_version") or AD_MATERIAL_POLICY_VERSION
    updated["ad_material_contract"] = deepcopy(contract)
    mode = _text(contract.get("recommended_h3_mode"), 40)
    if mode in {"text_to_video", "image_to_video", "reference_replay"}:
        updated["h3_mode"] = mode
    beats = [item for item in _list(contract.get("performance_beats")) if isinstance(item, dict)]
    if beats:
        is_dialogue = contract.get("content_format") == "dialogue" and int(contract.get("speaker_count") or 0) >= 2
        include_visual_dialogue = contract.get("subtitle_policy") != "forbid_burned_in_text"
        shots = []
        source_shots = [item for item in _list(updated.get("shots")) if isinstance(item, dict)]
        if not source_shots:
            source_shots = [{"start_seconds": 0, "end_seconds": 5, "framing": "medium shot"}]
        subject_identity = _cast_description_from_speakers(
            _list(contract.get("speakers")), _text(contract.get("cast_market"), 60)
        )
        for index, source in enumerate(source_shots):
            shot = dict(source)
            original_subject = _text(shot.get("subject"), 500)
            if is_dialogue:
                dialogue_identity = subject_identity or original_subject or "同一对明确成年人物"
                shot["subject"] = (
                    f"{dialogue_identity}；保持需求中明确的性别、人数和人物身份；"
                    "两人面对面斜向坐或站，身体朝向彼此并留出自然间距，保持稳定身份和清晰互相视线；"
                    "除非需求明确要求面向镜头口播，否则两人不得同时看镜头、并肩站正或摆成合影姿态"
                )
            elif contract.get("content_format") == "talking_head":
                presenter_identity = subject_identity or original_subject or "同一位明确成年人物"
                shot["subject"] = (
                    f"{presenter_identity}；所有镜头保持同一张脸、同一发型、同一服装和同一人物身份；"
                    "画面中只允许这一位人物，不得出现听者、搭档、路人、镜中人、背景人脸或第二具人体"
                )
            try:
                shot_start = float(shot.get("start_seconds") or 0)
                shot_end = float(shot.get("end_seconds") or shot_start)
            except (TypeError, ValueError):
                shot_start = 0.0
                shot_end = 0.0
            shot_beats = [
                item
                for item in beats
                if shot_start <= float(item.get("start_seconds") or 0) < shot_end
            ]
            if not shot_beats and index < len(beats):
                shot_beats = [beats[index]]
            performance_action = "；".join(
                _compact_beat(
                    item,
                    include_dialogue=include_visual_dialogue,
                    has_listener=is_dialogue,
                )
                for item in shot_beats
            )[:900]
            if index == 0:
                original = _text(shot.get("action"), 300)
                opening = _dict(contract.get("opening_visual_hook"))
                hook_action = "；".join(
                    item for item in (
                        _text(opening.get("speaker_action"), 220),
                        _text(opening.get("listener_reaction"), 220),
                        _text(opening.get("camera"), 180),
                    ) if item
                )
                combined = f"前1.5秒视觉钩子：{hook_action}；{performance_action}" if hook_action else performance_action
                shot["action"] = f"{combined}；{original}" if original else combined
            else:
                continuity = (
                    "延续上一镜头的两人人物身份、交流视线、重心与对话余韵；只执行当前说话轮次，不重复上一句手势"
                    if is_dialogue
                    else "延续上一镜头的同一位单人口播者、同一张脸、服装、视线、重心与表演余韵；不增加第二人物，不重复上一句手势"
                )
                shot["action"] = f"{continuity}；{performance_action}" if performance_action else continuity
            if shot_beats:
                shot["audio"] = " ".join(
                    f"{_text(item.get('speaker'), 12)}：{_text(item.get('line'), 160)}"
                    for item in shot_beats
                )
            shots.append(shot)
        updated["shots"] = shots
        updated["performance_timeline"] = beats
        updated["audio_prompt"] = _text(contract.get("executable_script"), 1200)
        goal = _text(updated.get("creative_goal"), 700)
        performance_goal = (
            "优先保证轮流说话、听者自然反应、视线和身体受力连续"
            if is_dialogue
            else "优先保证同一位单人口播者的身份、真实表情、自然动作和身体受力连续，绝不补充第二人物"
        )
        updated["creative_goal"] = f"{goal}；{performance_goal}" if goal else performance_goal

    assembly = _dict(contract.get("assembly_plan"))
    if assembly.get("commercial_preference") in {
        "clean_people_plate_then_deterministic_product_overlay",
        "dynamic_clean_plate_then_deterministic_product_overlay",
    }:
        dynamic_product_plate = (
            assembly.get("commercial_preference")
            == "dynamic_clean_plate_then_deterministic_product_overlay"
        )
        updated["product_overlay"] = {
            "enabled": True,
            "source_role": "overlay_image",
            "business_roles": ["product_packshot", "product_detail"],
            "anchor": "center" if dynamic_product_plate else "bottom_right",
            "width_ratio": 0.38 if dynamic_product_plate else 0.22,
            "safe_margin_ratio": 0.08 if dynamic_product_plate else 0.05,
            "motion_profile": (
                "static_verified_product_dynamic_background"
                if dynamic_product_plate
                else "static_verified_product"
            ),
            "asset_count": max(1, min(2, int(assembly.get("overlay_asset_count") or 1))),
            "layout": _text(assembly.get("overlay_layout"), 80) or "single_verified_product_v1",
        }
        clean_plate_negative = (
            "生成阶段只生产动态商业背景干净底片：不得生成、猜测、重绘或复制任何商品、包装、Logo、"
            "可读品牌文字、商品卡或人物；画面中央保留无遮挡的稳定植入安全区"
            if dynamic_product_plate
            else "生成阶段只生产人物与场景干净底片：不得生成、猜测、重绘或复制任何商品、包装、Logo、"
            "可读品牌文字或商品卡；画面右下角保留无遮挡的稳定植入安全区"
        )
        negative = [_text(item, 300) for item in _list(updated.get("negative_constraints")) if _text(item, 300)]
        if clean_plate_negative not in negative:
            negative.append(clean_plate_negative)
        updated["negative_constraints"] = negative
    if contract.get("subtitle_policy") == "forbid_burned_in_text":
        updated["subtitle_policy"] = "forbid_burned_in_text"

    negative = [_text(item, 300) for item in _list(updated.get("negative_constraints")) if _text(item, 300)]
    performance_negatives = [
        "手指融合、手腕反折、无支撑悬空手势、脚底滑动、身体重心瞬移、肩颈僵直和塑胶脸",
        "台词与口型错位、无停顿连续念稿、为了塞台词突然切镜或人物换位",
    ]
    if contract.get("content_format") == "dialogue" and int(contract.get("speaker_count") or 0) >= 2:
        performance_negatives.extend([
            "机械对口型、木偶式点头、两人同步重复动作、非说话人冻结、眼神漂移或直视虚空",
            "两人同时直视镜头、并肩站正、逐渐转成合影姿态、说话人与听者失去互相视线",
        ])
    elif contract.get("content_format") == "talking_head":
        performance_negatives.extend([
            "出现第二人物、听者、搭档、路人、镜中人物、背景人脸或额外人体",
            "网红模板脸、过度磨皮、蜡像皮肤、完美对称脸、塑料质感、CG 人脸和机械表情",
        ])
    for item in performance_negatives:
        if item not in negative:
            negative.append(item)
    updated["negative_constraints"] = negative
    warnings = [_text(item, 500) for item in _list(updated.get("warnings")) if _text(item, 500)]
    for item in _list(contract.get("warnings")):
        value = _text(item, 500)
        if value and value not in warnings:
            warnings.append(value)
    updated["warnings"] = warnings
    return updated
