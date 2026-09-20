"""Governed dialogue rendering for Material Workbench video jobs.

The H3 visual pass is intentionally silent because literal Mandarin dialogue
can reappear as burned caption pixels.  This module turns the fitted business
script into an immutable audio artifact and muxes it with that clean plate.
Only a server-side SiliconFlow endpoint, allow-listed model and built-in voices
are accepted; neither API keys nor arbitrary commands are exposed to projects
or Bridge nodes.
"""

from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.common.ai import get_ai_config, redact_secret_text
from app.media.ad_material_parser import parse_dialogue_turns


SPEECH_DELIVERY_RENDERER_ID = "skillforge-siliconflow-cosyvoice2-v4"
SPEECH_DELIVERY_POLICY_VERSION = "material-dialogue-delivery-v8"
SPEECH_DELIVERY_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
SPEECH_TRANSCRIPTION_MODEL = "FunAudioLLM/SenseVoiceSmall"
SPEECH_DELIVERY_ALLOWED_HOSTS = {"api.siliconflow.cn"}
SPEECH_DELIVERY_VOICES = {
    "alex", "benjamin", "charles", "david",
    "anna", "bella", "claire", "diana",
}
SPEECH_DELIVERY_MAX_LINES = 12
SPEECH_DELIVERY_MAX_SCRIPT_CHARS = 1600
SPEECH_DELIVERY_MAX_AUDIO_BYTES = 24 * 1024 * 1024
SPEECH_DELIVERY_STYLE_PROMPT = "自然生活化松弛"
SPEECH_DELIVERY_STYLE_PROMPTS = {
    "relaxed_natural": SPEECH_DELIVERY_STYLE_PROMPT,
    "confidential": "自然生活化轻声克制吐字清晰",
    "urgent_clear": "自然生活化略带急切吐字清晰",
    "restrained_surprise": "自然生活化轻微惊讶吐字清晰",
    "upbeat_confident": "自然生活化轻快自信吐字清晰",
    "restrained_tension": "自然生活化克制不满吐字清晰",
    "calm_informative": "自然生活化沉稳清晰",
}
SPEECH_TRANSCRIPTION_MIN_SIMILARITY = 0.82
SPEECH_TRANSCRIPTION_MIN_COVERAGE = 0.88


class SpeechDeliveryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DialogueLine:
    speaker: str
    speaker_role: str
    text: str
    voice: str
    style_prompt: str = SPEECH_DELIVERY_STYLE_PROMPT


_STAGE_DIRECTION_RE = re.compile(r"[（(]([^（）()]{1,40})[）)]")
_STAGE_DIRECTION_TERMS = (
    "语气", "手势", "动作", "表情", "停顿", "点头", "摇头", "看向", "拿出", "放下",
    "骄傲", "开心", "兴奋", "得意", "生气", "不耐烦", "吃惊", "惊讶", "小声", "轻声",
    "悄悄", "耳语", "笑", "哭",
)


def _strip_stage_directions(value: Any) -> tuple[str, list[str]]:
    """Remove only explicit production directions, never ordinary parentheses."""

    directions: list[str] = []

    def replace(match: re.Match[str]) -> str:
        content = _text(match.group(1), 40)
        if any(term in content for term in _STAGE_DIRECTION_TERMS):
            directions.append(content)
            return ""
        return match.group(0)

    spoken = re.sub(r"\s+", " ", _STAGE_DIRECTION_RE.sub(replace, _text(value, 500))).strip()
    return spoken, directions


def _line_delivery_style(directions: list[str], performance_profile: Any) -> str:
    joined = " ".join(directions)
    if any(term in joined for term in ("小声", "轻声", "悄悄", "耳语")):
        return "confidential"
    if any(term in joined for term in ("急", "快", "抢")):
        return "urgent_clear"
    if any(term in joined for term in ("吃惊", "惊讶")):
        return "restrained_surprise"
    if any(term in joined for term in ("骄傲", "开心", "兴奋", "得意")):
        return "upbeat_confident"
    if any(term in joined for term in ("生气", "不耐烦")):
        return "restrained_tension"
    profile = performance_profile if isinstance(performance_profile, dict) else {}
    requested = _text(profile.get("delivery_style"), 40)
    return requested if requested in SPEECH_DELIVERY_STYLE_PROMPTS else "relaxed_natural"


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _float(value: Any, default: float, lower: float, upper: float) -> float:
    try:
        return min(upper, max(lower, float(value)))
    except (TypeError, ValueError):
        return default


def _speaker_role(label: str, index: int) -> str:
    normalized = label.strip().lower()
    if re.search(r"女|woman|female|girl|s1", normalized, re.I):
        return "female"
    if re.search(r"男|man|male|boy|s2", normalized, re.I):
        return "male"
    # The real front-desk prompt library uses bare ``左边/右边`` labels for
    # two-woman friend dialogues.  Treat that exact unlabeled convention as
    # two distinct female voices; couple scripts already carry explicit 女/男
    # labels and remain gendered above.
    if re.fullmatch(r"(?:左边|右边|左侧|右侧|左|右)(?:[1-9一二三四五六七八九])?", normalized):
        return "female"
    return "female" if index % 2 == 0 else "male"


