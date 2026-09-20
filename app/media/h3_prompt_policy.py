"""Versioned MiniMax H3 prompt policy and deterministic prompt compiler.

The policy follows the current MiniMax H3 generation/Context-IR contract.  The
LLM creates an editable production plan; this module owns the executable prompt
shape so a model deployment cannot silently change runtime semantics.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any


H3_PROMPT_POLICY_VERSION = "minimax-h3-context-ir-v60"
H3_PROMPT_MAX_CHARS = 7000
H3_DURATION_MIN_SECONDS = 4
H3_DURATION_MAX_SECONDS = 15
H3_REFERENCE_MAX_TOTAL = 12
H3_REFERENCE_LIMITS = {"image": 9, "video": 3, "audio": 3}
H3_REFERENCE_MAX_BYTES = {
    "image": 30 * 1024 * 1024,
    "video": 50 * 1024 * 1024,
    "audio": 15 * 1024 * 1024,
}
H3_REFERENCE_ROLES = {
    "first_frame": "image",
    "reference_image": "image",
    "reference_video": "video",
    "reference_audio": "audio",
}
_DIRECT_H3_REFERENCE_PLACEHOLDER_RE = re.compile(r"<(Picture|Video|Audio)\s+([1-9]\d*)>")
_DIRECT_H3_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIRECT_H3_SPEECH_VISUAL_RE = re.compile(
    r"(?:"
    r"说话|讲话|开口|念稿|口播|对白|台词|对话|朗读|唱歌|嘴型|口型|唇形|"
    r"\b(?:speak|speaks|speaking|spoken|talk|talks|talking|dialogue|monologue|conversation|"
    r"lip[ -]?sync|mouth(?:s|ing)?|sing|sings|singing|read(?:s|ing)? aloud)\b"
    r")",
    re.IGNORECASE,
)
_DIRECT_H3_TEXT_EXCLUSION_RE = re.compile(
    r"(?:"
    r"字幕|文字|标题|角标|水印|商标字|品牌字|伪文字|乱码|汉字|字母|数字|"
    r"\b(?:text|caption|subtitle|title|watermark|letter|digit|glyph|logo)\b"
    r")",
    re.IGNORECASE,
)
_CLEAN_PLATE_SPEECH_CUE_RE = re.compile(
    r"\b(?:speak|speaks|speaking|spoken|talk|talks|talking|dialogue|monologue|conversation|"
    r"question|questions|questioning|lip[ -]?sync|mouth(?:s|ing)?|voiceover)\b",
    re.IGNORECASE,
)
H3_CAMERA_COMMANDS = (
    "[Truck left]",
    "[Truck right]",
    "[Push in]",
    "[Pull out]",
    "[Pan left]",
    "[Pan right]",
    "[Tilt up]",
    "[Tilt down]",
    "[Pedestal up]",
    "[Pedestal down]",
    "[Zoom in]",
    "[Zoom out]",
    "[Static shot]",
    "[Tracking shot]",
    "[Shake]",
)
H3_OFFICIAL_SOURCES = (
    "https://platform.minimax.io/docs/guides/video-generation",
    "https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir",
    "https://github.com/MiniMax-AI/skills/tree/main/skills/frontend-dev",
)

_CAMERA_BY_KEY = {item.casefold(): item for item in H3_CAMERA_COMMANDS}
_BRAND_FIDELITY_TERMS = (
    "包装",
    "产品图",
    "商品图",
    "logo",
    "商标",
    "品牌字",
    "中文文字",
    "中文文案",
    "瓶身文字",
    "盒身文字",
    "packshot",
    "package text",
    "brand mark",
)
_BRAND_EXCLUSION_CLAUSE_RE = re.compile(
    r"(?:无|不含|不带|不展示|不呈现|不露出|不生成|不出现|不使用|不编造|不要|无需|不需要|避免出现|禁止出现|不得出现|"
    r"without\b|\bno\b)[^。；;.!?\n]{0,100}",
    re.IGNORECASE,
)
_BRAND_REFERENCE_WARNING = "检测到包装、Logo 或文字真实性要求；已推荐图生视频，纯文生视频可能生成错误文字。"
_TRANSPARENT_CUTOUT_WARNING = (
    "已识别商品透明图：透明仅指图片背景通道，包装本体的几何、颜色、印刷和封边必须保持；"
    "“去掉文字”默认只去字幕、角标、伪文字和额外生成文字，保留已审核商品图自身印刷。"
)
_TRANSPARENT_CUTOUT_MARKERS = (
    "透明图",
    "透明底",
    "透明png",
    "png透明",
    "抠图",
    "cutout",
)
_EXPLICIT_TRANSPARENT_MATERIAL_MARKERS = (
    "包装材质改为透明",
    "将包装材质改成透明",
    "透明塑料盒",
    "透明吸塑盒",
    "透明容器",
    "透明材质包装",
)
_PACKAGE_FIDELITY_MARKERS = (
    "保持真实包装",
    "保持原包装",
    "包装不变",
    "不改变包装",
    "包装保真",
    "保持真实包装几何",
    "保持真实包装颜色",
)
_TRANSPARENT_PACKAGE_TRANSFORM_RE = re.compile(
    r"(?:白色)?透明(?:材质)?包装(?:盒)?|包装(?:本体)?变透明|透明材质版本",
    re.IGNORECASE,
)
_PRODUCT_ROTATION_RE = re.compile(
    r"(?:旋转|转动|翻转|环绕展示|绕轴|rotation|rotate|orbit)",
    re.IGNORECASE,
)
_CUTOUT_CONFLICTING_WARNING_RE = re.compile(
    r"(?:不生成|去掉|删除|移除)[^。；;]{0,30}(?:全部文字|文字或\s*logo|包装文字|logo)",
    re.IGNORECASE,
)
_PEOPLE_PRODUCT_FIDELITY_WARNING = (
    "人物与可读包装同框运动时，H3 不能保证包装文字或 Logo 逐帧保真；商业成片应优先生成不含可读包装的"
    "人物镜头，再使用已审核商品定帧或后期叠加。当前结果必须人工逐帧审片，不得自动成为训练正样本。"
)
_PEOPLE_PRODUCT_COMPOSITION_WARNING = (
    "检测到人物与商品物品同时出现在首帧。实测 H3 可能把静止商品当成可交互道具，即使提示词禁止触碰；"
    "正式投流默认应拆为人物镜头与独立商品镜头，或在人物片生成后确定性叠加已审核商品图。当前同框生成"
    "仅作为技术试片，必须人工逐帧审片且不得自动成为训练正样本。"
)
_DETERMINISTIC_PRODUCT_OVERLAY_WARNING = (
    "人物镜头将先生成无商品干净底片，再由 Bridge 使用已审核透明 PNG 固定植入；"
    "真实包装不会交给 H3 重绘，成片仍需检查遮挡、比例和安全区。"
)
_MISSING_PRODUCT_EVIDENCE_WARNING = (
    "当前只提供了人物/场景首帧，没有已审核的真实商品首帧；系统已把本任务收敛为人物/场景镜头，"
    "禁止 H3 凭空生成包装、Logo、可读文字、价格或活动信息。商品画面应使用独立真实商品首帧任务或后期确定性合成。"
)
_UNVERIFIED_PRODUCT_NEGATIVE = (
    "没有已审核的真实商品首帧，不得生成、补画、猜测或重绘任何商品、包装盒、Logo、品牌字、可读文字、价格或活动信息；"
    "人物/场景首帧不是商品真实性依据"
)
_REFERENCE_TEXT_SUPPRESSION_NEGATIVE = (
    "输出视频的任何一帧都不得出现字幕、标题、角标、贴纸、弹幕、Logo、水印、产品说明、资质号、"
    "价格、促销信息、界面控件、字母、数字、汉字或任何类似可读文字的伪字符"
)
_SOURCE_OVERLAY_TEXT_SUPPRESSION_NEGATIVE = (
    "不得复制源视频中的字幕、标题、角标、贴纸、弹幕、水印、商品卡、底部声明、资质号、价格、促销信息、"
    "界面控件或伪文字；唯一允许的文字像素来自已审核目标商品参考图自身，禁止额外补写或重绘"
)
_REFERENCE_TEXT_SUPPRESSION_WARNING = (
    "已启用参考视频去文字：系统仅向 H3 提供低频动作参考，原视频中的字幕、角标、商品卡和底部声明"
    "不属于复刻内容；执行提示会隔离中文台词和营销文案、关闭模型内同步口播，人物细节将由当前分镜"
    "重新生成，结果仍需逐帧审片。"
)
_PRODUCT_REPLACEMENT_TEXT_SUPPRESSION_WARNING = (
    "已启用商品换品复刻去源文字：源视频只提供镜头结构、商品槽位、局部手部动作和节奏；"
    "源字幕、角标、商品卡与底部声明不会重建。只有已审核目标商品参考图自身的真实印刷可以保留，"
    "不得新增完整人物、演员身份、台词或配音，结果仍需逐帧审片。"
)
_REFERENCE_IDENTITY_REPLACE_WARNING = (
    "已选择换成新演员：参考视频只用于动作、构图和节奏，不保留原人物身份；系统只生成匿名成年演员，"
    "不得指定或模仿真实个人、公众人物。"
)
_REFERENCE_IDENTITY_PRESERVE_WARNING = (
    "已选择保留原人物：系统会附加源视频中心人物身份锚点，但 H3 只能近似保持脸型与人物身份，"
    "正式投流仍需人工核对肖像一致性。"
)
_PRODUCT_PROP_TERMS = ("包装", "商品", "产品", "盒", "瓶", "罐", "package", "packshot", "product")
_CONTINUOUS_TAKE_TERMS = (
    "连续镜头",
    "一镜到底",
    "单镜头",
    "不切镜",
    "不得硬切",
    "不能硬切",
    "无硬切",
    "不要硬切",
    "禁止硬切",
    "同一个镜头",
    "同一镜头",
    "same uncut take",
    "single take",
    "continuous take",
    "no hard cut",
    "without cuts",
)
_EXPLICIT_CUT_TERMS = (
    "硬切",
    "跳切",
    "切到",
    "切换到",
    "多镜头",
    "hard cut",
    "jump cut",
    "cut to",
    "shot boundary",
)
_NON_HUMAN_EXCLUSION_TERMS = (
    "无人物",
    "不出现人物",
    "不得出现人物",
    "不要人物",
    "禁止人物",
    "without people",
    "without humans",
    "no people",
    "no humans",
)
_NON_HUMAN_EXCLUSION_RE = re.compile(
    r"(?:无|不含|不出现|不要|禁止|不得出现)[^。；;.!?\n]{0,100}(?:人物|人体|人像|身体部位)|"
    r"\b(?:no|without)\b[^.;!?\n]{0,100}\b(?:people|humans?|persons?|body parts?)\b",
    re.IGNORECASE,
)
_ABSTRACT_SUBJECT_TERMS = (
    "抽象",
    "丝绸",
    "液态",
    "流体",
    "光带",
    "几何",
    "abstract",
    "silk",
    "fluid",
    "light trail",
    "geometric",
)
_NON_HUMAN_ABSTRACT_GUARDRAIL = (
    "抽象主体必须保持非人体、非生物、非解剖形态，不得形成躯干、肢体、人脸、臀胯、胸部或人体剪影；"
    "禁止单一对称连续曲线主导画面，使用多主体、断续结构、角度变化和清晰负空间打破身体轮廓联想"
)

_PRODUCTION_COMPOSITIONS = (
    "主体居中但保留明显负空间，前中后景分层",
    "三分法构图，主体位于左侧，右侧保留信息空间",
    "三分法构图，主体位于右侧，左侧保留信息空间",
    "俯拍式几何排布，多个独立元素形成节奏",
    "低机位纵深构图，前景遮挡与远景主体分离",
    "微距细节开场后切换到完整主体，不使用单一连续曲线",
)
_PRODUCTION_LAYOUT_CONTRACTS = (
    "五到七个独立矩形模块固定在中心十字网格的不同锚点，模块之间留出清晰间隔",
    "三组矩形模块沿左侧垂直列对齐，右侧至少百分之六十画面保持纯净留白",
    "三组矩形模块沿右侧垂直列对齐，左侧至少百分之六十画面保持纯净留白",
    "四组方形模块分别固定在四个象限，使用水平和垂直网格对齐",
    "三排互不连接的短矩形由近到远沿直线透视排列，每排位置与尺度明确不同",
    "先显示一个矩形材质局部，再硬切到六个矩形模块组成的非对称网格",
)
_PRODUCTION_PALETTES = (
    "清洁高调中性色，仅以品牌蓝作小面积点缀",
    "冷白主光配深蓝轮廓光，避免大面积蓝金组合",
    "暖白零售灯光配低饱和背景，保持商品颜色真实",
    "深色背景配单一银色高光，不使用金色丝带",
    "自然日光与柔和阴影，呈现真实材质而非抽象流体",
)
_PRODUCTION_TRANSITIONS = (
    "镜头边界使用明确硬切，切换主体尺度和机位",
    "使用刚性矩形前景遮挡转场，遮挡物沿直线移动",
    "使用焦点转移建立镜头边界，前后主体必须可区分",
    "使用匹配剪辑连接不同物体形状，不重复同一轮廓",
    "使用水平或垂直的短促矩形光扫转场，不保留弧形拖尾，转场后必须出现不同空间布局",
)
_PRODUCTION_MOTIFS = (
    "几何块面与清晰直线",
    "多个独立颗粒或点阵",
    "折纸式平面与锐利折线",
    "真实桌面、展台或陈列空间",
)
_PRODUCTION_MOTIF_SUBJECTS = {
    "几何块面与清晰直线": "多个互不连接的哑光矩形块面、短矩形杆和水平垂直直线",
    "多个独立颗粒或点阵": "四组彼此分离的方形像素块、方形网格单元和短矩形条",
    "折纸式平面与锐利折线": "多片彼此分离的硬边多边形平面和锐利折线结构",
    "真实桌面、展台或陈列空间": "真实桌面上的多个独立方盒、卡片、低矮矩形展台和清晰陈列空间",
}
_PRODUCTION_ABSTRACT_ACTIONS = (
    "各矩形模块分别从最近的水平或垂直方向进入固定网格锚点，移动路径互不连接",
    "焦点从一个矩形模块硬切到另一组独立模块，同时改变尺度和景深",
    "各模块只沿水平、垂直或单段折线路径移动到新的网格锚点，不形成连续轨迹",
    "各模块分别沿水平或垂直直线离场，画面保留成块的清晰负空间",
)


class H3PromptPolicyError(ValueError):
    """A plan cannot be normalized into the governed H3 prompt contract."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def production_variant_for_candidate(candidate_index: int, campaign_id: str = "") -> dict[str, Any]:
    """Return one of 600 deterministic, traceable campaign variations."""

    try:
        index = max(1, int(candidate_index))
    except (TypeError, ValueError):
        index = 1
    position = (index - 1) % 600
    composition_index = position % len(_PRODUCTION_COMPOSITIONS)
    palette_index = (position // len(_PRODUCTION_COMPOSITIONS)) % len(_PRODUCTION_PALETTES)
    transition_index = (
        position // (len(_PRODUCTION_COMPOSITIONS) * len(_PRODUCTION_PALETTES))
    ) % len(_PRODUCTION_TRANSITIONS)
    motif_index = (
        position
        // (
            len(_PRODUCTION_COMPOSITIONS)
            * len(_PRODUCTION_PALETTES)
            * len(_PRODUCTION_TRANSITIONS)
        )
    ) % len(_PRODUCTION_MOTIFS)
    return {
        "campaign_id": _text(campaign_id, 80),
        "candidate_index": index,
        "variant_key": f"c{composition_index + 1}-p{palette_index + 1}-t{transition_index + 1}-m{motif_index + 1}",
        "composition": _PRODUCTION_COMPOSITIONS[composition_index],
        "layout_contract": _PRODUCTION_LAYOUT_CONTRACTS[composition_index],
        "palette": _PRODUCTION_PALETTES[palette_index],
        "transition": _PRODUCTION_TRANSITIONS[transition_index],
        "spatial_motif": _PRODUCTION_MOTIFS[motif_index],
    }


