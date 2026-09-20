"""
统一 LLM 调用封装（OpenAI 兼容接口，直连 DeepSeek/GLM 等，无需代理）。
所有 AI 功能通过此模块调用，确保一致的错误处理、缓存和降级。
配置优先从 system_config DB 表读取（admin/settings 页面管理），
fallback 到 .env 环境变量。
对要求只使用后台配置的调用，传入 require_system_config=True。
"""

import json
import re
from typing import Any, AsyncGenerator

import httpx
from loguru import logger

from app.common.cache import cache_get, cache_set
from app.config import settings


# ═══════════════════════════════════════════════════════
# [M3] 自定义异常类 — 替代下游字符串匹配 "rate" / "quota"
# 上游 (call_llm) 根据 HTTP status code 直接 raise 这些类,
# 下游用 isinstance 精确判断, 避免代理库改 typo 时漏检
# ═══════════════════════════════════════════════════════

class LLMError(Exception):
    """LLM 调用所有自定义异常的基类。"""
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMRateLimitError(LLMError):
    """HTTP 429 — 限流, 应立即熔断不重试。"""


class LLMQuotaExceededError(LLMError):
    """HTTP 402 / 余额不足 — 配额耗尽, 立即熔断。"""


class LLMAuthError(LLMError):
    """HTTP 401/403 — 鉴权失败, 立即熔断不重试。"""


_SECRET_TEXT_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.I | re.S), False),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:\s/@]+:)[^@\s/]+(@)"), True),
    (re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?\s*(?:bearer|basic|token|apikey)\s+)[^\s,;\"'}]+"), True),
    (re.compile(r"(?i)(bearer\s+)[^\s,;\"'}]+"), True),
    (
        re.compile(
            r"(?i)(\"?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|private[_-]?key|ssh[_-]?key|signing[_-]?key|secret[_-]?key|"
            r"service[_-]?account[_-]?key|session[_-]?id|token|secret|password|"
            r"credential|cookie)\"?\s*[:=]\s*[\"']?)[^\s,;\"'}]+"
        ),
        True,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b"), False),
)


def redact_secret_text(value: object, *, limit: int = 200) -> str:
    """返回可写日志/前端错误摘要的脱敏文本。"""
    text = str(value or "")
    for pattern, keep_prefix in _SECRET_TEXT_PATTERNS:
        text = pattern.sub(
            lambda match: (
                f"{match.group(1)}[REDACTED]{match.group(2) if match.lastindex and match.lastindex >= 2 else ''}"
                if keep_prefix else "[REDACTED]"
            ),
            text,
        )
    return text[:limit]


# 全局 AI 配置缓存（避免每次调用都查 DB）。按是否允许 .env fallback、
# 是否已做必填校验分开缓存，避免要求后台配置的调用误用普通 fallback 缓存。
_ai_config_cache: dict[tuple[bool, bool], dict] = {}
_ai_config_ts: dict[tuple[bool, bool], float] = {}
_AI_PROFILE_FIELDS = ("api_base", "api_key", "model", "max_tokens", "temperature", "timeout")
_SILICONFLOW_API_BASE = "https://api.siliconflow.cn/v1"
_SILICONFLOW_DEFAULT_MODELS = {
    "text": "Qwen/Qwen3-32B",
    "cheap": "Qwen/Qwen3-8B",
    "vision": "Qwen/Qwen3-VL-32B-Instruct",
}

# Coding Agent 配置独立缓存
_coding_agent_config_cache: dict | None = None
_coding_agent_config_ts: float = 0


def _ai_required_fields_missing(config: dict) -> bool:
    return (
        not config.get("ai.api_base")
        or not config.get("ai.api_key")
        or not config.get("ai.model")
    )


def _ai_provider(config: dict) -> str:
    provider = str(config.get("ai.provider") or "custom").strip().lower()
    return provider or "custom"


def _provider_default_model(profile: str = "") -> str:
    profile = str(profile or "").strip().lower().replace("_", "-")
    if profile in {"", "default", "global", "main", "text"}:
        return _SILICONFLOW_DEFAULT_MODELS["text"]
    return _SILICONFLOW_DEFAULT_MODELS.get(profile, _SILICONFLOW_DEFAULT_MODELS["text"])