def parse_dialogue_lines(
    script: Any,
    *,
    female_voice: str,
    male_voice: str,
    female_voice_secondary: str | None = None,
    male_voice_secondary: str | None = None,
    performance_profile: Any = None,
) -> list[DialogueLine]:
    raw = _text(script, SPEECH_DELIVERY_MAX_SCRIPT_CHARS)
    if not raw:
        raise SpeechDeliveryError("SPEECH_SCRIPT_EMPTY", "对白脚本为空")
    structured_turns = parse_dialogue_turns(raw)
    rows = (
        [f"{item['speaker']}：{item['line']}" for item in structured_turns]
        if structured_turns
        else [item.strip() for item in re.split(r"[\r\n]+", raw) if item.strip()]
    )
    lines: list[DialogueLine] = []
    speaker_voices: dict[tuple[str, str], str] = {}
    role_speakers: dict[str, list[str]] = {"female": [], "male": []}
    voice_pools = {
        "female": [female_voice, female_voice_secondary or female_voice],
        "male": [male_voice, male_voice_secondary or male_voice],
    }
    for index, row in enumerate(rows[:SPEECH_DELIVERY_MAX_LINES]):
        match = re.match(r"^([^：:\n]{1,12})[：:]\s*(.+)$", row)
        speaker = _text(match.group(1), 12) if match else f"说话人{index + 1}"
        spoken, directions = _strip_stage_directions(match.group(2) if match else row)
        if not spoken:
            continue
        role = _speaker_role(speaker, index)
        speaker_key = re.sub(r"\s+", "", speaker).lower()
        voice_key = (role, speaker_key)
        if voice_key not in speaker_voices:
            known = role_speakers[role]
            if speaker_key not in known:
                known.append(speaker_key)
            speaker_voices[voice_key] = voice_pools[role][min(known.index(speaker_key), 1)]
        style_key = _line_delivery_style(directions, performance_profile)
        lines.append(DialogueLine(
            speaker,
            role,
            spoken,
            speaker_voices[voice_key],
            SPEECH_DELIVERY_STYLE_PROMPTS[style_key],
        ))
    if not lines:
        raise SpeechDeliveryError("SPEECH_SCRIPT_EMPTY", "对白脚本没有可执行台词")
    return lines


def _validated_voice(value: Any, *, default_name: str) -> str:
    voice = _text(value, 180) or f"{SPEECH_DELIVERY_MODEL}:{default_name}"
    prefix = f"{SPEECH_DELIVERY_MODEL}:"
    if not voice.startswith(prefix) or voice[len(prefix):] not in SPEECH_DELIVERY_VOICES:
        raise SpeechDeliveryError("SPEECH_VOICE_NOT_ALLOWED", "语音音色不在平台白名单内")
    return voice