def _text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _direct_clean_plate_visual_intent(value: Any) -> tuple[str, list[str]]:
    """Keep operator visual intent while removing H3 caption triggers.

    MiniMax H3 uses one joint conditioning prompt.  A production direct-prompt
    run proved that merely describing a person as speaking can create burned-in
    pseudo subtitles even when the same prompt explicitly prohibits text.  For
    a brief whose script is delivered later by the governed voice renderer, the
    GPU therefore receives only non-verbal visual intent.  The exact operator
    prompt remains in lineage and is never sent to another planning model.
    """

    original = str(value or "").strip()
    segments = [
        item.strip()
        for item in re.split(r"(?<=[。！？；;])|(?<=[.!?])\s+|[\r\n]+", original)
        if item.strip()
    ]
    kept: list[str] = []
    removed: list[str] = []
    for segment in segments:
        has_reference_placeholder = bool(_DIRECT_H3_REFERENCE_PLACEHOLDER_RE.search(segment))
        # Reference placeholders are part of the validated H3 contract and
        # must survive.  Speech/text exclusions around them are still replaced
        # by the positive clean-surface profile below.
        if not has_reference_placeholder and (
            _DIRECT_H3_SPEECH_VISUAL_RE.search(segment)
            or _DIRECT_H3_TEXT_EXCLUSION_RE.search(segment)
        ):
            removed.append(segment)
            continue
        kept.append(segment)
    intent = " ".join(kept).strip()
    if not intent:
        intent = "A believable contemporary live-action scene with stable identity, camera, lighting, and room geometry."
    return intent, removed


def _compile_direct_clean_plate_prompt(value: Any) -> tuple[str, dict[str, Any]]:
    """Compile a positive-only, silent plate without calling a planner model."""

    intent, removed = _direct_clean_plate_visual_intent(value)
    prompt = "\n".join([
        "SILENT CLEAN LIVE-ACTION VISUAL PLATE.",
        "PERFORMANCE - every visible adult breathes, blinks, changes eye focus, shifts posture and makes one physically supported gesture; lips remain gently closed and relaxed for the entire take.",
        "WARDROBE - plain solid-color uninterrupted matte fabric covers every visible garment surface.",
        "SCENE SURFACES - photographed skin, plaster, glass, wood, fabric, light and shadow form continuous natural material surfaces across the full canvas, including the lower quarter.",
        "CONTINUITY - preserve the same identity, face, hair, wardrobe, lighting, camera position and room geometry from first frame to final frame.",
        f"OPERATOR VISUAL INTENT - {intent}",
    ])
    return prompt[:H3_PROMPT_MAX_CHARS], {
        "applied": True,
        "version": "direct-clean-plate-v1",
        "reason": "governed_script_isolated_from_h3_visual_generation",
        "removed_visual_speech_or_text_segments": removed[:20],
    }


def _nested_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_nested_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_nested_text(item) for item in value)
    return _text(value, 2000)


def _prefers_continuous_take(plan: dict[str, Any], brief: dict[str, Any] | None) -> bool:
    haystack = f"{_nested_text(brief)} {_nested_text(plan)}".casefold()
    return any(term.casefold() in haystack for term in _CONTINUOUS_TAKE_TERMS)


def _requests_explicit_cut(plan: dict[str, Any], brief: dict[str, Any] | None) -> bool:
    haystack = f"{_nested_text(brief)} {_nested_text(plan)}".casefold()
    return any(term.casefold() in haystack for term in _EXPLICIT_CUT_TERMS)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _duration_from_plan(plan: dict[str, Any]) -> int:
    raw = plan.get("duration_seconds")
    if raw is None:
        params = _dict(plan.get("recommended_params"))
        frames = _number(params.get("frames"), 124)
        fps = max(_number(params.get("fps"), 24), 1)
        raw = round(frames / fps)
    try:
        duration = int(raw)
    except (TypeError, ValueError) as exc:
        raise H3PromptPolicyError("duration_seconds", "duration_seconds must be an integer") from exc
    if duration < H3_DURATION_MIN_SECONDS or duration > H3_DURATION_MAX_SECONDS:
        raise H3PromptPolicyError(
            "duration_seconds",
            f"duration_seconds must be between {H3_DURATION_MIN_SECONDS} and {H3_DURATION_MAX_SECONDS}",
        )
    return duration


def _ratio_from_plan(plan: dict[str, Any]) -> str:
    ratio = _text(plan.get("ratio"), 20)
    if ratio:
        return ratio
    params = _dict(plan.get("recommended_params"))
    width = _number(params.get("width"), 480)
    height = _number(params.get("height"), 864)
    if width == height:
        return "1:1"
    return "9:16" if height > width else "16:9"


def _camera_command(value: Any, warnings: list[str]) -> str:
    command = _text(value, 80)
    if not command:
        return "[Static shot]"
    if not command.startswith("["):
        command = f"[{command}]"
    normalized = _CAMERA_BY_KEY.get(command.casefold())
    if normalized:
        return normalized
    warnings.append(f"不支持的运镜指令 {command} 已替换为 [Static shot]。")
    return "[Static shot]"


def _normalized_shots(plan: dict[str, Any], duration: int, warnings: list[str]) -> list[dict[str, Any]]:
    source = [item for item in _list(plan.get("shots")) if isinstance(item, dict)]
    if not source:
        raise H3PromptPolicyError("shots", "shots must contain at least one object")
    source = source[:12]
    segment = duration / len(source)
    shots: list[dict[str, Any]] = []
    cursor = 0.0
    for index, raw in enumerate(source, 1):
        default_start = cursor
        default_end = duration if index == len(source) else segment * index
        start = _number(raw.get("start_seconds"), default_start)
        end = _number(raw.get("end_seconds"), default_end)
        remaining = len(source) - index
        latest_end = float(duration) - remaining * 0.001
        if abs(start - cursor) > 0.1 or end <= start or end > latest_end + 0.0001:
            warnings.append(f"镜头 {index} 时间范围无效，已按总时长重新均分。")
            start, end = default_start, default_end
        start = max(cursor, min(start, latest_end - 0.001))
        end = min(latest_end, max(start + 0.001, end))
        if index == len(source):
            end = float(duration)
        shot = {
            "shot_id": _text(raw.get("shot_id") or raw.get("index") or index, 40),
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "framing": _text(raw.get("framing") or raw.get("shot_size") or "medium shot", 160),
            "camera_command": _camera_command(raw.get("camera_command") or raw.get("camera"), warnings),
            "subject": _text(raw.get("subject") or raw.get("character"), 500),
            # Keep enough room for an operator to amend an already detailed
            # model-authored action.  The executable prompt still receives a
            # bounded visual-only projection below, so this does not allow a
            # long storyboard field to overflow H3's 7,000-character limit.
            "action": _text(raw.get("action") or raw.get("motion"), 900),
            "scene": _text(raw.get("scene") or raw.get("environment"), 500),
            "lighting": _text(raw.get("lighting"), 300),
            "mood": _text(raw.get("mood") or raw.get("style"), 300),
            "audio": _text(raw.get("audio") or raw.get("sound"), 500),
        }
        shots.append(shot)
        cursor = end
    return shots


def _apply_production_variant(plan: dict[str, Any], warnings: list[str]) -> None:
    variant = _dict(plan.get("production_variant"))
    shots = [item for item in _list(plan.get("shots")) if isinstance(item, dict)]
    if not variant or not shots:
        return

    composition = _text(variant.get("composition"), 300)
    layout_contract = _text(variant.get("layout_contract"), 500)
    palette = _text(variant.get("palette"), 300)
    transition = _text(variant.get("transition"), 300)
    motif = _text(variant.get("spatial_motif"), 300)
    guardrails = _dict(plan.get("brand_guardrails"))
    original_visual = " ".join(
        _text(value, 500)
        for shot in shots
        for value in (shot.get("subject"), shot.get("action"), shot.get("scene"))
    ).casefold()
    replace_legacy_abstract = not guardrails.get("require_reference_image") and any(
        term.casefold() in original_visual for term in _ABSTRACT_SUBJECT_TERMS
    )

    if replace_legacy_abstract:
        subject = _PRODUCTION_MOTIF_SUBJECTS.get(motif, f"多个互不连接的{motif or '几何元素'}")
        plan["creative_goal"] = (
            f"制作非人物、非产品的可剪辑抽象 B-roll：{subject}；严格遵循{layout_contract or composition}；"
            "只使用互不连接的硬边模块和正交网格关系，每个镜头都能独立作为后续合成背景"
        )
        rewritten = []
        for index, raw in enumerate(shots):
            shot = dict(raw)
            shot["subject"] = subject
            shot["action"] = f"{_PRODUCTION_ABSTRACT_ACTIONS[index % len(_PRODUCTION_ABSTRACT_ACTIONS)]}；{transition}"
            shot["scene"] = (
                f"{composition}；正向布局合同：{layout_contract}；{motif}；"
                "所有模块固定在明确网格锚点，主体间不得连接，不沿圆弧、圆环、螺旋或字母路径排列"
            )
            shot["lighting"] = palette
            rewritten.append(shot)
        plan["shots"] = rewritten
        plan["production_variant_application"] = {
            "mode": "replace_legacy_abstract_visual",
            "replaced_shot_count": len(rewritten),
            "variant_key": _text(variant.get("variant_key"), 80),
        }
        hard_negative = (
            "连续生产硬约束：不得使用连续柔性带状主体、暖金属发光条、液态流体或单一对称连续曲线；"
            "不得把圆点、颗粒或短杆排列成圆环、弧线、螺旋、波浪或任何字母/书法形状；"
            "必须按当前变体使用多个互不连接的硬边矩形主体、正交网格、明确负空间和可观察的镜头边界"
        )
        negatives = [
            _text(item, 300)
            for item in _list(plan.get("negative_constraints"))
            if _text(item, 300) and not _text(item, 300).startswith("连续生产硬约束：")
        ]
        if hard_negative not in negatives:
            negatives.append(hard_negative)
        plan["negative_constraints"] = negatives
        warning = "连续生产变体已替换旧顶层创意目标和抽象镜头主体，避免原蓝金丝带语义覆盖正交布局。"
        if warning not in warnings:
            warnings.append(warning)
        return

    enriched = []
    for raw in shots:
        shot = dict(raw)
        shot["action"] = "; ".join(item for item in (_text(shot.get("action"), 500), transition) if item)
        shot["scene"] = "; ".join(item for item in (composition, motif, _text(shot.get("scene"), 500)) if item)
        shot["lighting"] = "; ".join(item for item in (palette, _text(shot.get("lighting"), 300)) if item)
        enriched.append(shot)
    plan["shots"] = enriched
    plan["production_variant_application"] = {
        "mode": "enrich_preserved_subject",
        "replaced_shot_count": 0,
        "variant_key": _text(variant.get("variant_key"), 80),
    }


def _reference_lines(reference_roles: list[Any]) -> list[str]:
    counters = {"image": 0, "video": 0, "audio": 0}
    result: list[str] = []
    for raw in reference_roles:
        if not isinstance(raw, dict):
            continue
        role = _text(raw.get("role"), 40)
        kind = H3_REFERENCE_ROLES.get(role)
        if not kind:
            continue
        counters[kind] += 1
        label = {"image": "Picture", "video": "Video", "audio": "Audio"}[kind]
        purpose = _text(raw.get("purpose") or raw.get("description"), 500)
        if purpose:
            result.append(f"<{label} {counters[kind]}> is the {role} reference for {purpose}.")
        else:
            result.append(f"<{label} {counters[kind]}> is the {role} reference.")
    return result


def _normalized_reference_roles(
    value: Any,
    *,
    mode: str,
    require_brand_reference: bool,
    warnings: list[str],
) -> list[dict[str, str]]:
    roles = []
    for raw in _list(value):
        if not isinstance(raw, dict):
            continue
        role = _text(raw.get("role"), 40)
        if role not in H3_REFERENCE_ROLES:
            warnings.append(f"不支持的参考素材角色 {role or '(empty)'} 已移除。")
            continue
        roles.append({
            "role": role,
            "purpose": _text(raw.get("purpose") or raw.get("description"), 500),
            **({"business_role": _text(raw.get("business_role"), 40)} if _text(raw.get("business_role"), 40) else {}),
        })
    if mode == "text_to_video":
        if roles:
            warnings.append("文生视频不接收参考素材；模型建议的参考角色已从执行提示词移除。")
        return []
    if mode == "image_to_video":
        image_roles = [item for item in roles if H3_REFERENCE_ROLES[item["role"]] == "image"]
        chosen = None
        if require_brand_reference:
            chosen = next(
                (
                    item
                    for item in image_roles
                    if any(term.casefold() in item["purpose"].casefold() for term in _BRAND_FIDELITY_TERMS)
                ),
                None,
            )
        chosen = chosen or next((item for item in image_roles if item["role"] == "first_frame"), None)
        chosen = chosen or (image_roles[0] if image_roles else None)
        purpose = chosen["purpose"] if chosen else (
            "使用已审核的真实产品图作为唯一首帧，画面同时包含后续运动所需的主体与场景。"
            if require_brand_reference
            else "使用一张已授权图片作为唯一首帧。"
        )
        if len(roles) != 1 or not chosen or chosen["role"] != "first_frame":
            warnings.append("本地图生视频模板只接受一张首帧；参考角色已收敛为唯一 first_frame。")
        return [{
            "role": "first_frame",
            "purpose": purpose,
            **({"business_role": chosen.get("business_role")} if chosen and chosen.get("business_role") else {}),
        }]

    normalized: list[dict[str, str]] = []
    counts = {"image": 0, "video": 0, "audio": 0}
    for item in roles:
        role = "reference_image" if item["role"] == "first_frame" else item["role"]
        kind = H3_REFERENCE_ROLES[role]
        if counts[kind] >= H3_REFERENCE_LIMITS[kind] or len(normalized) >= H3_REFERENCE_MAX_TOTAL:
            warnings.append(f"参考素材超过 H3 {kind} 数量限制，超出的角色已移除。")
            continue
        counts[kind] += 1
        normalized.append({
            "role": role,
            "purpose": item["purpose"],
            **({"business_role": item.get("business_role")} if item.get("business_role") else {}),
        })
    return normalized


