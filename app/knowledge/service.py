"""Department knowledge base service.

Production retrieval is exposed as a formal LightRAG-compatible RAG surface:
document ingestion creates auditable index jobs, chunks are embedded, retrieval
runs hybrid semantic + keyword recall with optional reranking, and ask/context
return citation metadata. Deployments can point at a LightRAG server through
SystemConfig; local/dev keeps an embedded implementation for tests and offline
operation.
"""

from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import math
import mimetypes
import os
import re
import socket
import time
import uuid
from pathlib import Path
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import (
    can_access_department,
    can_manage_department,
    get_accessible_departments,
    get_primary_department_id,
    get_related_departments,
    role_matches_any,
)
from app.auth.models import User
from app.common.ai import call_llm, call_llm_multimodal, get_ai_config, redact_secret_text
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.common.models import SystemConfig
from app.datasources.models import DataSource
from app.knowledge.models import (
    DepartmentKnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexJob,
    KnowledgeMediaAsset,
    KnowledgeQueryLog,
)
from app.org.models import OrgUnit
from app.skills.core.models import Skill

VECTOR_DIM = 128
RAG_BACKEND = "lightrag"
DEFAULT_RAG_MODE = "hybrid"

EMBEDDING_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "api_base": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen3-Embedding-8B",
        "models": {
            "Qwen/Qwen3-Embedding-8B": {"dim": 4096, "max_tokens": 32768},
            "Qwen/Qwen3-Embedding-4B": {"dim": 2560, "max_tokens": 32768},
            "Qwen/Qwen3-Embedding-0.6B": {"dim": 1024, "max_tokens": 32768},
            "BAAI/bge-m3": {"dim": 1024, "max_tokens": 8192},
            "BAAI/bge-large-zh-v1.5": {"dim": 1024, "max_tokens": 512},
            "BAAI/bge-large-en-v1.5": {"dim": 1024, "max_tokens": 512},
            "netease-youdao/bce-embedding-base_v1": {"dim": 768, "max_tokens": 512},
        },
    },
    "ollama": {
        "label": "Ollama 本地",
        "api_base": "http://127.0.0.1:11434/v1",
        "default_model": "bge-m3",
        "requires_api_key": False,
        "models": {
            "bge-m3": {"dim": 1024, "max_tokens": 8192},
        },
    },
    "openai": {
        "label": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "default_model": "text-embedding-3-large",
        "models": {
            "text-embedding-3-large": {"dim": 3072, "max_tokens": 8191},
            "text-embedding-3-small": {"dim": 1536, "max_tokens": 8191},
        },
    },
    "custom": {
        "label": "OpenAI 兼容自定义",
        "api_base": "",
        "default_model": "",
        "models": {},
    },
}

RERANK_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "api_base": "https://api.siliconflow.cn/v1",
        "default_model": "BAAI/bge-reranker-v2-m3",
        "models": {
            "BAAI/bge-reranker-v2-m3": {},
            "netease-youdao/bce-reranker-base_v1": {},
            "Qwen/Qwen3-Reranker-8B": {},
            "Qwen/Qwen3-Reranker-4B": {},
            "Qwen/Qwen3-Reranker-0.6B": {},
        },
    },
    "custom": {
        "label": "OpenAI 兼容自定义",
        "api_base": "",
        "default_model": "",
        "models": {},
    },
}
MAX_DOCUMENT_CHARS = 800_000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_CHUNK_CHARS = 1_100
CHUNK_OVERLAP_CHARS = 160
MAX_SEARCH_CANDIDATES = 800
DEFAULT_TOP_K = 8
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "are",
    "was",
    "were",
    "have",
    "has",
    "will",
    "you",
    "your",
    "我们",
    "这个",
    "一个",
    "以及",
    "或者",
    "如果",
    "可以",
    "需要",
    "进行",
    "使用",
    "部门",
}
SOURCE_TYPES = {
    "manual",
    "skill",
    "datasource",
    "upload",
    "url",
    "note",
    "execution",
    "report",
    "todo",
    "feedback",
    "sf",
    "agent",
    "training",
}
DOCUMENT_STATUSES = {"active", "archived", "deleted"}
INDEX_ACTIONS = {"upsert", "delete", "reindex", "sync", "import_url", "upload"}
INDEX_STATUSES = {"pending", "running", "indexed", "failed"}
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
KNOWLEDGE_MEDIA_ROOT = Path(os.environ.get("SKILLFORGE_KNOWLEDGE_MEDIA_DIR", "data/knowledge_media"))


def _config_value(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        if "value" in value:
            return value.get("value")
        if "enabled" in value and len(value) == 1:
            return value.get("enabled")
    return value


def _normalize_provider(value: Any, *, kind: str) -> str:
    provider = str(value or "").strip().lower()
    presets = EMBEDDING_PROVIDER_PRESETS if kind == "embedding" else RERANK_PROVIDER_PRESETS
    return provider if provider in presets else "custom"


def _infer_provider(raw_provider: Any, raw_api_base: Any, presets: dict[str, dict[str, Any]], *, default: str) -> str:
    provider = str(raw_provider or "").strip().lower()
    if provider:
        return provider if provider in presets else "custom"
    api_base = str(raw_api_base or "").strip().rstrip("/")
    if api_base:
        for key, preset in presets.items():
            preset_base = str(preset.get("api_base") or "").rstrip("/")
            if preset_base and api_base == preset_base:
                return key
        return "custom"
    return default


def _embedding_requires_api_key(config: dict[str, Any]) -> bool:
    provider = str(config.get("ai.embedding.provider") or "").strip().lower()
    preset = EMBEDDING_PROVIDER_PRESETS.get(provider) or {}
    return bool(preset.get("requires_api_key", True))


def _normalize_siliconflow_model(model: str, preset: dict[str, Any]) -> str:
    """Avoid SiliconFlow Pro/* paid-tier models in managed presets."""
    clean = str(model or "").strip()
    if clean.startswith("Pro/"):
        clean = clean.removeprefix("Pro/")
    models = preset.get("models") or {}
    if clean not in models:
        clean = str(preset.get("default_model") or "").strip()
    return clean


def _apply_rag_provider_defaults(config: dict[str, Any]) -> dict[str, Any]:
    embedding_provider = _infer_provider(
        config.get("ai.embedding.provider"),
        config.get("ai.embedding.api_base"),
        EMBEDDING_PROVIDER_PRESETS,
        default="siliconflow",
    )
    embedding_preset = EMBEDDING_PROVIDER_PRESETS[embedding_provider]
    config["ai.embedding.provider"] = embedding_provider
    if embedding_provider != "custom":
        config["ai.embedding.api_base"] = str(config.get("ai.embedding.api_base") or embedding_preset["api_base"]).strip().rstrip("/")
        embedding_model = str(config.get("ai.embedding.model") or embedding_preset["default_model"]).strip()
        if embedding_provider == "siliconflow":
            embedding_model = _normalize_siliconflow_model(embedding_model, embedding_preset)
        config["ai.embedding.model"] = embedding_model
    else:
        config["ai.embedding.api_base"] = str(config.get("ai.embedding.api_base") or "").strip().rstrip("/")
        config["ai.embedding.model"] = str(config.get("ai.embedding.model") or "").strip()

    model_meta = (embedding_preset.get("models") or {}).get(str(config.get("ai.embedding.model") or ""), {})
    if model_meta.get("dim"):
        config["ai.embedding.dim"] = model_meta["dim"]

    rerank_provider = _infer_provider(
        config.get("ai.reranker.provider"),
        config.get("ai.reranker.api_base"),
        RERANK_PROVIDER_PRESETS,
        default="siliconflow",
    )
    rerank_preset = RERANK_PROVIDER_PRESETS[rerank_provider]
    config["ai.reranker.provider"] = rerank_provider
    if rerank_provider != "custom":
        config["ai.reranker.api_base"] = str(config.get("ai.reranker.api_base") or rerank_preset["api_base"]).strip().rstrip("/")
        rerank_model = str(config.get("ai.reranker.model") or rerank_preset["default_model"]).strip()
        if rerank_provider == "siliconflow":
            rerank_model = _normalize_siliconflow_model(rerank_model, rerank_preset)
        config["ai.reranker.model"] = rerank_model
    else:
        config["ai.reranker.api_base"] = str(config.get("ai.reranker.api_base") or "").strip().rstrip("/")
        config["ai.reranker.model"] = str(config.get("ai.reranker.model") or "").strip()
    if not config.get("ai.reranker.api_key") and rerank_provider == embedding_provider:
        config["ai.reranker.api_key"] = config.get("ai.embedding.api_key") or ""
    return config


async def _load_rag_config(db: AsyncSession) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "knowledge.rag.backend": RAG_BACKEND,
        "knowledge.rag.enabled": False,
        "knowledge.lightrag.api_base": "",
        "knowledge.lightrag.api_key": "",
        "knowledge.lightrag.timeout": 30,
        "knowledge.lightrag.mode": "",
        "knowledge.lightrag.flavor": "official",
        "knowledge.rag.strict_server": True,
        "knowledge.docling.enabled": False,
        "knowledge.docling.api_base": "",
        "knowledge.docling.timeout": 30,
        "knowledge.vlm.provider": "",
        "knowledge.vlm.api_base": "",
        "knowledge.vlm.api_key": "",
        "knowledge.vlm.model": "",
        "knowledge.vlm.timeout": 45,
        "ai.embedding.provider": "",
        "ai.embedding.scenario": "knowledge_rag",
        "ai.embedding.api_base": "",
        "ai.embedding.api_key": "",
        "ai.embedding.model": "",
        "ai.embedding.dim": VECTOR_DIM,
        "ai.embedding.timeout": 30,
        "ai.reranker.provider": "",
        "ai.reranker.api_base": "",
        "ai.reranker.api_key": "",
        "ai.reranker.model": "",
        "ai.reranker.enabled": False,
        "ai.reranker.timeout": 20,
    }
    try:
        rows = (
            await db.execute(
                select(SystemConfig).where(
                    or_(
                        SystemConfig.key.like("knowledge.rag.%"),
                        SystemConfig.key.like("knowledge.lightrag.%"),
                        SystemConfig.key.like("knowledge.docling.%"),
                        SystemConfig.key.like("knowledge.vlm.%"),
                        SystemConfig.key.like("ai.embedding.%"),
                        SystemConfig.key.like("ai.reranker.%"),
                    )
                )
            )
        ).scalars().all()
    except Exception:
        rows = []
    config = dict(defaults)
    for row in rows:
        config[row.key] = _config_value(row.value, config.get(row.key))
    config = _apply_rag_provider_defaults(config)
    api_base = str(config.get("knowledge.lightrag.api_base") or "").strip().rstrip("/")
    mode = str(config.get("knowledge.lightrag.mode") or "").strip().lower()
    config["knowledge.lightrag.api_base"] = api_base
    config["knowledge.lightrag.mode"] = mode or ("server" if api_base else "embedded")
    return config


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _lightrag_flavor(cfg: dict[str, Any]) -> str:
    flavor = str(cfg.get("knowledge.lightrag.flavor") or "official").strip().lower()
    aliases = {
        "hkuds": "official",
        "official_hkuds": "official",
        "skillforge_compatible": "skillforge",
        "compatible": "skillforge",
    }
    flavor = aliases.get(flavor, flavor)
    return flavor if flavor in {"official", "skillforge"} else "official"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _embed_texts(db: AsyncSession, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
    cfg = await _load_rag_config(db)
    api_base = str(cfg.get("ai.embedding.api_base") or "").strip().rstrip("/")
    api_key = str(cfg.get("ai.embedding.api_key") or "").strip()
    model = str(cfg.get("ai.embedding.model") or "").strip()
    timeout = _safe_float(cfg.get("ai.embedding.timeout"), 30.0)
    requires_api_key = _embedding_requires_api_key(cfg)
    if api_base and model and (api_key or not requires_api_key):
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{api_base}/embeddings",
                    headers=headers,
                    json={"model": model, "input": texts},
                )
                response.raise_for_status()
                payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            vectors = [item.get("embedding") for item in (data or []) if isinstance(item, dict)]
            if len(vectors) == len(texts) and all(isinstance(vector, list) for vector in vectors):
                dim = len(vectors[0]) if vectors else int(cfg.get("ai.embedding.dim") or VECTOR_DIM)
                return vectors, {"provider": "openai_compatible", "model": model, "vector_dim": dim}
        except Exception as exc:
            mode = str(cfg.get("knowledge.lightrag.mode") or "embedded")
            if mode == "server" and _truthy(cfg.get("knowledge.rag.strict_server")):
                raise AppError("LLM_API_ERROR", 502, {"detail": f"embedding failed: {type(exc).__name__}"}) from exc
    # Embedded local mode: deterministic vectors keep local tests/dev usable; production should configure ai.embedding.*.
    return [_hash_embedding(text) for text in texts], {
        "provider": "local_hash_embedding",
        "model": "embedded-dev",
        "vector_dim": VECTOR_DIM,
    }