def _apply_ai_provider_defaults(config: dict, *, explicit_keys: set[str] | None = None) -> dict:
    merged = dict(config)
    if _ai_provider(merged) != "siliconflow":
        return merged

    def should_fill(key: str) -> bool:
        if not merged.get(key):
            return True
        return explicit_keys is not None and key not in explicit_keys

    if should_fill("ai.api_base"):
        merged["ai.api_base"] = _SILICONFLOW_API_BASE
    if should_fill("ai.model"):
        merged["ai.model"] = _SILICONFLOW_DEFAULT_MODELS["text"]

    for profile in ("cheap", "vision"):
        prefix = f"ai.{profile}."
        if should_fill(f"{prefix}api_base"):
            merged[f"{prefix}api_base"] = merged.get("ai.api_base") or _SILICONFLOW_API_BASE
        if should_fill(f"{prefix}model"):
            merged[f"{prefix}model"] = _SILICONFLOW_DEFAULT_MODELS[profile]
    return merged


def _ai_required_config_message(profile: str = "") -> str:
    profile = str(profile or "").strip().lower().replace("_", "-")
    if profile and profile not in {"default", "global", "main"}:
        return (
            f"AI 后台配置缺失：请在后台配置 ai.{profile}.api_base、"
            f"ai.{profile}.api_key 和 ai.{profile}.model，或配置全局 ai.* 供该模型档位继承"
        )
    return "AI 后台配置缺失：请在后台配置 ai.api_base、ai.api_key 和 ai.model"


async def _load_ai_config(*, require_system_config: bool = False, validate_required_config: bool = True) -> dict:
    """
    从 system_config 表读取 AI 配置，缓存 60 秒。
    validate_required_config=False 用于 profile 先合并 ai.<profile>.* 再校验。
    """
    import time
    global _ai_config_cache, _ai_config_ts

    now = time.time()
    cache_key = (bool(require_system_config), bool(validate_required_config))
    if cache_key in _ai_config_cache and (now - _ai_config_ts.get(cache_key, 0)) < 60:
        return _ai_config_cache[cache_key]

    env_defaults = _apply_ai_provider_defaults({
        "ai.provider": "custom",
        "ai.api_base": settings.AI_API_BASE,
        "ai.api_key": settings.AI_API_KEY,
        "ai.model": settings.AI_DEFAULT_MODEL,
        "ai.max_tokens": 1000,
        "ai.temperature": 0.3,
        "ai.timeout": 30,
        "ai.cheap.api_base": settings.AI_API_BASE,
        "ai.cheap.api_key": settings.AI_API_KEY,
        "ai.cheap.model": getattr(settings, "AI_CHEAP_MODEL", "") or "deepseek-chat",
        "ai.cheap.max_tokens": 2000,
        "ai.cheap.temperature": 0.2,
        "ai.cheap.timeout": 30,
        "ai.code.api_base": settings.AI_API_BASE,
        "ai.code.api_key": settings.AI_API_KEY,
        "ai.code.model": settings.AI_DEFAULT_MODEL,
        "ai.code.max_tokens": 8192,
        "ai.code.temperature": 0.2,
        "ai.code.timeout": 60,
        "ai.vision.api_base": settings.AI_API_BASE,
        "ai.vision.api_key": settings.AI_API_KEY,
        "ai.vision.model": "",
        "ai.vision.max_tokens": 4096,
        "ai.vision.temperature": 0.2,
        "ai.vision.timeout": 120,
    })
    system_defaults = _apply_ai_provider_defaults({
        "ai.provider": "custom",
        "ai.api_base": "",
        "ai.api_key": "",
        "ai.model": "",
        "ai.max_tokens": 1000,
        "ai.temperature": 0.3,
        "ai.timeout": 30,
        "ai.cheap.api_base": "",
        "ai.cheap.api_key": "",
        "ai.cheap.model": "deepseek-chat",
        "ai.cheap.max_tokens": 2000,
        "ai.cheap.temperature": 0.2,
        "ai.cheap.timeout": 30,
        "ai.code.api_base": "",
        "ai.code.api_key": "",
        "ai.code.model": "",
        "ai.code.max_tokens": 8192,
        "ai.code.temperature": 0.2,
        "ai.code.timeout": 60,
        "ai.vision.api_base": "",
        "ai.vision.api_key": "",
        "ai.vision.model": "Qwen/Qwen3-VL-32B-Instruct",
        "ai.vision.max_tokens": 4096,
        "ai.vision.temperature": 0.2,
        "ai.vision.timeout": 120,
    })
    defaults = system_defaults if require_system_config else env_defaults

    try:
        from sqlalchemy import select
        from app.common.models import SystemConfig
        from app.database import async_session_factory
        async with async_session_factory() as db:
            result = await db.execute(
                select(SystemConfig).where(SystemConfig.key.like("ai.%"))
            )
            rows = result.scalars().all()
        config = dict(defaults)
        explicit_keys: set[str] = set()
        for row in rows:
            config[row.key] = row.value
            explicit_keys.add(row.key)
        config = _apply_ai_provider_defaults(config, explicit_keys=explicit_keys)
        if require_system_config and validate_required_config and _ai_required_fields_missing(config):
            raise LLMAuthError(_ai_required_config_message())
        _ai_config_cache[cache_key] = config
        _ai_config_ts[cache_key] = now
        return config
    except Exception:
        if require_system_config:
            raise
        # DB 不可用时用 .env 默认值
        return defaults