async def load_speech_delivery_config() -> dict[str, Any]:
    config = await get_ai_config(require_system_config=True)
    explicit_api_base = _text(config.get("ai.speech.api_base"), 500).rstrip("/")
    vision_api_base = _text(config.get("ai.vision.api_base"), 500).rstrip("/")
    api_base = explicit_api_base or vision_api_base
    parsed = urlparse(api_base)
    if parsed.scheme != "https" or parsed.hostname not in SPEECH_DELIVERY_ALLOWED_HOSTS or parsed.path.rstrip("/") != "/v1":
        raise SpeechDeliveryError("SPEECH_ENDPOINT_NOT_ALLOWED", "独立配音仅允许使用平台白名单 SiliconFlow v1 端点")
    explicit_api_key = _text(config.get("ai.speech.api_key"), 2000)
    # Production already uses a dedicated SiliconFlow vision profile.  When
    # speech has no separate credential, safely reuse that same provider
    # account instead of falling back to the global DeepSeek key.  The key
    # never leaves this server-side module and the fallback is only permitted
    # when both profiles resolve to the exact allow-listed SiliconFlow base.
    inherited_vision_key = (
        _text(config.get("ai.vision.api_key"), 2000)
        if api_base == vision_api_base and urlparse(vision_api_base).hostname in SPEECH_DELIVERY_ALLOWED_HOSTS
        else ""
    )
    api_key = explicit_api_key or inherited_vision_key
    configured_default_enabled = bool(api_key and api_base == vision_api_base)
    if not _bool(config.get("ai.speech.enabled"), configured_default_enabled):
        raise SpeechDeliveryError("SPEECH_RENDERER_DISABLED", "平台已明确关闭受控独立配音执行器")
    if not api_key:
        raise SpeechDeliveryError(
            "SPEECH_CONFIG_MISSING",
            "独立配音缺少专用服务端 ai.speech.api_key，且没有可安全继承的 SiliconFlow 平台账号",
        )
    model = _text(config.get("ai.speech.model"), 180) or SPEECH_DELIVERY_MODEL
    if model != SPEECH_DELIVERY_MODEL:
        raise SpeechDeliveryError("SPEECH_MODEL_NOT_ALLOWED", "独立配音模型不在平台白名单内")
    return {
        "api_base": api_base,
        "api_key": api_key,
        "credential_source": "speech_profile" if explicit_api_key else "vision_siliconflow_profile",
        "model": model,
        # Production timing probes on the same allow-listed provider showed
        # ``anna`` stays close to the 4 chars/second planning contract.  The
        # previous ``claire`` default varied from 3.4 to about 1.2 chars/second
        # for short Mandarin ad lines, making a plan marked as 15 seconds turn
        # into more than 30 seconds at delivery time.
        "female_voice": _validated_voice(config.get("ai.speech.female_voice"), default_name="anna"),
        "female_voice_secondary": _validated_voice(
            config.get("ai.speech.female_voice_secondary"), default_name="bella"
        ),
        "male_voice": _validated_voice(config.get("ai.speech.male_voice"), default_name="alex"),
        "male_voice_secondary": _validated_voice(
            config.get("ai.speech.male_voice_secondary"), default_name="benjamin"
        ),
        "speed": _float(config.get("ai.speech.speed"), 1.0, 0.75, 1.25),
        "gain": _float(config.get("ai.speech.gain"), 0.0, -6.0, 6.0),
        "timeout": _float(config.get("ai.speech.timeout"), 120.0, 15.0, 300.0),
        "line_gap_seconds": _float(config.get("ai.speech.line_gap_seconds"), 0.18, 0.08, 0.8),
        "transcription_enabled": _bool(config.get("ai.speech.transcription_enabled"), True),
        "transcription_model": _text(config.get("ai.speech.transcription_model"), 180)
        or SPEECH_TRANSCRIPTION_MODEL,
        "transcription_min_similarity": _float(
            config.get("ai.speech.transcription_min_similarity"),
            SPEECH_TRANSCRIPTION_MIN_SIMILARITY,
            0.6,
            1.0,
        ),
        "transcription_min_coverage": _float(
            config.get("ai.speech.transcription_min_coverage"),
            SPEECH_TRANSCRIPTION_MIN_COVERAGE,
            0.6,
            1.0,
        ),
    }


def _normalize_transcription_text(value: Any) -> str:
    text = re.sub(r"<\|[^|]{1,80}\|>", "", _text(value, 6000))
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]+", text)).casefold()


def _expected_spoken_text(value: Any) -> str:
    """Remove speaker labels before comparing a script with ASR output."""

    raw = _text(value, SPEECH_DELIVERY_MAX_SCRIPT_CHARS)
    turns = parse_dialogue_turns(raw)
    if turns:
        return "".join(_text(item.get("line"), 400) for item in turns)
    rows = [item.strip() for item in re.split(r"[\r\n]+", raw) if item.strip()]
    return "".join(re.sub(r"^[^：:\n]{1,12}[：:]\s*", "", item) for item in rows)


def evaluate_dialogue_transcription(
    transcript: Any,
    expected_script: Any,
    *,
    min_similarity: float = SPEECH_TRANSCRIPTION_MIN_SIMILARITY,
    min_coverage: float = SPEECH_TRANSCRIPTION_MIN_COVERAGE,
) -> dict[str, Any]:
    """Compare provider ASR output with the exact business dialogue.

    Punctuation and SenseVoice language/emotion tags are intentionally ignored,
    while character order remains significant.  This proves the complete muxed
    dialogue instead of treating a non-empty AAC stream as proof of delivery.
    """

    expected = _normalize_transcription_text(_expected_spoken_text(expected_script))
    actual = _normalize_transcription_text(transcript)
    matcher = SequenceMatcher(None, expected, actual, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    similarity = matcher.ratio() if expected and actual else 0.0
    coverage = matched / len(expected) if expected else 0.0
    passed = bool(
        expected
        and actual
        and similarity >= float(min_similarity)
        and coverage >= float(min_coverage)
    )
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "similarity": round(similarity, 4),
        "coverage": round(coverage, 4),
        "expected_chars": len(expected),
        "transcribed_chars": len(actual),
        "minimum_similarity": round(float(min_similarity), 4),
        "minimum_coverage": round(float(min_coverage), 4),
    }