def _compile_prompt(plan: dict[str, Any]) -> str:
    lines = ["integrated_multimodal_description:"]
    goal = _text(plan.get("creative_goal"), 700)
    audience = _text(plan.get("audience"), 500)
    if goal:
        lines.append(f"Creative objective: {goal}.")
    if audience:
        lines.append(f"Audience: {audience}.")
    lines.extend(_reference_lines(_list(plan.get("reference_roles"))))
    appearance = _clean_plate_face_authenticity(plan)
    if appearance and plan.get("contains_person") is True:
        lines.append(f"VISIBLE COMMERCIAL APPEARANCE - {appearance}.")
    review_rework = _dict(plan.get("review_rework"))
    if review_rework:
        reason = _text(review_rework.get("reason"), 1000)
        notes = [
            _text(item.get("note"), 300)
            for item in _list(review_rework.get("annotations"))[:4]
            if isinstance(item, dict) and _text(item.get("note"), 300)
        ]
        directive = "; ".join(dict.fromkeys([item for item in (reason, *notes) if item]))
        if directive:
            lines.append(
                "REWORK DIRECTIVE — the previous output was rejected. Fix the following issues with highest priority while "
                f"preserving every unaffected approved element: {directive}. Do not reproduce the rejected failure."
            )
    variant = _dict(plan.get("production_variant"))
    if variant:
        lines.append(
            "HARD EXECUTION OVERRIDE — the following campaign variation controls every shot and takes priority over any "
            "conflicting legacy visual description. Campaign variation "
            f"{_text(variant.get('variant_key'), 80)}: composition={_text(variant.get('composition'), 300)}; "
            f"palette={_text(variant.get('palette'), 300)}; transition={_text(variant.get('transition'), 300)}; "
            f"spatial motif={_text(variant.get('spatial_motif'), 300)}; "
            f"positive layout contract={_text(variant.get('layout_contract'), 500)}. "
            "This candidate must be visibly different from adjacent candidates in composition, palette, spatial layout, "
            "and transition rhythm. Render only separated hard-edged modules at the named grid anchors; all motion paths "
            "must be horizontal, vertical, or one-segment polygonal moves. Do not arrange dots or bars along curves, rings, "
            "spirals, waves, letters, calligraphy, a dominant continuous ribbon, or a body-like silhouette."
        )
    guardrails = _dict(plan.get("brand_guardrails"))
    product_overlay = _dict(plan.get("product_overlay"))
    product_overlay_enabled = product_overlay.get("enabled") is True
    identity_policy = _text(guardrails.get("reference_identity_policy"), 40)
    if identity_policy == "replace_actor":
        lines.append(
            "REFERENCE IDENTITY REPLACEMENT LOCK — copy only coarse pose, gesture, camera composition, and timing from "
            "the reference video. Generate visibly different anonymous adult actors with a new face shape and identity. "
            "Do not preserve, reconstruct, or imitate the source person's face, biometric identity, or a named/public person."
        )
    elif identity_policy == "preserve_source":
        lines.append(
            "REFERENCE IDENTITY PRESERVATION LOCK — use the attached reference image as the source-person identity anchor. "
            "Keep the same adult person's face shape, hair, apparent age, and stable identity across the shot while using "
            "the filtered reference video only for pose, motion, composition, and timing."
        )
    if guardrails.get("reference_text_policy") == "suppress_all_source_text":
        lines.append(
            "REFERENCE VIDEO MOTION-ONLY LOCK — every reference-video frame was intentionally low-pass filtered to "
            "destroy source glyphs. Use it only for coarse adult-human pose, motion timing, composition, and shot rhythm. "
            "Do not copy or reconstruct its appearance overlays, subtitle bands, captions, labels, signs, logos, watermarks, "
            "product cards, medical or advertising registration numbers, prices, UI, letters, digits, Chinese characters, "
            "pseudo-text, or any other readable mark. The final frame must contain people and scene pixels only."
        )
    if guardrails.get("product_source_semantics") == "transparent_background_cutout":
        lines.append(
            "APPROVED PACKAGE SOURCE LOCK — the alpha channel belongs only to the input-image background. "
            "Render the approved package body as the same fully opaque rigid physical package shown in the first frame. "
            "Its geometry, pink color, seals, proportions, and approved surface printing remain exactly constant. The clean "
            "visual layer contains the approved package artwork and physical background, with no additional overlay layer."
        )
    if guardrails.get("product_motion_policy") == "locked_static_packshot":
        lines.append(
            "PRODUCT PACKSHOT MOTION LOCK — hold the package in the exact first-frame pose, scale, perspective, and pixel "
            "position for the entire shot. The package is a fully stationary rigid object. Use a locked tripod composition; "
            "only a very subtle background-light breathing effect supplies motion while the package stays unchanged."
        )
    if _text(plan.get("h3_mode"), 40) == "image_to_video":
        lines.append(
            "FIRST FRAME IDENTITY LOCK — the approved first frame is the visual source of truth. Preserve every named "
            "person, foreground product, package, prop, surface, and background object's identity, geometry, count, scale, "
            "color, and initial position. Animate only the actions explicitly named in the shot; stationary products and "
            "props must not be picked up, touched, opened, rotated, enlarged, duplicated, occluded, or replaced."
        )
    if guardrails.get("require_reference_image"):
        lines.append(
            "PRODUCT REFERENCE LOCK — preserve the approved first-frame package geometry, scale, position, colors, logo "
            "placement, and printed text throughout the shot instead of redrawing them. Keep the package on the same surface "
            "and do not let people hold, touch, open, rotate, enlarge, duplicate, or occlude it unless the approved action "
            "explicitly requires that exact interaction."
        )
    if guardrails.get("product_render_policy") == "suppress_without_verified_product_packshot":
        lines.append(
            "UNVERIFIED PRODUCT SUPPRESSION — no verified product_packshot is attached. A character or scene first frame "
            "is not product evidence. This rule overrides conflicting legacy creative descriptions: generate only the "
            "people and scene performance, and do not create, infer, redraw, or reveal any product, package, logo, legible "
            "brand text, price, promotion, or selling-point graphic. Product imagery belongs to a separate verified "
            "product_packshot shot or deterministic post-production composite."
        )
    if guardrails.get("product_render_policy") == "clean_plate_then_verified_overlay":
        lines.append(
            "DETERMINISTIC PRODUCT COMPOSITION LOCK — generate a clean people-and-scene plate only. Do not render, "
            "infer, redraw, duplicate, hold, or reveal any product, package, logo, label, product card, price, readable "
            "brand text, or pseudo-text. Keep a stable unobstructed lower-right placement zone; the exact approved "
            "transparent product PNG will be composited there by the governed Bridge after generation."
        )
    visual_dialogue_isolated = _text(plan.get("subtitle_policy"), 40) == "forbid_burned_in_text"
    governed_static_packshot = (
        guardrails.get("product_source_semantics") == "transparent_background_cutout"
        and guardrails.get("product_motion_policy") == "locked_static_packshot"
    )
    # A production run showed that H3 may literally burn long English
    # no-caption instructions into an otherwise static product shot.  The
    # governed packshot already has an approved-print-only positive contract,
    # so keep those instruction tokens entirely out of its visual prompt.
    embed_dialogue_isolation = visual_dialogue_isolated and not governed_static_packshot
    if embed_dialogue_isolation:
        lines.append(
            "NO-CAPTION VISUAL ISOLATION LOCK — render people, expressions, lip motion, and the physical scene only. "
            "Never draw subtitles, captions, dialogue text, Chinese characters, letters, digits, watermarks, labels, "
            "speech bubbles, lower thirds, or pseudo-text. Spoken lines appear only in the AUDIO TRACK ONLY instruction "
            "zone below and must never be converted into visible glyphs."
        )
    performance_timeline = [
        item for item in _list(plan.get("performance_timeline"))[:8] if isinstance(item, dict)
    ]
    if performance_timeline:
        lines.append(
            "NATURAL PERFORMANCE LOCK — dialogue is turn-taking, not simultaneous posing. Each speaker starts one small "
            "motivated action before speaking and settles after the line; the listener keeps breathing, blinking, eye-line "
            "and weight-shift reactions without freezing, nodding mechanically, stealing the action, or moving in sync."
        )
        for beat in performance_timeline:
            beat_parts = [
                f"Performance beat {float(beat.get('start_seconds') or 0):.2f}-"
                f"{float(beat.get('end_seconds') or 0):.2f}s: speaker={_text(beat.get('speaker'), 20)}",
                f"speaker action={_text(beat.get('speaker_action'), 240)}",
                f"listener reaction={_text(beat.get('listener_reaction'), 240)}",
                f"eye-line={_text(beat.get('eye_line'), 180)}",
            ]
            if not visual_dialogue_isolated:
                beat_parts.insert(1, f"line={_text(beat.get('line'), 120)}")
            lines.append("; ".join(beat_parts) + ".")
    shots = [shot for shot in _list(plan.get("shots")) if isinstance(shot, dict)]
    if shots and all(_text(shot.get("camera_command"), 80) == "[Static shot]" for shot in shots):
        lines.append(
            "STATIC CAMERA LOCK — use a rigid tripod with fixed focal length, fixed crop, fixed horizon, and fixed framing. "
            "No zoom, push, pull, pan, tilt, truck, roll, orbit, handheld drift, stabilization drift, reframing, or digital "
            "crop animation. Background edges and all stationary props must remain pixel-stable; only the explicitly named "
            "subject micro-motions may change."
        )
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            continue
        if index > 1:
            start = float(shot.get("start_seconds") or 0)
            if plan.get("continuous_take") is True:
                lines.append(
                    f"At {start:.3f}s, continue the same uncut take in the same physical scene; transition by following "
                    "the subject or action continuously, with no hard cut, teleport, identity change, or lighting reset."
                )
            else:
                lines.append(
                    f"At {start:.3f}s, establish a clearly visible shot boundary by changing framing, "
                    "subject scale, or camera position while preserving subject and motion continuity."
                )
        details = [
            _text(shot.get("framing"), 160),
            _text(shot.get("camera_command"), 80),
            f"Subject: {_text(shot.get('subject'), 500)}" if _text(shot.get("subject"), 500) else "",
            f"Action: {_text(shot.get('action'), 500)}" if _text(shot.get("action"), 500) else "",
            f"Scene: {_text(shot.get('scene'), 500)}" if _text(shot.get("scene"), 500) else "",
            f"Lighting: {_text(shot.get('lighting'), 300)}" if _text(shot.get("lighting"), 300) else "",
            f"Mood: {_text(shot.get('mood'), 300)}" if _text(shot.get("mood"), 300) else "",
            (
                f"Audio: {_text(shot.get('audio'), 500)}"
            if not visual_dialogue_isolated and _text(shot.get("audio"), 500)
                else ""
            ),
        ]
        details = [item for item in details if item]
        lines.append(
            f"[Shot {index}] At {float(shot.get('start_seconds') or 0):.3f}-{float(shot.get('end_seconds') or 0):.3f}s: "
            + "; ".join(details)
            + "."
        )
    audio_prompt = _text(plan.get("audio_prompt"), 800)
    if audio_prompt:
        if governed_static_packshot:
            lines.append("Overall audio direction: subtle nonverbal ambient sound with no spoken dialogue.")
        elif visual_dialogue_isolated:
            lines.append(
                "AUDIO TRACK ONLY — synthesize the following as naturally spoken dialogue with turn-taking, room tone, "
                "breathing, pauses, and matching lip motion. This is audio conditioning only: do not render any word, "
                f"glyph, caption, subtitle, label, or pseudo-text in video pixels. Dialogue: {audio_prompt}."
            )
        else:
            lines.append(f"Overall audio direction: {audio_prompt}.")
    negatives = [_text(item, 300) for item in _list(plan.get("negative_constraints"))]
    negatives = [item for item in negatives if item]
    if guardrails.get("product_source_semantics") == "transparent_background_cutout":
        # The cutout contract above is deliberately positive.  Repeating words
        # such as transparent glass, blister box or remove package text inside
        # the visual prompt can prime H3 to render the rejected concept or even
        # burn instruction fragments into the frame.
        negatives = [
            item for item in negatives
            if not any(
                marker in item.casefold()
                for marker in (
                    "透明",
                    "吸塑",
                    "文字",
                    "字幕",
                    "角标",
                    "水印",
                    "logo",
                    "letter",
                    "caption",
                    "subtitle",
                )
            )
        ]
    if negatives:
        lines.append("Avoid: " + "; ".join(negatives) + ".")
    return "\n".join(lines)


def _no_text_motion_subject(shots: list[dict[str, Any]]) -> str:
    """Return a small ASCII-only subject description for the no-text runtime prompt."""

    source = " ".join(
        _text(shot.get("subject"), 500)
        for shot in shots
        if isinstance(shot, dict)
    ).casefold()
    if any(term in source for term in ("两人", "一对", "男女", "couple", "two people", "two adults")):
        return "two visibly adult anonymous actors"
    if any(term in source for term in ("女性", "女人", "女生", "女主", "woman", "female")):
        return "one visibly adult anonymous woman"
    if any(term in source for term in ("男性", "男人", "男生", "男主", "man", "male")):
        return "one visibly adult anonymous man"
    return "visibly adult anonymous actor subjects"


def _no_text_motion_framing(shot: dict[str, Any]) -> str:
    source = _text(shot.get("framing") or shot.get("shot_size"), 160).casefold()
    if any(term in source for term in ("特写", "close-up", "close up")):
        return "close-up framing"
    if any(term in source for term in ("近景", "medium close")):
        return "medium close-up framing"
    if any(term in source for term in ("全景", "wide", "long shot")):
        return "wide framing"
    return "medium framing"


def _clean_plate_scene_profile(
    shots: list[dict[str, Any]], *, cast_market: str = "", actor_count: int = 0
) -> str:
    """Return a small positive scene description without product-trigger words."""

    source = " ".join(
        _text(shot.get("scene"), 500)
        for shot in shots
        if isinstance(shot, dict)
    ).casefold()
    mainland = cast_market == "mainland_china"
    local = " in a mainland Chinese city" if mainland else ""
    if any(term in source for term in ("诊室", "医院", "医生", "clinic", "consultation")):
        return f"a bright professional consultation room{local} with calm neutral decor and locally plausible furnishings"
    if any(term in source for term in ("咖啡馆", "咖啡厅", "咖啡店", "cafe", "coffee shop")):
        table_relation = "beside the single presenter" if actor_count == 1 else "between the adults"
        if any(term in source for term in ("落地窗", "floor-to-ceiling", "full-height window")):
            return (
                f"a contemporary urban cafe{local} with natural daylight, locally plausible modern furnishings, a broad warm plaster wall behind the shared "
                "seating area, a floor-to-ceiling window at the far-left background, and one small cafe table "
                f"{table_relation} in the lower-left foreground"
            )
        return f"a contemporary urban cafe{local} with natural daylight, locally plausible modern furnishings, and a small cafe table {table_relation}"
    if any(term in source for term in ("厨房", "kitchen")):
        return f"a tidy modern home kitchen{local} with warm neutral surfaces and ordinary lived-in details"
    if any(term in source for term in ("卧室", "bedroom")):
        return f"a tasteful modern bedroom sitting area{local} in soft daylight"
    if any(term in source for term in ("客厅", "居家", "家中", "living room", "home")):
        return f"a modern lived-in home living room{local} with warm neutral furniture"
    if any(term in source for term in ("办公室", "office")):
        return f"a quiet contemporary office lounge{local} with uncluttered furniture"
    return f"a clean contemporary indoor setting{local} with natural furniture and believable depth"


def _clean_plate_actor_profile(shots: list[dict[str, Any]]) -> str:
    source = " ".join(
        _text(shot.get("subject"), 500)
        for shot in shots
        if isinstance(shot, dict)
    ).casefold()
    stocky_man = any(term in source for term in ("男生体型稍胖", "男性体型稍胖", "微胖男", "stocky man"))
    two_women = bool(
        re.search(r"(?:两位|两个|两名|一对)[^，。；;,.]{0,12}(?:女性|女生|女人)", source)
    ) or any(term in source for term in ("女性朋友", "two women", "two adult women"))
    if two_women:
        return (
            "two unrelated adult women with clearly different, stable identities. Preserve only the facial structure, hair, "
            "wardrobe and placement explicitly described by the operator, and keep those visible differences stable in every frame"
        )
    if any(term in source for term in ("男女", "一男一女", "情侣", "两人", "一对", "couple", "two adults")):
        man = "a slightly stocky adult man" if stocky_man else "an adult man"
        return f"an adult woman and {man} with stable, clearly distinguishable identities"
    return _no_text_motion_subject(shots)