async def get_ai_config(*, require_system_config: bool = False) -> dict:
    """
    从 system_config 表读取全局 AI 配置，缓存 60 秒。
    key 前缀 ai.*。require_system_config=True 时只使用后台配置，不 fallback 到 settings（.env）。
    """
    return await _load_ai_config(
        require_system_config=require_system_config,
        validate_required_config=True,
    )


def _apply_ai_profile(config: dict, model_profile: str = "") -> dict:
    """Map profile-scoped keys (e.g. ai.cheap.*) onto the standard ai.* shape."""
    profile = str(model_profile or "").strip().lower().replace("_", "-")
    if not profile or profile in {"default", "global", "main"}:
        return dict(config)
    if not re.fullmatch(r"[a-z0-9-]{1,32}", profile):
        return dict(config)
    scoped_prefix = f"ai.{profile}."
    merged = dict(config)
    for field in _AI_PROFILE_FIELDS:
        scoped_key = f"{scoped_prefix}{field}"
        target_key = f"ai.{field}"
        value = config.get(scoped_key)
        if value not in (None, ""):
            merged[target_key] = value
        elif field in {"api_base", "api_key"} and config.get(target_key) not in (None, ""):
            merged[target_key] = config.get(target_key)
        elif field == "model" and _ai_provider(config) == "siliconflow":
            merged[target_key] = _provider_default_model(profile)
    return _apply_ai_provider_defaults(merged)


async def get_ai_profile_config(*, model_profile: str = "", require_system_config: bool = False) -> dict:
    """Read AI config and apply a server-side profile such as ``ai.cheap.*``."""
    profile = str(model_profile or "").strip().lower().replace("_", "-")
    if not profile or profile in {"default", "global", "main"}:
        return await get_ai_config(require_system_config=require_system_config)
    config = _apply_ai_profile(
        await _load_ai_config(
            require_system_config=require_system_config,
            validate_required_config=False,
        ),
        model_profile=profile,
    )
    if require_system_config and _ai_required_fields_missing(config):
        raise LLMAuthError(_ai_required_config_message(profile))
    return config


async def get_coding_agent_config() -> dict:
    """
    读取 AI 编程助手专用配置，缓存 60 秒。
    key 前缀 coding_agent.* ，fallback 链：DB → .env → 全局 ai.* 默认值。

    任何字段缺失时落到 ai.* 全局值，方便管理员先用全局 AI 配置快速跑通。
    """
    import time
    global _coding_agent_config_cache, _coding_agent_config_ts

    now = time.time()
    if _coding_agent_config_cache and (now - _coding_agent_config_ts) < 60:
        return _coding_agent_config_cache

    # 默认值：先用全局 ai 配置 fallback，确保即使没单独配也能跑
    ai_cfg = await get_ai_config()
    defaults = {
        "coding_agent.provider": "tencent",  # tencent / glm / kimi / "" (空时走 ANTHROPIC_* 通用)
        "coding_agent.api_base": "",  # 空时由 provider 决定默认 base_url
        "coding_agent.api_key": "",
        "coding_agent.model": "glm-5",  # 推荐 GLM-5 (腾讯云 coding plan, 2.7s 响应)
        "coding_agent.max_tokens": 8192,
        "coding_agent.permission_strategy": "balanced",  # strict / balanced / loose
        "coding_agent.bare_mode": True,  # 禁用 hooks/plugin/team memory 等上游隐式状态
        "coding_agent.enabled": False,
    }

    try:
        from sqlalchemy import select
        from app.common.models import SystemConfig
        from app.database import async_session_factory
        async with async_session_factory() as db:
            result = await db.execute(
                select(SystemConfig).where(SystemConfig.key.like("coding_agent.%"))
            )
            rows = result.scalars().all()
        config = dict(defaults)
        for row in rows:
            config[row.key] = row.value
        config = _apply_ai_provider_defaults(config)
        _coding_agent_config_cache = config
        _coding_agent_config_ts = now
        return config
    except Exception:
        return defaults