async def _transcribe_dialogue_bytes(
    client: httpx.AsyncClient,
    config: dict[str, Any],
    *,
    audio_content: bytes,
    expected_script: Any,
) -> dict[str, Any]:
    if not config.get("transcription_enabled"):
        raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_DISABLED", "平台已关闭对白转写门禁")
    model = _text(config.get("transcription_model"), 180)
    if model != SPEECH_TRANSCRIPTION_MODEL:
        raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_MODEL_NOT_ALLOWED", "语音识别模型不在平台白名单内")
    if len(audio_content) < 44 or len(audio_content) > SPEECH_DELIVERY_MAX_AUDIO_BYTES:
        raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_AUDIO_INVALID", "待识别对白音频大小不合法")

    response: httpx.Response | None = None
    for attempt in range(2):
        response = await client.post(
            f"{config['api_base']}/audio/transcriptions",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            files={"file": ("governed-dialogue.wav", audio_content, "audio/wav")},
            data={"model": model},
        )
        if response.status_code == 200:
            break
        if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt:
            detail = redact_secret_text(response.text, limit=500) or f"HTTP {response.status_code}"
            raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_FAILED", f"对白转写服务失败：{detail}")
        await asyncio.sleep(1.0)
    try:
        payload = response.json() if response is not None else {}
    except (ValueError, json.JSONDecodeError) as exc:
        raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_INVALID", "对白转写服务未返回合法 JSON") from exc
    transcript = _text(payload.get("text") if isinstance(payload, dict) else "", 6000)
    if not transcript:
        raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_EMPTY", "对白转写结果为空")
    evaluation = evaluate_dialogue_transcription(
        transcript,
        expected_script,
        min_similarity=float(config["transcription_min_similarity"]),
        min_coverage=float(config["transcription_min_coverage"]),
    )
    result = {
        **evaluation,
        "provider": "SiliconFlow",
        "model": model,
        "transcript": transcript,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "trace_id": _text(response.headers.get("x-siliconcloud-trace-id") if response is not None else "", 180)
        or None,
    }
    if not evaluation["passed"]:
        result.update({
            "error_code": "SPEECH_TRANSCRIPTION_MISMATCH",
            "error": (
                f"对白转写与原台词不一致：相似度 {evaluation['similarity']:.0%}，"
                f"覆盖率 {evaluation['coverage']:.0%}"
            ),
        })
    return result


async def transcribe_governed_dialogue(*, audio_path: Path, expected_script: Any) -> dict[str, Any]:
    config = await load_speech_delivery_config()
    try:
        content = audio_path.read_bytes()
    except OSError as exc:
        raise SpeechDeliveryError("SPEECH_TRANSCRIPTION_AUDIO_MISSING", "对白音频文件不可读取") from exc
    timeout = httpx.Timeout(float(config["timeout"]), connect=min(30.0, float(config["timeout"])))
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await _transcribe_dialogue_bytes(
            client,
            config,
            audio_content=content,
            expected_script=expected_script,
        )