def _dialogue_clean_plate_actor_profile(plan: dict[str, Any], shots: list[dict[str, Any]]) -> str:
    """Resolve cast facts from the worksheet contract before creative prose.

    DeepSeek may summarize two labelled women as a generic pair.  The explicit
    speaker labels captured by ``frontdesk-ad-material`` are business facts and
    therefore outrank the generated storyboard wording.
    """

    contract = _dict(plan.get("ad_material_contract"))
    speakers = [_text(item, 40).casefold() for item in _list(contract.get("speakers")) if _text(item, 40)]
    content_format = _text(contract.get("content_format"), 40)
    requested_cast_profile = _text(contract.get("requested_cast_profile"), 40)
    female_count = sum(
        1 for label in speakers
        if any(term in label for term in ("女", "woman", "female", "girl"))
    )
    male_count = sum(
        1 for label in speakers
        if any(term in label for term in ("男", "man", "male", "boy"))
    )
    if female_count >= 2 and male_count == 0:
        return (
            "two unrelated adult women with clearly different, stable identities. Preserve the operator-provided left/right "
            "appearance, wardrobe and placement without inventing ages, face shapes, hair styles or skin tones"
        )
    if female_count >= 1 and male_count >= 1:
        first_label = speakers[0] if speakers else ""
        woman_is_left = not any(term in first_label for term in ("男", "man", "male", "boy"))
        woman = "adult woman"
        man = "adult man"
        left, right = (woman, man) if woman_is_left else (man, woman)
        return (
            f"two adults with stable contrasting identities: left is an {left}; right is an {right}. Preserve only the "
            "operator-provided appearance, wardrobe and placement without inventing ages, face shapes, hair styles or skin tones"
        )
    if content_format == "talking_head" or int(contract.get("actor_count") or 0) == 1:
        if female_count == 1 or requested_cast_profile == "single_woman":
            return "exactly one adult woman with one stable identity and only the appearance specified by the operator"
        if male_count == 1 or requested_cast_profile == "single_man":
            return "exactly one adult man with one stable identity and only the appearance specified by the operator"
        return "exactly one adult presenter with one stable identity and only the appearance specified by the operator"
    return _clean_plate_actor_profile(shots)


def _commercial_appearance_enabled(plan: dict[str, Any]) -> bool:
    options = _dict(plan.get("generation_options"))
    return options.get("commercial_appearance", True) is not False


def _clean_plate_face_authenticity(plan: dict[str, Any]) -> str:
    """Return the visible, operator-controlled people styling contract.

    The old policy silently invented age, ethnicity, face shape, hairstyle,
    skin tone and restrained makeup.  Those hidden defaults made unrelated
    jobs converge on the same plain-looking cast.  4.1 keeps only observable
    commercial grooming details when the user-visible switch is enabled.
    """

    if not _commercial_appearance_enabled(plan):
        return ""
    return (
        "camera-ready adults with individualized, recognizable facial structure; women use refined natural commercial makeup "
        "with clean brows, defined eyes and natural lip color, while men use neat camera-ready hair, clean grooming and subtle "
        "complexion correction. Preserve real pores, slight asymmetry, fine facial hair and separate hair strands; avoid waxy "
        "skin, excessive smoothing, influencer filters and a synthetic AI face"
    )


def _safe_shot_motion_directive(value: Any) -> str:
    """Project an edited Chinese storyboard action into safe visual English.

    Dialogue clean-plate prompts deliberately exclude literal business copy so
    H3 cannot burn it into the pixels.  Previously that also discarded every
    operator edit to ``shot.action``.  Keep a small deterministic vocabulary of
    visible performance instructions instead: timing, breathing, gaze, head,
    brow, torso, hand and weight changes.  Unknown prose falls back to a
    restrained physical action rather than being copied verbatim.
    """

    source = re.sub(r"\s+", " ", _text(value, 900)).strip()
    if not source:
        return ""
    folded = source.casefold()
    motions: list[str] = []
    timing: list[str] = []

    has_woman = any(term in folded for term in ("女方", "女性", "女生", "女人", "woman", "female"))
    has_man = any(term in folded for term in ("男方", "男性", "男生", "男人", "man", "male"))
    turn_actor = "the active adult"
    if re.search(r"(?:男方|男性|男生|男人|\bman\b)[^。；;]{0,80}(?:转头|转向|head turn|turns? (?:the )?head)", folded):
        turn_actor = "the man"
    elif re.search(r"(?:女方|女性|女生|女人|\bwoman\b)[^。；;]{0,80}(?:转头|转向|head turn|turns? (?:the )?head)", folded):
        turn_actor = "the woman"

    if any(term in folded for term in ("自然呼吸", "保持呼吸", "呼吸", "breath")):
        subject = "both adults" if has_woman and has_man else "the visible adult"
        motions.append(f"{subject} keep natural breathing")
    if any(term in folded for term in ("转头", "转向", "回头", "head turn", "turn the head", "turns the head")):
        motions.append(f"{turn_actor} head-and-eye turn")
    if any(term in folded for term in ("挑眉", "抬眉", "眉眼", "眉峰", "eyebrow", "brow")):
        motions.append("one small eyebrow change")
    if any(term in folded for term in ("前倾", "后仰", "靠近", "躯干", "上身", "torso", "lean")):
        motions.append("one supported torso shift")
    if any(term in folded for term in ("手势", "抬手", "挥手", "手部", "gesture", "hand")):
        motions.append("one restrained hand gesture")
    if any(term in folded for term in ("重心", "weight shift", "weight-shift")):
        motions.append("one supported weight shift")
    if any(term in folded for term in ("眨眼", "blink")):
        motions.append("unsynchronized natural blinking")
    if any(term in folded for term in ("嘴角", "微笑", "笑", "mouth-corner", "smile")):
        motions.append("one restrained cheek response")
    if any(term in folded for term in ("停住", "回落", "松弛", "settle", "relax")):
        motions.append("active motion settles before partner response")

    delay_matches = re.findall(
        r"(?:延迟|等待|delay(?:ed)?(?:\s+by)?|after)\s*(?:约|about)?\s*(\d+(?:\.\d+)?)\s*(?:秒|seconds?|s)",
        source,
        re.IGNORECASE,
    )
    if delay_matches:
        try:
            delay = min(5.0, max(0.05, float(delay_matches[-1])))
            timing.append(f"exact {delay:g}-second delay")
        except (TypeError, ValueError):
            pass
    if any(
        term in folded
        for term in (
            "不得同时",
            "不能同时",
            "动作起点错开",
            "错开动作",
            "先后",
            "先再",
            "stagger",
            "not simultaneously",
            "asynchronous",
        )
    ) or delay_matches:
        timing.append("stagger onsets, never moving in sync")

    if not motions and not timing:
        motions.append("one restrained supported action with a delayed listener reaction")
    # H3 performs better with a short positive motion contract.  Timing edits
    # outrank generic model-authored prose, followed by at most two visible
    # motion primitives.  Keep the per-shot projection tiny so a 12-shot plan
    # cannot crowd out identity, framing and continuity locks.
    compact = [*dict.fromkeys(timing), *list(dict.fromkeys(motions))[:2]]
    return "; ".join(compact)[:80]


def _dialogue_clean_plate_wardrobe_profile(shots: list[dict[str, Any]]) -> str:
    """Describe clean wardrobe surfaces without naming common failure props.

    Repeating a prohibited prop by name inside the positive H3 prompt caused
    that prop to appear in production. Prefer a concrete, positive material and
    silhouette contract that the video model can render directly.
    """

    source = " ".join(
        f"{_text(shot.get('subject'), 500)} {_text(shot.get('scene'), 500)}"
        for shot in shots
        if isinstance(shot, dict)
    ).casefold()
    if any(term in source for term in ("诊室", "医院", "医生", "clinic", "consultation", "doctor")):
        return (
            "the left adult wears a muted teal solid-color medical scrub top and the right adult wears a slate-blue scrub top; "
            "necklines, sleeve cuts and matte colors remain visibly different and stable"
        )
    return (
        "the left adult wears a muted sage collarless knit top with lightly rolled sleeves and the right adult wears a "
        "charcoal-blue crew-neck knit top with straight sleeves; colors, silhouettes and fabric weave remain visibly "
        "different and stable"
    )


def build_character_first_frame_prompt(plan: dict[str, Any]) -> str:
    """Build the governed still-image anchor used before H3 image-to-video.

    Text-to-video can understand a detailed cast contract yet still crop a
    two-person dialogue into a tight portrait.  The still-image model is much
    better at establishing one exact camera distance, actor placement and
    wardrobe split.  This prompt deliberately contains no business copy or
    product request: it creates a clean, reviewable people/scene anchor which
    later becomes the sole ``first_frame`` input to the allow-listed H3 i2v
    workflow.
    """

    shots = [item for item in _list(plan.get("shots")) if isinstance(item, dict)]
    contract = _dict(plan.get("ad_material_contract"))
    content_format = _text(contract.get("content_format"), 40)
    try:
        actor_count = max(1, int(contract.get("actor_count") or contract.get("speaker_count") or 1))
    except (TypeError, ValueError):
        actor_count = 1
    actor_profile = _dialogue_clean_plate_actor_profile(plan, shots)
    scene_profile = _clean_plate_scene_profile(
        shots,
        cast_market=_text(contract.get("cast_market"), 60),
        actor_count=actor_count,
    )
    face_profile = _clean_plate_face_authenticity(plan)
    wardrobe = _dialogue_clean_plate_wardrobe_profile(shots)
    dialogue = content_format == "dialogue" and actor_count >= 2

    if dialogue:
        composition = (
            "Use one eye-level portrait camera about 2.6 metres away with a natural 50mm full-frame field of view. "
            "Show a steady waist-up medium two-shot: both crowns, complete shoulder lines, torsos, both elbows and waist "
            "support are fully visible. Place the left adult at x=32 percent and the right adult at x=66 percent, with an "
            "8-12 percent clear corridor between the inner face contours. Each face stays below 24 percent of canvas height. "
            "Both bodies angle inward in complementary three-quarter views and look at each other, never at the camera."
        )
        pose = (
            "The left adult already has one small physically supported 3-5 centimetre forward torso lean and a restrained "
            "eyebrow change; the right adult remains neutral, breathing and attentive. Their head heights, shoulder yaw and "
            "weight support are visibly asymmetric. Hands remain separate, naturally supported inside each person's own body "
            "space, below the sternum, without touching or crossing into the partner. Both lips are naturally closed and relaxed."
        )
        count_lock = "Exactly two adults are visible; no third person, reflection, background face or extra body."
    else:
        composition = (
            "Use one eye-level portrait camera about 2.4 metres away with a natural 50mm full-frame field of view. Show one "
            "waist-up adult with crown, complete shoulders, torso, both elbows and waist support visible, leaving generous clean "
            "space around the body. The face stays below 26 percent of canvas height."
        )
        pose = (
            "The adult holds a relaxed supported posture with natural facial asymmetry, calm focused eyes, closed relaxed lips, "
            "one slightly lowered shoulder and both hands naturally supported below the sternum."
        )
        count_lock = "Exactly one adult is visible; no listener, reflection, background face or extra body."

    lines = [
            "Create one photorealistic 9:16 vertical live-action first frame for short-form commercial material.",
            f"CAST - {actor_profile}.",
            f"WARDROBE - {wardrobe}; plain matte fabric, stable colors and no printed marks.",
            f"LOCATION - {scene_profile}; believable daylight, coherent room geometry and ordinary lived-in detail.",
            f"COMPOSITION - {composition}",
            f"MOTION-READY POSE - {pose}",
            f"PERSON COUNT LOCK - {count_lock}",
            "CLEAN COMMERCIAL PLATE - no product, package, condom, logo, brand mark, price, promotion, caption, subtitle, "
            "watermark, sticker, interface, legible text, letter or number anywhere in the frame.",
        ]
    if face_profile:
        lines.insert(2, f"VISIBLE COMMERCIAL APPEARANCE - {face_profile}.")
    return "\n".join(lines)[:4000]


def _phonetic_dialogue_audio(value: Any) -> list[str]:
    """Encode Mandarin dialogue as phonetic audio direction for the H3 runtime.

    Literal Chinese copy in H3's single joint audio/video conditioning prompt can
    reappear as burned-in pixels.  The original copy remains in ``audio_prompt``
    for lineage; only this runtime projection is transliterated.
    """

    try:
        from pypinyin import lazy_pinyin
    except Exception:  # pragma: no cover - production dependencies include pypinyin
        lazy_pinyin = None
    output: list[str] = []
    for raw_line in str(value or "").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        speaker, separator, spoken = line.partition("：")
        if not separator:
            speaker, separator, spoken = line.partition(":")
        speaker_key = speaker.strip().casefold() if separator else ""
        spoken = spoken.strip() if separator else line
        if speaker_key in {"女", "女生", "女人", "女方", "woman", "female"}:
            voice = "adult woman"
        elif speaker_key in {"男", "男生", "男人", "男方", "man", "male"}:
            voice = "adult man"
        else:
            voice = "adult speaker"
        if lazy_pinyin is not None:
            syllables = [
                re.sub(r"[^A-Za-z0-9'_-]+", "", part.strip())
                for part in lazy_pinyin(spoken, errors=lambda chars: list(chars))
            ]
            phonetic = " ".join(part for part in syllables if part)
        else:
            phonetic = spoken
        if phonetic:
            output.append(f"{voice} speaks Mandarin naturally: {phonetic}")
    return output[:8]


