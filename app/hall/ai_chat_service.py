"""Durable, user-isolated multi-model chat for the Hall ``ai-chat`` Skill.

The Skill is the versioned Hall descriptor.  This module is the native runtime:
credentials never enter the Skill checkout, browser payloads, or audit details.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import time
import uuid
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from fastapi import HTTPException, UploadFile
from loguru import logger
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import redact_secret_text
from app.common.audit import audit
from app.common.time_utils import isoformat_bjt, now_bjt
from app.database import async_session_factory
from app.hall.models import AIChatAttachment, AIChatMessage, AIChatThread, AIChatUserPreference


CAPABILITY_ID = "ai-chat"
DEFAULT_MODEL = "deepseek-v4-pro"
FASTAITOKEN_BASE_URL = os.environ.get("FASTAITOKEN_BASE_URL", "https://www.fastaitoken.com/v1").rstrip("/")
# Sent images are part of the permanent conversation record. Keep the default
# under the application's persistent data tree instead of an OS temp folder;
# production may mount a dedicated volume through the environment override.
ATTACHMENT_ROOT = Path(os.environ.get("SKILLFORGE_AI_CHAT_UPLOAD_DIR", "data/ai-chat-assets"))
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_MESSAGE_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 4
MAX_MESSAGE_CHARS = 32_000
THREAD_PAGE_SIZE = 30
MESSAGE_PAGE_SIZE = 50
MAX_CONTEXT_FETCH_MESSAGES = 400
MAX_CONTEXT_TURNS = 80
SUMMARY_TRIGGER_MESSAGES = 40
SUMMARY_BATCH_MESSAGES = 20
STREAM_LEASE_TTL_SECONDS = 900
STREAM_STALE_AFTER_SECONDS = STREAM_LEASE_TTL_SECONDS + 300
STREAM_HEARTBEAT_SECONDS = 15
STREAM_TOTAL_TIMEOUT_SECONDS = 600
UPSTREAM_READ_TIMEOUT_SECONDS = 180
MAX_USER_STREAMS = 2
MODEL_LIMIT_POLICY_VERSION = "ai-chat-context-v3"

MODEL_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "domestic",
        "name": "国产模型",
        "key_env": "FASTAITOKEN_DOMESTIC_API_KEY",
        "models": ("deepseek-v4-pro", "glm-5.2", "kimi-k3"),
    },
    {
        "id": "claude_max",
        "name": "Claude Max",
        "key_env": "FASTAITOKEN_CLAUDE_MAX_API_KEY",
        "models": (
            "claude-fable-5",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-6",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-6",
        ),
    },
    {
        "id": "gpt_pro",
        "name": "GPT Pro",
        "key_env": "FASTAITOKEN_GPT_PRO_API_KEY",
        "models": (
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.5",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
        ),
    },
)

# Vendor limits are shown to users as model capabilities. Chat limits are the
# smaller budgets SkillForge actually uses for conversation history and a
# single synchronous reply. FastAIToken's /models endpoint currently returns
# identifiers but no token limits, so every value below comes from the model
# vendor's current documentation. Canonical IDs must also exist in the live
# FastAIToken account catalog before they are exposed in the UI.
MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "deepseek-v4-pro": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 384_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "DeepSeek official",
        "limit_source_url": "https://api-docs.deepseek.com/quick_start/pricing",
        "limit_note": "1M total context; 384K is the documented maximum output.",
    },
    "glm-5.2": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Zhipu official",
        "limit_source_url": "https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2",
        "limit_note": "1M total context; 128K maximum output.",
    },
    "kimi-k3": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 1_048_576,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Kimi official",
        "limit_source_url": "https://platform.kimi.com/docs/guide/kimi-k3-quickstart",
        "limit_note": "max_completion_tokens accepts up to 1,048,576, but input plus output must still fit the 1M context window.",
        "max_output_display": "≤1M (bounded by total context)",
    },
    "claude-fable-5": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/about-claude/models/overview",
        "limit_note": "Synchronous Messages API limit.",
    },
    "claude-haiku-4-5-20251001": {
        "context_window_tokens": 200_000,
        "max_output_tokens": 64_000,
        "chat_context_tokens": 120_000,
        "chat_max_output_tokens": 16_384,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/about-claude/models/overview",
        "limit_note": "Canonical snapshot returned by the configured FastAIToken account.",
    },
    "claude-opus-4-6": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/build-with-claude/context-windows",
        "limit_note": "Synchronous Messages API limit.",
    },
    "claude-opus-4-7": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/build-with-claude/context-windows",
        "limit_note": "Synchronous Messages API limit.",
    },
    "claude-opus-4-8": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/about-claude/models/overview",
        "limit_note": "Synchronous Messages API limit.",
    },
    "claude-sonnet-4-5-20250929": {
        "context_window_tokens": 200_000,
        "max_output_tokens": 64_000,
        "chat_context_tokens": 120_000,
        "chat_max_output_tokens": 16_384,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/about-claude/models/migration-guide",
        "limit_note": "Canonical snapshot; the retired 1M beta is not treated as the current limit.",
    },
    "claude-sonnet-4-6": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 800_000,
        "chat_max_output_tokens": 32_768,
        "limit_source": "Anthropic official",
        "limit_source_url": "https://platform.claude.com/docs/en/build-with-claude/context-windows",
        "limit_note": "Current synchronous Messages API limit; Batch beta limits are intentionally excluded.",
    },
    "gpt-5.4-mini": {
        "context_window_tokens": 400_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 256_000,
        "chat_max_output_tokens": 128_000,
        "limit_source": "OpenAI official",
        "limit_source_url": "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
        "limit_note": "400K total context; 128K maximum output.",
    },
    "gpt-5.4": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 256_000,
        "chat_max_output_tokens": 128_000,
        "limit_source": "OpenAI official",
        "limit_source_url": "https://developers.openai.com/api/docs/models/gpt-5.4",
        "limit_note": "The current chat budget stays below OpenAI's 272K long-context pricing threshold.",
    },
    "gpt-5.5": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 256_000,
        "chat_max_output_tokens": 128_000,
        "limit_source": "OpenAI official",
        "limit_source_url": "https://developers.openai.com/api/docs/models/gpt-5.5",
        "limit_note": "The current chat budget stays below OpenAI's 272K long-context pricing threshold.",
    },
    "gpt-5.6-luna": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 256_000,
        "chat_max_output_tokens": 128_000,
        "limit_source": "OpenAI official",
        "limit_source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
        "limit_note": "The current chat budget stays below OpenAI's 272K long-context pricing threshold.",
    },
    "gpt-5.6-sol": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 256_000,
        "chat_max_output_tokens": 128_000,
        "limit_source": "OpenAI official",
        "limit_source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
        "limit_note": "The current chat budget stays below OpenAI's 272K long-context pricing threshold.",
    },
    "gpt-5.6-terra": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "chat_context_tokens": 256_000,
        "chat_max_output_tokens": 128_000,
        "limit_source": "OpenAI official",
        "limit_source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
        "limit_note": "The current chat budget stays below OpenAI's 272K long-context pricing threshold.",
    },
}
GPT_REASONING_EFFORTS: dict[str, tuple[str, ...]] = {
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.4-mini": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.5": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-terra": ("none", "low", "medium", "high", "xhigh", "max"),
}
MODEL_REASONING_OPTIONS: dict[str, tuple[str, ...]] = {
    "deepseek-v4-pro": ("auto", "none", "high"),
    "glm-5.2": ("auto", "none", "low", "high", "max"),
    "kimi-k3": ("auto", "low", "high", "max"),
    "claude-fable-5": ("auto", "low", "medium", "high", "max"),
    "claude-haiku-4-5-20251001": ("auto", "none", "low", "medium", "high", "max"),
    "claude-opus-4-6": ("auto", "low", "medium", "high", "max"),
    "claude-opus-4-7": ("auto", "low", "medium", "high", "max"),
    "claude-opus-4-8": ("auto", "low", "medium", "high", "max"),
    "claude-sonnet-4-5-20250929": ("auto", "none", "low", "medium", "high", "max"),
    "claude-sonnet-4-6": ("auto", "low", "medium", "high", "max"),
    **{model: ("auto", *values) for model, values in GPT_REASONING_EFFORTS.items()},
}

# Keep modality metadata deterministic and versioned. Hall page loads must not
# perform N upstream /models and chat-completion probes: those made the model
# picker and attachment button wait tens of seconds and turned transient 5xx
# responses into a broken page. These values come from the vendors' published
# model capabilities; upstream availability is still reported precisely when a
# user actually sends a message.
MODEL_CAPABILITY_POLICY_VERSION = "ai-chat-model-capabilities-v1"
MODEL_VISION_CAPABILITIES: dict[str, dict[str, Any]] = {
    "deepseek-v4-pro": {
        "supported": False,
        "source": "DeepSeek official",
        "source_url": "https://api-docs.deepseek.com/quick_start/agent_integrations/github_copilot/",
        "note": "DeepSeek V4 is text-only; SkillForge uses a visual model before it when images are attached.",
    },
    "glm-5.2": {
        "supported": False,
        "source": "Zhipu official",
        "source_url": "https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2",
        "note": "The GLM-5.2 model page declares text input and text output.",
    },
    "kimi-k3": {
        "supported": True,
        "source": "Kimi official",
        "source_url": "https://www.moonshot.ai/",
        "note": "Kimi K3 is documented as natively multimodal.",
    },
    **{
        model: {
            "supported": True,
            "source": "Anthropic official",
            "source_url": "https://docs.anthropic.com/en/docs/build-with-claude/vision",
            "note": "Claude supports image understanding through the Messages API.",
        }
        for model in (
            "claude-fable-5",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-6",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-6",
        )
    },
    **{
        model: {
            "supported": True,
            "source": "OpenAI official",
            "source_url": str(MODEL_PROFILES[model]["limit_source_url"]),
            "note": "The model page declares image input support.",
        }
        for model in (
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.5",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
        )
    },
}
VISION_PRIORITY = (
    "gpt-5.4",
    "gpt-5.4-mini",
    "kimi-k3",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
)

_MODEL_TO_GROUP = {
    model: group
    for group in MODEL_GROUPS
    for model in group["models"]
}
_local_stream_lock = asyncio.Lock()
_local_streams: dict[str, set[str]] = defaultdict(set)
_local_cancelled_messages: set[str] = set()
_stream_cleanup_tasks: set[asyncio.Task[Any]] = set()
_orphan_cleanup_lock = asyncio.Lock()
_orphan_cleanup_at = 0.0


class AIChatUpstreamError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class AIChatUserCancelled(RuntimeError):
    """Internal control-flow signal for an explicit user cancellation."""


def _provider_group(model: str) -> dict[str, Any]:
    group = _MODEL_TO_GROUP.get(str(model or "").strip())
    if not group:
        raise HTTPException(status_code=400, detail="不支持的模型")
    return group


def _model_profile(model: str) -> dict[str, Any]:
    """Return the versioned vendor and SkillForge chat limits for a model."""
    _provider_group(model)
    profile = MODEL_PROFILES.get(model)
    if profile is None:
        raise HTTPException(status_code=500, detail="模型能力配置缺失")
    return profile


def _normalize_reasoning_effort(model: str, value: str | None) -> str:
    effort = str(value or "auto").strip().lower() or "auto"
    allowed = MODEL_REASONING_OPTIONS.get(model, ("auto",))
    if effort not in allowed:
        raise HTTPException(status_code=400, detail=f"{model} 不支持该思考深度")
    return effort


def _estimated_tokens(value: str) -> int:
    """Conservative tokenizer-free estimate for mixed Chinese and Latin text."""
    text = str(value or "")
    if not text:
        return 0
    cjk = sum(
        1
        for char in text
        if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"
    )
    return cjk + max(1, (len(text) - cjk + 3) // 4)


def _api_key(model: str) -> str:
    group = _provider_group(model)
    value = str(os.environ.get(str(group["key_env"]), "") or "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{group['name']} 尚未配置服务端密钥")
    return value


def _supported_vision_models() -> set[str]:
    return {
        model
        for model, capability in MODEL_VISION_CAPABILITIES.items()
        if capability.get("supported") and model in _MODEL_TO_GROUP
    }


def _selected_vision_model() -> str | None:
    supported = _supported_vision_models()
    preferred = str(os.environ.get("FASTAITOKEN_VISION_MODEL", "") or "").strip()
    if preferred in supported and os.environ.get(str(_MODEL_TO_GROUP[preferred]["key_env"])):
        return preferred
    for model in VISION_PRIORITY:
        if model in supported and os.environ.get(str(_MODEL_TO_GROUP[model]["key_env"])):
            return model
    return None


def _encode_cursor(timestamp: datetime, row_id: str) -> str:
    raw = json.dumps([timestamp.isoformat(), row_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, row_id = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return datetime.fromisoformat(str(timestamp)), str(row_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="分页游标无效") from exc


def _thread_payload(row: AIChatThread) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "default_model": row.default_model,
        "message_count": row.message_count,
        "archived": row.archived,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
        "last_message_at": isoformat_bjt(row.last_message_at),
    }


def _message_payload(row: AIChatMessage, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message_meta = row.message_meta or {}
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "role": row.role,
        "content": row.content,
        "status": row.status,
        "model": row.model,
        "provider_group": row.provider_group,
        "vision_model": row.vision_model,
        "reasoning_effort": message_meta.get("reasoning_effort") or "auto",
        "turn_id": row.turn_id,
        "reply_to_message_id": row.reply_to_message_id,
        "variant_index": row.variant_index,
        "attachments": attachments or [],
        "usage": row.usage or {},
        "latency_ms": row.latency_ms,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "event_seq": row.event_seq,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


def _attachment_payload(row: AIChatAttachment) -> dict[str, Any]:
    base = f"/api/hall/ai-chat/attachments/{row.id}/content"
    return {
        "id": row.id,
        "name": row.original_name,
        "mime_type": row.mime_type,
        "byte_size": row.byte_size,
        "width": row.width,
        "height": row.height,
        "url": base,
        "thumbnail_url": f"{base}?thumbnail=true" if row.thumbnail_path else base,
    }


async def get_model_catalog(*, refresh: bool = False) -> dict[str, Any]:
    del refresh  # Kept for API compatibility; the catalog is deterministic.
    groups_payload: list[dict[str, Any]] = []
    for group in MODEL_GROUPS:
        key_ready = bool(str(os.environ.get(str(group["key_env"]), "") or "").strip())
        models_payload = []
        for model in group["models"]:
            vision = MODEL_VISION_CAPABILITIES[model]
            models_payload.append({
                "id": model,
                "name": model,
                "available": key_ready,
                "status": "available" if key_ready else "unavailable",
                "reason": "" if key_ready else "服务端密钥未配置",
                "latency_ms": None,
                "supports_vision": bool(vision["supported"]),
                "vision_label": "支持图片" if vision["supported"] else "仅文本",
                "vision_source": vision["source"],
                "vision_source_url": vision["source_url"],
                "vision_note": vision["note"],
                "reasoning_options": list(MODEL_REASONING_OPTIONS.get(model, ("auto",))),
                "limit_policy_version": MODEL_LIMIT_POLICY_VERSION,
                "capability_policy_version": MODEL_CAPABILITY_POLICY_VERSION,
                **_model_profile(model),
            })
        groups_payload.append({
            "id": group["id"],
            "name": group["name"],
            "configured": key_ready,
            "models": models_payload,
        })
    vision_model = _selected_vision_model()
    return {
        "default_model": DEFAULT_MODEL,
        "groups": groups_payload,
        "vision": {
            "ready": bool(vision_model),
            "model": vision_model,
            "strategy": "selected_model_or_static_vision_fallback",
        },
        "catalog_source": "built_in_vendor_metadata",
        "capability_policy_version": MODEL_CAPABILITY_POLICY_VERSION,
        "generated_at": isoformat_bjt(now_bjt()),
        "cache_ttl_seconds": 0,
    }


async def list_threads(
    db: AsyncSession,
    *,
    user_id: str,
    archived: bool = False,
    q: str = "",
    cursor: str | None = None,
    limit: int = THREAD_PAGE_SIZE,
) -> dict[str, Any]:
    await _maybe_cleanup_expired_orphans(db)
    limit = max(1, min(int(limit), 60))
    conditions = [AIChatThread.user_id == user_id, AIChatThread.archived.is_(archived)]
    if q.strip():
        conditions.append(AIChatThread.title.ilike(f"%{q.strip()[:100]}%"))
    decoded = _decode_cursor(cursor)
    if decoded:
        timestamp, row_id = decoded
        conditions.append(or_(
            AIChatThread.last_message_at < timestamp,
            and_(AIChatThread.last_message_at == timestamp, AIChatThread.id < row_id),
        ))
    result = await db.execute(
        select(AIChatThread)
        .where(*conditions)
        .order_by(AIChatThread.last_message_at.desc(), AIChatThread.id.desc())
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor(rows[-1].last_message_at, rows[-1].id) if has_more and rows else None
    return {"items": [_thread_payload(row) for row in rows], "next_cursor": next_cursor, "has_more": has_more}


async def create_thread(db: AsyncSession, *, user_id: str, title: str = "", model: str = DEFAULT_MODEL) -> dict[str, Any]:
    _provider_group(model)
    now = now_bjt()
    row = AIChatThread(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=(title.strip()[:160] or "新对话"),
        default_model=model,
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    db.add(row)
    await update_user_preference(db, user_id=user_id, model=model)
    await db.flush()
    return _thread_payload(row)


async def get_user_preference(db: AsyncSession, *, user_id: str) -> dict[str, Any]:
    row = await db.get(AIChatUserPreference, user_id)
    return {"last_model": row.last_model if row else DEFAULT_MODEL}


async def update_user_preference(db: AsyncSession, *, user_id: str, model: str) -> dict[str, Any]:
    _provider_group(model)
    row = await db.get(AIChatUserPreference, user_id)
    now = now_bjt()
    if row is None:
        row = AIChatUserPreference(user_id=user_id, last_model=model, created_at=now, updated_at=now)
        db.add(row)
    else:
        row.last_model = model
        row.updated_at = now
    await db.flush()
    return {"last_model": row.last_model}


async def get_owned_thread(db: AsyncSession, *, user_id: str, thread_id: str) -> AIChatThread:
    row = await db.get(AIChatThread, thread_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="对话不存在")
    return row


async def update_thread(
    db: AsyncSession,
    *,
    user_id: str,
    thread_id: str,
    title: str | None = None,
    archived: bool | None = None,
    default_model: str | None = None,
) -> dict[str, Any]:
    row = await get_owned_thread(db, user_id=user_id, thread_id=thread_id)
    if title is not None:
        normalized = title.strip()[:160]
        if not normalized:
            raise HTTPException(status_code=400, detail="对话标题不能为空")
        row.title = normalized
    if archived is not None:
        row.archived = bool(archived)
    if default_model is not None:
        _provider_group(default_model)
        row.default_model = default_model
    row.updated_at = now_bjt()
    await db.flush()
    return _thread_payload(row)


async def delete_thread(db: AsyncSession, *, user_id: str, thread_id: str) -> dict[str, Any]:
    row = await get_owned_thread(db, user_id=user_id, thread_id=thread_id)
    message_rows = list((await db.execute(select(AIChatMessage).where(
        AIChatMessage.thread_id == row.id,
        AIChatMessage.user_id == user_id,
    ))).scalars().all())
    attachment_rows = list((await db.execute(select(AIChatAttachment).where(
        AIChatAttachment.thread_id == row.id,
        AIChatAttachment.user_id == user_id,
    ))).scalars().all())

    for message in message_rows:
        if message.status in {"queued", "streaming"}:
            _local_cancelled_messages.add(message.id)
            try:
                from app.common.cache import _get_pool

                await _get_pool().set(f"sf:ai_chat:cancel:{message.id}", "1", ex=STREAM_LEASE_TTL_SECONDS)
            except Exception:
                pass

    await db.execute(delete(AIChatMessage).where(
        AIChatMessage.thread_id == row.id,
        AIChatMessage.user_id == user_id,
    ))
    await db.execute(delete(AIChatAttachment).where(
        AIChatAttachment.thread_id == row.id,
        AIChatAttachment.user_id == user_id,
    ))
    await db.delete(row)
    await db.commit()

    root = ATTACHMENT_ROOT.resolve()
    for attachment in attachment_rows:
        for raw_path in (attachment.storage_path, attachment.thumbnail_path):
            if not raw_path:
                continue
            try:
                path = Path(raw_path).resolve()
                if root in path.parents:
                    path.unlink(missing_ok=True)
            except OSError:
                logger.warning("AI Chat attachment cleanup failed for thread {}", thread_id)
    try:
        await audit.log(
            user_id=user_id,
            action="ai_chat.thread.delete",
            target_type="ai_chat_thread",
            target_id=thread_id,
            detail={"message_count": len(message_rows), "attachment_count": len(attachment_rows)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI Chat delete audit failed for thread {}: {}", thread_id, redact_secret_text(exc))
    return {"deleted": True, "id": thread_id}


async def list_messages(
    db: AsyncSession,
    *,
    user_id: str,
    thread_id: str,
    cursor: str | None = None,
    limit: int = MESSAGE_PAGE_SIZE,
) -> dict[str, Any]:
    await get_owned_thread(db, user_id=user_id, thread_id=thread_id)
    await reconcile_stale_streaming_messages(db, user_id=user_id, thread_id=thread_id)
    limit = max(1, min(int(limit), 100))
    conditions = [AIChatMessage.thread_id == thread_id, AIChatMessage.user_id == user_id]
    decoded = _decode_cursor(cursor)
    if decoded:
        timestamp, row_id = decoded
        conditions.append(or_(
            AIChatMessage.created_at < timestamp,
            and_(AIChatMessage.created_at == timestamp, AIChatMessage.id < row_id),
        ))
    result = await db.execute(
        select(AIChatMessage)
        .where(*conditions)
        .order_by(AIChatMessage.created_at.desc(), AIChatMessage.id.desc())
        .limit(limit + 1)
    )
    rows_desc = list(result.scalars().all())
    has_more = len(rows_desc) > limit
    rows_desc = rows_desc[:limit]
    attachment_ids = {item for row in rows_desc for item in (row.attachment_ids or [])}
    attachment_map: dict[str, AIChatAttachment] = {}
    if attachment_ids:
        attachments = await db.execute(
            select(AIChatAttachment).where(
                AIChatAttachment.user_id == user_id,
                AIChatAttachment.id.in_(attachment_ids),
            )
        )
        attachment_map = {row.id: row for row in attachments.scalars().all()}
    items = [
        _message_payload(row, [_attachment_payload(attachment_map[item]) for item in (row.attachment_ids or []) if item in attachment_map])
        for row in reversed(rows_desc)
    ]
    next_cursor = _encode_cursor(rows_desc[-1].created_at, rows_desc[-1].id) if has_more and rows_desc else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


def _sniff_image_type(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


async def _cleanup_expired_orphans(db: AsyncSession) -> None:
    result = await db.execute(
        select(AIChatAttachment).where(
            AIChatAttachment.message_id.is_(None),
            AIChatAttachment.orphan_expires_at < now_bjt(),
        ).limit(100)
    )
    rows = list(result.scalars().all())
    for row in rows:
        for raw_path in (row.storage_path, row.thumbnail_path):
            if not raw_path:
                continue
            try:
                path = Path(raw_path).resolve()
                if ATTACHMENT_ROOT.resolve() in path.parents:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
    if rows:
        await db.execute(delete(AIChatAttachment).where(AIChatAttachment.id.in_([row.id for row in rows])))


async def _maybe_cleanup_expired_orphans(db: AsyncSession, *, force: bool = False) -> None:
    """Run bounded orphan cleanup at most once every ten minutes per worker."""
    global _orphan_cleanup_at
    if not force and time.monotonic() - _orphan_cleanup_at < 600:
        return
    async with _orphan_cleanup_lock:
        if not force and time.monotonic() - _orphan_cleanup_at < 600:
            return
        await _cleanup_expired_orphans(db)
        _orphan_cleanup_at = time.monotonic()


async def save_attachment(db: AsyncSession, *, user_id: str, file: UploadFile) -> dict[str, Any]:
    await _maybe_cleanup_expired_orphans(db)
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="单张图片不能超过 10MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    detected = _sniff_image_type(data)
    if not detected:
        raise HTTPException(status_code=400, detail="仅支持真实的 PNG、JPEG 或 WebP 图片")
    mime_type, extension = detected
    if file.content_type and file.content_type.lower() not in {mime_type, "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="图片 MIME 与文件内容不一致")

    attachment_id = str(uuid.uuid4())
    user_bucket = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    target_dir = ATTACHMENT_ROOT / user_bucket / attachment_id[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{attachment_id}{extension}"
    target_path.write_bytes(data)
    thumbnail_path: Path | None = None
    width = height = None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("image dimensions exceed the safety limit")
            image.thumbnail((512, 512))
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image)
                image = background
            thumbnail_path = target_dir / f"{attachment_id}.thumb.jpg"
            image.convert("RGB").save(thumbnail_path, "JPEG", quality=82, optimize=True)
    except Exception as exc:  # noqa: BLE001
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="图片无法解码或文件已损坏") from exc

    row = AIChatAttachment(
        id=attachment_id,
        user_id=user_id,
        original_name=(file.filename or f"image{extension}")[:255],
        mime_type=mime_type,
        byte_size=size,
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=str(target_path),
        thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
        width=width,
        height=height,
        created_at=now_bjt(),
        orphan_expires_at=now_bjt() + timedelta(hours=24),
    )
    db.add(row)
    await db.flush()
    return _attachment_payload(row)


async def attachment_path(
    db: AsyncSession,
    *,
    user_id: str,
    attachment_id: str,
    thumbnail: bool = False,
) -> tuple[Path, AIChatAttachment, str]:
    row = await db.get(AIChatAttachment, attachment_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="图片不存在")
    raw = row.thumbnail_path if thumbnail and row.thumbnail_path else row.storage_path
    path = Path(raw).resolve()
    if ATTACHMENT_ROOT.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    media_type = "image/jpeg" if thumbnail and row.thumbnail_path else row.mime_type
    return path, row, media_type


async def _owned_attachments(
    db: AsyncSession,
    *,
    user_id: str,
    attachment_ids: list[str],
    thread_id: str,
) -> list[AIChatAttachment]:
    unique_ids = list(dict.fromkeys(str(item) for item in attachment_ids if item))
    if len(unique_ids) > MAX_IMAGES_PER_MESSAGE:
        raise HTTPException(status_code=400, detail="每条消息最多添加 4 张图片")
    if not unique_ids:
        return []
    result = await db.execute(select(AIChatAttachment).where(AIChatAttachment.id.in_(unique_ids)))
    rows = list(result.scalars().all())
    if len(rows) != len(unique_ids) or any(row.user_id != user_id for row in rows):
        raise HTTPException(status_code=404, detail="图片不存在")
    if any(row.message_id is not None or (row.thread_id and row.thread_id != thread_id) for row in rows):
        raise HTTPException(status_code=409, detail="图片已绑定到其他消息")
    if sum(row.byte_size for row in rows) > MAX_MESSAGE_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="单条消息图片总大小不能超过 20MB")
    by_id = {row.id: row for row in rows}
    return [by_id[item] for item in unique_ids]


async def prepare_turn(
    db: AsyncSession,
    *,
    user_id: str,
    thread_id: str,
    content: str,
    model: str,
    reasoning_effort: str = "auto",
    attachment_ids: list[str],
    idempotency_key: str,
) -> tuple[AIChatMessage, AIChatMessage, bool]:
    thread = await get_owned_thread(db, user_id=user_id, thread_id=thread_id)
    normalized = content.strip()
    if not normalized and not attachment_ids:
        raise HTTPException(status_code=400, detail="请输入消息或添加图片")
    if len(normalized) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail="单条消息不能超过 32000 个字符")
    _api_key(model)
    normalized_reasoning = _normalize_reasoning_effort(model, reasoning_effort)
    if len(idempotency_key.strip()) < 8 or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="幂等键无效")

    existing = await db.execute(select(AIChatMessage).where(
        AIChatMessage.user_id == user_id,
        AIChatMessage.idempotency_key == idempotency_key,
    ))
    user_message = existing.scalar_one_or_none()
    if user_message:
        if user_message.thread_id != thread_id:
            raise HTTPException(status_code=409, detail="幂等键已用于其他对话")
        assistant = (await db.execute(select(AIChatMessage).where(
            AIChatMessage.thread_id == thread_id,
            AIChatMessage.user_id == user_id,
            AIChatMessage.turn_id == user_message.turn_id,
            AIChatMessage.role == "assistant",
        ).order_by(AIChatMessage.variant_index.desc()).limit(1))).scalar_one()
        return user_message, assistant, True

    attachments = await _owned_attachments(
        db,
        user_id=user_id,
        attachment_ids=attachment_ids,
        thread_id=thread_id,
    )
    now = now_bjt()
    turn_id = str(uuid.uuid4())
    user_message = AIChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        user_id=user_id,
        role="user",
        content=normalized,
        status="completed",
        model=model,
        provider_group=str(_provider_group(model)["id"]),
        turn_id=turn_id,
        idempotency_key=idempotency_key,
        attachment_ids=[row.id for row in attachments],
        message_meta={"reasoning_effort": normalized_reasoning},
        created_at=now,
        updated_at=now,
    )
    assistant = AIChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        user_id=user_id,
        role="assistant",
        content="",
        status="streaming",
        model=model,
        provider_group=str(_provider_group(model)["id"]),
        turn_id=turn_id,
        reply_to_message_id=user_message.id,
        variant_index=1,
        attachment_ids=[],
        message_meta={"reasoning_effort": normalized_reasoning},
        # Keep the user prompt and its answer deterministically ordered even on
        # databases whose timestamp precision makes both inserts identical.
        created_at=now + timedelta(microseconds=1),
        updated_at=now + timedelta(microseconds=1),
    )
    db.add_all([user_message, assistant])
    for attachment in attachments:
        attachment.thread_id = thread_id
        attachment.message_id = user_message.id
        attachment.bound_at = now
        attachment.orphan_expires_at = None
    if thread.message_count == 0 and thread.title == "新对话":
        source = normalized or "图片对话"
        thread.title = re.sub(r"\s+", " ", source).strip()[:36] or "图片对话"
    thread.default_model = model
    thread.message_count += 2
    thread.last_message_at = now
    thread.updated_at = now
    await update_user_preference(db, user_id=user_id, model=model)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(AIChatMessage).where(
            AIChatMessage.user_id == user_id,
            AIChatMessage.idempotency_key == idempotency_key,
        ))).scalar_one_or_none()
        if existing is None:
            raise
        assistant = (await db.execute(select(AIChatMessage).where(
            AIChatMessage.thread_id == existing.thread_id,
            AIChatMessage.user_id == user_id,
            AIChatMessage.turn_id == existing.turn_id,
            AIChatMessage.role == "assistant",
        ).order_by(AIChatMessage.variant_index.desc()).limit(1))).scalar_one()
        return existing, assistant, True
    return user_message, assistant, False


async def prepare_regeneration(
    db: AsyncSession,
    *,
    user_id: str,
    assistant_message_id: str,
    model: str,
    reasoning_effort: str = "auto",
) -> tuple[AIChatMessage, AIChatMessage]:
    source = await db.get(AIChatMessage, assistant_message_id)
    if source is None or source.user_id != user_id or source.role != "assistant":
        raise HTTPException(status_code=404, detail="回复不存在")
    await get_owned_thread(db, user_id=user_id, thread_id=source.thread_id)
    _api_key(model)
    normalized_reasoning = _normalize_reasoning_effort(model, reasoning_effort)
    user_message = await db.get(AIChatMessage, source.reply_to_message_id)
    if user_message is None or user_message.user_id != user_id:
        raise HTTPException(status_code=409, detail="原始消息不存在")
    max_variant = (await db.execute(select(func.max(AIChatMessage.variant_index)).where(
        AIChatMessage.thread_id == source.thread_id,
        AIChatMessage.turn_id == source.turn_id,
        AIChatMessage.role == "assistant",
    ))).scalar() or 1
    now = now_bjt()
    assistant = AIChatMessage(
        id=str(uuid.uuid4()),
        thread_id=source.thread_id,
        user_id=user_id,
        role="assistant",
        content="",
        status="streaming",
        model=model,
        provider_group=str(_provider_group(model)["id"]),
        turn_id=source.turn_id,
        reply_to_message_id=user_message.id,
        variant_index=int(max_variant) + 1,
        attachment_ids=[],
        message_meta={"reasoning_effort": normalized_reasoning},
        created_at=now,
        updated_at=now,
    )
    db.add(assistant)
    thread = await db.get(AIChatThread, source.thread_id)
    if thread:
        thread.default_model = model
        thread.message_count += 1
        thread.last_message_at = now
        thread.updated_at = now
    await update_user_preference(db, user_id=user_id, model=model)
    await db.commit()
    return user_message, assistant


async def acquire_stream_lease(user_id: str, message_id: str) -> str:
    try:
        from app.common.cache import _get_pool

        pool = _get_pool()
        key = f"sf:ai_chat:streams:{user_id}"
        script = """
        redis.call('SREM', KEYS[1], '')
        if redis.call('SCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
        redis.call('SADD', KEYS[1], ARGV[1])
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
        return 1
        """
        acquired = await pool.eval(script, 1, key, message_id, MAX_USER_STREAMS, STREAM_LEASE_TTL_SECONDS)
        if not acquired:
            raise HTTPException(status_code=429, detail="每位用户最多同时生成 2 条回复")
        return "redis"
    except HTTPException:
        raise
    except Exception:
        async with _local_stream_lock:
            active = _local_streams[user_id]
            if len(active) >= MAX_USER_STREAMS:
                raise HTTPException(status_code=429, detail="每位用户最多同时生成 2 条回复")
            active.add(message_id)
        return "local"


async def release_stream_lease(user_id: str, message_id: str, backend: str) -> None:
    if backend == "redis":
        try:
            from app.common.cache import _get_pool

            await _get_pool().srem(f"sf:ai_chat:streams:{user_id}", message_id)
            return
        except Exception:
            pass
    async with _local_stream_lock:
        _local_streams[user_id].discard(message_id)


async def fail_stream_admission(
    db: AsyncSession,
    *,
    message_id: str,
    error_code: str = "concurrency_limit",
    error_message: str = "并发生成数量已达到上限，请等待当前回复完成后重试",
) -> AIChatMessage | None:
    """Terminalize a turn that was persisted before stream admission failed."""
    row = await db.get(AIChatMessage, message_id)
    if row is None or row.status not in {"queued", "streaming"}:
        return row
    row.status = "failed"
    row.error_code = error_code
    row.error_message = error_message
    row.updated_at = now_bjt()
    row.event_seq = max(int(row.event_seq or 0), 1)
    await db.commit()
    return row


async def reconcile_stale_streaming_messages(
    db: AsyncSession,
    *,
    user_id: str,
    thread_id: str | None = None,
) -> int:
    """Self-heal orphaned stream rows after their maximum valid lifetime."""
    cutoff = now_bjt() - timedelta(seconds=STREAM_STALE_AFTER_SECONDS)
    conditions = [
        AIChatMessage.user_id == user_id,
        AIChatMessage.role == "assistant",
        AIChatMessage.status.in_(["queued", "streaming"]),
        AIChatMessage.updated_at < cutoff,
    ]
    if thread_id:
        conditions.append(AIChatMessage.thread_id == thread_id)
    rows = list((await db.execute(
        select(AIChatMessage).where(*conditions).limit(100)
    )).scalars().all())
    if not rows:
        return 0
    now = now_bjt()
    for row in rows:
        row.status = "interrupted" if row.content else "failed"
        row.error_code = "stale_stream"
        row.error_message = "生成连接已超时中断，可重新生成"
        row.updated_at = now
        row.event_seq = max(int(row.event_seq or 0), 1)
    await db.commit()
    for row in rows:
        await release_stream_lease(user_id, row.id, "redis")
    logger.warning(
        "Reconciled {} stale AI Chat stream(s) for user {} thread {}",
        len(rows),
        user_id,
        thread_id or "*",
    )
    return len(rows)


async def cancel_message(db: AsyncSession, *, user_id: str, message_id: str) -> dict[str, Any]:
    row = await db.get(AIChatMessage, message_id)
    if row is None or row.user_id != user_id or row.role != "assistant":
        raise HTTPException(status_code=404, detail="回复不存在")
    if row.status not in {"streaming", "queued"}:
        return _message_payload(row)
    _local_cancelled_messages.add(message_id)
    try:
        from app.common.cache import _get_pool

        await _get_pool().set(f"sf:ai_chat:cancel:{message_id}", "1", ex=STREAM_LEASE_TTL_SECONDS)
    except Exception:
        pass
    return _message_payload(row)


async def _is_cancelled(message_id: str) -> bool:
    if message_id in _local_cancelled_messages:
        return True
    try:
        from app.common.cache import _get_pool

        return bool(await _get_pool().exists(f"sf:ai_chat:cancel:{message_id}"))
    except Exception:
        return False


def _upstream_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
        else:
            message = payload.get("message") if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001
        message = None
    return redact_secret_text(message or f"上游返回 HTTP {response.status_code}", limit=300)


def _raise_for_upstream(response: httpx.Response, *, emitted: bool = False) -> None:
    if response.status_code == 200:
        return
    message = _upstream_error_message(response)
    if response.status_code in {401, 403}:
        raise AIChatUpstreamError("auth_failed", "模型账号鉴权失败", status_code=503)
    if response.status_code in {402}:
        raise AIChatUpstreamError("quota_exhausted", "模型账号余额不足", status_code=429)
    if response.status_code == 404:
        raise AIChatUpstreamError("model_not_found", message, status_code=400)
    if response.status_code == 429:
        raise AIChatUpstreamError("rate_limited", "模型请求过于频繁", retryable=not emitted, status_code=429)
    if response.status_code >= 500:
        raise AIChatUpstreamError("upstream_unavailable", message, retryable=not emitted)
    raise AIChatUpstreamError("invalid_request", message, status_code=400)


def _apply_model_options(payload: dict[str, Any], model: str, reasoning_effort: str = "auto") -> None:
    # Sampling controls differ across these providers. Claude rejects some
    # non-default values, while Kimi K3 fixes temperature at 1.0 and recommends
    # that clients do not send it. Provider defaults are the only portable
    # choice for a selector that can switch models between turns.
    payload.pop("temperature", None)
    effort = _normalize_reasoning_effort(model, reasoning_effort)
    if model in GPT_REASONING_EFFORTS:
        if "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if effort != "auto":
            payload["reasoning_effort"] = effort
        return
    if model == "deepseek-v4-pro":
        if effort == "none":
            payload["thinking"] = {"type": "disabled"}
        elif effort != "auto":
            payload["thinking"] = {"type": "enabled"}
        return
    if model == "glm-5.2":
        if effort == "none":
            payload["thinking"] = {"type": "disabled"}
        elif effort != "auto":
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = effort
        return
    if model == "kimi-k3":
        if effort != "auto":
            payload["reasoning_effort"] = effort
        return
    if model in {"claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"}:
        if effort == "none":
            payload["thinking"] = {"type": "disabled"}
        elif effort != "auto":
            budgets = {"low": 1_024, "medium": 4_096, "high": 8_192, "max": 12_288}
            payload["thinking"] = {"type": "enabled", "budget_tokens": budgets[effort]}
        return
    if model.startswith("claude-") and effort != "auto":
        payload["reasoning_effort"] = effort


async def _non_stream_completion(model: str, messages: list[dict[str, Any]], *, max_tokens: int = 2000) -> tuple[str, dict]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    _apply_model_options(payload, model)
    last_error: Exception | None = None
    for attempt, delay in enumerate((0, 1, 3)):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
                response = await client.post(
                    f"{FASTAITOKEN_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {_api_key(model)}", "Content-Type": "application/json"},
                    json=payload,
                )
            _raise_for_upstream(response)
            data = response.json()
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            return str(content), data.get("usage") or {}
        except AIChatUpstreamError as exc:
            last_error = exc
            if not exc.retryable or attempt == 2:
                raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt == 2:
                raise AIChatUpstreamError("upstream_timeout", "模型服务连接超时", retryable=False) from exc
    raise AIChatUpstreamError("upstream_unavailable", redact_secret_text(last_error or "模型服务不可用"))


async def _upstream_stream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    reasoning_effort: str = "auto",
) -> AsyncGenerator[dict[str, Any], None]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.6,
        "max_tokens": int(_model_profile(model)["chat_max_output_tokens"]),
    }
    _apply_model_options(payload, model, reasoning_effort)
    for attempt, delay in enumerate((0, 1, 3)):
        if delay:
            await asyncio.sleep(delay)
        emitted = False
        try:
            timeout = httpx.Timeout(connect=20, read=UPSTREAM_READ_TIMEOUT_SECONDS, write=60, pool=20)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{FASTAITOKEN_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {_api_key(model)}", "Content-Type": "application/json"},
                    json=payload,
                ) as response:
                    _raise_for_upstream(response)
                    content_type = str(response.headers.get("content-type") or "").lower()
                    if "text/event-stream" not in content_type:
                        raw = await response.aread()
                        data = json.loads(raw.decode("utf-8"))
                        text_value = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                        if text_value:
                            emitted = True
                            yield {"type": "delta", "text": str(text_value), "degraded": True}
                        yield {"type": "usage", "usage": data.get("usage") or {}, "degraded": True}
                        return
                    usage: dict[str, Any] = {}
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_text = line[5:].strip()
                        if not data_text or data_text == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_text)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(data.get("usage"), dict):
                            usage = data["usage"]
                        choice = (data.get("choices") or [{}])[0]
                        delta = choice.get("delta") if isinstance(choice, dict) else {}
                        text_value = delta.get("content") if isinstance(delta, dict) else ""
                        if text_value:
                            emitted = True
                            yield {"type": "delta", "text": str(text_value), "degraded": False}
                    yield {"type": "usage", "usage": usage, "degraded": False}
                    return
        except AIChatUpstreamError as exc:
            if emitted or not exc.retryable or attempt == 2:
                raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if emitted or attempt == 2:
                raise AIChatUpstreamError("upstream_timeout", "模型服务连接超时") from exc


async def _upstream_events_with_heartbeat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    reasoning_effort: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Keep the browser connection alive and put a hard ceiling on a stream."""
    upstream = _upstream_stream(model, messages, reasoning_effort=reasoning_effort)
    pending: asyncio.Task[dict[str, Any]] | None = None
    started = time.monotonic()
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= STREAM_TOTAL_TIMEOUT_SECONDS:
                raise AIChatUpstreamError("stream_timeout", "模型生成超过 10 分钟，已自动停止")
            if pending is None:
                pending = asyncio.create_task(upstream.__anext__())
            remaining = max(0.1, STREAM_TOTAL_TIMEOUT_SECONDS - elapsed)
            done, _ = await asyncio.wait(
                {pending},
                timeout=min(float(STREAM_HEARTBEAT_SECONDS), remaining),
            )
            if not done:
                yield {"type": "heartbeat"}
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield event
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        with suppress(Exception):
            await upstream.aclose()


def _coherent_recent_context(
    rows: list[AIChatMessage],
    *,
    max_tokens: int = 120_000,
    max_turns: int = MAX_CONTEXT_TURNS,
) -> list[AIChatMessage]:
    """Keep complete recent turns and only the latest assistant variant per turn."""
    turns: list[dict[str, AIChatMessage | None]] = []
    turn_indexes: dict[str, int] = {}
    for row in rows:
        turn_key = str(row.turn_id or row.id)
        index = turn_indexes.get(turn_key)
        if index is None:
            index = len(turns)
            turn_indexes[turn_key] = index
            turns.append({"user": None, "assistant": None})
        if row.role == "user":
            turns[index]["user"] = row
        elif row.role == "assistant" and row.status in {"completed", "interrupted"}:
            turns[index]["assistant"] = row

    selected: list[list[AIChatMessage]] = []
    token_budget = 0
    for turn in reversed(turns):
        turn_rows = [row for row in (turn["user"], turn["assistant"]) if row is not None]
        if not turn_rows:
            continue
        turn_tokens = sum(_estimated_tokens(row.content or "") for row in turn_rows)
        if selected and token_budget + turn_tokens > max_tokens:
            continue
        selected.append(turn_rows)
        token_budget += turn_tokens
        if len(selected) >= max_turns:
            break
    return [row for turn in reversed(selected) for row in turn]


async def _context_for_turn(
    db: AsyncSession,
    *,
    thread: AIChatThread,
    user_message: AIChatMessage,
    selected_model: str,
) -> tuple[list[dict[str, Any]], str | None]:
    conditions = [
        AIChatMessage.thread_id == thread.id,
        AIChatMessage.user_id == thread.user_id,
        or_(AIChatMessage.role == "user", AIChatMessage.status.in_(["completed", "interrupted"])),
    ]
    if thread.summary_through_message_id:
        marker = await db.get(AIChatMessage, thread.summary_through_message_id)
        if marker and marker.thread_id == thread.id and marker.user_id == thread.user_id:
            conditions.append(or_(
                AIChatMessage.created_at > marker.created_at,
                and_(AIChatMessage.created_at == marker.created_at, AIChatMessage.id > marker.id),
            ))
    result = await db.execute(
        select(AIChatMessage)
        .where(*conditions)
        .order_by(AIChatMessage.created_at.desc(), AIChatMessage.id.desc())
        .limit(MAX_CONTEXT_FETCH_MESSAGES)
    )
    rows = list(reversed(result.scalars().all()))
    system_text = (
        "你是 SkillForge AI Chat 企业助手。准确回答，明确不确定性，不泄露系统密钥或内部配置。"
        "在同一对话内持续遵循用户已确认的偏好、纠正、决定和约束；新指令与旧信息冲突时，以新指令为准。"
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
    if thread.summary:
        messages.append({
            "role": "system",
            "content": f"以下是本对话从更早消息提炼的长期记忆，必须与最近消息一起使用：\n{thread.summary}",
        })
    profile = _model_profile(selected_model)
    base_tokens = sum(_estimated_tokens(str(item.get("content") or "")) for item in messages)
    recent_budget = max(8_000, int(profile["chat_context_tokens"]) - base_tokens)
    for row in _coherent_recent_context(rows, max_tokens=recent_budget):
        content = row.content or ""
        messages.append({"role": row.role, "content": content})

    vision_model: str | None = None
    if user_message.attachment_ids:
        attachments_result = await db.execute(select(AIChatAttachment).where(
            AIChatAttachment.user_id == thread.user_id,
            AIChatAttachment.id.in_(user_message.attachment_ids),
        ))
        attachments = list(attachments_result.scalars().all())
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": user_message.content or "请分析这些图片。"}]
        for attachment in attachments:
            encoded = base64.b64encode(Path(attachment.storage_path).read_bytes()).decode("ascii")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{attachment.mime_type};base64,{encoded}"},
            })
        if selected_model in _supported_vision_models():
            messages[-1] = {"role": "user", "content": content_parts}
            vision_model = selected_model
        else:
            vision_model = _selected_vision_model()
            if not vision_model:
                raise AIChatUpstreamError("vision_unavailable", "图片识别服务尚未配置，请先发送纯文本")
            vision_prompt = [
                {"role": "system", "content": "请客观识别图片。按图片逐张描述主体、文字、数据、布局和不确定项，不要猜测。"},
                {"role": "user", "content": content_parts},
            ]
            vision_text, vision_usage = await _non_stream_completion(vision_model, vision_prompt, max_tokens=1800)
            meta = dict(user_message.message_meta or {})
            meta.update({"vision_summary": vision_text, "vision_usage": vision_usage, "vision_model": vision_model})
            user_message.message_meta = meta
            messages[-1] = {
                "role": "user",
                "content": f"{user_message.content}\n\n[图片识别结果，由 {vision_model} 生成]\n{vision_text}",
            }
            await db.commit()
    return messages, vision_model


def _sse(event: str, seq: int, payload: dict[str, Any]) -> str:
    body = {"type": event, "seq": seq, **payload}
    return f"id: {seq}\nevent: {event}\ndata: {json.dumps(body, ensure_ascii=False, default=str)}\n\n"


async def _persist_assistant(
    *,
    message_id: str,
    content: str,
    status: str,
    event_seq: int,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    vision_model: str | None = None,
    degraded: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> AIChatMessage | None:
    async with async_session_factory() as db:
        row = await db.get(AIChatMessage, message_id)
        if row is None:
            return None
        row.content = content
        row.status = status
        row.event_seq = event_seq
        row.usage = usage or row.usage
        row.latency_ms = latency_ms
        row.vision_model = vision_model
        row.error_code = error_code
        row.error_message = redact_secret_text(error_message, limit=500) if error_message else None
        row.message_meta = {**(row.message_meta or {}), "stream_degraded": degraded}
        row.updated_at = now_bjt()
        await db.commit()
        return row


def _track_cleanup_task(task: asyncio.Task[Any]) -> None:
    _stream_cleanup_tasks.add(task)

    def done(completed: asyncio.Task[Any]) -> None:
        _stream_cleanup_tasks.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:
            logger.warning("AI Chat terminal cleanup task was cancelled")
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI Chat terminal cleanup failed: {}", redact_secret_text(exc))

    task.add_done_callback(done)


async def _shielded_stream_cleanup(awaitable) -> Any:
    """Let terminal DB/Redis cleanup finish even after the browser disconnects."""
    task = asyncio.create_task(awaitable)
    _track_cleanup_task(task)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        return None


async def _maybe_summarize_thread(thread_id: str, user_id: str) -> None:
    try:
        async with async_session_factory() as db:
            thread = await get_owned_thread(db, user_id=user_id, thread_id=thread_id)
            if thread.message_count < SUMMARY_TRIGGER_MESSAGES or thread.message_count % SUMMARY_BATCH_MESSAGES not in {0, 1, 2}:
                return
            conditions = [AIChatMessage.thread_id == thread_id, AIChatMessage.user_id == user_id]
            if thread.summary_through_message_id:
                marker = await db.get(AIChatMessage, thread.summary_through_message_id)
                if marker:
                    conditions.append(or_(
                        AIChatMessage.created_at > marker.created_at,
                        and_(AIChatMessage.created_at == marker.created_at, AIChatMessage.id > marker.id),
                    ))
            rows = list((await db.execute(
                select(AIChatMessage)
                .where(*conditions)
                .order_by(AIChatMessage.created_at.asc(), AIChatMessage.id.asc())
                .limit(SUMMARY_BATCH_MESSAGES)
            )).scalars().all())
            if len(rows) < SUMMARY_BATCH_MESSAGES:
                return
            transcript = "\n".join(f"{row.role}: {row.content[:4000]}" for row in rows)
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "你负责维护企业对话的长期记忆。合并旧摘要和新增对话，不添加未出现的内容。"
                        "使用以下固定栏目：用户偏好与表达习惯、长期事实与背景、已确认决定与约束、"
                        "进行中目标与待办、用户纠正与易混淆项。删除已被新信息明确替代的旧结论，"
                        "保留名称、数字、日期和关键因果。没有内容的栏目写“无”。"
                    ),
                },
                {"role": "user", "content": f"已有摘要：\n{thread.summary or '无'}\n\n新增对话：\n{transcript}"},
            ]
            summary, _ = await _non_stream_completion(DEFAULT_MODEL, prompt, max_tokens=2500)
            thread.summary = summary
            thread.summary_through_message_id = rows[-1].id
            thread.updated_at = now_bjt()
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI Chat history summarization skipped: {}", redact_secret_text(exc))


async def stream_turn(
    *,
    user_id: str,
    user_message_id: str,
    assistant_message_id: str,
    duplicate: bool = False,
    lease_backend: str | None = None,
) -> AsyncGenerator[str, None]:
    lease_backend = lease_backend or await acquire_stream_lease(user_id, assistant_message_id)
    seq = 0
    content = ""
    usage: dict[str, Any] = {}
    degraded = False
    vision_model: str | None = None
    started = time.monotonic()
    try:
        async with async_session_factory() as db:
            user_message = await db.get(AIChatMessage, user_message_id)
            assistant = await db.get(AIChatMessage, assistant_message_id)
            if user_message is None or assistant is None or user_message.user_id != user_id or assistant.user_id != user_id:
                raise AIChatUpstreamError("message_not_found", "消息不存在", status_code=404)
            seq += 1
            yield _sse("message.started", seq, {
                "thread_id": assistant.thread_id,
                "message": _message_payload(assistant),
                "duplicate": duplicate,
            })
            if duplicate:
                if assistant.content:
                    seq += 1
                    yield _sse("message.delta", seq, {"thread_id": assistant.thread_id, "message_id": assistant.id, "delta": assistant.content})
                seq += 1
                terminal = {
                    "completed": "message.completed",
                    "failed": "message.failed",
                }.get(assistant.status, "message.interrupted")
                yield _sse(terminal, seq, {"thread_id": assistant.thread_id, "message": _message_payload(assistant)})
                return
            thread = await get_owned_thread(db, user_id=user_id, thread_id=assistant.thread_id)
            if user_message.attachment_ids:
                seq += 1
                yield _sse("vision.started", seq, {"thread_id": thread.id, "message_id": assistant.id})
            context, vision_model = await _context_for_turn(
                db,
                thread=thread,
                user_message=user_message,
                selected_model=str(assistant.model),
            )
            reasoning_effort = _normalize_reasoning_effort(
                str(assistant.model),
                str((assistant.message_meta or {}).get("reasoning_effort") or "auto"),
            )
            if user_message.attachment_ids:
                seq += 1
                yield _sse("vision.completed", seq, {
                    "thread_id": thread.id,
                    "message_id": assistant.id,
                    "vision_model": vision_model,
                })

        chunk_count = 0
        async for event in _upstream_events_with_heartbeat(
            str(assistant.model),
            context,
            reasoning_effort=reasoning_effort,
        ):
            if await _is_cancelled(assistant_message_id):
                raise AIChatUserCancelled
            if event["type"] == "heartbeat":
                seq += 1
                yield _sse("message.heartbeat", seq, {
                    "thread_id": assistant.thread_id,
                    "message_id": assistant_message_id,
                })
                continue
            if event["type"] == "usage":
                usage = event.get("usage") or {}
                degraded = bool(event.get("degraded"))
                continue
            delta = str(event.get("text") or "")
            if not delta:
                continue
            content += delta
            chunk_count += 1
            seq += 1
            yield _sse("message.delta", seq, {
                "thread_id": assistant.thread_id,
                "message_id": assistant_message_id,
                "model": assistant.model,
                "delta": delta,
            })
            if chunk_count % 20 == 0:
                await _persist_assistant(
                    message_id=assistant_message_id,
                    content=content,
                    status="streaming",
                    event_seq=seq,
                    vision_model=vision_model,
                    degraded=degraded,
                )
        latency_ms = int((time.monotonic() - started) * 1000)
        completed = await _persist_assistant(
            message_id=assistant_message_id,
            content=content,
            status="completed",
            event_seq=seq + 1,
            usage=usage,
            latency_ms=latency_ms,
            vision_model=vision_model,
            degraded=degraded,
        )
        seq += 1
        if completed:
            model_profile = _model_profile(str(completed.model))
            yield _sse("message.completed", seq, {
                "thread_id": completed.thread_id,
                "message": _message_payload(completed),
                "stream_degraded": degraded,
            })
            await audit.log(
                user_id,
                "hall.ai_chat.run",
                "skill",
                CAPABILITY_ID,
                detail={
                    "thread_id": completed.thread_id,
                    "message_id": completed.id,
                    "model": completed.model,
                    "provider_group": completed.provider_group,
                    "vision_model": vision_model,
                    "image_count": len(user_message.attachment_ids or []),
                    "latency_ms": latency_ms,
                    "usage": usage,
                    "chat_context_tokens": model_profile["chat_context_tokens"],
                    "chat_max_output_tokens": model_profile["chat_max_output_tokens"],
                    "reasoning_effort": reasoning_effort,
                    "limit_policy_version": MODEL_LIMIT_POLICY_VERSION,
                    "status": "completed",
                },
            )
            asyncio.create_task(_maybe_summarize_thread(completed.thread_id, user_id))
    except AIChatUserCancelled:
        latency_ms = int((time.monotonic() - started) * 1000)
        interrupted = await _persist_assistant(
            message_id=assistant_message_id,
            content=content,
            status="interrupted",
            event_seq=seq + 1,
            usage=usage,
            latency_ms=latency_ms,
            vision_model=vision_model,
            degraded=degraded,
            error_code="cancelled",
            error_message="用户停止或连接中断",
        )
        seq += 1
        if interrupted:
            yield _sse("message.interrupted", seq, {"thread_id": interrupted.thread_id, "message": _message_payload(interrupted)})
    except (asyncio.CancelledError, GeneratorExit):
        # A disconnected browser closes the response generator. Persist the
        # partial result, but never yield while Python is finalizing it.
        await _shielded_stream_cleanup(
            _persist_assistant(
                message_id=assistant_message_id,
                content=content,
                status="interrupted",
                event_seq=seq + 1,
                usage=usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                vision_model=vision_model,
                degraded=degraded,
                error_code="connection_closed",
                error_message="浏览器连接中断",
            )
        )
        raise
    except Exception as exc:  # noqa: BLE001
        error = exc if isinstance(exc, AIChatUpstreamError) else AIChatUpstreamError(
            "upstream_error", redact_secret_text(exc, limit=300) or "模型服务异常"
        )
        failed = await _persist_assistant(
            message_id=assistant_message_id,
            content=content,
            status="failed" if not content else "interrupted",
            event_seq=seq + 1,
            usage=usage,
            latency_ms=int((time.monotonic() - started) * 1000),
            vision_model=vision_model,
            degraded=degraded,
            error_code=error.code,
            error_message=str(error),
        )
        seq += 1
        if failed:
            yield _sse("message.failed", seq, {
                "thread_id": failed.thread_id,
                "message": _message_payload(failed),
                "error": {"code": error.code, "message": redact_secret_text(error, limit=300)},
            })
    finally:
        _local_cancelled_messages.discard(assistant_message_id)
        await _shielded_stream_cleanup(
            release_stream_lease(user_id, assistant_message_id, lease_backend)
        )