async def _render_line(client: httpx.AsyncClient, config: dict[str, Any], line: DialogueLine) -> bytes:
    payload = {
        "model": config["model"],
        "voice": line.voice,
        # Keep the official CosyVoice2 instruction separator, but avoid a long
        # role-specific prose prompt on every line.  Real provider probes show
        # that the old prose prompt changes prosody unpredictably and can more
        # than double Mandarin delivery time.  This compact instruction is the
        # same timing profile used by the workbench's 4 chars/second planner.
        "input": _speech_input_text(line),
        "response_format": "wav",
        "sample_rate": 44100,
        "speed": config["speed"],
        "gain": config["gain"],
        "stream": False,
    }
    response: httpx.Response | None = None
    for attempt in range(2):
        response = await client.post(
            f"{config['api_base']}/audio/speech",
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code == 200:
            break
        if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt:
            detail = redact_secret_text(response.text, limit=500) or f"HTTP {response.status_code}"
            raise SpeechDeliveryError("SPEECH_PROVIDER_FAILED", f"独立配音服务失败：{detail}")
        await asyncio.sleep(1.0)
    content = bytes(response.content if response is not None else b"")
    if len(content) < 44 or len(content) > SPEECH_DELIVERY_MAX_AUDIO_BYTES or content[:4] != b"RIFF":
        raise SpeechDeliveryError("SPEECH_AUDIO_INVALID", "独立配音服务未返回合法 WAV 音频")
    return content


def _speech_input_text(line: DialogueLine) -> str:
    style_prompt = line.style_prompt if line.style_prompt in SPEECH_DELIVERY_STYLE_PROMPTS.values() else SPEECH_DELIVERY_STYLE_PROMPT
    return f"{style_prompt}<|endofprompt|>{line.text}"


def _ffmpeg_executable() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        bundled = Path(str(imageio_ffmpeg.get_ffmpeg_exe() or "")).resolve(strict=True)
        if bundled.is_file():
            return str(bundled)
    except Exception as exc:  # noqa: BLE001
        raise SpeechDeliveryError("SPEECH_MUX_RUNTIME_MISSING", "生产服务缺少受控 ffmpeg 混音运行时") from exc
    raise SpeechDeliveryError("SPEECH_MUX_RUNTIME_MISSING", "生产服务缺少受控 ffmpeg 混音运行时")


def _wave_info(path: Path) -> tuple[int, int, int, int, float]:
    try:
        with wave.open(str(path), "rb") as stream:
            if stream.getcomptype() != "NONE":
                raise SpeechDeliveryError("SPEECH_AUDIO_INVALID", "独立配音 WAV 必须是未压缩 PCM")
            channels = stream.getnchannels()
            sample_width = stream.getsampwidth()
            sample_rate = stream.getframerate()
            # SiliconFlow returns streaming WAV files whose data-chunk header
            # may use 0x7fffffff/0xffffffff as a sentinel.  ``getnframes`` then
            # reports roughly 13.5 hours for a two-second line.  Count the
            # bytes that can actually be decoded instead of trusting that
            # placeholder; this also catches truncated provider responses.
            block_align = max(1, channels * sample_width)
            decoded_bytes = 0
            while True:
                chunk = stream.readframes(max(1, min(sample_rate, 65536)))
                if not chunk:
                    break
                decoded_bytes += len(chunk)
            frames = decoded_bytes // block_align
    except (wave.Error, EOFError) as exc:
        raise SpeechDeliveryError("SPEECH_AUDIO_INVALID", "独立配音 WAV 无法解码") from exc
    if channels not in {1, 2} or sample_width not in {2, 3, 4} or sample_rate not in {16000, 24000, 32000, 44100, 48000}:
        raise SpeechDeliveryError("SPEECH_AUDIO_INVALID", "独立配音 WAV 参数不受支持")
    if frames <= 0:
        raise SpeechDeliveryError("SPEECH_AUDIO_INVALID", "独立配音 WAV 没有可解码的音频帧")
    return channels, sample_width, sample_rate, frames, frames / max(sample_rate, 1)


def _write_silence(path: Path, *, channels: int, sample_width: int, sample_rate: int, duration: float) -> None:
    frames = max(1, int(sample_rate * duration))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\0" * frames * channels * sample_width)


def _normalize_pcm_wav_duration(path: Path, *, duration: float) -> None:
    """Pad or trim a PCM WAV to an exact sample count.

    ffmpeg filter graphs can finish one encoder frame before the requested
    timestamp even when an exact-duration silence input is present.  Review
    annotations and dialogue slots are sample/time based, so normalize the
    intermediate PCM file deterministically before muxing it into MP4.
    """

    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        target_frames = max(1, int(round(sample_rate * duration)))
        content = source.readframes(target_frames)
    expected_bytes = target_frames * channels * sample_width
    if len(content) < expected_bytes:
        content += b"\0" * (expected_bytes - len(content))
    elif len(content) > expected_bytes:
        content = content[:expected_bytes]
    normalized = path.with_name(f"{path.stem}-normalized{path.suffix}")
    with wave.open(str(normalized), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(sample_rate)
        target.writeframes(content)
    normalized.replace(path)


def _run_ffmpeg(command: list[str], *, timeout: int = 180) -> None:
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = redact_secret_text(result.stderr.decode("utf-8", errors="replace"), limit=800)
        raise SpeechDeliveryError("SPEECH_MUX_FAILED", f"独立配音混音失败：{detail or 'ffmpeg error'}")


def _validated_timeline_slots(
    value: Any,
    *,
    line_count: int,
    target_duration_seconds: float,
    line_gap_seconds: float,
) -> list[dict[str, float]]:
    """Return monotonic dialogue slots that are safe to hand to ffmpeg.

    The visual planner already records the intended start/end of every turn.
    Delivery must use that contract instead of concatenating speech at t=0 and
    padding all remaining silence at the end of the video.
    """

    rows = value if isinstance(value, list) else []
    if line_count <= 0 or len(rows) < line_count:
        return []
    slots: list[dict[str, float]] = []
    previous_end = 0.0
    for index, row in enumerate(rows[:line_count]):
        if not isinstance(row, dict):
            return []
        try:
            start = float(row.get("start_seconds"))
            end = float(row.get("end_seconds"))
        except (TypeError, ValueError):
            return []
        if start < -0.001 or end <= start or end > target_duration_seconds + 0.15:
            return []
        if index and start < previous_end - 0.03:
            return []
        reserved_gap = line_gap_seconds if index < line_count - 1 else 0.0
        speech_end = max(start + 0.12, end - reserved_gap)
        if speech_end > end + 0.001:
            return []
        slots.append({"start_seconds": max(0.0, start), "end_seconds": min(end, speech_end)})
        previous_end = end
    return slots


def _rebalance_timeline_slots_for_audio(
    slots: list[dict[str, float]],
    source_durations: list[float],
    *,
    target_duration_seconds: float,
    line_gap_seconds: float,
) -> tuple[list[dict[str, float]], bool]:
    """Move turn boundaries when measured TTS audio cannot fit the AI plan.

    The visual planner estimates speech duration before the provider has
    rendered a voice.  Short turns such as ``开了吗`` can therefore receive a
    sub-second slot even though the provider returns a natural 1.1-1.3 second
    utterance.  Rejecting that otherwise valid delivery wastes the provider
    call and leaves a silent review asset.  Rebalance all turns from the real
    WAV durations while preserving speaker order, a real inter-turn pause and
    a short final reaction beat.  We still fail closed when the complete
    script would require more than the governed 1.35x speed ceiling.
    """

    if not slots or len(slots) != len(source_durations):
        return slots, False
    current_ratios = [
        duration / max(0.12, slot["end_seconds"] - slot["start_seconds"])
        for slot, duration in zip(slots, source_durations, strict=True)
    ]
    if max(current_ratios, default=0.0) <= 1.35:
        return slots, False

    gap = max(0.0, float(line_gap_seconds))
    gap_total = gap * max(0, len(source_durations) - 1)
    voice_budget = max(0.12, float(target_duration_seconds) - gap_total)
    required_speed = sum(source_durations) / voice_budget
    if required_speed > 1.35:
        raise SpeechDeliveryError(
            "SPEECH_SCRIPT_TOO_LONG",
            (
                f"完整对白约 {sum(source_durations):.1f} 秒，超过 {target_duration_seconds:.1f} 秒成片在"
                "自然语速范围内可承载的长度"
            ),
        )

    # Never slow speech down merely to fill the video.  When acceleration is
    # needed, use one common speed so voices remain consistent across turns.
    common_speed = max(1.0, required_speed)
    fitted_durations = [duration / common_speed for duration in source_durations]
    minimum_total = sum(fitted_durations) + gap_total
    slack = max(0.0, float(target_duration_seconds) - minimum_total)
    final_reaction = min(0.5, slack * 0.35)
    distributable = max(0.0, slack - final_reaction)
    extra_gap = distributable / max(1, len(source_durations) - 1) if len(source_durations) > 1 else 0.0
    # Long artificial silences are not useful for a continuous dialogue.  Any
    # remaining slack stays after the final line as a clean reaction beat.
    extra_gap = min(0.6, extra_gap)

    rebalanced: list[dict[str, float]] = []
    cursor = 0.0
    for index, duration in enumerate(fitted_durations):
        start = cursor
        end = min(float(target_duration_seconds), start + duration)
        rebalanced.append({"start_seconds": start, "end_seconds": end})
        cursor = end
        if index < len(fitted_durations) - 1:
            cursor += gap + extra_gap
    if rebalanced and rebalanced[-1]["end_seconds"] > target_duration_seconds + 0.001:
        raise SpeechDeliveryError("SPEECH_SCRIPT_TOO_LONG", "对白重排后超过成片时长")
    return rebalanced, True


def _verify_mux_stream(ffmpeg: str, path: Path, *, selector: str, label: str) -> None:
    """Decode a governed mux stream when ffprobe is not installed."""

    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", selector, "-t", "0.25", "-f", "null", "-",
        ],
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = redact_secret_text(result.stderr.decode("utf-8", errors="replace"), limit=500)
        raise SpeechDeliveryError("SPEECH_FINAL_VALIDATION_FAILED", f"最终成片缺少可解码的{label}：{detail}")


def _assemble_and_mux(
    video_path: Path,
    rendered_lines: list[tuple[DialogueLine, bytes]],
    *,
    target_duration_seconds: float,
    line_gap_seconds: float,
    performance_timeline: Any = None,
) -> tuple[bytes, bytes, dict[str, Any]]:
    if target_duration_seconds < 1 or target_duration_seconds > 60:
        raise SpeechDeliveryError("SPEECH_DURATION_INVALID", "独立配音目标时长不合法")
    ffmpeg = _ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="sf-dialogue-delivery-") as temp_dir:
        root = Path(temp_dir)
        line_paths: list[Path] = []
        wave_specs: list[tuple[int, int, int, int, float]] = []
        for index, (_line, content) in enumerate(rendered_lines):
            path = root / f"line-{index + 1:02d}.wav"
            path.write_bytes(content)
            line_paths.append(path)
            wave_specs.append(_wave_info(path))
        first = wave_specs[0]
        if any(spec[:3] != first[:3] for spec in wave_specs):
            raise SpeechDeliveryError("SPEECH_AUDIO_INVALID", "多角色配音 WAV 参数不一致")
        dialogue_path = root / "dialogue.wav"
        slots = _validated_timeline_slots(
            performance_timeline,
            line_count=len(line_paths),
            target_duration_seconds=target_duration_seconds,
            line_gap_seconds=line_gap_seconds,
        )
        timeline_rebalanced = False
        if slots:
            slots, timeline_rebalanced = _rebalance_timeline_slots_for_audio(
                slots,
                [float(spec[4]) for spec in wave_specs],
                target_duration_seconds=target_duration_seconds,
                line_gap_seconds=line_gap_seconds,
            )
        timeline_lines: list[dict[str, Any]] = []
        if slots:
            command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
            for path in line_paths:
                command.extend(["-i", str(path)])
            timeline_silence_path = root / "timeline-silence.wav"
            _write_silence(
                timeline_silence_path,
                channels=1,
                sample_width=2,
                sample_rate=44100,
                duration=target_duration_seconds,
            )
            command.extend(["-i", str(timeline_silence_path)])
            filters: list[str] = []
            labels: list[str] = []
            for index, (slot, spec) in enumerate(zip(slots, wave_specs, strict=True)):
                source_duration = float(spec[4])
                available = max(0.12, slot["end_seconds"] - slot["start_seconds"])
                ratio = source_duration / available
                if ratio > 1.35:
                    raise SpeechDeliveryError(
                        "SPEECH_SCRIPT_TOO_LONG",
                        f"第 {index + 1} 句对白约 {source_duration:.1f} 秒，超过其 {available:.1f} 秒镜头节拍的自然语速范围",
                    )
                # Do not make a short line unnaturally slow merely to fill its
                # entire visual beat.  A restrained 0.75x floor leaves a real
                # pause before the next speaker while long lines still fit.
                ratio = max(0.75, ratio)
                delay_ms = max(0, int(round(slot["start_seconds"] * 1000)))
                label = f"line{index}"
                filters.append(
                    f"[{index}:a]aresample=44100,atempo={ratio:.6f},adelay={delay_ms}:all=1[{label}]"
                )
                labels.append(f"[{label}]")
                timeline_lines.append({
                    "index": index + 1,
                    "start_seconds": round(slot["start_seconds"], 3),
                    "end_seconds": round(slot["end_seconds"], 3),
                    "source_duration_seconds": round(source_duration, 3),
                    "speed_ratio": round(ratio, 4),
                    "fitted_duration_seconds": round(source_duration / ratio, 3),
                })
            # Keep an explicit silent bed for the complete target duration.  Relying
            # on ``apad`` alone makes some ffmpeg/WAV combinations stop at the last
            # spoken sample, which shifts the later review timeline and drops the
            # intended trailing reaction beat.
            filters.append(f"[{len(line_paths)}:a]aresample=44100[silence]")
            filters.append(
                "".join(labels)
                + "[silence]"
                + f"amix=inputs={len(labels) + 1}:duration=longest:normalize=0,"
                + f"atrim=start=0:end={target_duration_seconds:.3f},asetpts=PTS-STARTPTS[voice]"
            )
            command.extend([
                "-filter_complex", ";".join(filters), "-map", "[voice]",
                "-acodec", "pcm_s16le", "-ar", "44100", "-y", str(dialogue_path),
            ])
            _run_ffmpeg(command)
            _normalize_pcm_wav_duration(dialogue_path, duration=target_duration_seconds)
            source_audio_duration = sum(float(spec[4]) for spec in wave_specs)
            speed_ratio = round(
                sum(float(item["speed_ratio"]) * float(item["source_duration_seconds"]) for item in timeline_lines)
                / max(source_audio_duration, 0.001),
                4,
            )
            audio_filter = "anull"
            timeline_alignment = {
                "mode": "performance_timeline_v1",
                "line_count": len(timeline_lines),
                "planned_end_seconds": round(max(slot["end_seconds"] for slot in slots), 3),
                "audio_rebalanced": timeline_rebalanced,
                "lines": timeline_lines,
            }
        else:
            silence_path = root / "gap.wav"
            _write_silence(
                silence_path,
                channels=first[0],
                sample_width=first[1],
                sample_rate=first[2],
                duration=line_gap_seconds,
            )
            concat_paths: list[Path] = []
            for index, path in enumerate(line_paths):
                if index:
                    concat_paths.append(silence_path)
                concat_paths.append(path)
            manifest = root / "concat.txt"
            manifest.write_text(
                "\n".join(f"file '{path.resolve().as_posix()}'" for path in concat_paths),
                encoding="utf-8",
            )
            _run_ffmpeg([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(manifest),
                "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-y", str(dialogue_path),
            ])
            _channels, _width, _rate, _frames, source_audio_duration = _wave_info(dialogue_path)
            usable_duration = max(0.5, target_duration_seconds - 0.12)
            speed_ratio = max(1.0, source_audio_duration / usable_duration)
            if speed_ratio > 1.35:
                raise SpeechDeliveryError(
                    "SPEECH_SCRIPT_TOO_LONG",
                    f"对白约 {source_audio_duration:.1f} 秒，超过 {target_duration_seconds:.1f} 秒镜头的自然语速范围",
                )
            audio_filter = f"atempo={speed_ratio:.6f},apad=pad_dur={target_duration_seconds:.3f}"
            timeline_alignment = {"mode": "sequential_fallback_v1", "line_count": len(line_paths), "lines": []}
        _channels, _width, _rate, _frames, aligned_audio_duration = _wave_info(dialogue_path)
        final_path = root / "dialogue-delivered.mp4"
        _run_ffmpeg([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-i", str(dialogue_path),
            "-filter_complex", f"[1:a]{audio_filter}[voice]",
            "-map", "0:v:0", "-map", "[voice]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-t", f"{target_duration_seconds:.3f}", "-map_metadata", "-1", "-movflags", "+faststart", "-y", str(final_path),
        ])
        _verify_mux_stream(ffmpeg, final_path, selector="0:v:0", label="视频流")
        _verify_mux_stream(ffmpeg, final_path, selector="0:a:0", label="音频流")
        video_content = final_path.read_bytes()
        dialogue_content = dialogue_path.read_bytes()
        if len(video_content) < 1024 or len(dialogue_content) < 44:
            raise SpeechDeliveryError("SPEECH_MUX_FAILED", "独立配音没有生成有效成片")
        return dialogue_content, video_content, {
            "raw_audio_duration_seconds": round(source_audio_duration, 3),
            "aligned_audio_duration_seconds": round(aligned_audio_duration, 3),
            "target_duration_seconds": round(target_duration_seconds, 3),
            "speed_ratio": round(speed_ratio, 4),
            "line_gap_seconds": round(line_gap_seconds, 3),
            "timeline_alignment": timeline_alignment,
            "verified_streams": ["video", "audio"],
            "video_codec": "source_copy",
            "audio_codec": "aac",
        }