def _compile_product_overlay_clean_plate_prompt(
    plan: dict[str, Any], *, reserve_product_overlay: bool = True
) -> str:
    """Compile the H3-facing half of the governed people-plus-packshot workflow.

    This prompt intentionally omits the business goal, product description,
    negative list and rejected copy.  H3 produces only a natural people/scene
    plate; Bridge owns the exact transparent-image composition afterwards.
    """

    shots = [shot for shot in _list(plan.get("shots")) if isinstance(shot, dict)]
    overlay = _dict(plan.get("product_overlay"))
    try:
        overlay_asset_count = max(1, min(2, int(overlay.get("asset_count") or 1)))
    except (TypeError, ValueError):
        overlay_asset_count = 1
    contract = _dict(plan.get("ad_material_contract"))
    content_format = _text(contract.get("content_format"), 40)
    if (
        reserve_product_overlay
        and content_format == "product_demo"
        and contract.get("people_requested") is not True
    ):
        return _compile_dynamic_product_background_prompt(plan)
    subject_text = " ".join(_text(shot.get("subject"), 500) for shot in shots).casefold()
    try:
        declared_actor_count = int(contract.get("actor_count") or contract.get("speaker_count") or 0)
    except (TypeError, ValueError):
        declared_actor_count = 0
    inferred_two_people = any(
        term in subject_text
        for term in ("两人", "一对", "男女", "一男一女", "双人", "couple", "two people", "two adults")
    )
    actor_count = max(1, declared_actor_count or (2 if inferred_two_people else 1))
    is_dialogue = actor_count >= 2 and (content_format == "dialogue" or not content_format)
    actor_profile = _dialogue_clean_plate_actor_profile(plan, shots)
    scene_profile = _clean_plate_scene_profile(
        shots, cast_market=_text(contract.get("cast_market"), 60), actor_count=actor_count
    )
    face_authenticity = _clean_plate_face_authenticity(plan)
    plate_intro = (
        "CLEAN TWO-PERSON LIVE-ACTION SCENE PLATE - a believable silent everyday interaction with natural skin texture, restrained facial "
        if is_dialogue
        else "CLEAN ONE-PERSON LIVE-ACTION SCENE PLATE - a believable silent direct-to-camera reaction with natural skin texture, restrained facial "
    )
    lines = [
        "integrated_multimodal_description:",
        plate_intro + "micro-expression, breathing, blinking, eye-line changes, posture shifts, and physically supported gestures.",
        f"CAST AND LOCATION - {actor_profile}; {scene_profile}.",
    ]
    if face_authenticity:
        lines.append(f"VISIBLE COMMERCIAL APPEARANCE - {face_authenticity}.")
    if is_dialogue:
        lines.extend(_clean_plate_performance_profile_lines(contract))
    operator_action_indices = {
        int(item)
        for item in _list(plan.get("operator_edited_action_indices"))
        if str(item).isdigit()
    }
    if reserve_product_overlay:
        reserved_percent = "thirty-four" if overlay_asset_count > 1 else "twenty-two"
        lines.append(
            f"COMPOSITION - keep the lower-right {reserved_percent} percent visually calm, unobstructed, stable, and continuous "
            "with the surrounding room surfaces and lighting throughout the take."
        )
    if is_dialogue:
        lines.append(
            "SILENT PERFORMANCE - one adult starts a short motivated eyebrow, shoulder and torso beat while the partner keeps "
            "breathing, blinking and eye contact, then gives a small delayed cheek and head reaction; they never pose, freeze, "
            "move in sync, or mirror each other. Both adults keep naturally closed relaxed lips."
        )
    else:
        lines.append(
            "SINGLE-PRESENTER LOCK - exactly one adult remains visible for the entire take. The presenter faces the camera naturally "
            "with asynchronous eye, brow, head, shoulder, hand and weight changes, then settles between timed expression beats. The frame contains "
            "no listener, partner, passer-by, reflected person, background face or additional body."
        )
    for index, shot in enumerate(shots, 1):
        camera = _text(shot.get("camera_command"), 80) or "[Static shot]"
        if camera not in H3_CAMERA_COMMANDS:
            camera = "[Static shot]"
        start = float(shot.get("start_seconds") or 0)
        end = float(shot.get("end_seconds") or start)
        framing = _no_text_motion_framing(shot)
        if is_dialogue:
            lines.append(
                f"[Shot {index}] At {start:.3f}-{end:.3f}s: {framing}; {camera}; preserve the same two adult identities, "
                "wardrobe, furniture, lighting, camera position, body support, and room geometry; use restrained staggered "
                "eye, brow, cheek, shoulder and torso motion with delayed partner reactions and naturally closed relaxed lips."
            )
        else:
            lines.append(
                f"[Shot {index}] At {start:.3f}-{end:.3f}s: {framing}; {camera}; preserve the same single adult identity, face, "
                "wardrobe, furniture, lighting, camera position, body support, and room geometry; use restrained direct-to-camera gestures, "
                "natural breathing, naturally closed relaxed lips, and a complete expression reset between timed beats; keep every other person outside the frame."
            )
        motion_directive = _safe_shot_motion_directive(shot.get("action"))
        if index in operator_action_indices and motion_directive:
            lines.append(f"[Shot {index}] OPERATOR-EDITED VISUAL MOTION - {motion_directive}.")
    return "\n".join(lines)


def _compile_dynamic_product_background_prompt(plan: dict[str, Any]) -> str:
    """Compile an animated clean plate for deterministic packshot composition.

    The verified PNG never enters H3.  H3 owns only a premium moving background;
    Bridge later places the exact approved pixels in the reserved center zone.
    Keeping the two layers separate makes packaging geometry and print stable
    without sacrificing the motion hook required by paid-social B-roll.
    """

    overlay = _dict(plan.get("product_overlay"))
    try:
        overlay_asset_count = max(1, min(2, int(overlay.get("asset_count") or 1)))
    except (TypeError, ValueError):
        overlay_asset_count = 1
    width_ratio = (
        0.52 if overlay_asset_count > 1 else 0.38
    ) if overlay.get("anchor") == "center" else (
        0.34 if overlay_asset_count > 1 else 0.22
    )
    return "\n".join([
        "integrated_multimodal_description:",
        "CLEAN DYNAMIC COMMERCIAL BACKGROUND PLATE - one premium vertical live-action studio set photographed as a continuous take.",
        "STAGE - warm ivory and muted rose matte surfaces form a shallow layered set with one rigid horizontal base plane, "
        "soft depth separation, realistic material texture, and a calm editorial color palette.",
        "CAMERA - [Push in]; one unbroken stabilized take with a slow two-percent optical push across the full duration, "
        "continuous focus, a level horizon, and stable perspective.",
        "LIGHTING ACTION - one broad diffused highlight travels once from upper-left to lower-right across the rear matte surface; "
        "a softer reflected glow follows about half a second later, then both settle naturally.",
        "BACKGROUND MOTION - only the broad light gradient, two distant soft reflections, and subtle depth parallax change over time; "
        "edges, base plane, horizon, shadows, and geometry remain rigid and continuous.",
        f"COMPOSITION - keep the central {round(width_ratio * 100)} percent of frame width calm, evenly lit, unobstructed, and visually "
        "separated from the moving background highlights for the complete take.",
        "OPENING HOOK - frame one already contains a visible diagonal light gradient; during the first 1.2 seconds the highlight "
        "advances by eight percent of frame width, creating an immediate but restrained before-and-after change.",
        "FINAL SETTLE - during the final twenty percent the highlight slows and the background returns to a balanced premium stillness "
        "while the reserved center zone keeps identical brightness and contrast.",
    ])


def _clean_plate_performance_profile_lines(contract: dict[str, Any]) -> list[str]:
    """Compile worksheet performance intent without exposing business copy.

    Keep this positive and visual-only.  The clean-plate safety gate rejects
    speech semantics because even generic verbal wording previously caused H3
    to burn caption-like pixels into a silent plate.
    """

    profile = _dict(contract.get("performance_profile"))
    social_context = _text(profile.get("social_context"), 40)
    delivery_style = _text(profile.get("delivery_style"), 40)
    social_direction = {
        "close_friends": (
            "two familiar adult friends share relaxed arm-length proximity, informal asymmetric shoulders, and warm inward eye-lines"
        ),
        "couple": (
            "two familiar adult partners keep comfortable personal distance, inward three-quarter body angles, and small reciprocal responses"
        ),
        "professional": (
            "two adults keep respectful working distance, measured upright support, calm eye-lines, and restrained low hand motion"
        ),
        "everyday": (
            "two adults keep everyday proximity, asymmetric supported posture, and relaxed inward eye-lines"
        ),
    }.get(social_context, "two adults keep everyday proximity, asymmetric supported posture, and relaxed inward eye-lines")
    rhythm_direction = {
        "confidential": "compact private motion, soft shoulders, small cheek changes, and no broad gestures",
        "urgent_clear": "one quicker supported torso change followed by a complete readable settle",
        "restrained_surprise": "one delayed eyebrow and cheek change followed by a neutral reset",
        "upbeat_confident": "one light cheek lift and one supported low emphasis followed by a relaxed reset",
        "restrained_tension": "one brief brow hold and small supported weight shift without exaggerated anger",
        "calm_informative": "measured stable posture, one restrained emphasis, and a complete neutral reset",
        "relaxed_natural": "relaxed asynchronous motion with one readable action and a complete neutral reset",
    }.get(delivery_style, "relaxed asynchronous motion with one readable action and a complete neutral reset")
    try:
        max_downward_gaze = min(1.2, max(0.3, float(profile.get("max_continuous_downward_gaze_seconds") or 0.6)))
    except (TypeError, ValueError):
        max_downward_gaze = 0.6
    return [
        f"SOCIAL DYNAMICS - {social_direction}; PERFORMANCE RHYTHM - {rhythm_direction}; "
        f"GAZE RECOVERY - any glance below the partner's face lasts no longer than {max_downward_gaze:.1f} seconds and returns inward; "
        "neither adult holds a downward gaze through the middle or final part of the take."
    ]


def _compile_dialogue_clean_plate_prompt(plan: dict[str, Any]) -> str:
    """Compile a silent, text-free visual plate for a later voiceover pass.

    A production H3 run showed that literal Mandarin dialogue in the joint
    audio/video request can still become burned caption pixels even when the
    prompt explicitly forbids subtitles.  Keep the business script in lineage,
    but project only anonymous cast, scene, timing, and silent reaction beats to
    the visual generator.  Even generic phrases such as ``speaking turn`` and
    ``lips beginning a question`` caused H3 to hallucinate burned captions in a
    real 15-second production run, so the GPU-facing prompt contains no speech
    semantics at all.  A governed voice renderer owns the spoken result.
    """

    shots = [shot for shot in _list(plan.get("shots")) if isinstance(shot, dict)]
    contract = _dict(plan.get("ad_material_contract"))
    continuity_lock = _dict(contract.get("dialogue_continuity_lock"))
    checkpoint_values = [
        int(item)
        for item in _list(continuity_lock.get("checkpoints_percent"))
        if str(item).isdigit() and 0 < int(item) <= 100
    ] or [40, 60, 80, 100]
    mid_checkpoint_text = ", ".join(str(item) for item in checkpoint_values if item < 100) or "40, 60, 80"
    actor_profile = _dialogue_clean_plate_actor_profile(plan, shots)
    scene_profile = _clean_plate_scene_profile(
        shots, cast_market=_text(contract.get("cast_market"), 60), actor_count=2
    )
    wardrobe_profile = _dialogue_clean_plate_wardrobe_profile(shots)
    hook_pattern = _text(
        _dict(_dict(plan.get("ad_material_contract")).get("opening_visual_hook")).get("pattern"), 60
    )
    # Production review showed that long lists of forbidden visual concepts can
    # be rendered literally by the local H3 text encoder.  This profile therefore
    # describes one coherent positive scene and keeps policy exclusions in the
    # structured lineage instead of repeating them to the GPU.
    hook_instructions = {
        "question_turn_reveal": "in frame one the active adult is already leaning the upper torso forward by 3-5 centimeters with one eyebrow raised; after about 0.3 seconds the partner gives one small delayed eyebrow and cheek reaction while keeping the inward partner gaze",
        "held_reaction_reveal": "in frame one the active adult is already shifting upper-body weight backward by 3-5 centimeters; after about 0.3 seconds the partner notices and turns the head and eyes toward that visible movement",
        "delayed_surprise_reveal": "in frame one the active adult already holds one raised eyebrow and a slight forward upper-body lean; after about 0.3 seconds the partner gives one restrained surprise response while keeping the inward partner gaze",
        "decisive_eye_line_reveal": "in frame one the active adult is already rotating the shoulders 5-8 degrees toward the partner with a decisive inward eye-line; after about 0.3 seconds the partner stops and turns the head and eyes toward that movement",
        "expectant_eye_line_reveal": "in frame one the active adult already holds a slight forward upper-body lean and expectant eyebrow change; after about 0.3 seconds the partner gives one small delayed eyebrow and cheek reaction while keeping the inward partner gaze",
    }
    hook_instruction = hook_instructions.get(hook_pattern, hook_instructions["expectant_eye_line_reveal"])
    scene_is_cafe = "cafe" in scene_profile.casefold()
    environment_anchor = (
        "one rigid floor-supported cafe table stays off-center in the lower-left foreground with one continuous tabletop "
        "and visible pedestal; tableware stays still"
        if scene_is_cafe
        else "ordinary floor-supported furniture keeps stable edges, scale and perspective; every surface remains clear, "
        "uninterrupted and physically attached to the room"
    )
    lines = [
        "integrated_multimodal_description:",
        "CLEAN TWO-PERSON LIVE-ACTION PLATE - a believable social moment photographed as one coherent full-canvas frame.",
        "CAMERA - one eye-level portrait camera about 2.6 meters away with a natural 50mm full-frame field of view; a steady "
        "waist-up medium two-shot keeps both complete shoulder lines, torsos, elbows and waist support visible.",
        f"CAST - {actor_profile}.",
        f"WARDROBE - {wardrobe_profile}; the same fabric, color and silhouette continue through the take.",
        f"LOCATION - {scene_profile}. One broad plaster surface fills the space between both faces; daylight, floor, wall and "
        "furniture keep one perspective.",
        "COMPOSITION - left adult stands or sits nearer at x=32 percent; right adult is farther at x=66 percent. Their heads "
        "differ in height, both bodies angle inward, and a clear corridor separates their shoulder lines.",
        "FRAMING LOCK - camera position, focal length and distance stay fixed. Crowns, complete shoulder lines, elbows, torsos "
        "and waist support remain visible; each face stays below twenty-four percent of canvas height, outside the center line, "
        "with an eight-to-twelve-percent corridor between inner face contours.",
        "OPENING HOOK - during the first 1.5 seconds, " + hook_instruction + ". The shoulder center is already displaced "
        "by 1.5-2.5 percent of frame width and the supported torso changes by 2-3 percent of frame height in the opening "
        "frames, creating a readable before-and-after pose inside the waist-up view.",
        "STAGGERED REACTION - the active adult starts with one small shoulder rotation, torso lean and eyebrow change. The partner "
        "keeps breathing and a calm inward gaze, then reacts about 0.3 seconds later with one head turn and restrained cheek "
        "change. The first adult settles before the second reaction begins.",
        "RESTING FACE - both adults keep naturally closed relaxed lips. Visible motion stays in the eyes, brows, cheeks, head, "
        "shoulders, torso, breathing and weight transfer.",
        "EXPRESSION ARC - both begin neutral and attentive. Each brow or cheek response is brief, asymmetric and returns to "
        "its own neutral baseline before the next turn; intensity changes instead of holding one repeated smile.",
        "IDENTITY CONTINUITY - the left and right adults retain their distinct age, face shape, nose, jaw, eye spacing, hair, "
        "skin texture, wardrobe and individual motion timing from first frame to final frame.",
        "GAZE - both faces remain complementary inward three-quarter views. Their nasal bridges, iris direction and shoulder "
        "angles point into the shared interaction space while each outer cheek contour remains visible to the camera.",
        f"MID-TO-END ORIENTATION LOCK - at {mid_checkpoint_text} percent, left eyes, nose and shoulders aim screen-right "
        "toward the partner; right equivalents aim screen-left; head heights and shoulder yaw remain asymmetric.",
        "FINAL-FRAME SETTLE - during the final twenty percent both adults return inward after each reaction. The final frame "
        "keeps crossed partner eye-lines, visible outer cheeks and separately supported torsos settling asynchronously.",
        "BODY SUPPORT - each neck, torso, elbow and waist is anatomically continuous. The partner keeps both hands supported "
        "below the lower canvas boundary. During a medium or long motion window, the active adult may lift exactly one supported "
        "forearm into the lower third, trace one small open-palm arc, then return the hand to waist support; it never rises above "
        "the sternum and the other hand remains supported.",
        f"ENVIRONMENT GEOMETRY - {environment_anchor}.",
        "CLEAN FRAME - skin, plain fabric, plaster, glass, wood and daylight form one uninterrupted photographic canvas.",
    ]
    face_authenticity = _clean_plate_face_authenticity(plan)
    if face_authenticity:
        lines.insert(4, f"VISIBLE COMMERCIAL APPEARANCE - {face_authenticity}.")
    lines[9:9] = _clean_plate_performance_profile_lines(contract)
    timeline = []
    beats = [item for item in _list(plan.get("performance_timeline")) if isinstance(item, dict)]
    speaker_sides: dict[str, str] = {}
    available_sides = ["left", "right"]
    for index, beat in enumerate(beats, 1):
        label = _text(beat.get("speaker"), 40).casefold()
        explicit_side = (
            "left" if any(term in label for term in ("左", "left"))
            else "right" if any(term in label for term in ("右", "right"))
            else ""
        )
        identity = re.sub(r"\s+", "", label) or f"speaker-{index}"
        if identity not in speaker_sides:
            speaker_sides[identity] = explicit_side or (
                available_sides.pop(0) if available_sides else ("left" if index % 2 else "right")
            )
        side = speaker_sides[identity]
        partner = "right" if side == "left" else "left"
        start = float(beat.get("start_seconds") or 0)
        end = float(beat.get("end_seconds") or start)
        window = max(0.0, end - start)
        intent = _text(beat.get("intent"), 32).casefold()
        if intent == "resistance":
            entry_motion = "shifts the supported torso backward and briefly redirects the eyes"
            expression_motion = "one restrained brow release"
        elif intent == "surprise":
            entry_motion = "raises one eyebrow while the supported torso shifts toward the partner"
            expression_motion = "one restrained cheek response"
        elif intent == "call_to_action":
            entry_motion = "rotates the shoulders 5-8 degrees toward the partner"
            expression_motion = "one decisive open-palm emphasis"
        elif intent == "question":
            entry_motion = "leans the supported torso toward the partner by 2-3 percent of frame height"
            expression_motion = "one restrained eyebrow change"
        else:
            entry_motion = "shifts the supported torso toward the partner by 2-3 percent of frame height"
            expression_motion = "one restrained cheek or eyebrow change"
        if window <= 1.5:
            choreography = (
                f"{side} adult {entry_motion}, holds the readable pose, then settles with {expression_motion}; "
                f"{partner} adult begins one eye-and-head response 0.3 seconds later"
            )
        elif window <= 4.0:
            first_end = start + window * 0.34
            second_end = start + window * 0.70
            choreography = (
                f"P1 {start:.2f}-{first_end:.2f}s {side} adult {entry_motion}; "
                f"P2 {first_end:.2f}-{second_end:.2f}s one supported forearm traces a single 6-10 percent-frame "
                f"open-palm arc inside the lower third with {expression_motion}; P3 {second_end:.2f}-{end:.2f}s "
                f"the hand lowers and torso settles; {partner} adult starts one delayed eye-and-head response after P1"
            )
        else:
            first_end = start + window * 0.28
            second_end = start + window * 0.68
            choreography = (
                f"P1 {start:.2f}-{first_end:.2f}s {side} adult {entry_motion} and holds the new pose; "
                f"P2 {first_end:.2f}-{second_end:.2f}s exactly one supported forearm traces one 8-12 percent-frame "
                f"open-palm arc inside the lower third with {expression_motion}; P3 {second_end:.2f}-{end:.2f}s "
                f"the hand returns to waist support and the torso settles inward; {partner} adult waits, then gives one "
                "eye-and-head response 0.3 seconds after P2 begins"
            )
        timeline.append(f"T{index} {start:.2f}-{end:.2f}s: {choreography}.")
    if not timeline:
        duration = max([float(shot.get("end_seconds") or 0) for shot in shots] or [5.0])
        midpoint = duration * 0.62
        timeline = [
            f"T1 0.00-{midpoint:.2f}s: left adult performs one short silent expression beat; right adult holds then reacts.",
            f"T2 {midpoint:.2f}-{duration:.2f}s: right adult gives one delayed natural reaction; left adult settles.",
        ]
    lines.append("SHOT TIMELINE - " + " | ".join(
        f"S{index}:{float(shot.get('start_seconds') or 0):.2f}-{float(shot.get('end_seconds') or 0):.2f}s "
        "fixed waist-up medium two-shot [Static shot]"
        for index, shot in enumerate(shots, 1)
    ))
    lines.append("PERFORMANCE TIMELINE - " + " ".join(timeline))
    operator_action_indices = {
        int(item)
        for item in _list(plan.get("operator_edited_action_indices"))
        if str(item).isdigit()
    }
    edited_motion = [
        f"S{index}: {_safe_shot_motion_directive(shot.get('action'))}"
        for index, shot in enumerate(shots, 1)
        if index in operator_action_indices and _safe_shot_motion_directive(shot.get("action"))
    ]
    if edited_motion:
        lines.append("OPERATOR-EDITED VISUAL MOTION - " + " | ".join(edited_motion))
    return "\n".join(lines)[:H3_PROMPT_MAX_CHARS]