async def _rerank_hits(db: AsyncSession, query: str, hits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    cfg = await _load_rag_config(db)
    if not hits or not _truthy(cfg.get("ai.reranker.enabled")):
        return hits, False, {"provider": "none"}
    api_base = str(cfg.get("ai.reranker.api_base") or "").strip().rstrip("/")
    api_key = str(cfg.get("ai.reranker.api_key") or "").strip()
    model = str(cfg.get("ai.reranker.model") or "").strip()
    timeout = _safe_float(cfg.get("ai.reranker.timeout"), 20.0)
    if not (api_base and api_key and model):
        return hits, False, {"provider": "none"}
    try:
        documents = [hit.get("content") or hit.get("snippet") or "" for hit in hits]
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{api_base}/rerank",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "query": query, "documents": documents},
            )
            response.raise_for_status()
            payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if isinstance(results, list):
            by_index: dict[int, float] = {}
            for item in results:
                if not isinstance(item, dict):
                    continue
                index = int(item.get("index", -1))
                score = item.get("relevance_score", item.get("score"))
                if 0 <= index < len(hits):
                    by_index[index] = _safe_float(score, hits[index].get("score", 0.0))
            if by_index:
                reranked = []
                for idx, hit in enumerate(hits):
                    row = dict(hit)
                    if idx in by_index:
                        row["rerank_score"] = round(by_index[idx], 4)
                        row["score"] = round((row.get("score", 0.0) * 0.35) + (by_index[idx] * 0.65), 4)
                    reranked.append(row)
                reranked.sort(key=lambda item: (item.get("rerank_score", item.get("score", 0)), item.get("score", 0)), reverse=True)
                return reranked, True, {"provider": "openai_compatible", "model": model}
    except Exception:
        return hits, False, {"provider": "error_fallback", "model": model}
    return hits, False, {"provider": "none"}


def _rag_retrieval_meta(cfg: dict[str, Any], embedding: dict[str, Any], *, rerank_used: bool, source_trace: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "backend": RAG_BACKEND,
        "mode": DEFAULT_RAG_MODE,
        "lightrag_mode": cfg.get("knowledge.lightrag.mode") or "embedded",
        "embedding": embedding.get("provider") or "unknown",
        "embedding_model": embedding.get("model"),
        "vector_dim": embedding.get("vector_dim") or VECTOR_DIM,
        "rerank_used": rerank_used,
        "source_trace": source_trace or {},
    }


def _is_read_all(user: User) -> bool:
    return role_matches_any(user, ("admin",)) or bool(getattr(user, "can_view_all", False))


def _clean_text(value: Any, *, limit: int = 0, required: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
    if required and not text:
        raise AppError("PARAM_INVALID", 422)
    return text


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in tags[:30]:
        tag = _clean_text(item, limit=30)
        if tag and tag not in seen:
            seen.add(tag)
            normalized.append(tag)
    return normalized


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{1,}|[\u4e00-\u9fff]+", (text or "").lower())
    tokens: list[str] = []
    for part in raw:
        if not part or part in STOPWORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) <= 4:
                tokens.append(part)
            for idx, char in enumerate(part):
                if char not in STOPWORDS:
                    tokens.append(char)
                if idx + 2 <= len(part):
                    tokens.append(part[idx : idx + 2])
                if idx + 3 <= len(part):
                    tokens.append(part[idx : idx + 3])
        else:
            tokens.append(part)
    return [token for token in tokens if token and token not in STOPWORDS]


def _hash_embedding(text: str) -> list[float]:
    vec = [0.0] * VECTOR_DIM
    counts = Counter(_tokenize(text))
    if not counts:
        return vec
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % VECTOR_DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(v * v for v in vec))
    if not norm:
        return vec
    return [round(v / norm, 6) for v in vec]


def _cosine(left: list[float] | Any, right: list[float] | Any) -> float:
    if not isinstance(left, list) or not isinstance(right, list) or not left or not right:
        return 0.0
    size = min(len(left), len(right))
    try:
        return float(sum(float(left[idx]) * float(right[idx]) for idx in range(size)))
    except (TypeError, ValueError):
        return 0.0


def _extract_keywords(text: str, *, limit: int = 16) -> list[str]:
    counts = Counter(_tokenize(text))
    return [token for token, _ in counts.most_common(limit)]


def _extract_entities(text: str, *, limit: int = 20) -> list[dict[str, str]]:
    entities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    patterns = [
        ("skill_id", r"\b[a-z][a-z0-9-]{2,60}\b"),
        ("version", r"\bv?\d+\.\d+(?:\.\d+)?\b"),
        ("metric", r"\b(?:roi|ppc|ctr|cvr|gmv|uv|pv|cpu|gpu)\b"),
        ("cn_phrase", r"[\u4e00-\u9fff]{3,12}"),
    ]
    for kind, pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.I):
            value = match.group(0).strip()
            if len(value) < 2:
                continue
            key = (kind, value.lower())
            if key in seen:
                continue
            seen.add(key)
            entities.append({"type": kind, "value": value[:80]})
            if len(entities) >= limit:
                return entities
    return entities


def _chunk_text(text: str) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > DEFAULT_CHUNK_CHARS * 2:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(paragraph), DEFAULT_CHUNK_CHARS - CHUNK_OVERLAP_CHARS):
                chunk = paragraph[start : start + DEFAULT_CHUNK_CHARS].strip()
                if chunk:
                    chunks.append(chunk)
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= DEFAULT_CHUNK_CHARS:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = paragraph
    if current:
        chunks.append(current.strip())
    return chunks[:400]


def _snippet(text: str, query_terms: list[str], *, limit: int = 260) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    lower = clean.lower()
    first = -1
    for term in query_terms:
        if not term:
            continue
        idx = lower.find(term.lower())
        if idx >= 0 and (first < 0 or idx < first):
            first = idx
    if first < 0:
        return clean[:limit].rstrip() + "..."
    start = max(first - 80, 0)
    end = min(start + limit, len(clean))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def _normalize_modality(value: Any) -> str:
    modality = str(value or "all").strip().lower()
    return modality if modality in {"all", "text", "image"} else "all"


def _detect_image_mime(filename: str, content_type: str | None, data: bytes) -> str | None:
    mime = (content_type or "").split(";")[0].strip().lower()
    name = (filename or "").lower()
    guessed = (mimetypes.guess_type(name)[0] or "").lower()
    candidate = mime if mime in IMAGE_MIME_TYPES else guessed if guessed in IMAGE_MIME_TYPES else ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return candidate or None


def _image_extension(mime: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime, ".img")


def _store_media_file(kb_id: str, digest: str, mime: str, data: bytes) -> str:
    root = KNOWLEDGE_MEDIA_ROOT / kb_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}{_image_extension(mime)}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


def _safe_media_path(storage_path: str) -> Path:
    path = Path(storage_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    root = KNOWLEDGE_MEDIA_ROOT
    if not root.is_absolute():
        root = Path.cwd() / root
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
        return resolved
    except Exception as exc:
        raise AppError("AUTH_PERMISSION_DENIED", 403, {"detail": "invalid media path"}) from exc


def _image_data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _media_asset_to_dict(asset: KnowledgeMediaAsset | None, *, include_url: bool = True) -> dict[str, Any] | None:
    if not asset:
        return None
    payload = {
        "id": asset.id,
        "document_id": asset.document_id,
        "kb_id": asset.kb_id,
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256,
        "status": asset.status,
        "caption": asset.caption,
        "visible_text": asset.visible_text,
        "objects": asset.objects_json or [],
        "metadata": asset.metadata_json or {},
        "index_error": asset.index_error,
        "indexed_at": isoformat_bjt(asset.indexed_at),
        "created_at": isoformat_bjt(asset.created_at),
        "updated_at": isoformat_bjt(asset.updated_at),
    }
    if include_url:
        payload["content_url"] = f"/api/knowledge/media/{asset.id}/content"
    return payload


def _media_summary_from_metadata(doc: KnowledgeDocument, media_asset_id: str | None = None) -> dict[str, Any] | None:
    metadata = doc.metadata_json or {}
    raw_assets = metadata.get("media_assets") if isinstance(metadata, dict) else None
    if not isinstance(raw_assets, list):
        return None
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        if media_asset_id and str(item.get("id") or "") != str(media_asset_id):
            continue
        summary = dict(item)
        if summary.get("id") and not summary.get("content_url"):
            summary["content_url"] = f"/api/knowledge/media/{summary['id']}/content"
        return summary
    return None


def _base_to_dict(base: DepartmentKnowledgeBase, *, stats: dict[str, Any] | None = None, can_write: bool = False) -> dict[str, Any]:
    payload = {
        "id": base.id,
        "org_unit_id": base.org_unit_id,
        "department": base.department,
        "name": base.name,
        "description": base.description,
        "storage_backend": base.storage_backend,
        "status": base.status,
        "config": base.config_json or {},
        "stats": stats or base.stats_json or {},
        "can_write": can_write,
        "created_at": isoformat_bjt(base.created_at),
        "updated_at": isoformat_bjt(base.updated_at),
    }
    return payload


def _document_to_dict(doc: KnowledgeDocument, *, include_content: bool = False, can_write: bool = False) -> dict[str, Any]:
    metadata = doc.metadata_json or {}
    media_assets = metadata.get("media_assets") if isinstance(metadata, dict) else []
    payload = {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "org_unit_id": doc.org_unit_id,
        "department": doc.department,
        "title": doc.title,
        "source_type": doc.source_type,
        "source_ref": doc.source_ref,
        "source_hash": doc.source_hash,
        "status": doc.status,
        "index_status": getattr(doc, "index_status", None) or ("indexed" if doc.indexed_at else "pending"),
        "index_error": getattr(doc, "index_error", None),
        "index_version": getattr(doc, "index_version", None) or 1,
        "content_mime": getattr(doc, "content_mime", None),
        "source_version": getattr(doc, "source_version", None),
        "tags": doc.tags_json or [],
        "metadata": metadata,
        "modality": metadata.get("modality", "text") if isinstance(metadata, dict) else "text",
        "media_assets": media_assets if isinstance(media_assets, list) else [],
        "chunk_count": doc.chunk_count,
        "token_count": doc.token_count,
        "indexed_at": isoformat_bjt(doc.indexed_at),
        "created_by": doc.created_by,
        "updated_by": doc.updated_by,
        "created_at": isoformat_bjt(doc.created_at),
        "updated_at": isoformat_bjt(doc.updated_at),
        "can_write": can_write,
    }
    if include_content:
        payload["content"] = doc.content
    return payload


async def ensure_department_knowledge_bases(
    db: AsyncSession,
    *,
    actor_id: str | None = None,
) -> list[DepartmentKnowledgeBase]:
    orgs = (
        await db.execute(
            select(OrgUnit)
            .where(OrgUnit.type == "department")
            .order_by(OrgUnit.sort_order, OrgUnit.name, OrgUnit.id)
        )
    ).scalars().all()
    if not orgs:
        return []

    existing = (
        await db.execute(
            select(DepartmentKnowledgeBase).where(
                DepartmentKnowledgeBase.org_unit_id.in_([org.id for org in orgs])
            )
        )
    ).scalars().all()
    by_org = {base.org_unit_id: base for base in existing}
    changed = False
    for org in orgs:
        name = org.name or org.id
        base = by_org.get(org.id)
        if base is None:
            base = DepartmentKnowledgeBase(
                id=_stable_id("kb", org.id),
                org_unit_id=org.id,
                department=name,
                name=f"{name}知识库",
                description="自动维护的部门知识库，承载业务 SOP、Skill 说明、数据资产说明和检索问答。",
                created_by=actor_id or "system",
                storage_backend=RAG_BACKEND,
                config_json={
                    "backend": RAG_BACKEND,
                    "mode": DEFAULT_RAG_MODE,
                    "embedding": "ai.embedding.*",
                    "retrieval": "lightrag_hybrid_graph_vector_keyword",
                    "reranker": "ai.reranker.*",
                },
            )
            db.add(base)
            by_org[org.id] = base
            changed = True
        else:
            if base.storage_backend != RAG_BACKEND:
                base.storage_backend = RAG_BACKEND
                config = dict(base.config_json or {})
                config.update({"backend": RAG_BACKEND, "mode": DEFAULT_RAG_MODE, "retrieval": "lightrag_hybrid_graph_vector_keyword"})
                base.config_json = config
                changed = True
            if base.department != name:
                base.department = name
                base.name = base.name or f"{name}知识库"
                changed = True
    if changed:
        await db.flush()
    return list(by_org.values())


async def _ensure_user_fallback_base(db: AsyncSession, user: User) -> DepartmentKnowledgeBase | None:
    department = _clean_text(getattr(user, "department", None), limit=100)
    if not department:
        return None
    base_id = _stable_id("kb", f"department-name:{department}")
    base = await db.get(DepartmentKnowledgeBase, base_id)
    if base:
        return base
    base = DepartmentKnowledgeBase(
        id=base_id,
        org_unit_id=None,
        department=department,
        name=f"{department}知识库",
        description="自动维护的部门知识库，承载业务 SOP、Skill 说明、数据资产说明和检索问答。",
        created_by=getattr(user, "id", None) or "system",
        storage_backend=RAG_BACKEND,
        config_json={
            "backend": RAG_BACKEND,
            "mode": DEFAULT_RAG_MODE,
            "embedding": "ai.embedding.*",
            "retrieval": "lightrag_hybrid_graph_vector_keyword",
            "reranker": "ai.reranker.*",
            "fallback_department_name": True,
        },
    )
    db.add(base)
    await db.flush()
    return base


async def _visible_base_filter(db: AsyncSession, user: User):
    if _is_read_all(user):
        return True
    accessible = await get_accessible_departments(db, user)
    org_ids = set(accessible or set())
    dept = _clean_text(getattr(user, "department", None), limit=100)
    conditions = []
    if org_ids:
        conditions.append(DepartmentKnowledgeBase.org_unit_id.in_(sorted(org_ids)))
    if dept:
        conditions.append(DepartmentKnowledgeBase.department == dept)
    return or_(*conditions) if conditions else False


async def _visible_chunk_filter(db: AsyncSession, user: User):
    if _is_read_all(user):
        return True
    accessible = await get_accessible_departments(db, user)
    org_ids = set(accessible or set())
    dept = _clean_text(getattr(user, "department", None), limit=100)
    conditions = []
    if org_ids:
        conditions.append(KnowledgeChunk.org_unit_id.in_(sorted(org_ids)))
    if dept:
        conditions.append(KnowledgeChunk.department == dept)
    return or_(*conditions) if conditions else False


async def _visible_document_filter(db: AsyncSession, user: User):
    if _is_read_all(user):
        return True
    accessible = await get_accessible_departments(db, user)
    org_ids = set(accessible or set())
    dept = _clean_text(getattr(user, "department", None), limit=100)
    conditions = []
    if org_ids:
        conditions.append(KnowledgeDocument.org_unit_id.in_(sorted(org_ids)))
    if dept:
        conditions.append(KnowledgeDocument.department == dept)
    return or_(*conditions) if conditions else False


async def _can_write_base(db: AsyncSession, user: User, base: DepartmentKnowledgeBase) -> bool:
    if role_matches_any(user, ("admin",)):
        return True
    if getattr(user, "role", "") == "dept_admin" and base.org_unit_id:
        return await can_manage_department(db, user, base.org_unit_id)
    if role_matches_any(user, ("aibp", "ai_engineer", "biz_owner")):
        if base.org_unit_id and await can_access_department(db, user, base.org_unit_id):
            return True
        return bool(base.department and base.department == getattr(user, "department", None))
    return False


async def _require_base_access(db: AsyncSession, user: User, base: DepartmentKnowledgeBase) -> None:
    if _is_read_all(user):
        return
    if base.org_unit_id and await can_access_department(db, user, base.org_unit_id):
        return
    if base.department and base.department == getattr(user, "department", None):
        return
    raise AppError("AUTH_DEPARTMENT_DENIED", 403)


async def _require_base_write(db: AsyncSession, user: User, base: DepartmentKnowledgeBase) -> None:
    await _require_base_access(db, user, base)
    if not await _can_write_base(db, user, base):
        raise AppError("AUTH_PERMISSION_DENIED", 403)


async def _get_base_by_selector(
    db: AsyncSession,
    user: User,
    *,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    require_write: bool = False,
) -> DepartmentKnowledgeBase:
    await ensure_department_knowledge_bases(db, actor_id=getattr(user, "id", None))
    base: DepartmentKnowledgeBase | None = None
    if kb_id:
        base = await db.get(DepartmentKnowledgeBase, kb_id)
    elif org_unit_id:
        base = (
            await db.execute(
                select(DepartmentKnowledgeBase).where(DepartmentKnowledgeBase.org_unit_id == org_unit_id)
            )
        ).scalar_one_or_none()
    else:
        primary = await get_primary_department_id(db, user)
        if primary:
            base = (
                await db.execute(
                    select(DepartmentKnowledgeBase).where(DepartmentKnowledgeBase.org_unit_id == primary)
                )
            ).scalar_one_or_none()
        if base is None:
            base = await _ensure_user_fallback_base(db, user)
    if base is None:
        raise AppError("ORG_UNIT_NOT_FOUND", 404)
    if require_write:
        await _require_base_write(db, user, base)
    else:
        await _require_base_access(db, user, base)
    return base


async def _base_stats(db: AsyncSession, base_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not base_ids:
        return {}
    doc_rows = (
        await db.execute(
            select(
                KnowledgeDocument.kb_id,
                func.count(KnowledgeDocument.id).label("doc_count"),
                func.sum(KnowledgeDocument.chunk_count).label("chunk_count"),
                func.max(KnowledgeDocument.indexed_at).label("last_indexed_at"),
            )
            .where(KnowledgeDocument.kb_id.in_(base_ids))
            .where(KnowledgeDocument.status == "active")
            .group_by(KnowledgeDocument.kb_id)
        )
    ).all()
    stats = {
        kb_id: {
            "document_count": 0,
            "chunk_count": 0,
            "media_count": 0,
            "last_indexed_at": None,
        }
        for kb_id in base_ids
    }
    for kb_id, doc_count, chunk_count, last_indexed_at in doc_rows:
        stats[kb_id] = {
            "document_count": int(doc_count or 0),
            "chunk_count": int(chunk_count or 0),
            "media_count": 0,
            "last_indexed_at": isoformat_bjt(last_indexed_at),
        }
    media_rows = (
        await db.execute(
            select(KnowledgeMediaAsset.kb_id, func.count(KnowledgeMediaAsset.id))
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeMediaAsset.document_id)
            .where(KnowledgeMediaAsset.kb_id.in_(base_ids))
            .where(KnowledgeMediaAsset.status != "deleted")
            .where(KnowledgeDocument.status == "active")
            .group_by(KnowledgeMediaAsset.kb_id)
        )
    ).all()
    for kb_id, media_count in media_rows:
        row = stats.setdefault(
            kb_id,
            {"document_count": 0, "chunk_count": 0, "media_count": 0, "last_indexed_at": None},
        )
        row["media_count"] = int(media_count or 0)
    return stats


async def list_bases(db: AsyncSession, user: User) -> dict[str, Any]:
    await ensure_department_knowledge_bases(db, actor_id=getattr(user, "id", None))
    if not _is_read_all(user):
        if not await get_primary_department_id(db, user):
            await _ensure_user_fallback_base(db, user)
    visible_filter = await _visible_base_filter(db, user)
    bases = (
        await db.execute(
            select(DepartmentKnowledgeBase)
            .where(DepartmentKnowledgeBase.status == "active")
            .where(visible_filter)
            .order_by(DepartmentKnowledgeBase.department, DepartmentKnowledgeBase.id)
        )
    ).scalars().all()
    stats = await _base_stats(db, [base.id for base in bases])
    items = [
        _base_to_dict(base, stats=stats.get(base.id), can_write=await _can_write_base(db, user, base))
        for base in bases
    ]
    return {
        "items": items,
        "total": len(items),
        "storage": {
            "backend": RAG_BACKEND,
            "mode": DEFAULT_RAG_MODE,
            "embedding": "ai.embedding.*",
            "vector_dim": VECTOR_DIM,
            "retrieval": "lightrag_hybrid_graph_vector_keyword",
            "reranker": "ai.reranker.*",
            "lightrag_reference": "https://github.com/HKUDS/LightRAG",
        },
    }


async def get_summary(db: AsyncSession, user: User) -> dict[str, Any]:
    bases_payload = await list_bases(db, user)
    base_ids = [item["id"] for item in bases_payload["items"]]
    if not base_ids:
        return {
            "department_count": 0,
            "document_count": 0,
            "chunk_count": 0,
            "media_count": 0,
            "storage_backend": RAG_BACKEND,
            "last_indexed_at": None,
            "departments": [],
        }
    stats = await _base_stats(db, base_ids)
    return {
        "department_count": len(base_ids),
        "document_count": sum(item.get("document_count", 0) for item in stats.values()),
        "chunk_count": sum(item.get("chunk_count", 0) for item in stats.values()),
        "media_count": sum(item.get("media_count", 0) for item in stats.values()),
        "storage_backend": RAG_BACKEND,
        "last_indexed_at": max(
            [item.get("last_indexed_at") for item in stats.values() if item.get("last_indexed_at")] or [None]
        ),
        "departments": bases_payload["items"],
    }


async def _lightrag_server_request(
    cfg: dict[str, Any],
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    raise_http: bool = True,
) -> dict[str, Any]:
    api_base = str(cfg.get("knowledge.lightrag.api_base") or "").rstrip("/")
    if not api_base:
        raise AppError("PARAM_INVALID", 422, {"detail": "knowledge.lightrag.api_base is required for server mode"})
    api_key = str(cfg.get("knowledge.lightrag.api_key") or "").strip()
    timeout = _safe_float(cfg.get("knowledge.lightrag.timeout"), 30.0)
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    url = f"{api_base}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, params=payload or {})
            else:
                response = await client.request(method.upper(), url, headers=headers, json=payload or {})
            if raise_http:
                response.raise_for_status()
            elif response.status_code >= 400:
                return {"ok": False, "status_code": response.status_code, "text": redact_secret_text(response.text, limit=500)}
            if response.content:
                try:
                    data = response.json()
                    return data if isinstance(data, dict) else {"data": data}
                except Exception:
                    return {"data": response.text}
            return {"ok": True}
    except AppError:
        raise
    except Exception as exc:
        raise AppError("LLM_API_ERROR", 502, {"detail": f"LightRAG server request failed: {type(exc).__name__}"}) from exc


async def _lightrag_server_health(cfg: dict[str, Any]) -> dict[str, Any]:
    first = await _lightrag_server_request(cfg, "/health", method="GET", raise_http=False)
    if first.get("ok") is not False:
        first["endpoint"] = "/health"
        return first
    second = await _lightrag_server_request(cfg, "/documents/pipeline_status", method="GET", raise_http=False)
    if second.get("ok") is not False:
        second["endpoint"] = "/documents/pipeline_status"
        return second
    return {
        "ok": False,
        "health": first,
        "pipeline_status": second,
    }


async def _http_json_health(
    api_base: str,
    *,
    timeout: float = 10.0,
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    clean_base = str(api_base or "").strip().rstrip("/")
    if not clean_base:
        raise AppError("PARAM_INVALID", 422, {"detail": "api_base is required"})
    candidates = candidates or ["/health", "/"]
    async with httpx.AsyncClient(timeout=timeout) as client:
        last: dict[str, Any] | None = None
        for candidate in candidates:
            url = f"{clean_base}/{candidate.lstrip('/')}"
            try:
                response = await client.get(url)
                detail = {
                    "ok": 200 <= response.status_code < 400,
                    "status_code": response.status_code,
                    "endpoint": candidate,
                    "content_type": response.headers.get("content-type", "")[:120],
                }
                if response.content:
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            detail["data"] = data
                    except Exception:
                        detail["text_preview"] = redact_secret_text(response.text, limit=200)
                if detail["ok"]:
                    return detail
                last = detail
            except Exception as exc:  # noqa: BLE001
                last = {"ok": False, "endpoint": candidate, "error": f"{type(exc).__name__}: {exc}"}
    return last or {"ok": False}


def _lightrag_document_payload(doc: KnowledgeDocument, media_assets: list[KnowledgeMediaAsset]) -> dict[str, Any]:
    return {
        "document_id": doc.id,
        "kb_id": doc.kb_id,
        "department": doc.department,
        "title": doc.title,
        "content": doc.content,
        "modality": "image" if media_assets or (doc.metadata_json or {}).get("modality") == "image" else "text",
        "source_type": doc.source_type,
        "source_ref": doc.source_ref,
        "tags": doc.tags_json or [],
        "metadata": doc.metadata_json or {},
        "media_assets": [
            _media_asset_to_dict(asset, include_url=False) for asset in media_assets
        ],
    }


async def _lightrag_upsert_document(
    cfg: dict[str, Any],
    doc: KnowledgeDocument,
    media_assets: list[KnowledgeMediaAsset],
) -> dict[str, Any]:
    if _lightrag_flavor(cfg) == "skillforge":
        return await _lightrag_server_request(cfg, "/documents/upsert", _lightrag_document_payload(doc, media_assets))

    file_source = f"skillforge:{doc.kb_id}:{doc.id}:{doc.title}"
    official_payload = {"text": doc.content, "file_source": file_source}
    result = await _lightrag_server_request(cfg, "/documents/insert", official_payload, raise_http=False)
    if result.get("ok") is not False:
        result["endpoint"] = "/documents/insert"
        return result
    if int(result.get("status_code") or 0) == 404:
        result = await _lightrag_server_request(cfg, "/documents/text", official_payload, raise_http=False)
        if result.get("ok") is not False:
            result["endpoint"] = "/documents/text"
            return result
    raise AppError("LLM_API_ERROR", 502, {"detail": f"LightRAG official insert failed: {result}"})


async def _create_index_job(
    db: AsyncSession,
    user: User | None,
    doc: KnowledgeDocument,
    *,
    action: str = "upsert",
    payload: dict[str, Any] | None = None,
) -> KnowledgeIndexJob:
    clean_action = action if action in INDEX_ACTIONS else "upsert"
    now = now_bjt()
    job = KnowledgeIndexJob(
        job_id=f"kidx-{uuid.uuid4().hex[:18]}",
        document_id=doc.id,
        kb_id=doc.kb_id,
        org_unit_id=doc.org_unit_id,
        department=doc.department,
        action=clean_action,
        status="pending",
        payload_json=payload or {},
        created_by=getattr(user, "id", None) if user else None,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    return job


async def _document_media_assets(db: AsyncSession, document_id: str) -> list[KnowledgeMediaAsset]:
    return (
        await db.execute(
            select(KnowledgeMediaAsset)
            .where(KnowledgeMediaAsset.document_id == document_id)
            .where(KnowledgeMediaAsset.status != "deleted")
            .order_by(KnowledgeMediaAsset.created_at, KnowledgeMediaAsset.id)
        )
    ).scalars().all()


async def _process_index_job(db: AsyncSession, job: KnowledgeIndexJob, doc: KnowledgeDocument) -> None:
    now = now_bjt()
    job.status = "running"
    job.attempts = int(job.attempts or 0) + 1
    job.started_at = now
    job.updated_at = now
    doc.index_status = "running"
    doc.index_error = None
    await db.flush()
    try:
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
        media_assets = await _document_media_assets(db, doc.id)
        is_image_doc = bool(media_assets) or (doc.metadata_json or {}).get("modality") == "image"
        if job.action == "delete" or doc.status != "active":
            doc.chunk_count = 0
            doc.token_count = 0
            doc.index_status = "indexed"
            doc.indexed_at = now_bjt()
            for asset in media_assets:
                asset.status = "archived"
                asset.updated_at = now_bjt()
        else:
            chunks = _chunk_text(doc.content)
            token_total = 0
            embeddings, embedding_meta = await _embed_texts(db, chunks) if chunks else ([], {"provider": "none", "vector_dim": VECTOR_DIM})
            for idx, chunk_text in enumerate(chunks):
                tokens = _tokenize(chunk_text)
                token_total += len(tokens)
                asset = media_assets[0] if media_assets else None
                db.add(
                    KnowledgeChunk(
                        document_id=doc.id,
                        kb_id=doc.kb_id,
                        org_unit_id=doc.org_unit_id,
                        department=doc.department,
                        media_asset_id=asset.id if asset else None,
                        modality="image" if is_image_doc else "text",
                        chunk_index=idx,
                        content=chunk_text,
                        content_hash=_content_hash(chunk_text),
                        token_count=len(tokens),
                        embedding_json=embeddings[idx] if idx < len(embeddings) else _hash_embedding(chunk_text),
                        keywords_json=_extract_keywords(chunk_text),
                        entities_json=_extract_entities(chunk_text),
                    )
                )
            doc.chunk_count = len(chunks)
            doc.token_count = token_total
            doc.indexed_at = now_bjt()
            doc.index_status = "indexed"
            doc.index_error = None
            for asset_idx, asset in enumerate(media_assets):
                if asset_idx == 0 and embeddings:
                    asset.embedding_json = embeddings[0]
                asset.status = "indexed"
                asset.indexed_at = doc.indexed_at
                asset.updated_at = doc.indexed_at
            metadata = dict(doc.metadata_json or {})
            if media_assets:
                metadata["modality"] = "image"
                metadata["media_assets"] = [
                    _media_asset_to_dict(asset, include_url=True) for asset in media_assets
                ]
            metadata["rag"] = {
                "backend": RAG_BACKEND,
                "mode": DEFAULT_RAG_MODE,
                "modalities": ["image"] if is_image_doc else ["text"],
                "embedding": embedding_meta,
                "indexed_at": isoformat_bjt(doc.indexed_at),
            }
            doc.metadata_json = metadata
            cfg = await _load_rag_config(db)
            if str(cfg.get("knowledge.lightrag.mode")) == "server":
                await _lightrag_upsert_document(cfg, doc, media_assets)
        doc.index_version = int(getattr(doc, "index_version", 1) or 1) + 1
        job.status = "succeeded"
        job.error = None
        job.finished_at = now_bjt()
        job.updated_at = job.finished_at
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:2000]
        job.finished_at = now_bjt()
        job.updated_at = job.finished_at
        doc.index_status = "failed"
        doc.index_error = job.error
        await db.flush()
        raise
    await db.flush()


async def _index_document(
    db: AsyncSession,
    doc: KnowledgeDocument,
    *,
    user: User | None = None,
    action: str = "upsert",
    payload: dict[str, Any] | None = None,
) -> KnowledgeIndexJob:
    job = await _create_index_job(db, user, doc, action=action, payload=payload)
    await _process_index_job(db, job, doc)
    return job


async def create_document(
    db: AsyncSession,
    user: User,
    *,
    title: str,
    content: str,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    source_type: str = "manual",
    source_ref: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    upsert_source: bool = False,
    content_mime: str | None = None,
    source_version: str | None = None,
) -> dict[str, Any]:
    base = await _get_base_by_selector(db, user, kb_id=kb_id, org_unit_id=org_unit_id, require_write=True)
    clean_title = _clean_text(title, limit=240, required=True)
    clean_content = _clean_text(content, required=True)
    if len(clean_content) > MAX_DOCUMENT_CHARS:
        raise AppError("PARAM_INVALID", 422, {"detail": f"document content exceeds {MAX_DOCUMENT_CHARS} characters"})
    clean_source_type = _clean_text(source_type, limit=30) or "manual"
    if clean_source_type not in SOURCE_TYPES:
        raise AppError("PARAM_INVALID", 422, {"detail": "unsupported source_type"})
    clean_source_ref = _clean_text(source_ref, limit=200) or None
    doc: KnowledgeDocument | None = None
    if upsert_source and clean_source_ref:
        doc = (
            await db.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.kb_id == base.id,
                    KnowledgeDocument.source_type == clean_source_type,
                    KnowledgeDocument.source_ref == clean_source_ref,
                )
            )
        ).scalar_one_or_none()
    now = now_bjt()
    digest = _content_hash(clean_content)
    if doc is None:
        doc = KnowledgeDocument(
            id=f"kdoc-{uuid.uuid4().hex[:18]}",
            kb_id=base.id,
            org_unit_id=base.org_unit_id,
            department=base.department,
            title=clean_title,
            source_type=clean_source_type,
            source_ref=clean_source_ref,
            source_hash=digest,
            content=clean_content,
            content_hash=digest,
            tags_json=_normalize_tags(tags or []),
            metadata_json=metadata if isinstance(metadata, dict) else {},
            content_mime=_clean_text(content_mime, limit=120) or "text/plain",
            source_version=_clean_text(source_version, limit=120) or None,
            created_by=getattr(user, "id", None),
            updated_by=getattr(user, "id", None),
            created_at=now,
            updated_at=now,
        )
        db.add(doc)
    else:
        doc.title = clean_title
        doc.content = clean_content
        doc.content_hash = digest
        doc.source_hash = digest
        doc.tags_json = _normalize_tags(tags or [])
        doc.metadata_json = metadata if isinstance(metadata, dict) else {}
        if content_mime is not None:
            doc.content_mime = _clean_text(content_mime, limit=120) or doc.content_mime
        if source_version is not None:
            doc.source_version = _clean_text(source_version, limit=120) or None
        doc.status = "active"
        doc.updated_by = getattr(user, "id", None)
        doc.updated_at = now
    await db.flush()
    await _index_document(db, doc, user=user, action="upsert")
    return _document_to_dict(doc, include_content=True, can_write=True)


async def list_documents(
    db: AsyncSession,
    user: User,
    *,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    status: str = "active",
    query: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    base = None
    stmt = select(KnowledgeDocument)
    if kb_id or org_unit_id:
        base = await _get_base_by_selector(db, user, kb_id=kb_id, org_unit_id=org_unit_id)
        stmt = stmt.where(KnowledgeDocument.kb_id == base.id)
    else:
        visible = await _visible_document_filter(db, user)
        stmt = stmt.where(visible)
    if status != "all":
        if status not in DOCUMENT_STATUSES:
            raise AppError("PARAM_INVALID", 422)
        stmt = stmt.where(KnowledgeDocument.status == status)
    clean_query = _clean_text(query, limit=120)
    if clean_query:
        like = f"%{clean_query}%"
        stmt = stmt.where(or_(KnowledgeDocument.title.ilike(like), KnowledgeDocument.content.ilike(like)))
    rows = (
        await db.execute(
            stmt.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id).limit(max(1, min(limit, 200)))
        )
    ).scalars().all()
    can_write_cache: dict[str, bool] = {}
    items = []
    for doc in rows:
        if doc.kb_id not in can_write_cache:
            current_base = base or await db.get(DepartmentKnowledgeBase, doc.kb_id)
            can_write_cache[doc.kb_id] = bool(current_base and await _can_write_base(db, user, current_base))
        items.append(_document_to_dict(doc, can_write=can_write_cache.get(doc.kb_id, False)))
    return {"items": items, "total": len(items)}


async def get_document(db: AsyncSession, user: User, document_id: str) -> dict[str, Any]:
    doc = await db.get(KnowledgeDocument, document_id)
    if not doc:
        raise AppError("NOT_FOUND", 404)
    base = await db.get(DepartmentKnowledgeBase, doc.kb_id)
    if not base:
        raise AppError("NOT_FOUND", 404)
    await _require_base_access(db, user, base)
    can_write = await _can_write_base(db, user, base)
    return _document_to_dict(doc, include_content=True, can_write=can_write)


async def update_document(
    db: AsyncSession,
    user: User,
    document_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    source_type: str | None = None,
    source_ref: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    doc = await db.get(KnowledgeDocument, document_id)
    if not doc:
        raise AppError("NOT_FOUND", 404)
    base = await db.get(DepartmentKnowledgeBase, doc.kb_id)
    if not base:
        raise AppError("NOT_FOUND", 404)
    await _require_base_write(db, user, base)
    reindex = False
    if title is not None:
        doc.title = _clean_text(title, limit=240, required=True)
    if content is not None:
        clean_content = _clean_text(content, required=True)
        if len(clean_content) > MAX_DOCUMENT_CHARS:
            raise AppError("PARAM_INVALID", 422)
        doc.content = clean_content
        doc.content_hash = _content_hash(clean_content)
        doc.source_hash = doc.content_hash
        reindex = True
    if source_type is not None:
        clean_source_type = _clean_text(source_type, limit=30) or "manual"
        if clean_source_type not in SOURCE_TYPES:
            raise AppError("PARAM_INVALID", 422, {"detail": "unsupported source_type"})
        doc.source_type = clean_source_type
    if source_ref is not None:
        doc.source_ref = _clean_text(source_ref, limit=200) or None
    if tags is not None:
        doc.tags_json = _normalize_tags(tags)
    if status is not None:
        if status not in DOCUMENT_STATUSES:
            raise AppError("PARAM_INVALID", 422)
        doc.status = status
    if metadata is not None:
        doc.metadata_json = metadata if isinstance(metadata, dict) else {}
    doc.updated_by = getattr(user, "id", None)
    doc.updated_at = now_bjt()
    await db.flush()
    if reindex or status is not None:
        await _index_document(db, doc, user=user, action="delete" if doc.status != "active" else "upsert")
    return _document_to_dict(doc, include_content=True, can_write=True)


async def archive_document(db: AsyncSession, user: User, document_id: str) -> dict[str, Any]:
    return await update_document(db, user, document_id, status="archived")


async def delete_document(db: AsyncSession, user: User, document_id: str) -> dict[str, Any]:
    """Soft-delete a knowledge document while keeping audit/history rows."""
    return await update_document(db, user, document_id, status="deleted")


async def reindex_document(db: AsyncSession, user: User, document_id: str) -> dict[str, Any]:
    doc = await db.get(KnowledgeDocument, document_id)
    if not doc:
        raise AppError("NOT_FOUND", 404)
    base = await db.get(DepartmentKnowledgeBase, doc.kb_id)
    if not base:
        raise AppError("NOT_FOUND", 404)
    await _require_base_write(db, user, base)
    await _index_document(db, doc, user=user, action="reindex")
    return _document_to_dict(doc, include_content=True, can_write=True)


def _skill_asset_content(skill: Skill) -> str:
    fields = [
        f"# {skill.display_name or skill.name or skill.id}",
        f"Skill ID: {skill.id}",
        f"部门: {skill.department}",
        f"状态: {skill.status}",
        f"分类: {skill.category or '-'}",
        f"风险等级: {skill.risk_level or '-'}",
        f"触发方式: {skill.trigger_type or 'manual'} {skill.trigger_expression or ''}".strip(),
        f"当前版本: {skill.current_version or '-'}",
        f"Git commit: {skill.git_commit or '-'}",
        "",
        skill.summary or skill.description or "暂无描述",
    ]
    return "\n".join(fields)


def _datasource_asset_content(source: DataSource) -> str:
    related = ", ".join(source.related_skills or []) if isinstance(source.related_skills, list) else "-"
    fields = [
        f"# {source.name}",
        f"数据源 ID: {source.id}",
        f"部门: {source.department}",
        f"类型: {source.source_type}",
        f"可见性: {source.visibility}",
        f"活跃: {'是' if source.is_active else '否'}",
        f"相关 Skills: {related or '-'}",
        f"刷新计划: {source.schedule or '-'}",
        f"过期阈值: {source.stale_threshold_hours} 小时",
        "",
        source.description or "暂无描述",
        "",
        source.usage_hint or "",
    ]
    return "\n".join(part for part in fields if part is not None)


async def sync_department_assets(
    db: AsyncSession,
    user: User,
    *,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
) -> dict[str, Any]:
    base = await _get_base_by_selector(db, user, kb_id=kb_id, org_unit_id=org_unit_id, require_write=True)
    skill_conditions = [Skill.department == base.department]
    if base.org_unit_id:
        skill_conditions.append(Skill.org_unit_id == base.org_unit_id)
    skills = (
        await db.execute(
            select(Skill)
            .where(or_(*skill_conditions))
            .order_by(Skill.updated_at.desc(), Skill.id)
            .limit(300)
        )
    ).scalars().all()
    sources = (
        await db.execute(
            select(DataSource)
            .where(DataSource.department == base.department)
            .order_by(DataSource.updated_at.desc(), DataSource.id)
            .limit(300)
        )
    ).scalars().all()

    skill_count = 0
    for skill in skills:
        await create_document(
            db,
            user,
            kb_id=base.id,
            title=f"Skill: {skill.display_name or skill.name or skill.id}",
            content=_skill_asset_content(skill),
            source_type="skill",
            source_ref=skill.id,
            tags=["Skill", skill.status or "unknown", skill.risk_level or "R1"],
            metadata={"skill_id": skill.id, "git_commit": skill.git_commit, "current_version": skill.current_version},
            upsert_source=True,
        )
        skill_count += 1

    datasource_count = 0
    for source in sources:
        await create_document(
            db,
            user,
            kb_id=base.id,
            title=f"数据源: {source.name}",
            content=_datasource_asset_content(source),
            source_type="datasource",
            source_ref=source.id,
            tags=["数据源", source.source_type, source.visibility],
            metadata={"source_id": source.id, "related_skills": source.related_skills or []},
            upsert_source=True,
        )
        datasource_count += 1

    return {
        "kb_id": base.id,
        "department": base.department,
        "skill_documents": skill_count,
        "datasource_documents": datasource_count,
        "total": skill_count + datasource_count,
    }


async def _visible_base_ids(db: AsyncSession, user: User, *, kb_id: str | None = None, org_unit_id: str | None = None) -> tuple[list[str], DepartmentKnowledgeBase | None]:
    if kb_id or org_unit_id:
        base = await _get_base_by_selector(db, user, kb_id=kb_id, org_unit_id=org_unit_id)
        return [base.id], base
    await ensure_department_knowledge_bases(db, actor_id=getattr(user, "id", None))
    visible_filter = await _visible_base_filter(db, user)
    rows = (
        await db.execute(
            select(DepartmentKnowledgeBase)
            .where(DepartmentKnowledgeBase.status == "active")
            .where(visible_filter)
            .order_by(DepartmentKnowledgeBase.department, DepartmentKnowledgeBase.id)
        )
    ).scalars().all()
    return [row.id for row in rows], None


async def _server_search_knowledge(
    db: AsyncSession,
    cfg: dict[str, Any],
    *,
    query: str,
    kb_ids: list[str],
    top_k: int,
    modality: str = "all",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if _lightrag_flavor(cfg) == "official":
        return await _official_server_search_knowledge(
            db,
            cfg,
            query=query,
            kb_ids=kb_ids,
            top_k=top_k,
            modality=modality,
        )
    payload = await _lightrag_server_request(
        cfg,
        "/query",
        {"query": query, "kb_ids": kb_ids, "top_k": top_k, "mode": DEFAULT_RAG_MODE, "modality": modality},
    )
    raw_items = payload.get("items") or payload.get("results") or payload.get("data") or []
    if not isinstance(raw_items, list):
        raw_items = []
    doc_ids = [str(item.get("document_id") or item.get("doc_id") or "") for item in raw_items if isinstance(item, dict)]
    doc_map: dict[str, KnowledgeDocument] = {}
    if doc_ids:
        docs = (
            await db.execute(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id.in_(doc_ids))
                .where(KnowledgeDocument.kb_id.in_(kb_ids))
                .where(KnowledgeDocument.status == "active")
            )
        ).scalars().all()
        doc_map = {doc.id: doc for doc in docs}
    query_terms = _extract_keywords(query, limit=20)
    hits: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("document_id") or item.get("doc_id") or "")
        doc = doc_map.get(doc_id)
        if not doc:
            continue
        doc_modality = str((doc.metadata_json or {}).get("modality") or "text")
        if modality != "all" and doc_modality != modality:
            continue
        media = _media_summary_from_metadata(doc)
        content = str(item.get("content") or item.get("text") or item.get("snippet") or "")
        if not content:
            content = doc.content[:800]
        score = _safe_float(item.get("score", item.get("relevance_score", 0.0)))
        hits.append({
            "score": round(score, 4),
            "vector_score": round(_safe_float(item.get("vector_score"), score), 4),
            "keyword_score": round(_safe_float(item.get("keyword_score"), 0.0), 4),
            "title_score": round(_safe_float(item.get("title_score"), 0.0), 4),
            "matched_terms": item.get("matched_terms") or [],
            "chunk_id": item.get("chunk_id") or idx,
            "chunk_index": item.get("chunk_index") or 0,
            "document_id": doc.id,
            "document_title": doc.title,
            "kb_id": doc.kb_id,
            "department": doc.department,
            "source_type": doc.source_type,
            "source_ref": doc.source_ref,
            "modality": doc_modality,
            "media_asset_id": media.get("id") if media else item.get("media_asset_id"),
            "media": media,
            "tags": doc.tags_json or [],
            "snippet": _snippet(str(item.get("snippet") or content), query_terms),
            "content": content,
            "keywords": item.get("keywords") or [],
            "entities": item.get("entities") or [],
            "updated_at": isoformat_bjt(doc.updated_at),
            "source_trace": {"backend": "lightrag_server", "rank": idx + 1, "modality": doc_modality},
        })
    return hits[:top_k], {"server_payload_keys": sorted(payload.keys())[:20], "kb_ids": kb_ids, "modality": modality}


def _extract_server_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("response", "answer", "result", "data", "content", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = _extract_server_text(value)
                if nested:
                    return nested
        try:
            return str(payload)[:4000]
        except Exception:
            return ""
    return str(payload or "")[:4000]


async def _official_server_search_knowledge(
    db: AsyncSession,
    cfg: dict[str, Any],
    *,
    query: str,
    kb_ids: list[str],
    top_k: int,
    modality: str = "all",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = await _lightrag_server_request(
        cfg,
        "/query",
        {
            "query": query,
            "mode": DEFAULT_RAG_MODE,
            "top_k": top_k,
            "chunk_top_k": max(top_k, 8),
            "only_need_context": True,
        },
    )
    server_text = _extract_server_text(payload)
    query_terms = _extract_keywords(f"{query}\n{server_text}", limit=30)
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.kb_id.in_(kb_ids))
        .where(KnowledgeDocument.status == "active")
        .order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
        .limit(MAX_SEARCH_CANDIDATES)
    )
    docs = (await db.execute(stmt)).scalars().all()
    scored: list[dict[str, Any]] = []
    server_lower = server_text.lower()
    for doc in docs:
        doc_modality = str((doc.metadata_json or {}).get("modality") or "text")
        if modality != "all" and doc_modality != modality:
            continue
        content_lower = (doc.content or "").lower()
        title_lower = (doc.title or "").lower()
        matched_terms = [
            term
            for term in query_terms
            if term.lower() in content_lower or term.lower() in title_lower or term.lower() in server_lower
        ]
        id_hit = doc.id.lower() in server_lower
        title_hit = doc.title.lower() in server_lower
        keyword_score = min(len(set(matched_terms)) / max(len(set(query_terms)), 1), 1.0)
        title_score = 1.0 if title_hit or any(term.lower() in title_lower for term in query_terms) else 0.0
        score = min(1.0, 0.55 * keyword_score + 0.25 * title_score + (0.20 if id_hit else 0.0))
        if score <= 0 and query_terms:
            continue
        media = _media_summary_from_metadata(doc)
        scored.append({
            "score": round(score, 4),
            "vector_score": 0.0,
            "keyword_score": round(keyword_score, 4),
            "title_score": round(title_score, 4),
            "matched_terms": sorted(set(matched_terms))[:12],
            "chunk_id": f"official-{doc.id}",
            "chunk_index": 0,
            "document_id": doc.id,
            "document_title": doc.title,
            "kb_id": doc.kb_id,
            "department": doc.department,
            "source_type": doc.source_type,
            "source_ref": doc.source_ref,
            "modality": doc_modality,
            "media_asset_id": media.get("id") if media else None,
            "media": media,
            "tags": doc.tags_json or [],
            "snippet": _snippet(doc.content, query_terms),
            "content": doc.content[:2000],
            "keywords": _extract_keywords(doc.content, limit=16),
            "entities": _extract_entities(doc.content, limit=20),
            "updated_at": isoformat_bjt(doc.updated_at),
            "source_trace": {"backend": "lightrag_official_bridge", "modality": doc_modality},
        })
    scored.sort(key=lambda item: (item["score"], item["updated_at"] or ""), reverse=True)
    return scored[:top_k], {
        "server_payload_keys": sorted(payload.keys())[:20] if isinstance(payload, dict) else [],
        "kb_ids": kb_ids,
        "modality": modality,
        "flavor": "official",
        "server_context_chars": len(server_text),
    }


async def search_knowledge(
    db: AsyncSession,
    user: User,
    *,
    query: str,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    modality: str = "all",
) -> dict[str, Any]:
    started = time.perf_counter()
    clean_query = _clean_text(query, limit=500, required=True)
    limit = max(1, min(int(top_k or DEFAULT_TOP_K), 20))
    clean_modality = _normalize_modality(modality)
    cfg = await _load_rag_config(db)
    kb_ids, base = await _visible_base_ids(db, user, kb_id=kb_id, org_unit_id=org_unit_id)
    query_terms = _extract_keywords(clean_query, limit=20)
    embedding_meta: dict[str, Any]
    source_trace: dict[str, Any]

    if not kb_ids:
        hits: list[dict[str, Any]] = []
        embedding_meta = {"provider": "none", "vector_dim": VECTOR_DIM}
        source_trace = {"candidate_count": 0, "kb_ids": [], "modality": clean_modality}
    elif str(cfg.get("knowledge.lightrag.mode")) == "server":
        hits, source_trace = await _server_search_knowledge(
            db,
            cfg,
            query=clean_query,
            kb_ids=kb_ids,
            top_k=limit,
            modality=clean_modality,
        )
        embedding_meta = {"provider": "lightrag_server", "model": "server", "vector_dim": cfg.get("ai.embedding.dim") or VECTOR_DIM}
    else:
        vectors, embedding_meta = await _embed_texts(db, [clean_query])
        query_vector = vectors[0] if vectors else _hash_embedding(clean_query)
        stmt = select(KnowledgeChunk, KnowledgeDocument).join(
            KnowledgeDocument,
            KnowledgeDocument.id == KnowledgeChunk.document_id,
        ).where(KnowledgeDocument.status == "active").where(KnowledgeChunk.kb_id.in_(kb_ids))
        if clean_modality != "all":
            stmt = stmt.where(KnowledgeChunk.modality == clean_modality)

        keyword_conditions = []
        for term in query_terms[:8]:
            if len(term) >= 2:
                like = f"%{term}%"
                keyword_conditions.append(or_(KnowledgeChunk.content.ilike(like), KnowledgeDocument.title.ilike(like)))
        if keyword_conditions:
            stmt = stmt.where(or_(*keyword_conditions))

        rows = (
            await db.execute(
                stmt.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeChunk.id.desc()).limit(MAX_SEARCH_CANDIDATES)
            )
        ).all()
        if not rows and keyword_conditions:
            fallback_stmt = select(KnowledgeChunk, KnowledgeDocument).join(
                KnowledgeDocument,
                KnowledgeDocument.id == KnowledgeChunk.document_id,
            ).where(KnowledgeDocument.status == "active").where(KnowledgeChunk.kb_id.in_(kb_ids))
            if clean_modality != "all":
                fallback_stmt = fallback_stmt.where(KnowledgeChunk.modality == clean_modality)
            rows = (
                await db.execute(
                    fallback_stmt.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeChunk.id.desc()).limit(MAX_SEARCH_CANDIDATES)
                )
            ).all()

        scored: list[dict[str, Any]] = []
        for chunk, doc in rows:
            hit_modality = str(getattr(chunk, "modality", None) or (doc.metadata_json or {}).get("modality") or "text")
            media = _media_summary_from_metadata(doc, getattr(chunk, "media_asset_id", None))
            chunk_keywords = {str(item).lower() for item in (chunk.keywords_json or [])}
            content_lower = (chunk.content or "").lower()
            title_lower = (doc.title or "").lower()
            matched_terms = [
                term for term in query_terms
                if term.lower() in chunk_keywords or term.lower() in content_lower or term.lower() in title_lower
            ]
            keyword_score = min(len(set(matched_terms)) / max(len(set(query_terms)), 1), 1.0)
            title_score = 1.0 if any(term.lower() in title_lower for term in query_terms) else 0.0
            vector_score = max(_cosine(query_vector, chunk.embedding_json), 0.0)
            # LightRAG-style hybrid: semantic vector, local/global keyword graph hints, title boost.
            score = 0.62 * vector_score + 0.30 * keyword_score + 0.08 * title_score
            scored.append({
                "score": round(score, 4),
                "vector_score": round(vector_score, 4),
                "keyword_score": round(keyword_score, 4),
                "title_score": round(title_score, 4),
                "matched_terms": sorted(set(matched_terms))[:12],
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "document_id": doc.id,
                "document_title": doc.title,
                "kb_id": doc.kb_id,
                "department": doc.department,
                "source_type": doc.source_type,
                "source_ref": doc.source_ref,
                "modality": hit_modality,
                "media_asset_id": getattr(chunk, "media_asset_id", None),
                "media": media,
                "tags": doc.tags_json or [],
                "snippet": _snippet(chunk.content, query_terms),
                "content": chunk.content,
                "keywords": chunk.keywords_json or [],
                "entities": chunk.entities_json or [],
                "updated_at": isoformat_bjt(doc.updated_at),
                "source_trace": {"backend": "embedded_lightrag", "chunk_index": chunk.chunk_index, "modality": hit_modality},
            })
        scored.sort(key=lambda item: (item["score"], item["updated_at"] or ""), reverse=True)
        hits = scored[: max(limit * 2, limit)]
        source_trace = {"candidate_count": len(rows), "kb_ids": kb_ids, "modality": clean_modality}

    hits, rerank_used, rerank_meta = await _rerank_hits(db, clean_query, hits)
    hits = hits[:limit]
    source_trace["reranker"] = rerank_meta
    latency_ms = int((time.perf_counter() - started) * 1000)
    retrieval = _rag_retrieval_meta(cfg, embedding_meta, rerank_used=rerank_used, source_trace=source_trace)
    await _log_query(
        db,
        user,
        query=clean_query,
        mode="search",
        kb_id=base.id if base else (kb_id if len(kb_ids) == 1 else None),
        org_unit_id=base.org_unit_id if base else org_unit_id,
        department=base.department if base else None,
        top_k=limit,
        result_count=len(hits),
        context={"document_ids": [hit["document_id"] for hit in hits], "modality": clean_modality},
        retrieval_backend=RAG_BACKEND,
        lightrag_mode=str(cfg.get("knowledge.lightrag.mode") or "embedded"),
        rerank_used=rerank_used,
        latency_ms=latency_ms,
        source_trace=source_trace,
    )
    return {
        "query": clean_query,
        "modality": clean_modality,
        "top_k": limit,
        "items": hits,
        "total": len(hits),
        "retrieval": retrieval,
    }


async def _log_query(
    db: AsyncSession,
    user: User,
    *,
    query: str,
    mode: str,
    kb_id: str | None,
    org_unit_id: str | None,
    department: str | None,
    top_k: int,
    result_count: int,
    context: dict[str, Any],
    answer_preview: str | None = None,
    retrieval_backend: str | None = None,
    lightrag_mode: str | None = None,
    rerank_used: bool = False,
    latency_ms: int | None = None,
    source_trace: dict[str, Any] | None = None,
) -> None:
    row = KnowledgeQueryLog(
            kb_id=kb_id,
            org_unit_id=org_unit_id,
            department=department,
            user_id=getattr(user, "id", None),
            query=query,
            mode=mode,
            top_k=top_k,
            result_count=result_count,
            answer_preview=(answer_preview or "")[:500] or None,
            context_json=context,
            retrieval_backend=retrieval_backend,
            lightrag_mode=lightrag_mode,
            rerank_used=rerank_used,
            latency_ms=latency_ms,
            source_trace_json=source_trace or {},
        )
    db.add(row)
    await db.flush()
    try:
        from app.learning.service import capture_knowledge_query

        await capture_knowledge_query(db, row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("智能闭环捕获知识检索失败 query_log={} err={}", row.id, exc)


def _extractive_answer(query: str, hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "当前可见知识库没有检索到足够依据。请补充部门知识文档，或换一个更具体的问题。"
    lines = [f"围绕“{query}”，知识库命中了 {len(hits)} 条依据："]
    for idx, hit in enumerate(hits[:5], start=1):
        lines.append(f"{idx}. {hit['document_title']}：{hit['snippet']}")
    lines.append("以上回答只基于当前可见部门知识库内容生成。")
    return "\n".join(lines)


async def ask_knowledge(
    db: AsyncSession,
    user: User,
    *,
    query: str,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    use_llm: bool = True,
    modality: str = "all",
) -> dict[str, Any]:
    search = await search_knowledge(
        db,
        user,
        query=query,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        top_k=top_k,
        modality=modality,
    )
    hits = search["items"]
    max_score = max([_safe_float(hit.get("score"), 0.0) for hit in hits] or [0.0])
    confidence = round(min(0.98, max_score), 4)
    insufficient_evidence = not hits or max_score < 0.08
    answer = _extractive_answer(search["query"], hits)
    answer_mode = "extractive"
    if insufficient_evidence:
        answer = "当前可见知识库没有检索到足够可靠的依据，暂不生成扩展回答。请补充文档或缩小问题范围。"
        answer_mode = "insufficient_evidence"
    if use_llm and hits and not insufficient_evidence:
        context = "\n\n".join(
            f"[{idx}] {hit['document_title']} / {hit['department']}\n{hit['content']}"
            for idx, hit in enumerate(hits[:8], start=1)
        )
        prompt = (
            "你是 SkillForge 部门知识库助手。只能依据给定上下文回答；"
            "如果上下文不足，明确说明不足。回答要包含可核验的来源编号。"
        )
        user_prompt = f"问题：{search['query']}\n\n上下文：\n{context}\n\n请给出简洁答案。"
        try:
            llm_answer = await call_llm(
                prompt,
                user_prompt,
                json_mode=False,
                max_tokens=900,
                temperature=0.2,
                call_source="knowledge_base_ask",
                cost_context={
                    "user_id": getattr(user, "id", None),
                    "department": getattr(user, "department", None),
                },
            )
            if isinstance(llm_answer, str) and llm_answer.strip():
                answer = llm_answer.strip()
                answer_mode = "llm"
        except Exception:
            answer_mode = "extractive_fallback"
    await _log_query(
        db,
        user,
        query=search["query"],
        mode="ask",
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        department=hits[0]["department"] if hits else None,
        top_k=search["top_k"],
        result_count=len(hits),
        answer_preview=answer,
        context={"document_ids": [hit["document_id"] for hit in hits], "answer_mode": answer_mode, "modality": search.get("modality")},
        retrieval_backend=RAG_BACKEND,
        lightrag_mode=str((search.get("retrieval") or {}).get("lightrag_mode") or "embedded"),
        rerank_used=bool((search.get("retrieval") or {}).get("rerank_used")),
        source_trace=(search.get("retrieval") or {}).get("source_trace") or {},
    )
    citations = [
        {
            "document_id": hit["document_id"],
            "document_title": hit["document_title"],
            "department": hit["department"],
            "source_type": hit["source_type"],
            "source_ref": hit["source_ref"],
            "modality": hit.get("modality", "text"),
            "media": hit.get("media"),
            "score": hit["score"],
            "snippet": hit["snippet"],
        }
        for hit in hits
    ]
    return {
        "query": search["query"],
        "answer": answer,
        "answer_mode": answer_mode,
        "confidence": confidence,
        "insufficient_evidence": insufficient_evidence,
        "citations": citations,
        "sources": [
            {
                "document_id": hit["document_id"],
                "document_title": hit["document_title"],
                "department": hit["department"],
                "source_type": hit["source_type"],
                "source_ref": hit["source_ref"],
                "modality": hit.get("modality", "text"),
                "media": hit.get("media"),
                "score": hit["score"],
                "snippet": hit["snippet"],
            }
            for hit in hits
        ],
        "retrieval": search["retrieval"],
    }


async def build_knowledge_context(
    db: AsyncSession,
    user: User,
    *,
    query: str,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    max_chars: int = 6000,
    modality: str = "all",
) -> dict[str, Any]:
    """Return compact RAG context that Skills/Agent flows can inject into prompts."""
    limit_chars = max(500, min(int(max_chars or 6000), 20_000))
    result = await search_knowledge(
        db,
        user,
        query=query,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        top_k=top_k,
        modality=modality,
    )
    blocks: list[str] = []
    used_chars = 0
    for idx, hit in enumerate(result["items"], start=1):
        content = _clean_text(hit.get("content"), limit=limit_chars)
        header = (
            f"[{idx}] {hit['document_title']} | 部门: {hit['department']} | "
            f"来源: {hit['source_type']}:{hit.get('source_ref') or '-'} | score={hit['score']}"
        )
        block = f"{header}\n{content}".strip()
        if used_chars + len(block) > limit_chars:
            remaining = limit_chars - used_chars - len(header) - 2
            if remaining <= 120:
                break
            block = f"{header}\n{content[:remaining].rstrip()}..."
        blocks.append(block)
        used_chars += len(block)

    prompt_context = "\n\n".join(blocks)
    await _log_query(
        db,
        user,
        query=result["query"],
        mode="context",
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        department=result["items"][0]["department"] if result["items"] else None,
        top_k=result["top_k"],
        result_count=len(result["items"]),
        context={
            "document_ids": [hit["document_id"] for hit in result["items"]],
            "max_chars": limit_chars,
            "modality": result.get("modality"),
        },
        retrieval_backend=RAG_BACKEND,
        lightrag_mode=str((result.get("retrieval") or {}).get("lightrag_mode") or "embedded"),
        rerank_used=bool((result.get("retrieval") or {}).get("rerank_used")),
        source_trace=(result.get("retrieval") or {}).get("source_trace") or {},
    )
    return {
        "query": result["query"],
        "prompt_context": prompt_context,
        "context_chars": len(prompt_context),
        "sources": [
            {
                "document_id": hit["document_id"],
                "document_title": hit["document_title"],
                "department": hit["department"],
                "source_type": hit["source_type"],
                "source_ref": hit["source_ref"],
                "modality": hit.get("modality", "text"),
                "media": hit.get("media"),
                "score": hit["score"],
                "snippet": hit["snippet"],
            }
            for hit in result["items"]
        ],
        "retrieval": result["retrieval"],
        "usage": {
            "api": "POST /api/knowledge/context",
            "intended_for": "Skill/Agent prompt context injection",
            "visibility": "current user department scope",
        },
    }

def _extract_text_from_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _extract_docx_text(data: bytes) -> str:
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        raw = archive.read("word/document.xml")
    root = ET.fromstring(raw)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{ns}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{ns}t")]
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _extract_plain_text_from_bytes(filename: str, content_type: str | None, data: bytes) -> tuple[str, str]:
    name = (filename or "document").lower()
    mime = (content_type or "").split(";")[0].strip().lower()
    if len(data) > MAX_DOCUMENT_CHARS * 6:
        raise AppError("PARAM_INVALID", 422, {"detail": "uploaded file is too large"})
    if name.endswith(".docx") or mime.endswith("wordprocessingml.document"):
        try:
            return _extract_docx_text(data), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except Exception as exc:
            raise AppError("DATASOURCE_PARSE_ERROR", 422, {"detail": f"docx parse failed: {type(exc).__name__}"}) from exc
    if name.endswith(".pdf") or mime == "application/pdf":
        try:
            from pypdf import PdfReader  # type: ignore
            import io

            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages), "application/pdf"
        except ModuleNotFoundError as exc:
            raise AppError("PARAM_INVALID", 415, {"detail": "PDF import requires pypdf dependency"}) from exc
        except Exception as exc:
            raise AppError("DATASOURCE_PARSE_ERROR", 422, {"detail": f"pdf parse failed: {type(exc).__name__}"}) from exc
    if name.endswith(('.html', '.htm')) or mime in {"text/html", "application/xhtml+xml"}:
        text = data.decode("utf-8", errors="ignore")
        return _extract_text_from_html(text), "text/html"
    if mime.startswith("text/") or name.endswith((".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".xml", ".log")):
        return data.decode("utf-8", errors="ignore"), mime or "text/plain"
    try:
        return data.decode("utf-8"), mime or "text/plain"
    except UnicodeDecodeError as exc:
        raise AppError("PARAM_INVALID", 415, {"detail": "unsupported upload format"}) from exc


def _normalize_vision_list(value: Any, *, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，、\n]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    result: list[str] = []
    seen: set[str] = set()
    for item in parts[: limit * 2]:
        if isinstance(item, dict):
            item = item.get("name") or item.get("label") or item.get("text") or ""
        text = _clean_text(item, limit=60)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _build_image_proxy_text(
    *,
    title: str,
    filename: str,
    tags: list[str],
    analysis: dict[str, Any],
) -> str:
    caption = _clean_text(analysis.get("caption"), limit=1200) or f"图片文件：{filename}"
    visible_text = _clean_text(analysis.get("visible_text"), limit=4000)
    objects = _normalize_vision_list(analysis.get("objects"), limit=30)
    vision_tags = _normalize_vision_list(analysis.get("tags"), limit=20)
    lines = [
        f"# {title}",
        "类型：图片知识资产",
        f"文件名：{filename}",
        f"图片说明：{caption}",
    ]
    if visible_text:
        lines.append(f"图片可见文字：{visible_text}")
    if objects:
        lines.append(f"识别对象/场景：{'、'.join(objects)}")
    combined_tags = _normalize_tags([*tags, *vision_tags])
    if combined_tags:
        lines.append(f"标签：{'、'.join(combined_tags)}")
    if analysis.get("status") and analysis.get("status") != "ok":
        lines.append(f"视觉解析状态：{analysis.get('status')}")
    return "\n".join(lines)


async def _analyze_image_with_vision(
    data: bytes,
    *,
    mime: str,
    filename: str,
    tags: list[str] | None = None,
    user: User | None = None,
) -> dict[str, Any]:
    """Parse image into searchable text through a configured vision model.

    When ai.* vision configuration is missing/unavailable, upload still stores
    the binary and creates a visibly degraded metadata-only proxy. The status
    and index_error keep this non-silent for UI/health checks.
    """
    fallback_tags = _normalize_tags(tags or [])
    fallback = {
        "status": "vision_failed",
        "caption": f"图片文件 {filename}（视觉模型未成功解析）",
        "visible_text": "",
        "objects": [],
        "tags": fallback_tags,
        "error": "vision_model_unavailable",
    }
    system = (
        "你是 SkillForge 知识库图片解析器。请解析图片内容，用中文返回 JSON，"
        "字段必须包含 caption、visible_text、objects、tags。"
        "caption 是一句完整说明；visible_text 是图片里能看到的文字；"
        "objects/tags 为字符串数组。不要返回 Markdown。"
    )
    prompt = (
        f"文件名：{filename}\n"
        f"业务标签：{'、'.join(fallback_tags) or '-'}\n"
        "请为 RAG 检索生成准确、保守、可引用的图片描述。"
    )
    try:
        result = await call_llm_multimodal(
            system,
            [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(mime, data)}},
            ],
            json_mode=True,
            max_tokens=900,
            temperature=0.1,
            timeout=45,
            call_source="knowledge_image_index",
            cost_context={
                "user_id": getattr(user, "id", None) if user else None,
                "department": getattr(user, "department", None) if user else None,
            },
            require_system_config=True,
        )
    except Exception as exc:
        fallback["error"] = redact_secret_text(exc, limit=300) or type(exc).__name__
        return fallback
    if not isinstance(result, dict):
        fallback["error"] = "vision_model_empty_response"
        return fallback
    caption = _clean_text(result.get("caption") or result.get("summary") or result.get("description"), limit=1200)
    visible_text = _clean_text(result.get("visible_text") or result.get("ocr") or result.get("text"), limit=4000)
    objects = _normalize_vision_list(result.get("objects") or result.get("entities") or result.get("items"), limit=30)
    vision_tags = _normalize_vision_list(result.get("tags") or result.get("keywords"), limit=20)
    if not caption and not visible_text and not objects:
        fallback["error"] = "vision_model_unusable_response"
        return fallback
    return {
        "status": "ok",
        "caption": caption or f"图片文件 {filename}",
        "visible_text": visible_text,
        "objects": objects,
        "tags": _normalize_tags([*fallback_tags, *vision_tags]),
        "raw": {
            key: value
            for key, value in result.items()
            if key in {"caption", "summary", "description", "visible_text", "ocr", "objects", "entities", "items", "tags", "keywords"}
        },
    }


async def _upload_image_document(
    db: AsyncSession,
    user: User,
    *,
    filename: str,
    mime: str,
    data: bytes,
    kb_id: str | None,
    org_unit_id: str | None,
    title: str | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("PARAM_INVALID", 422, {"detail": f"image upload exceeds {MAX_IMAGE_BYTES // 1024 // 1024}MB"})
    base = await _get_base_by_selector(db, user, kb_id=kb_id, org_unit_id=org_unit_id, require_write=True)
    clean_filename = _clean_text(filename, limit=240, required=True)
    clean_title = _clean_text(title, limit=240) or clean_filename
    tag_list = _normalize_tags(tags or ["图片"])
    digest = hashlib.sha256(data).hexdigest()
    storage_path = _store_media_file(base.id, digest, mime, data)
    analysis = await _analyze_image_with_vision(data, mime=mime, filename=clean_filename, tags=tag_list, user=user)
    proxy_text = _build_image_proxy_text(title=clean_title, filename=clean_filename, tags=tag_list, analysis=analysis)
    now = now_bjt()
    doc = KnowledgeDocument(
        id=f"kdoc-{uuid.uuid4().hex[:18]}",
        kb_id=base.id,
        org_unit_id=base.org_unit_id,
        department=base.department,
        title=clean_title,
        source_type="upload",
        source_ref=clean_filename,
        source_hash=digest,
        content=proxy_text,
        content_hash=_content_hash(proxy_text),
        tags_json=_normalize_tags([*tag_list, *_normalize_vision_list(analysis.get("tags"), limit=20)]),
        metadata_json={
            "filename": clean_filename,
            "content_type": mime,
            "bytes": len(data),
            "modality": "image",
            "image_sha256": digest,
            "vision_status": analysis.get("status") or "unknown",
            "vision_error": analysis.get("error"),
        },
        content_mime=mime,
        source_version=digest[:16],
        created_by=getattr(user, "id", None),
        updated_by=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.add(doc)
    await db.flush()
    asset = KnowledgeMediaAsset(
        id=f"kmedia-{uuid.uuid4().hex[:18]}",
        document_id=doc.id,
        kb_id=base.id,
        org_unit_id=base.org_unit_id,
        department=base.department,
        file_name=clean_filename,
        mime_type=mime,
        byte_size=len(data),
        sha256=digest,
        storage_path=storage_path,
        status="pending",
        caption=_clean_text(analysis.get("caption"), limit=1200) or None,
        visible_text=_clean_text(analysis.get("visible_text"), limit=4000) or None,
        objects_json=_normalize_vision_list(analysis.get("objects"), limit=30),
        metadata_json={
            "vision_status": analysis.get("status") or "unknown",
            "vision_error": analysis.get("error"),
            "analysis": analysis.get("raw") or {},
        },
        index_error=_clean_text(analysis.get("error"), limit=1000) or None,
        created_by=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.add(asset)
    await db.flush()
    metadata = dict(doc.metadata_json or {})
    metadata["media_assets"] = [_media_asset_to_dict(asset, include_url=True)]
    doc.metadata_json = metadata
    await db.flush()
    await _index_document(db, doc, user=user, action="upload", payload={"modality": "image", "asset_id": asset.id})
    return _document_to_dict(doc, include_content=True, can_write=True)


def _validate_import_url(url: str) -> str:
    clean_url = _clean_text(url, limit=2000, required=True)
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AppError("PARAM_INVALID", 422, {"detail": "only http/https URLs are supported"})
    host = parsed.hostname or ""
    if host.lower() in {"localhost", "localtest.me"} or host.endswith(".localhost"):
        raise AppError("PARAM_INVALID", 422, {"detail": "local URLs are not allowed"})
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AppError("PARAM_INVALID", 422, {"detail": "URL host cannot be resolved"}) from exc
    for info in infos:
        address = info[4][0]
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise AppError("PARAM_INVALID", 422, {"detail": "private or local network URLs are not allowed"})
    return clean_url


async def get_rag_health(db: AsyncSession, user: User) -> dict[str, Any]:
    cfg = await _load_rag_config(db)
    mode = str(cfg.get("knowledge.lightrag.mode") or "embedded")
    status = "ok"
    server: dict[str, Any] | None = None
    if mode == "server":
        try:
            server = await _lightrag_server_request(cfg, "/health", method="GET")
        except Exception as exc:
            status = "degraded"
            server = {"ok": False, "error": type(exc).__name__}
    try:
        ai_cfg = await get_ai_config(require_system_config=True)
        vision_configured = bool(ai_cfg.get("ai.api_base") and ai_cfg.get("ai.api_key") and ai_cfg.get("ai.model"))
        vision_model = str(ai_cfg.get("ai.model") or "")
    except Exception:
        vision_configured = False
        vision_model = ""
    docling_enabled = _truthy(cfg.get("knowledge.docling.enabled"))
    docling_configured = bool(cfg.get("knowledge.docling.api_base"))
    vlm_configured = bool(
        cfg.get("knowledge.vlm.api_base")
        and cfg.get("knowledge.vlm.api_key")
        and cfg.get("knowledge.vlm.model")
    )
    kb_ids, _ = await _visible_base_ids(db, user)
    image_index_count = 0
    if kb_ids:
        image_index_count = int(
            (
                await db.execute(
                    select(func.count(KnowledgeMediaAsset.id))
                    .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeMediaAsset.document_id)
                    .where(KnowledgeMediaAsset.kb_id.in_(kb_ids))
                    .where(KnowledgeMediaAsset.status != "deleted")
                    .where(KnowledgeDocument.status == "active")
                )
            ).scalar()
            or 0
        )
    return {
        "status": status,
        "backend": RAG_BACKEND,
        "mode": mode,
        "server_flavor": _lightrag_flavor(cfg),
        "server_api_base_configured": bool(cfg.get("knowledge.lightrag.api_base")),
        "retrieval": DEFAULT_RAG_MODE,
        "rag_enabled": _truthy(cfg.get("knowledge.rag.enabled")),
        "embedding_configured": bool(cfg.get("ai.embedding.api_base") and cfg.get("ai.embedding.api_key") and cfg.get("ai.embedding.model")),
        "reranker_enabled": _truthy(cfg.get("ai.reranker.enabled")),
        "multimodal_enabled": True,
        "vision_configured": vision_configured,
        "vision_model": vision_model,
        "docling_enabled": docling_enabled,
        "docling_configured": docling_configured,
        "docling_api_base": cfg.get("knowledge.docling.api_base") or "",
        "vlm_configured": vlm_configured,
        "vlm_provider": cfg.get("knowledge.vlm.provider") or "",
        "vlm_model": cfg.get("knowledge.vlm.model") or "",
        "supported_modalities": ["text", "image"],
        "supported_image_mime_types": sorted(IMAGE_MIME_TYPES),
        "max_image_bytes": MAX_IMAGE_BYTES,
        "image_index_count": image_index_count,
        "server": server,
    }


async def test_lightrag_server_config(
    db: AsyncSession,
    user: User,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not role_matches_any(user, ("admin", "system_admin")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    cfg = await _load_rag_config(db)
    overrides = overrides or {}
    for key in ("api_base", "api_key", "timeout", "flavor"):
        if key in overrides and overrides.get(key) not in (None, ""):
            cfg[f"knowledge.lightrag.{key}"] = overrides[key]
    cfg["knowledge.lightrag.api_base"] = str(cfg.get("knowledge.lightrag.api_base") or "").strip().rstrip("/")
    if not cfg["knowledge.lightrag.api_base"]:
        raise AppError("PARAM_INVALID", 422, {"detail": "请先填写 LightRAG Server API 地址"})
    started = time.perf_counter()
    health = await _lightrag_server_health(cfg)
    ok = health.get("ok") is not False
    if not ok:
        raise AppError("LLM_API_ERROR", 502, {"detail": f"LightRAG Server 健康检查失败: {health}"})
    return {
        "ok": True,
        "api_base": cfg["knowledge.lightrag.api_base"],
        "flavor": _lightrag_flavor(cfg),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "health": health,
        "used_unsaved_key": bool(str(overrides.get("api_key") or "").strip()),
    }


async def test_docling_config(
    db: AsyncSession,
    user: User,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not role_matches_any(user, ("admin", "system_admin")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    cfg = await _load_rag_config(db)
    overrides = overrides or {}
    for key in ("api_base", "timeout", "enabled"):
        if key in overrides and overrides.get(key) not in (None, ""):
            cfg[f"knowledge.docling.{key}"] = overrides[key]
    api_base = str(cfg.get("knowledge.docling.api_base") or "").strip().rstrip("/")
    if not api_base:
        raise AppError("PARAM_INVALID", 422, {"detail": "请先填写 Docling API 地址"})
    timeout = _safe_float(cfg.get("knowledge.docling.timeout"), 30.0)
    started = time.perf_counter()
    try:
        health = await _http_json_health(api_base, timeout=timeout, candidates=["/docs", "/health", "/openapi.json"])
    except Exception as exc:
        raise AppError("LLM_API_ERROR", 502, {"detail": f"Docling 连接失败：{type(exc).__name__}"}) from exc
    if health.get("ok") is False:
        raise AppError("LLM_API_ERROR", 502, {"detail": f"Docling 健康检查失败: {health}"})
    return {
        "ok": True,
        "api_base": api_base,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "health": health,
    }


async def test_vlm_config(
    db: AsyncSession,
    user: User,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not role_matches_any(user, ("admin", "system_admin")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    cfg = await _load_rag_config(db)
    overrides = overrides or {}
    for key in ("provider", "api_base", "api_key", "model", "timeout"):
        if key in overrides and overrides.get(key) not in (None, ""):
            cfg[f"knowledge.vlm.{key}"] = overrides[key]
    api_base = str(cfg.get("knowledge.vlm.api_base") or "").strip().rstrip("/")
    api_key = str(cfg.get("knowledge.vlm.api_key") or "").strip()
    model = str(cfg.get("knowledge.vlm.model") or "").strip()
    if not api_base or not api_key or not model:
        raise AppError("PARAM_INVALID", 422, {"detail": "请先填写 VLM API 地址、API Key 和模型名"})
    timeout = _safe_float(cfg.get("knowledge.vlm.timeout"), 45.0)
    started = time.perf_counter()
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        probe_image = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return JSON only."},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": 'Read the image and return {"ok":true,"probe":"vlm"}.',
                                },
                                {"type": "image_url", "image_url": {"url": probe_image}},
                            ],
                        },
                    ],
                    "max_tokens": 32,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 0
        body = redact_secret_text(exc.response.text if exc.response else "", limit=300)
        raise AppError("LLM_API_ERROR", 502, {"detail": f"VLM 连接失败 HTTP {status}: {body}"}) from exc
    except httpx.TimeoutException as exc:
        raise AppError("LLM_TIMEOUT", 504, {"detail": "VLM 连接超时，请检查网络或调大 timeout"}) from exc
    except Exception as exc:
        raise AppError("LLM_API_ERROR", 502, {"detail": f"VLM 连接失败：{type(exc).__name__}"}) from exc
    return {
        "ok": True,
        "provider": cfg.get("knowledge.vlm.provider") or "custom",
        "api_base": api_base,
        "model": model,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "vision_probe": True,
        "response_keys": sorted(payload.keys())[:20] if isinstance(payload, dict) else [],
        "used_unsaved_key": bool(str(overrides.get("api_key") or "").strip()),
    }


async def sync_lightrag_server(
    db: AsyncSession,
    user: User,
    *,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    if not role_matches_any(user, ("admin", "system_admin")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    cfg = await _load_rag_config(db)
    if str(cfg.get("knowledge.lightrag.mode") or "embedded") != "server":
        raise AppError("PARAM_INVALID", 422, {"detail": "请先保存 knowledge.lightrag.mode=server"})
    await test_lightrag_server_config(db, user)
    kb_ids, _ = await _visible_base_ids(db, user, kb_id=kb_id, org_unit_id=org_unit_id)
    if not kb_ids:
        return {"ok": True, "synced": 0, "failed": 0, "items": []}
    docs = (
        await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.kb_id.in_(kb_ids))
            .where(KnowledgeDocument.status == "active")
            .order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
            .limit(max(1, min(int(limit or 200), 1000)))
        )
    ).scalars().all()
    items: list[dict[str, Any]] = []
    synced = 0
    failed = 0
    for doc in docs:
        try:
            await _index_document(db, doc, user=user, action="reindex", payload={"target": "lightrag_server"})
            synced += 1
            items.append({"document_id": doc.id, "title": doc.title, "status": "synced"})
        except Exception as exc:
            failed += 1
            items.append({"document_id": doc.id, "title": doc.title, "status": "failed", "error": type(exc).__name__})
    return {"ok": failed == 0, "synced": synced, "failed": failed, "total": len(docs), "items": items[:50]}


async def list_index_jobs(
    db: AsyncSession,
    user: User,
    *,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    kb_ids, _ = await _visible_base_ids(db, user, kb_id=kb_id, org_unit_id=org_unit_id)
    if not kb_ids:
        return {"items": [], "total": 0}
    stmt = select(KnowledgeIndexJob).where(KnowledgeIndexJob.kb_id.in_(kb_ids))
    clean_status = _clean_text(status, limit=20)
    if clean_status:
        stmt = stmt.where(KnowledgeIndexJob.status == clean_status)
    rows = (
        await db.execute(
            stmt.order_by(KnowledgeIndexJob.created_at.desc(), KnowledgeIndexJob.id.desc()).limit(max(1, min(limit, 200)))
        )
    ).scalars().all()
    items = [
        {
            "id": row.id,
            "job_id": row.job_id,
            "document_id": row.document_id,
            "kb_id": row.kb_id,
            "department": row.department,
            "action": row.action,
            "status": row.status,
            "attempts": row.attempts,
            "error": row.error,
            "payload": row.payload_json or {},
            "created_by": row.created_by,
            "created_at": isoformat_bjt(row.created_at),
            "started_at": isoformat_bjt(row.started_at),
            "finished_at": isoformat_bjt(row.finished_at),
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


async def get_media_asset_content(
    db: AsyncSession,
    user: User,
    asset_id: str,
) -> tuple[bytes, str, str]:
    asset = await db.get(KnowledgeMediaAsset, asset_id)
    if not asset or asset.status == "deleted":
        raise AppError("NOT_FOUND", 404)
    base = await db.get(DepartmentKnowledgeBase, asset.kb_id)
    if not base:
        raise AppError("NOT_FOUND", 404)
    await _require_base_access(db, user, base)
    doc = await db.get(KnowledgeDocument, asset.document_id)
    if not doc or doc.status != "active":
        raise AppError("NOT_FOUND", 404)
    path = _safe_media_path(asset.storage_path)
    if not path.exists() or not path.is_file():
        raise AppError("NOT_FOUND", 404, {"detail": "media file missing"})
    return path.read_bytes(), asset.mime_type, asset.file_name


async def search_knowledge_by_image(
    db: AsyncSession,
    user: User,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    modality: str = "all",
) -> dict[str, Any]:
    image_mime = _detect_image_mime(filename, content_type, data)
    if not image_mime:
        raise AppError("PARAM_INVALID", 415, {"detail": "only png/jpg/webp/gif images are supported"})
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("PARAM_INVALID", 422, {"detail": f"image upload exceeds {MAX_IMAGE_BYTES // 1024 // 1024}MB"})
    analysis = await _analyze_image_with_vision(data, mime=image_mime, filename=filename, tags=[], user=user)
    query_parts = [
        _clean_text(analysis.get("caption"), limit=800),
        _clean_text(analysis.get("visible_text"), limit=1000),
        " ".join(_normalize_vision_list(analysis.get("objects"), limit=20)),
        " ".join(_normalize_vision_list(analysis.get("tags"), limit=20)),
    ]
    clean_query = _clean_text(" ".join(part for part in query_parts if part), limit=500)
    if not clean_query:
        clean_query = _clean_text(filename, limit=240, required=True)
    result = await search_knowledge(
        db,
        user,
        query=clean_query,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        top_k=top_k,
        modality=modality,
    )
    result["image_query"] = {
        "filename": filename,
        "mime_type": image_mime,
        "caption": analysis.get("caption"),
        "visible_text": analysis.get("visible_text"),
        "objects": _normalize_vision_list(analysis.get("objects"), limit=20),
        "status": analysis.get("status"),
        "error": analysis.get("error"),
    }
    return result


async def upload_document(
    db: AsyncSession,
    user: User,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    image_mime = _detect_image_mime(filename, content_type, data)
    if image_mime:
        return await _upload_image_document(
            db,
            user,
            filename=filename,
            mime=image_mime,
            data=data,
            kb_id=kb_id,
            org_unit_id=org_unit_id,
            title=title,
            tags=tags,
        )
    text, mime = _extract_plain_text_from_bytes(filename, content_type, data)
    clean_title = _clean_text(title, limit=240) or _clean_text(filename, limit=240, required=True)
    return await create_document(
        db,
        user,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        title=clean_title,
        content=text,
        source_type="upload",
        source_ref=filename,
        tags=tags or ["上传"],
        metadata={"filename": filename, "content_type": content_type, "bytes": len(data)},
        upsert_source=False,
        content_mime=mime,
        source_version=_content_hash(text)[:16],
    )


async def import_url_document(
    db: AsyncSession,
    user: User,
    *,
    url: str,
    kb_id: str | None = None,
    org_unit_id: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    upsert_source: bool = True,
) -> dict[str, Any]:
    if not role_matches_any(user, ("admin", "system_admin")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    clean_url = _validate_import_url(url)
    timeout = 20.0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, max_redirects=3) as client:
            response = await client.get(clean_url, headers={"User-Agent": "SkillForge-KnowledgeBot/1.0"})
            response.raise_for_status()
            final_url = str(response.url)
            _validate_import_url(final_url)
            content_type = response.headers.get("content-type", "")
            data = response.content
    except AppError:
        raise
    except Exception as exc:
        raise AppError("REQUEST_TIMEOUT", 504, {"detail": f"URL import failed: {type(exc).__name__}"}) from exc
    text, mime = _extract_plain_text_from_bytes(clean_url, content_type, data)
    clean_title = _clean_text(title, limit=240) or _clean_text(re.sub(r"https?://", "", clean_url).split("?")[0], limit=240) or clean_url
    return await create_document(
        db,
        user,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        title=clean_title,
        content=text,
        source_type="url",
        source_ref=clean_url,
        tags=tags or ["链接"],
        metadata={"url": clean_url, "final_url": final_url, "content_type": content_type, "bytes": len(data)},
        upsert_source=upsert_source,
        content_mime=mime,
        source_version=_content_hash(text)[:16],
    )

async def test_embedding_config(
    db: AsyncSession,
    user: User,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not role_matches_any(user, ("admin", "system_admin")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    cfg = await _load_rag_config(db)
    overrides = overrides or {}
    for key in ("provider", "api_base", "api_key", "model", "dim", "timeout"):
        if key in overrides and overrides.get(key) not in (None, ""):
            cfg[f"ai.embedding.{key}"] = overrides[key]
    cfg = _apply_rag_provider_defaults(cfg)
    api_base = str(cfg.get("ai.embedding.api_base") or "").strip().rstrip("/")
    api_key = str(cfg.get("ai.embedding.api_key") or "").strip()
    model = str(cfg.get("ai.embedding.model") or "").strip()
    requires_api_key = _embedding_requires_api_key(cfg)
    if not api_base or not model or (requires_api_key and not api_key):
        raise AppError(
            "PARAM_INVALID",
            422,
            {"detail": "请先选择向量供应商/模型；Ollama 本地不需要 API Key，SiliconFlow/OpenAI 需要填写 API Key。"},
        )
    timeout = _safe_float(cfg.get("ai.embedding.timeout"), 30.0)
    started = time.perf_counter()
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{api_base}/embeddings",
                headers=headers,
                json={"model": model, "input": ["SkillForge 向量模型连接测试"]},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 0
        response_text = exc.response.text if exc.response else ""
        body = redact_secret_text(response_text, limit=300)
        hint = ""
        body_lower = response_text.lower()
        if "balance" in body_lower and "insufficient" in body_lower:
            hint = "；SiliconFlow 返回余额不足，请确认该 API Key 所属账号的 API 可用余额/chargeBalance，不只是页面显示的赠送点数/总点数"
        elif status in (401, 403):
            hint = "；请检查 API Key 是否属于当前供应商，且账户有调用该模型的权限"
        elif status == 404:
            hint = "；请检查模型名或 API 地址是否正确"
        elif status == 429:
            hint = "；请求被限流或账户额度不足"
        raise AppError("LLM_API_ERROR", 502, {"detail": f"向量模型连接失败 HTTP {status}{hint}: {body}"}) from exc
    except httpx.TimeoutException as exc:
        raise AppError("LLM_TIMEOUT", 504, {"detail": "向量模型连接超时，请检查网络或调大 timeout"}) from exc
    except Exception as exc:
        raise AppError("LLM_API_ERROR", 502, {"detail": f"向量模型连接失败：{type(exc).__name__}"}) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    vector = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        vector = data[0].get("embedding")
    if not isinstance(vector, list) or not vector:
        safe_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        raise AppError("LLM_API_ERROR", 502, {"detail": f"向量模型响应缺少 embedding 向量，响应字段={safe_keys}"})
    dim = len(vector)
    expected_dim = int(cfg.get("ai.embedding.dim") or dim)
    return {
        "ok": True,
        "provider": cfg.get("ai.embedding.provider") or "custom",
        "api_base": api_base,
        "model": model,
        "dimension": dim,
        "expected_dimension": expected_dim,
        "dimension_match": dim == expected_dim,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "used_unsaved_key": bool(str(overrides.get("api_key") or "").strip()),
    }