def invalidate_coding_agent_config_cache() -> None:
    """配置变更后立即失效缓存（无需等 60s）。"""
    global _coding_agent_config_cache, _coding_agent_config_ts
    _coding_agent_config_cache = None
    _coding_agent_config_ts = 0


def invalidate_ai_config_cache() -> None:
    """全局 ai.* 配置变更后立即失效缓存。"""
    global _ai_config_cache, _ai_config_ts
    _ai_config_cache = {}
    _ai_config_ts = {}


def _strip_code_fences(text: str) -> str:
    """去除 LLM 返回的 markdown 代码围栏"""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.index("\n") if "\n" in text else 3
        text = text[first_nl + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _extract_chat_content(data: dict[str, Any]) -> str:
    """Extract OpenAI-compatible message content across provider variants."""
    try:
        message = data.get("choices", [])[0].get("message", {})
    except (AttributeError, IndexError):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    for key in ("reasoning_content", "reasoning", "thought"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _model_uses_qwen_thinking(model: str) -> bool:
    normalized = str(model or "").lower()
    return any(marker in normalized for marker in ("qwen/qwen3", "qwen3", "qwq"))


def _apply_provider_request_options(payload: dict[str, Any], *, model: str) -> None:
    # SiliconFlow/Qwen3 defaults may put useful text into reasoning tokens and leave
    # content empty. Force non-thinking mode for deterministic platform analysis.
    if _model_uses_qwen_thinking(model):
        payload.setdefault("enable_thinking", False)
    # DeepSeek V4 enables thinking by default. SkillForge's common structured
    # analysis path expects the final answer in ``message.content`` and uses
    # JSON mode extensively, so request the official non-thinking mode unless
    # a future caller explicitly supplies a mode. This also preserves the
    # historical ``deepseek-chat`` (V4 Flash non-thinking) latency contract.
    if str(model or "").strip().lower() in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        payload.setdefault("thinking", {"type": "disabled"})


async def call_llm(
    system: str,
    user: str,
    *,
    max_tokens: int = 0,
    temperature: float = 0,
    timeout: int = 0,
    json_mode: bool = True,
    call_source: str = "unknown",
    cost_context: dict | None = None,
    model_override: str = "",
    model_profile: str = "",
    require_system_config: bool = False,
) -> dict | str | None:
    """
    统一调用 AI 模型，返回解析后的 dict（json_mode=True）
    或原始文本（json_mode=False）。失败返回 None。
    配置从 system_config 表读取；require_system_config=True 时不 fallback 到 .env。
    参数传 0 表示使用配置值。

    F4 成本追踪：
        call_source: 调用来源标记，如 "agent_chat" / "verifier" / "reviewer"
        cost_context: 可选 dict，包含 user_id / department / skill_id /
                      conversation_id / prompt_hash 等用于成本归因

    model_override: 显式指定使用哪个模型（v7 D4 跨模型 check 用）
    """
    import time
    config = await get_ai_profile_config(
        model_profile=model_profile,
        require_system_config=require_system_config,
    )
    api_base = str(config["ai.api_base"]).rstrip("/")
    api_key = str(config.get("ai.api_key", ""))
    model = model_override or str(config["ai.model"])
    max_tokens = max_tokens or int(config.get("ai.max_tokens", 2000))
    temperature = temperature or float(config.get("ai.temperature", 0.3))
    timeout = timeout or int(config.get("ai.timeout", 30))

    url = f"{api_base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    _apply_provider_request_options(payload, model=model)

    start_ts = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)

        duration_ms = int((time.monotonic() - start_ts) * 1000)

        # [M3] HTTP status code 精确分类 — 替代下游字符串匹配
        if resp.status_code == 429:
            safe_text = redact_secret_text(resp.text)
            logger.warning("LLM 限流 (429): {}", safe_text)
            raise LLMRateLimitError(f"rate limited: {safe_text}", status_code=429)
        if resp.status_code == 402:
            safe_text = redact_secret_text(resp.text)
            logger.warning("LLM 配额耗尽 (402): {}", safe_text)
            raise LLMQuotaExceededError(f"quota exceeded: {safe_text}", status_code=402)
        if resp.status_code in (401, 403):
            safe_text = redact_secret_text(resp.text)
            logger.warning("LLM 鉴权失败 ({}): {}", resp.status_code, safe_text)
            raise LLMAuthError(f"auth failed: {safe_text}", status_code=resp.status_code)

        if resp.status_code >= 500:
            from app.common.retry import RetryableError
            raise RetryableError(f"LLM 服务端错误 ({resp.status_code}): {redact_secret_text(resp.text)}")
        if resp.status_code != 200:
            logger.warning("LLM 返回 {}: {}", resp.status_code, redact_secret_text(resp.text))
            return None

        data = resp.json()
        content = _extract_chat_content(data)

        # F4: 成本追踪（不阻塞主流程）
        await _record_usage(model, data.get("usage", {}), call_source, cost_context, duration_ms)

        if not json_mode:
            return content

        cleaned = _strip_code_fences(content)
        return json.loads(cleaned)

    except (LLMRateLimitError, LLMQuotaExceededError, LLMAuthError):
        # 这三个分类是给上层判断的, 让它们冒泡到调用方
        raise
    except json.JSONDecodeError as e:
        logger.warning("AI 返回非 JSON: {}", e)
        return None
    except httpx.TimeoutException:
        from app.common.retry import RetryableError
        raise RetryableError(f"LLM 调用超时 ({timeout}s)")
    except Exception as e:
        from app.common.retry import RetryableError
        if isinstance(e, RetryableError):
            raise
        logger.error("LLM 调用失败: {}", redact_secret_text(e))
        return None


async def call_llm_multimodal(
    system: str,
    content_parts: list[dict[str, Any]],
    *,
    max_tokens: int = 0,
    temperature: float = 0,
    timeout: int = 0,
    json_mode: bool = True,
    call_source: str = "unknown",
    cost_context: dict | None = None,
    model_override: str = "",
    model_profile: str = "",
    require_system_config: bool = False,
) -> dict | str | None:
    """OpenAI-compatible multimodal chat completion wrapper.

    `content_parts` follows the OpenAI Chat Completions content-list shape,
    e.g. [{"type": "text", "text": "..."}, {"type": "image_url", ...}].
    The API key is always read server-side from ai.* SystemConfig/.env.
    """
    import time

    if not isinstance(content_parts, list) or not content_parts:
        raise ValueError("content_parts must be a non-empty list")

    config = await get_ai_profile_config(
        model_profile=model_profile,
        require_system_config=require_system_config,
    )
    api_base = str(config["ai.api_base"]).rstrip("/")
    api_key = str(config.get("ai.api_key", ""))
    model = model_override or str(config["ai.model"])
    max_tokens = max_tokens or int(config.get("ai.max_tokens", 2000))
    temperature = temperature or float(config.get("ai.temperature", 0.3))
    timeout = timeout or int(config.get("ai.timeout", 30))

    url = f"{api_base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content_parts},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    _apply_provider_request_options(payload, model=model)

    start_ts = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)

        duration_ms = int((time.monotonic() - start_ts) * 1000)
        if resp.status_code == 429:
            safe_text = redact_secret_text(resp.text)
            logger.warning("多模态 LLM 限流 (429): {}", safe_text)
            raise LLMRateLimitError(f"rate limited: {safe_text}", status_code=429)
        if resp.status_code == 402:
            safe_text = redact_secret_text(resp.text)
            logger.warning("多模态 LLM 配额耗尽 (402): {}", safe_text)
            raise LLMQuotaExceededError(f"quota exceeded: {safe_text}", status_code=402)
        if resp.status_code in (401, 403):
            safe_text = redact_secret_text(resp.text)
            logger.warning("多模态 LLM 鉴权失败 ({}): {}", resp.status_code, safe_text)
            raise LLMAuthError(f"auth failed: {safe_text}", status_code=resp.status_code)
        if resp.status_code >= 500:
            from app.common.retry import RetryableError

            raise RetryableError(f"LLM 服务端错误 ({resp.status_code}): {redact_secret_text(resp.text)}")
        if resp.status_code != 200:
            logger.warning("多模态 LLM 返回 {}: {}", resp.status_code, redact_secret_text(resp.text))
            return None

        data = resp.json()
        content = _extract_chat_content(data)
        await _record_usage(model, data.get("usage", {}), call_source, cost_context, duration_ms)
        if not json_mode:
            return content
        cleaned = _strip_code_fences(content)
        return json.loads(cleaned)

    except (LLMRateLimitError, LLMQuotaExceededError, LLMAuthError):
        raise
    except json.JSONDecodeError as e:
        logger.warning("多模态 AI 返回非 JSON: {}", e)
        return None
    except httpx.TimeoutException:
        from app.common.retry import RetryableError

        raise RetryableError(f"LLM 调用超时 ({timeout}s)")
    except Exception as e:
        from app.common.retry import RetryableError

        if isinstance(e, RetryableError):
            raise
        logger.error("多模态 LLM 调用失败: {}", redact_secret_text(e))
        return None


async def _record_usage(
    model: str,
    usage: dict,
    call_source: str,
    cost_context: dict | None,
    duration_ms: int,
) -> None:
    """F4 内部辅助：把模型响应的 usage 写入 cost_tracker"""
    try:
        from app.common.cost_tracker import cost_tracker, UsageEvent
        ctx = cost_context or {}
        # OpenAI 兼容字段：prompt_tokens / completion_tokens / total_tokens
        # 部分后端有 prompt_tokens_details.cached_tokens 表示 cache_read
        cache_read = 0
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cache_read = details.get("cached_tokens", 0)

        event = UsageEvent(
            user_id=ctx.get("user_id"),
            department=ctx.get("department"),
            skill_id=ctx.get("skill_id"),
            conversation_id=ctx.get("conversation_id"),
            call_source=call_source or "unknown",
            model=model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cache_read_tokens=cache_read,
            cache_write_tokens=0,  # OpenAI 兼容协议无此字段
            prompt_hash=ctx.get("prompt_hash"),
            duration_ms=duration_ms,
        )
        await cost_tracker.record(event)
    except Exception as e:
        # 成本追踪失败绝不影响主流程
        logger.warning(f"成本追踪失败 (call_source={call_source}): {e}")


async def call_llm_with_retry(
    system: str,
    user: str,
    *,
    max_retries: int = 2,
    json_mode: bool = True,
    retry_on_parse_error: bool = True,
    description: str = "llm",
    **kwargs,
) -> dict | str | None:
    """v2.8.1 C5：`call_llm` 的 retry 封装。

    处理三类失败：
    1. **RetryableError**（5xx / 超时）→ 指数退避 + 抖动，最多 `max_retries` 次
    2. **LLMRateLimitError**（429）→ 作为可重试异常处理（退避给下游时间）
    3. **JSON 解析失败** → `json_mode=True` 且 `retry_on_parse_error=True` 时
       重试一次，追加 "Please return ONLY valid JSON" 提示

    返回：
    - 正常：`dict`（json_mode=True）或 `str`（json_mode=False）
    - 彻底失败：`None`（调用方自行兜底）

    **注意**：`LLMAuthError` / `LLMQuotaExceededError` 不重试，直接冒泡让调用方感知。
    """
    from app.common.retry import RetryableError, retry_with_backoff

    async def _call(extra_hint: str = "") -> dict | str | None:
        effective_user = f"{user}\n\n{extra_hint}" if extra_hint else user
        return await call_llm(system, effective_user, json_mode=json_mode, **kwargs)

    # Retry 5xx / 超时（LLMRateLimitError 也当作可重试）
    try:
        result = await retry_with_backoff(
            _call,
            max_retries=max_retries,
            retryable_exceptions=(RetryableError, LLMRateLimitError),
            description=description,
        )
    except (LLMAuthError, LLMQuotaExceededError):
        raise  # 认证 / 配额不重试，让调用方看到明确错误
    except Exception as exc:  # noqa: BLE001
        logger.error("{} retry 耗尽: {}", description, exc)
        return None

    # JSON 模式下，LLM 返回 None（通常是解析失败）→ 加提示再试一次
    if result is None and json_mode and retry_on_parse_error:
        logger.info("{} 首次返回 None（JSON 解析失败？），带提示重试", description)
        try:
            result = await _call(
                "IMPORTANT: Your previous response was not valid JSON. "
                "Please return ONLY a valid JSON object, no prose, no code fences."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("{} 提示重试失败: {}", description, exc)
            return None

    return result


async def call_llm_cached(
    cache_key: str,
    system: str,
    user: str,
    *,
    cache_ttl: int = 600,
    **kwargs,
) -> dict | None:
    """
    带 Redis 缓存的 LLM 调用。缓存命中直接返回，未命中则调用后写入缓存。
    仅缓存成功结果（非 None）。
    """
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    result = await call_llm(system, user, **kwargs)

    if result is not None:
        await cache_set(cache_key, result, ttl=cache_ttl)

    return result


async def call_llm_stream(
    system: str = "",
    user: str = "",
    *,
    messages: list[dict] | None = None,
    max_tokens: int = 0,
    temperature: float = 0,
    timeout: int = 120,
    call_source: str = "unknown",
    cost_context: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """
    流式 LLM 调用（OpenAI 兼容 SSE 协议）。

    yield 一系列事件 dict：
        {"type": "delta",   "content": "片段文本"}     - 增量内容
        {"type": "usage",   "input_tokens": 100, ...}   - 最终 usage（部分后端支持）
        {"type": "done",    "finish_reason": "stop"}    - 正常结束
        {"type": "error",   "error": "错误消息"}        - 异常

    可选参数：
        messages: 完整的 messages 列表（优先级高于 system+user）
        max_tokens / temperature / timeout: 0 表示使用配置默认值

    使用方式：
        async for event in call_llm_stream(messages=msgs):
            if event["type"] == "delta":
                ws.send_text(event["content"])
            elif event["type"] == "done":
                break
            elif event["type"] == "error":
                ...
    """
    config = await get_ai_config()
    api_base = str(config["ai.api_base"]).rstrip("/")
    api_key = str(config.get("ai.api_key", ""))
    model = str(config["ai.model"])
    max_tokens = max_tokens or int(config.get("ai.max_tokens", 2000))
    temperature = temperature or float(config.get("ai.temperature", 0.3))

    url = f"{api_base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    msgs = messages if messages is not None else [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        # 大部分 OpenAI 兼容后端支持 stream_options.include_usage 在最后一条 chunk 返回 usage
        "stream_options": {"include_usage": True},
    }

    import time
    start_ts = time.monotonic()
    captured_usage: dict = {}  # F4: 累计 usage 用于成本追踪

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    detail = redact_secret_text(body.decode("utf-8", "ignore"), limit=500)
                    logger.warning("LLM 流式返回 {}: {}", resp.status_code, detail)
                    yield {"type": "error", "error": f"HTTP {resp.status_code}: {detail}"}
                    return

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        yield {"type": "done", "finish_reason": "stop"}
                        # F4: 记录成本
                        await _record_usage(
                            model, captured_usage, call_source, cost_context,
                            int((time.monotonic() - start_ts) * 1000),
                        )
                        return
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        # 跳过格式异常的行（不致命）
                        continue

                    # choices[0].delta.content
                    choices = data.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta", {}) or {}
                        content = delta.get("content")
                        if content:
                            yield {"type": "delta", "content": content}
                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason:
                            # 不立即 return，可能后面还有 usage chunk
                            yield {"type": "done", "finish_reason": finish_reason}

                    # 最终 usage（OpenAI 规范：在最后一条 chunk）
                    usage = data.get("usage")
                    if usage:
                        captured_usage = usage  # F4: 保存供成本追踪用
                        yield {
                            "type": "usage",
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                            # 部分后端透传 cache 字段
                            "cache_read_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                            if isinstance(usage.get("prompt_tokens_details"), dict)
                            else 0,
                        }
        # 流正常结束（无 [DONE]）也要记录成本
        if captured_usage:
            await _record_usage(
                model, captured_usage, call_source, cost_context,
                int((time.monotonic() - start_ts) * 1000),
            )

    except httpx.TimeoutException:
        logger.warning("LLM 流式调用超时 ({}s)", timeout)
        yield {"type": "error", "error": f"timeout after {timeout}s"}
    except httpx.ConnectError as e:
        safe_error = redact_secret_text(e)
        logger.error("无法连接 LLM 流: {}", safe_error)
        yield {"type": "error", "error": f"connect error: {safe_error}"}
    except Exception as e:
        safe_error = redact_secret_text(e)
        logger.error("LLM 流式调用异常: {}", safe_error)
        yield {"type": "error", "error": safe_error}