def _compile_no_text_motion_prompt(plan: dict[str, Any]) -> str:
    """Compile an ASCII-only visual prompt for governed no-text replay.

    H3 can turn literal dialogue, campaign copy, or a rejected Chinese subtitle
    quoted in the prompt into a new burned-in caption.  In this profile the
    structured business plan remains in lineage and review, while the GPU sees
    only generic live-action motion instructions, official media placeholders,
    camera commands and timestamps.  Audio is intentionally handled outside
    the visual generation request.
    """

    references = [item for item in _list(plan.get("reference_roles")) if isinstance(item, dict)]
    counters = {"image": 0, "video": 0, "audio": 0}
    lines = ["integrated_multimodal_description:"]
    for item in references:
        role = _text(item.get("role"), 40)
        kind = H3_REFERENCE_ROLES.get(role)
        if kind not in counters or kind == "audio":
            continue
        counters[kind] += 1
        placeholder = "Picture" if kind == "image" else "Video"
        usage = (
            "an approved adult identity anchor"
            if kind == "image"
            else "a silent low-frequency motion-only reference"
        )
        lines.append(f"<{placeholder} {counters[kind]}> is {usage}.")

    guardrails = _dict(plan.get("brand_guardrails"))
    identity_policy = _text(guardrails.get("reference_identity_policy"), 40)
    if identity_policy == "preserve_source":
        lines.append(
            "REFERENCE IDENTITY PRESERVATION LOCK - keep the approved adult identity anchor stable, while using the "
            "video only for pose, motion, composition, and timing."
        )
    else:
        lines.append(
            "REFERENCE IDENTITY REPLACEMENT LOCK - copy only coarse pose, gesture, camera composition, and timing. "
            "Generate visibly different anonymous adult actors with new face shapes and identities."
        )
    lines.append(
        "REFERENCE VIDEO MOTION-ONLY LOCK - use the silent blurred video only for coarse adult pose, motion timing, "
        "composition, and shot rhythm. Never restore source overlays or fine appearance details."
    )
    lines.append(
        "VISIBLE PIXEL CONTRACT - render clean live-action people and natural scene pixels only. The frame must contain "
        "no captions, subtitles, lower thirds, labels, signs, logos, watermarks, product cards, registration marks, "
        "prices, user interfaces, letters, digits, glyphs, pseudo-text, or graphic overlays."
    )
    lines.append(
        "EMPTY-HANDS AND NO-PROPS LOCK - every actor keeps empty, clearly visible hands. Do not render or imply any "
        "handheld object, bottle, jar, can, tube, box, packet, container, product, package, card, paper, phone, screen, "
        "sign, badge, clothing mark, or other surface that could carry a label or symbol. If the motion reference reaches "
        "for or presents an object, replace that action with an empty-hand conversational gesture."
    )
    lines.append(
        "AUDIO ISOLATION LOCK - generate visual motion only with silent neutral room tone. No spoken dialogue, singing, "
        "lyrics, narration, quoted words, or synchronized captions. Speech can be added later as a governed audio track."
    )
    shots = [shot for shot in _list(plan.get("shots")) if isinstance(shot, dict)]
    subject = _no_text_motion_subject(shots)
    for index, shot in enumerate(shots, 1):
        camera = _text(shot.get("camera_command"), 80) or "[Static shot]"
        if camera not in H3_CAMERA_COMMANDS:
            camera = "[Static shot]"
        start = float(shot.get("start_seconds") or 0)
        end = float(shot.get("end_seconds") or start)
        framing = _no_text_motion_framing(shot)
        lines.append(
            f"[Shot {index}] At {start:.3f}-{end:.3f}s: {framing}; {camera}; {subject}; natural restrained "
            "facial movement and gestures follow the motion reference; clean uncluttered indoor setting; soft natural "
            "light; both hands empty; no handheld objects, products, packaging, bottles, containers, labels, visible "
            "text, or speech."
        )
    return "\n".join(lines)


def _compile_product_replacement_prompt(plan: dict[str, Any]) -> str:
    """Compile strict structure replay without inventing people or global text bans."""

    references = [item for item in _list(plan.get("reference_roles")) if isinstance(item, dict)]
    counters = {"image": 0, "video": 0, "audio": 0}
    lines = ["integrated_multimodal_description:"]
    for item in references:
        role = _text(item.get("role"), 40)
        kind = H3_REFERENCE_ROLES.get(role)
        if kind not in counters or kind == "audio":
            continue
        counters[kind] += 1
        placeholder = "Picture" if kind == "image" else "Video"
        business_role = _text(item.get("business_role"), 40)
        if kind == "video":
            usage = "the silent low-frequency source structure, camera, composition, object motion, hand motion, and timing reference"
        elif business_role == "product_packshot":
            usage = "the approved target product package reference"
        elif business_role == "product_detail":
            usage = "the approved target loose-unit or product-detail reference"
        else:
            usage = "an approved target product visual reference"
        lines.append(f"<{placeholder} {counters[kind]}> is {usage}.")

    contract = _dict(plan.get("source_replication_contract"))
    people_presence = _text(contract.get("source_people_presence"), 30)
    lines.extend([
        "STRICT SOURCE STRUCTURE LOCK - reproduce the source video's shot duration, framing, camera position, object count, "
        "object placement, hand trajectory, action timing, lighting, background, and rhythm. Do not add a new shot, scene, "
        "story beat, prop, dialogue, camera move, or subject that is absent from the source structure reference.",
        "PRODUCT SLOT REPLACEMENT LOCK - replace only the source product objects with the approved target product references. "
        "Use the package reference for box-shaped source slots and the loose-unit reference for individual packet slots. "
        "Preserve target geometry, color, relative scale, count, orientation, and rigid flat edges. Do not substitute a person, "
        "generic box, bottle, card, or invented package.",
        "SOURCE OVERLAY REMOVAL LOCK - remove every source-video caption, title, corner label, sticker, watermark, lower-third, "
        "medical registration footer, price, promotion, UI element, and pseudo-text. The only permitted printed pixels are those "
        "already present on the approved target product references; do not add, rewrite, translate, or invent any wording.",
        "AUDIO ISOLATION LOCK - visual generation only. Do not reconstruct source speech, narration, lyrics, or synchronized captions.",
    ])
    if people_presence == "partial_hands":
        lines.append(
            "PARTIAL-HAND LOCK - the source contains only a local hand interaction. Preserve only that partial hand and its exact "
            "trajectory; never reveal or invent a face, head, torso, full body, actor identity, gender, couple, presenter, or doctor."
        )
    elif people_presence == "none":
        lines.append(
            "NO-PERSON LOCK - the source contains no person. Do not generate any hand, face, body, actor, presenter, couple, or human silhouette."
        )
    else:
        lines.append(
            "SOURCE-SUBJECT LOCK - preserve only the people visibly evidenced by the source; do not add or change subject count or identity."
        )

    shots = [shot for shot in _list(plan.get("shots")) if isinstance(shot, dict)]
    for index, shot in enumerate(shots, 1):
        camera = _text(shot.get("camera_command"), 80) or "[Static shot]"
        if camera not in H3_CAMERA_COMMANDS:
            camera = "[Static shot]"
        start = float(shot.get("start_seconds") or 0)
        end = float(shot.get("end_seconds") or start)
        framing = _no_text_motion_framing(shot)
        lines.append(
            f"[Shot {index}] At {start:.3f}-{end:.3f}s: {framing}; {camera}; preserve the matching source segment "
            "one-for-one; replace product slots only; keep target packaging rigid and visually faithful; no source overlays, "
            "no invented subjects, and no extra actions."
        )
    return "\n".join(lines)


def brief_requires_brand_reference(brief: dict[str, Any] | None) -> bool:
    haystack = " ".join(str(value or "") for value in _dict(brief).values()).casefold()
    # Exclusion lists such as ``无品牌、无包装、无 Logo、无文字`` describe what
    # must not appear.  Removing the whole negated clause prevents the safety
    # detector from turning an explicitly abstract T2V request into I2V.
    positive_context = _BRAND_EXCLUSION_CLAUSE_RE.sub(" ", haystack)
    return any(term.casefold() in positive_context for term in _BRAND_FIDELITY_TERMS)


def brief_excludes_brand_fidelity(brief: dict[str, Any] | None) -> bool:
    haystack = " ".join(str(value or "") for value in _dict(brief).values()).casefold()
    exclusion_terms = (*_BRAND_FIDELITY_TERMS, "商品", "产品", "品牌")
    return any(
        any(term.casefold() in match.group(0) for term in exclusion_terms)
        for match in _BRAND_EXCLUSION_CLAUSE_RE.finditer(haystack)
    )


def brief_excludes_people(brief: dict[str, Any] | None) -> bool:
    """Return whether the business brief explicitly forbids visible people.

    The operator's original brief is the source of truth.  This intentionally
    remains independent from the model-authored ``ad_material_contract`` so a
    stale or hallucinated ``people_requested=true`` cannot silently reverse an
    explicit request such as ``不要人物``.
    """

    # Only operator-owned business fields may decide this hard gate.  A
    # production brief also carries nested, system-authored lineage such as
    # ``ad_material_contract`` and performance rules.  Flattening the entire
    # dict allowed phrases like ``不得补充第二具人体`` or ``人体关节`` to pair
    # with an unrelated earlier ``不要字幕`` and falsely turn a two-person
    # dialogue into a no-people task.
    haystack = _brief_request_text(brief)
    return any(term.casefold() in haystack for term in _NON_HUMAN_EXCLUSION_TERMS) or bool(
        _NON_HUMAN_EXCLUSION_RE.search(haystack)
    )


def _plan_has_product_prop(plan: dict[str, Any]) -> bool:
    values = [plan.get("creative_goal")]
    for shot in _list(plan.get("shots")):
        if isinstance(shot, dict):
            values.extend((shot.get("subject"), shot.get("action"), shot.get("scene")))
    normalized_values = [
        _BRAND_EXCLUSION_CLAUSE_RE.sub(" ", str(value or "").casefold())
        for value in values
    ]
    # A clean-plate instruction such as ``无商品元素`` is a hard exclusion,
    # not evidence that a product prop is present.  Reuse the governed
    # negation parser so an i2v character anchor does not receive a false
    # people+product warning merely because the request names what to omit.
    haystack = " ".join(normalized_values)
    return any(term.casefold() in haystack for term in _PRODUCT_PROP_TERMS)


def _brief_source_business_roles(brief: dict[str, Any] | None) -> set[str]:
    roles: set[str] = set()
    for item in _list(_dict(brief).get("source_roles")):
        if not isinstance(item, dict):
            continue
        role = _text(item.get("business_role") or item.get("role"), 40)
        if role:
            roles.add(role)
    return roles


def _brief_request_text(brief: dict[str, Any] | None) -> str:
    data = _dict(brief)
    values = [
        data.get("request"),
        data.get("requirements"),
        data.get("generation_requirements"),
        data.get("product"),
        data.get("script"),
    ]
    return " ".join(str(value or "") for value in values).casefold()


def _has_transparent_product_cutout(brief: dict[str, Any] | None) -> bool:
    for item in _list(_dict(brief).get("source_roles")):
        if not isinstance(item, dict):
            continue
        role = _text(item.get("business_role") or item.get("role"), 40)
        if role not in {"product_packshot", "product_detail"}:
            continue
        source_text = " ".join(
            _text(item.get(key), 300)
            for key in ("mention_token", "purpose", "display_name", "name", "filename")
        ).casefold()
        if any(marker.casefold() in source_text for marker in _TRANSPARENT_CUTOUT_MARKERS):
            return True
    return False


def _transparent_cutout_fidelity_lock_required(brief: dict[str, Any] | None) -> bool:
    if not _has_transparent_product_cutout(brief):
        return False
    request_text = _brief_request_text(brief)
    fidelity_requested = any(marker.casefold() in request_text for marker in _PACKAGE_FIDELITY_MARKERS)
    explicit_material_change = any(
        marker.casefold() in request_text for marker in _EXPLICIT_TRANSPARENT_MATERIAL_MARKERS
    )
    # A transparent-background product cutout is the governed default.  Only a
    # fully explicit material-change request can override it, and never when the
    # same request also asks to preserve the real package.
    return fidelity_requested or not explicit_material_change


def _sanitize_transparent_cutout_plan(plan: dict[str, Any], *, contains_person: bool) -> None:
    replacement = "已审核真实商品包装（透明仅指图片背景）"

    def clean(value: Any) -> str:
        return _TRANSPARENT_PACKAGE_TRANSFORM_RE.sub(replacement, str(value or ""))

    goal = clean(plan.get("creative_goal"))
    if goal:
        plan["creative_goal"] = goal
    shots = [shot for shot in _list(plan.get("shots")) if isinstance(shot, dict)]
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        for key in ("subject", "action", "scene"):
            if shot.get(key):
                shot[key] = clean(shot.get(key))
        if not contains_person:
            shot["camera_command"] = "[Static shot]"
            shot["action"] = (
                "已审核真实商品包装保持首帧姿态、比例、透视和位置完全静止，"
                "仅背景光线做极轻微呼吸变化"
            )
            shot["scene"] = "沿用已审核首帧的原始背景、构图和商品位置"
            shot["lighting"] = "首帧原始商品光线保持稳定，背景光线仅有极轻微呼吸变化"
    if not contains_person and shots:
        # Product-only governed cutouts are one immutable packshot, not a
        # model-authored sequence of push-ins, side moves or synthetic cuts.
        # A single canonical shot removes cross-shot contradictions before H3.
        duration = float(plan.get("duration_seconds") or shots[-1].get("end_seconds") or 5)
        first = shots[0]
        first["start_seconds"] = 0.0
        first["end_seconds"] = duration
        plan["shots"] = [first]


def _requires_nonhuman_abstract_guardrail(plan: dict[str, Any], brief: dict[str, Any] | None) -> bool:
    brief_text = " ".join(str(value or "") for value in _dict(brief).values()).casefold()
    plan_text = " ".join(
        str(value or "")
        for shot in _list(plan.get("shots"))
        if isinstance(shot, dict)
        for value in (shot.get("subject"), shot.get("action"), shot.get("scene"))
    ).casefold()
    negatives = " ".join(str(value or "") for value in _list(plan.get("negative_constraints"))).casefold()
    exclusion_text = f"{brief_text} {negatives}"
    excludes_humans = any(term.casefold() in exclusion_text for term in _NON_HUMAN_EXCLUSION_TERMS) or bool(
        _NON_HUMAN_EXCLUSION_RE.search(exclusion_text)
    )
    describes_abstract_subject = any(term.casefold() in plan_text for term in _ABSTRACT_SUBJECT_TERMS)
    return excludes_humans and describes_abstract_subject


def compile_h3_plan(value: dict[str, Any], *, brief: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize an editable plan and deterministically compile its H3 prompt."""

    if not isinstance(value, dict):
        raise H3PromptPolicyError("plan", "plan must be an object")
    plan = deepcopy(value)
    direct_prompt = None
    if "direct_h3_prompt" in plan:
        direct_prompt = str(plan.get("direct_h3_prompt") or "").strip()
        if not direct_prompt:
            raise H3PromptPolicyError("direct_h3_prompt", "direct_h3_prompt must not be empty")
        if len(direct_prompt) > H3_PROMPT_MAX_CHARS:
            raise H3PromptPolicyError(
                "direct_h3_prompt",
                f"direct prompt has {len(direct_prompt)} characters and exceeds the {H3_PROMPT_MAX_CHARS} character limit",
            )
        if _DIRECT_H3_UNSAFE_CONTROL_RE.search(direct_prompt):
            raise H3PromptPolicyError("direct_h3_prompt", "direct prompt contains unsupported control characters")
    mode = _text(plan.get("h3_mode"), 40)
    if mode not in {"text_to_video", "image_to_video", "reference_replay"}:
        raise H3PromptPolicyError("h3_mode", "unsupported h3_mode")
    warnings = [_text(item, 500) for item in _list(plan.get("warnings"))]
    warnings = [item for item in warnings if item]
    brief_data = _dict(brief)
    generation_options = {
        **_dict(brief_data.get("generation_options")),
        **_dict(plan.get("generation_options")),
    }
    requested_commercial_appearance = (
        generation_options.get("commercial_appearance", True) is not False
    )
    replay_mode = _text(brief_data.get("replay_mode") or brief_data.get("replay_type"), 60)
    local_replacement = replay_mode in {
        "local_replace", "local_replacement", "source_local_replace", "in_place_replacement"
    }
    generation_options["commercial_appearance"] = requested_commercial_appearance and not local_replacement
    plan["generation_options"] = generation_options
    if brief_data.get("contains_person") is not None:
        plan["contains_person"] = brief_data.get("contains_person") is True
    appearance_source = " ".join(
        [
            _text(brief_data.get("visual_prompt"), 4000),
            _text(brief_data.get("request"), 4000),
            _text(plan.get("video_prompt"), 4000),
            " ".join(
                f"{_text(item.get('subject'), 500)} {_text(item.get('scene'), 500)} {_text(item.get('action'), 500)}"
                for item in _list(plan.get("shots"))
                if isinstance(item, dict)
            ),
        ]
    ).casefold()
    appearance_conflict = bool(
        requested_commercial_appearance
        and any(term in appearance_source for term in ("素颜", "无妆", "纪实", "普通人", "bare face", "documentary"))
    )
    plan["commercial_appearance"] = {
        "requested": requested_commercial_appearance,
        "enabled": generation_options["commercial_appearance"],
        "not_applicable_reason": "local_replacement_preserves_source_pixels" if local_replacement else None,
        "conflict_requires_operator_review": appearance_conflict,
        "prompt": _clean_plate_face_authenticity(plan),
    }
    if local_replacement and requested_commercial_appearance:
        warning = "原片局部替换会保留人物像素，人物商业质感设置在此模式不生效。"
        if warning not in warnings:
            warnings.append(warning)
    if appearance_conflict:
        warning = "人物商业质感与输入中的素颜、纪实或普通人要求存在冲突；当前保持开关值，请在生成前明确确认或关闭。"
        if warning not in warnings:
            warnings.append(warning)
    requested_mode = _text(brief_data.get("requested_h3_mode"), 40)
    if requested_mode in {"text_to_video", "image_to_video", "reference_replay"} and requested_mode != mode:
        mode = requested_mode
        plan["h3_mode"] = requested_mode
        warning = f"已按用户在素材工作台的明确选择使用 {requested_mode} 模式。"
        if warning not in warnings:
            warnings.append(warning)
    duration = _duration_from_plan(plan)
    plan["duration_seconds"] = duration
    plan["ratio"] = _ratio_from_plan(plan)
    plan["shots"] = _normalized_shots(plan, duration, warnings)
    explicitly_continuous = plan.get("continuous_take") is True or _prefers_continuous_take(plan, brief)
    default_person_i2v_continuity = (
        mode == "image_to_video"
        and duration <= 5
        and brief_data.get("contains_person") is True
        and len(plan["shots"]) > 1
        and not _requests_explicit_cut(plan, brief)
    )
    plan["continuous_take"] = explicitly_continuous or default_person_i2v_continuity
    if default_person_i2v_continuity and not explicitly_continuous:
        warnings.append("人物首帧的 5 秒多段分镜已默认编译为同一连续镜头，降低硬切和人物身份漂移风险。")
    plan["prompt_policy_version"] = H3_PROMPT_POLICY_VERSION
    plan["official_sources"] = list(H3_OFFICIAL_SOURCES)
    plan["assumptions"] = [_text(item, 500) for item in _list(plan.get("assumptions")) if _text(item, 500)]
    review_rework = _dict(plan.get("review_rework"))
    if review_rework:
        reason = re.sub(r"\s+", " ", _text(review_rework.get("reason"), 1000)).strip()
        annotations = []
        for item in _list(review_rework.get("annotations"))[:4]:
            if not isinstance(item, dict):
                continue
            note = re.sub(r"\s+", " ", _text(item.get("note"), 300)).strip()
            if not note:
                continue
            annotations.append({
                "category": _text(item.get("category"), 40),
                "severity": _text(item.get("severity"), 40),
                "start_seconds": item.get("start_seconds"),
                "end_seconds": item.get("end_seconds"),
                "note": note,
            })
        if reason or annotations:
            plan["review_rework"] = {"reason": reason, "annotations": annotations}
        else:
            plan.pop("review_rework", None)
    guardrails = _dict(plan.get("brand_guardrails"))
    product_overlay = _dict(plan.get("product_overlay"))
    product_overlay_enabled = product_overlay.get("enabled") is True
    source_business_roles = _brief_source_business_roles(brief)
    has_verified_product_packshot = bool(source_business_roles & {"product_packshot", "product_detail"})
    has_character_first_frame = "character_first_frame" in source_business_roles
    brand_reference_required = brief_requires_brand_reference(brief)
    brand_fidelity_excluded = brief_excludes_brand_fidelity(brief)
    brief_source_roles = [item for item in _list(brief_data.get("source_roles")) if isinstance(item, dict)]
    has_reference_video = any(
        _text(item.get("technical_role") or item.get("role"), 40)
        in {"reference_video", "motion_reference", "continuity_anchor"}
        for item in brief_source_roles
    )
    suppress_reference_text = bool(
        mode == "reference_replay"
        and brief_data.get("strip_reference_text") is True
        and has_reference_video
    )
    generated_dialogue_enabled = bool(
        brief_data.get("audio_enabled") is True
        and _text(_dict(brief_data.get("script_timing")).get("shot_script") or brief_data.get("script"), 6000)
    )
    replication_contract = _dict(brief_data.get("source_replication_contract"))
    strict_product_replacement = bool(
        suppress_reference_text
        and replication_contract.get("enabled") is True
        and replication_contract.get("mode") == "structure_replay_product_replace"
        and has_verified_product_packshot
    )
    reference_identity_policy = _text(brief_data.get("reference_identity_policy"), 40)
    if mode == "reference_replay" and reference_identity_policy not in {"preserve_source", "replace_actor"}:
        reference_identity_policy = "replace_actor"
    identity_relevant = not (
        strict_product_replacement
        and replication_contract.get("source_people_presence") in {"none", "partial_hands"}
    )
    if mode == "reference_replay" and identity_relevant:
        guardrails = {**guardrails, "reference_identity_policy": reference_identity_policy}
        identity_warning = (
            _REFERENCE_IDENTITY_PRESERVE_WARNING
            if reference_identity_policy == "preserve_source"
            else _REFERENCE_IDENTITY_REPLACE_WARNING
        )
        if identity_warning not in warnings:
            warnings.append(identity_warning)
    elif mode == "reference_replay":
        guardrails = {
            **guardrails,
            "reference_identity_policy": "not_applicable",
            "source_people_presence": replication_contract.get("source_people_presence"),
        }
        warnings = [
            item for item in warnings
            if item not in {_REFERENCE_IDENTITY_REPLACE_WARNING, _REFERENCE_IDENTITY_PRESERVE_WARNING}
        ]
    if suppress_reference_text:
        guardrails = {
            **guardrails,
            "reference_text_policy": "suppress_all_source_text",
            "reference_video_usage": "motion_composition_timing_only",
            "execution_prompt_profile": (
                "structure_replay_product_replace_no_source_text_v1"
                if strict_product_replacement
                else "motion_only_no_text_ascii_v3"
            ),
            "audio_policy": (
                "source_audio_stripped_generated_dialogue_allowed"
                if generated_dialogue_enabled
                else "disabled_to_prevent_caption_leakage"
            ),
        }
        suppression_warning = (
            _PRODUCT_REPLACEMENT_TEXT_SUPPRESSION_WARNING
            if strict_product_replacement
            else _REFERENCE_TEXT_SUPPRESSION_WARNING
        )
        warnings = [
            item for item in warnings
            if item not in {_REFERENCE_TEXT_SUPPRESSION_WARNING, _PRODUCT_REPLACEMENT_TEXT_SUPPRESSION_WARNING}
        ]
        warnings.append(suppression_warning)
    if brand_fidelity_excluded:
        guardrails = {
            **guardrails,
            "require_reference_image": False,
            "approved_product_image_required": False,
            "reason": "用户明确排除品牌、Logo 或文字保真；不要求已审核品牌产品图，但仍锁定首帧物品身份与位置。",
        }
        warnings = [item for item in warnings if item != _BRAND_REFERENCE_WARNING]
    elif brand_reference_required and has_verified_product_packshot:
        guardrails = {
            **guardrails,
            "require_reference_image": not product_overlay_enabled,
            "approved_product_image_required": False,
            "verified_product_evidence": True,
            "reason": (
                "已提供真实商品透明图；人物底片生成后由 Bridge 确定性植入，H3 不负责重绘包装。"
                if product_overlay_enabled
                else "已提供业务角色为 product_packshot 的真实商品首帧，可用于包装、Logo 或文字保真。"
            ),
        }
        if product_overlay_enabled:
            guardrails.update({
                "people_product_workflow": "clean_plate_then_verified_overlay",
                "product_render_policy": "clean_plate_then_verified_overlay",
                "product_overlay_required": True,
                "product_overlay_anchor": "bottom_right",
                "product_overlay_width_ratio": 0.22,
                "product_overlay_safe_margin_ratio": 0.05,
            })
        if brief_data.get("contains_person") is True:
            if product_overlay_enabled:
                warnings = [item for item in warnings if item != _PEOPLE_PRODUCT_FIDELITY_WARNING]
                if _DETERMINISTIC_PRODUCT_OVERLAY_WARNING not in warnings:
                    warnings.append(_DETERMINISTIC_PRODUCT_OVERLAY_WARNING)
            elif _PEOPLE_PRODUCT_FIDELITY_WARNING not in warnings:
                warnings.append(_PEOPLE_PRODUCT_FIDELITY_WARNING)
    elif brand_reference_required:
        # A technical ``first_frame`` is not automatically product evidence.
        # Keep the current T2V/I2V mode usable, suppress invented products, and
        # tell the production/review loop that a separate verified packshot is
        # still required for a commercial product shot.
        guardrails = {
            **guardrails,
            "require_reference_image": False,
            "approved_product_image_required": True,
            "verified_product_evidence": False,
            "product_render_policy": "suppress_without_verified_product_packshot",
            "reason": "包装、Logo 或文字有真实性要求，但当前没有业务角色为 product_packshot 的真实商品首帧。",
        }
        warnings = [item for item in warnings if item != _BRAND_REFERENCE_WARNING]
        if _MISSING_PRODUCT_EVIDENCE_WARNING not in warnings:
            warnings.append(_MISSING_PRODUCT_EVIDENCE_WARNING)
        if _UNVERIFIED_PRODUCT_NEGATIVE not in _list(plan.get("negative_constraints")):
            plan["negative_constraints"] = [*_list(plan.get("negative_constraints")), _UNVERIFIED_PRODUCT_NEGATIVE]
        if has_character_first_frame:
            guardrails["character_frame_is_not_product_evidence"] = True
    transparent_cutout_lock = _transparent_cutout_fidelity_lock_required(brief)
    if transparent_cutout_lock:
        _sanitize_transparent_cutout_plan(plan, contains_person=brief_data.get("contains_person") is True)
        guardrails = {
            **guardrails,
            "product_source_semantics": "transparent_background_cutout",
            "product_material_transform": "forbidden",
            "product_geometry_color_lock": True,
            "reference_text_policy": "suppress_generated_text_preserve_approved_product_surface",
        }
        if brief_data.get("contains_person") is not True:
            guardrails["product_motion_policy"] = "locked_static_packshot"
            motion_warning = (
                "真实商品透明图默认使用稳定定帧：包装不旋转、不翻面、不改变透视，"
                "只允许背景光线极轻微变化，优先保证投流素材中的商品真实性。"
            )
            if motion_warning not in warnings:
                warnings.append(motion_warning)
        warnings = [item for item in warnings if not _CUTOUT_CONFLICTING_WARNING_RE.search(item)]
        if _TRANSPARENT_CUTOUT_WARNING not in warnings:
            warnings.append(_TRANSPARENT_CUTOUT_WARNING)
    if (
        _text(plan.get("h3_mode"), 40) == "image_to_video"
        and brief_data.get("contains_person") is True
        and _plan_has_product_prop(plan)
    ):
        guardrails["people_product_workflow"] = "separate_people_and_product_for_commercial_output"
        if _PEOPLE_PRODUCT_COMPOSITION_WARNING not in warnings:
            warnings.append(_PEOPLE_PRODUCT_COMPOSITION_WARNING)
    requested_reference_roles = _list(brief_data.get("reference_roles")) or _list(plan.get("reference_roles"))
    if product_overlay_enabled:
        # ``overlay_image`` is a governed Bridge post-process input, not an H3
        # reference placeholder.  A clean-plate production also ignores stale
        # model-suggested H3 references from the first compile pass.  Keep one
        # canonical business warning instead of surfacing those internal role
        # corrections as user errors.
        requested_reference_roles = []
        overlay_warning_markers = (
            "人物镜头先生成无商品干净底片",
            "人物镜头将先生成无商品干净底片",
            "成片需检查遮挡、比例和安全区",
            "不得将包装文字交给 H3 重绘",
            "文生视频不接收参考素材；模型建议的参考角色已从执行提示词移除",
            "不支持的参考素材角色 overlay_image 已移除",
        )
        warnings = [
            item for item in warnings
            if not any(marker in item for marker in overlay_warning_markers)
        ]
        warnings.append(_DETERMINISTIC_PRODUCT_OVERLAY_WARNING)
    plan["reference_roles"] = _normalized_reference_roles(
        requested_reference_roles,
        mode=str(plan.get("h3_mode") or mode),
        require_brand_reference=bool(guardrails.get("require_reference_image")),
        warnings=warnings,
    )
    plan["brand_guardrails"] = guardrails
    _apply_production_variant(plan, warnings)
    negatives = [_text(item, 300) for item in _list(plan.get("negative_constraints")) if _text(item, 300)]
    if suppress_reference_text and not generated_dialogue_enabled:
        suppression_negative = (
            _SOURCE_OVERLAY_TEXT_SUPPRESSION_NEGATIVE
            if strict_product_replacement
            else _REFERENCE_TEXT_SUPPRESSION_NEGATIVE
        )
        if suppression_negative not in negatives:
            negatives.append(suppression_negative)
    if mode == "reference_replay" and reference_identity_policy == "replace_actor":
        replacement_negative = (
            "不得保留、重建或模仿参考视频中真实人物的脸型、生物识别身份或可识别面部特征；"
            "不得生成与任何真实个人或公众人物相似的新演员"
        )
        if replacement_negative not in negatives:
            negatives.append(replacement_negative)
    if _requires_nonhuman_abstract_guardrail(plan, brief):
        if _NON_HUMAN_ABSTRACT_GUARDRAIL not in negatives:
            negatives.append(_NON_HUMAN_ABSTRACT_GUARDRAIL)
        warning = "检测到无人物的抽象主体；已增加非人体、非解剖轮廓约束，降低材质形态被误读为人体的概率。"
        if warning not in warnings:
            warnings.append(warning)
    plan["negative_constraints"] = negatives
    # A real production run proved that even literal Mandarin dialogue with a
    # no-subtitle instruction can still reappear as burned caption pixels.  Do
    # not limit the safety gate to product-overlay jobs: every business script
    # must first produce a silent visual plate and later pass through a governed
    # voice renderer with immutable artifact lineage.
    requested_dialogue = str(
        _dict(brief_data.get("script_timing")).get("shot_script")
        or brief_data.get("script")
        or plan.get("audio_prompt")
        or ""
    ).strip()[:6000]
    structured_dialogue = bool(
        re.search(
            r"(?:^|\n)\s*(?:女|男|女生|男生|女方|男方|woman|man|speaker\s*\d*)\s*[：:]",
            requested_dialogue,
            re.IGNORECASE,
        )
        or _list(plan.get("performance_timeline"))
    )
    dialogue_clean_plate_required = bool(
        requested_dialogue and (generated_dialogue_enabled or structured_dialogue)
    )
    final_audio_requested = brief_data.get("audio_enabled") is not False
    governed_voiceover_required = bool(dialogue_clean_plate_required and final_audio_requested)
    if dialogue_clean_plate_required:
        delivery_mode = "auto_after_visual_gate" if governed_voiceover_required else "silent_output"
        plan["dialogue_delivery"] = {
            "requested": governed_voiceover_required,
            "status": "voiceover_renderer_required" if governed_voiceover_required else "silent_output_requested",
            "delivery_mode": delivery_mode,
            "auto_finalize": governed_voiceover_required,
            "visual_audio_enabled": False,
            "renderer": None,
            "reason": (
                "joint_h3_dialogue_can_burn_caption_pixels"
                if governed_voiceover_required
                else "operator_requested_silent_output"
            ),
            "visual_strategy": "silent_reaction_plate_v2",
            "native_lip_sync": False,
        }
    if governed_voiceover_required:
        plan["requested_audio_prompt"] = requested_dialogue
    elif dialogue_clean_plate_required:
        plan.pop("requested_audio_prompt", None)
    if dialogue_clean_plate_required:
        plan["audio_prompt"] = ""
        recommended = _dict(plan.get("recommended_params"))
        plan["recommended_params"] = {**recommended, "audio_enabled": False}
        guardrails = {
            **_dict(plan.get("brand_guardrails")),
            "audio_policy": "silent_clean_plate_until_governed_voiceover_renderer",
        }
        plan["brand_guardrails"] = guardrails
        try:
            speaker_count = int(_dict(plan.get("ad_material_contract")).get("speaker_count") or 0)
        except (TypeError, ValueError):
            speaker_count = 0
        voice_scope = "单角色语音" if speaker_count == 1 else "分角色语音" if speaker_count > 1 else "对应语音"
        warning = (
            "实测 H3 联合生成中文对白会烧入可见字幕；当前先生成无字静音人物底片，"
            f"原台词已保留，画面门禁通过后由平台自动使用受控配音器生成{voice_scope}并合成最终成片；"
            "当前安全底片使用闭口反应表演，不把口型同步伪装为已完成；只有实际检测到视频流和音频流后才进入完整审片。"
            if governed_voiceover_required
            else "已保留台词作为人物轮次、动作和情绪依据，但操作员关闭了最终声音；"
            "H3 只生成无字静音人物底片，不调用配音器，也不会把台词文字暴露给画面模型。"
        )
        warnings = [
            item for item in warnings
            if not item.startswith((
                "实测 H3 联合生成中文对白会烧入可见字幕；",
                "已保留台词作为人物轮次、动作和情绪依据",
            ))
        ]
        warnings.append(warning)
        content_format = _text(_dict(plan.get("ad_material_contract")).get("content_format"), 40)
        plan["h3_execution_profile"] = (
            "clean_people_plate_silent_v4"
            if product_overlay_enabled or content_format == "talking_head"
            else "clean_dialogue_plate_silent_v1"
        )
    if suppress_reference_text:
        # Keep the original editable shots and business copy for audit/training,
        # but do not expose literal dialogue or campaign wording to H3's visual
        # generator.  The UI and server both make this audio isolation visible.
        plan["audio_prompt"] = ""
        recommended = _dict(plan.get("recommended_params"))
        plan["recommended_params"] = {**recommended, "audio_enabled": False}
        plan["h3_execution_profile"] = (
            "structure_replay_product_replace_no_source_text_v1"
            if strict_product_replacement
            else "motion_only_no_text_ascii_v3"
        )
    overlay_contract = _dict(plan.get("ad_material_contract"))
    if (
        product_overlay_enabled
        and _text(overlay_contract.get("content_format"), 40) == "product_demo"
        and overlay_contract.get("people_requested") is not True
    ):
        plan["h3_execution_profile"] = "clean_dynamic_product_background_plate_v1"
        recommended = _dict(plan.get("recommended_params"))
        plan["recommended_params"] = {**recommended, "audio_enabled": False}
        plan["audio_prompt"] = ""
    plan["warnings"] = warnings
    if direct_prompt is not None:
        expected = {
            (label, int(index))
            for label, index in _DIRECT_H3_REFERENCE_PLACEHOLDER_RE.findall(
                "\n".join(_reference_lines(_list(plan.get("reference_roles"))))
            )
        }
        actual = {
            (label, int(index))
            for label, index in _DIRECT_H3_REFERENCE_PLACEHOLDER_RE.findall(direct_prompt)
        }
        if actual != expected:
            missing = sorted(f"<{label} {index}>" for label, index in expected - actual)
            unexpected = sorted(f"<{label} {index}>" for label, index in actual - expected)
            raise H3PromptPolicyError(
                "direct_h3_prompt",
                "reference placeholders must exactly match validated source assets"
                f"; missing={missing}; unexpected={unexpected}",
            )
        prompt = direct_prompt
        plan["planning_mode"] = "direct_h3_prompt"
        plan["planning_model"] = None
        plan["prompt_source"] = "operator_direct"
        plan["direct_prompt_validated"] = True
        plan["direct_h3_prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        plan["operator_direct_h3_prompt"] = direct_prompt
        if dialogue_clean_plate_required:
            prompt, transform = _compile_direct_clean_plate_prompt(direct_prompt)
            plan["direct_prompt_transform"] = transform
            plan["prompt_source"] = "operator_direct_deterministic_clean_plate"
            plan["h3_execution_profile"] = "direct_clean_people_plate_silent_v1"
            direct_warning = (
                "直接 H3 模式未调用 DeepSeek；检测到独立台词后，服务端仅做确定性无字静音底片编译，"
                "原始提示词和台词均已完整保留在任务血缘中。"
            )
        else:
            plan.pop("direct_prompt_transform", None)
            plan.setdefault("h3_execution_profile", "direct_h3_prompt_v1")
            direct_warning = "直接 H3 提示词已通过服务端校验并原样送入白名单工作流；未调用 DeepSeek 规划。"
        execution_placeholders = {
            (label, int(index))
            for label, index in _DIRECT_H3_REFERENCE_PLACEHOLDER_RE.findall(prompt)
        }
        if execution_placeholders != expected:
            raise H3PromptPolicyError(
                "direct_h3_prompt",
                "deterministic clean-plate compilation must preserve every validated reference placeholder",
            )
        plan["direct_execution_prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if direct_warning not in warnings:
            warnings.append(direct_warning)
        plan["warnings"] = warnings
    elif product_overlay_enabled:
        prompt = _compile_product_overlay_clean_plate_prompt(plan)
    elif strict_product_replacement:
        plan["source_replication_contract"] = replication_contract
        prompt = _compile_product_replacement_prompt(plan)
    elif dialogue_clean_plate_required and not suppress_reference_text:
        prompt = (
            _compile_product_overlay_clean_plate_prompt(plan, reserve_product_overlay=False)
            if _text(_dict(plan.get("ad_material_contract")).get("content_format"), 40) == "talking_head"
            else _compile_dialogue_clean_plate_prompt(plan)
        )
    else:
        prompt = (
            _compile_no_text_motion_prompt(plan)
            if suppress_reference_text
            else _compile_prompt(plan)
        )
    clean_plate_speech_cue = _CLEAN_PLATE_SPEECH_CUE_RE.search(prompt) if dialogue_clean_plate_required else None
    if clean_plate_speech_cue:
        raise H3PromptPolicyError(
            "integrated_multimodal_description",
            "governed clean plate must not expose speech semantics to the visual model: "
            f"{clean_plate_speech_cue.group(0)}",
        )
    if len(prompt) > H3_PROMPT_MAX_CHARS:
        raise H3PromptPolicyError(
            "integrated_multimodal_description",
            f"compiled prompt has {len(prompt)} characters and exceeds the {H3_PROMPT_MAX_CHARS} character limit",
        )
    plan["integrated_multimodal_description"] = prompt
    plan["video_prompt"] = prompt
    return plan


def planning_contract_description() -> str:
    commands = "、".join(H3_CAMERA_COMMANDS)
    return (
        "使用当前 MiniMax H3 Context-IR 风格拆分。duration_seconds 为 4 到 15 的整数，ratio 默认 9:16。"
        "shots 每项必须包含 start_seconds、end_seconds、framing、camera_command、subject、action、scene、"
        "lighting、mood、audio。camera_command 只能从以下值选择："
        f"{commands}。reference_roles 每项包含 role 和 purpose；role 只能是 first_frame、reference_image、"
        "reference_video、reference_audio。不要把包装、Logo、中文文字交给模型凭空重绘；有真实性要求时"
        "将 h3_mode 设为 image_to_video，并在 brand_guardrails 中要求已审核产品图。"
        "如果用户明确要求无品牌、无包装、无 Logo、无文字或纯抽象画面，则使用 text_to_video，"
        "brand_guardrails.require_reference_image 必须为 false，reference_roles 必须为空。"
        "本地图生视频只允许一个 first_frame；需要多图片、视频或音频时使用 reference_replay。"
        "多个镜头之间默认通过景别、主体尺度或机位变化形成可观察的镜头边界，同时保持主体与运动连续；"
        "如果用户明确要求连续镜头、一镜到底、单镜头或不得硬切，则设置 continuous_take=true，并在同一物理场景中"
        "用连续跟随运动衔接，不得加入硬切、瞬移、人物身份变化或光线重置。"
        "批量生产时平台会追加版本化 production_variant，强制候选在构图、配色、空间母题和转场节奏上"
        "产生可追溯差异，避免仅改种子却重复同一视觉母题。"
    )