async def render_governed_dialogue(
    *,
    script: Any,
    video_path: Path,
    target_duration_seconds: float,
    performance_timeline: Any = None,
    performance_profile: Any = None,
) -> dict[str, Any]:
    config = await load_speech_delivery_config()
    lines = parse_dialogue_lines(
        script,
        female_voice=config["female_voice"],
        male_voice=config["male_voice"],
        female_voice_secondary=config["female_voice_secondary"],
        male_voice_secondary=config["male_voice_secondary"],
        performance_profile=performance_profile,
    )
    delivery_script = "\n".join(f"{line.speaker}：{line.text}" for line in lines)
    timeout = httpx.Timeout(float(config["timeout"]), connect=min(30.0, float(config["timeout"])))
    rendered: list[tuple[DialogueLine, bytes]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for line in lines:
            rendered.append((line, await _render_line(client, config, line)))
        dialogue_content, video_content, mux = await asyncio.to_thread(
            _assemble_and_mux,
            video_path,
            rendered,
            target_duration_seconds=target_duration_seconds,
            line_gap_seconds=float(config["line_gap_seconds"]),
            performance_timeline=performance_timeline,
        )
        try:
            transcription = await _transcribe_dialogue_bytes(
                client,
                config,
                audio_content=dialogue_content,
                expected_script=delivery_script,
            )
        except SpeechDeliveryError as exc:
            # The governed WAV and mux remain valuable immutable artifacts.
            # Persist them with a closed ASR gate so a later retry can
            # transcribe the same audio instead of paying for TTS again.
            transcription = {
                "status": "failed",
                "passed": False,
                "provider": "SiliconFlow",
                "model": config.get("transcription_model"),
                "error_code": exc.code,
                "error": redact_secret_text(exc, limit=500),
            }
    script_hash = hashlib.sha256(_text(script, SPEECH_DELIVERY_MAX_SCRIPT_CHARS).encode("utf-8")).hexdigest()
    delivery_script_hash = hashlib.sha256(delivery_script.encode("utf-8")).hexdigest()
    return {
        "renderer": SPEECH_DELIVERY_RENDERER_ID,
        "policy_version": SPEECH_DELIVERY_POLICY_VERSION,
        "provider": "SiliconFlow",
        "model": config["model"],
        "credential_source": config["credential_source"],
        "voices": [
            {
                "speaker": line.speaker,
                "role": line.speaker_role,
                "voice": line.voice,
                "style_prompt": line.style_prompt,
            }
            for line in lines
        ],
        "line_count": len(lines),
        "script_sha256": script_hash,
        "delivery_script_sha256": delivery_script_hash,
        "audio_content": dialogue_content,
        "video_content": video_content,
        "audio_sha256": hashlib.sha256(dialogue_content).hexdigest(),
        "video_sha256": hashlib.sha256(video_content).hexdigest(),
        "mux": mux,
        "transcription": transcription,
        "request_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "renderer": SPEECH_DELIVERY_RENDERER_ID,
                    "script_sha256": script_hash,
                    "delivery_script_sha256": delivery_script_hash,
                    "video_sha256": _sha256_file(video_path),
                    "target_duration_seconds": round(target_duration_seconds, 3),
                    "performance_timeline": performance_timeline if isinstance(performance_timeline, list) else [],
                    "performance_profile": performance_profile if isinstance(performance_profile, dict) else {},
                    "voices": [line.voice for line in lines],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }
