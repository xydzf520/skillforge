"""Governed SF data store service."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.models import User
from app.codex.models import SfDataRecord
from app.common.audit import AuditLog
from app.common.cache import invalidate_sf_cache
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings


INLINE_JSON_MAX_BYTES = 256 * 1024
PREVIEW_TEXT_CHARS = 12_000
MAX_PAYLOAD_BYTES = 50 * 1024 * 1024
MAX_LIST_LIMIT = 200
GLOBAL_ROLES = ("admin", "system_admin")
SECRET_KEYS = re.compile(r"(?i)(token|cookie|authorization|password|secret|api[_-]?key|client[_-]?secret)")
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,\"']+"),
    re.compile(r"(?i)(access[_-]?token[\"'\s:=]+)[^,\"'\s<>]+"),
    re.compile(r"(?i)(refresh[_-]?token[\"'\s:=]+)[^,\"'\s<>]+"),
    re.compile(r"(?i)(cookie[\"'\s:=]+)[^\\n\\r]+"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|client[_-]?secret)([\"'\s:=]+)[^,\"'\s<>]+"),
)
PostCommitCallback = Callable[[], Awaitable[None]]


def _register_post_commit(db: AsyncSession, callback: PostCommitCallback) -> None:
    db.info.setdefault("_post_commit_callbacks", []).append(callback)


def _clean_text(value: Any, *, limit: int = 160, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return fallback
    return text[:limit]


def _safe_segment(value: str | None, fallback: str = "unknown") -> str:
    text = _clean_text(value, limit=120, fallback=fallback)
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    return text.strip(".-")[:120] or fallback


def _to_iso(value) -> str | None:
    return isoformat_bjt(value) if value else None


def _redact_text(value: str) -> str:
    text = str(value or "")
    for pattern in SECRET_TEXT_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)(api"):
            text = pattern.sub(r"\1\2***", text)
        else:
            text = pattern.sub(r"\1***", text)
    return text


def _redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "***truncated***"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEYS.search(key_text):
                cleaned[key_text] = "***"
            else:
                cleaned[key_text] = _redact_value(item, depth=depth + 1)
        return cleaned
    if isinstance(value, list):
        return [_redact_value(item, depth=depth + 1) for item in value[:5000]]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _artifact_root() -> Path:
    root = Path(settings.EXECUTION_ARTIFACT_ROOT).expanduser()
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root / "sf-data"


def _artifact_ref(*, namespace: str, record_id: str, raw: bytes, digest: str, content_type: str) -> dict[str, Any]:
    compressed = gzip.compress(raw, compresslevel=6)
    rel = Path(_safe_segment(namespace)) / f"{_safe_segment(record_id)}-{digest[:16]}.json.gz"
    path = _artifact_root() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {
        "storage_backend": "local",
        "storage_path": str(Path("sf-data") / rel),
        "media_type": "application/json+gzip" if content_type != "binary" else "application/octet-stream+gzip",
        "encoding": "gzip",
        "size_bytes": len(compressed),
        "uncompressed_size_bytes": len(raw),
        "sha256": digest,
    }


def _infer_schema(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "type": "object",
            "key_count": len(value),
            "sample_keys": [str(key) for key in list(value.keys())[:30]],
        }
    if isinstance(value, list):
        sample = value[0] if value else None
        item_schema = _infer_schema(sample) if sample is not None else None
        return {
            "type": "array",
            "item_count": len(value),
            "item_schema": item_schema,
        }
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, (int, float, bool)) or value is None:
        return {"type": type(value).__name__}
    return None


def _preview_from_value(value: Any, *, content_type: str) -> str:
    if isinstance(value, str):
        return _redact_text(value)[:PREVIEW_TEXT_CHARS]
    if content_type == "table" and isinstance(value, list):
        return json.dumps(_redact_value(value[:20]), ensure_ascii=False, default=str)[:PREVIEW_TEXT_CHARS]
    return json.dumps(_redact_value(value), ensure_ascii=False, default=str)[:PREVIEW_TEXT_CHARS]


def _normalize_payload(args: dict[str, Any]) -> tuple[Any, str]:
    explicit_type = _clean_text(args.get("content_type") or args.get("contentType"), limit=40).lower()
    if "data" in args:
        data = args.get("data")
    elif "content" in args:
        data = args.get("content")
    elif "text" in args:
        data = str(args.get("text") or "")
        explicit_type = explicit_type or "text"
    elif "rows" in args:
        data = args.get("rows")
        explicit_type = explicit_type or "table"
    elif "base64" in args:
        raw = base64.b64decode(str(args.get("base64") or ""), validate=True)
        data = {"base64": str(args.get("base64") or ""), "filename": args.get("filename")}
        explicit_type = explicit_type or "binary"
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise AppError("PARAM_INVALID", 413, {"detail": "sf data payload too large"})
    else:
        raise AppError("PARAM_INVALID", 422, {"detail": "data/content/text/rows/base64 is required"})

    if explicit_type:
        content_type = explicit_type
    elif isinstance(data, str):
        content_type = "text"
    elif isinstance(data, list):
        content_type = "table" if all(isinstance(item, dict) for item in data[:20]) else "json"
    else:
        content_type = "json"
    if content_type not in {"json", "text", "table", "binary"}:
        content_type = "json"
    return data, content_type


def _record_to_dict(row: SfDataRecord, *, include_data: bool = False) -> dict[str, Any]:
    data = {
        "id": row.id,
        "namespace": row.namespace,
        "title": row.title,
        "content_type": row.content_type,
        "text_preview": row.text_preview,
        "artifact_ref": row.artifact_ref_json or None,
        "metadata": row.metadata_json or {},
        "schema": row.schema_json or None,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "source": row.source,
        "source_tool": row.source_tool,
        "source_ref": row.source_ref,
        "skill_id": row.skill_id,
        "run_id": row.run_id,
        "user_id": row.user_id,
        "department": row.department,
        "visibility": row.visibility,
        "created_at": _to_iso(row.created_at),
        "has_inline_data": row.data_json is not None,
    }
    if include_data:
        data["data"] = row.data_json
    return data


def _can_read_all(user: User) -> bool:
    return role_matches_any(user, GLOBAL_ROLES) or bool(getattr(user, "can_view_all", False))


def _visibility_filter(user: User):
    if _can_read_all(user):
        return True
    user_id = str(getattr(user, "id", "") or "")
    department = str(getattr(user, "department", "") or "")
    return or_(
        SfDataRecord.visibility == "global",
        SfDataRecord.user_id == user_id,
        and_(SfDataRecord.visibility == "department", SfDataRecord.department == department),
    )


async def write_sf_data(
    db: AsyncSession,
    user: User,
    args: dict[str, Any],
    *,
    dry_run: bool = True,
    idempotency_key: str | None = None,
    source_tool: str | None = None,
    source: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    namespace = _clean_text(args.get("namespace"), limit=160, fallback="default")
    title = _clean_text(args.get("title"), limit=240) or None
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    provided_schema = args.get("schema") if isinstance(args.get("schema"), dict) else None
    payload, content_type = _normalize_payload(args)
    redacted_payload = _redact_value(payload)
    raw = _json_bytes(redacted_payload)
    size_bytes = len(raw)
    if size_bytes > MAX_PAYLOAD_BYTES:
        raise AppError("PARAM_INVALID", 413, {"detail": "sf data payload too large", "max_bytes": MAX_PAYLOAD_BYTES})
    digest = hashlib.sha256(raw).hexdigest()
    preview = _preview_from_value(redacted_payload, content_type=content_type)
    schema = provided_schema or _infer_schema(redacted_payload)
    effective_skill_id = _clean_text(args.get("skill_id") or args.get("skillId") or skill_id, limit=100) or None
    effective_run_id = _clean_text(args.get("run_id") or args.get("runId") or run_id, limit=80) or None
    effective_source = _clean_text(args.get("source") or source or "sf", limit=80) or None
    effective_source_tool = _clean_text(args.get("source_tool") or args.get("sourceTool") or source_tool, limit=160) or None
    source_ref = _clean_text(args.get("source_ref") or args.get("sourceRef"), limit=160) or None
    visibility = _clean_text(args.get("visibility"), limit=30, fallback="global")
    if visibility not in {"global", "department", "private"}:
        visibility = "global"
    clean_idempotency_key = _clean_text(idempotency_key or args.get("idempotency_key") or args.get("idempotencyKey"), limit=160) or None

    preview_result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "would_write": bool(dry_run),
        "namespace": namespace,
        "title": title,
        "content_type": content_type,
        "sha256": digest,
        "size_bytes": size_bytes,
        "text_preview": preview,
        "schema": schema,
        "metadata": _redact_value(metadata),
        "visibility": visibility,
    }
    if dry_run:
        return preview_result

    if not clean_idempotency_key:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", 400)
    existing = (
        await db.execute(select(SfDataRecord).where(SfDataRecord.idempotency_key == clean_idempotency_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return {"ok": True, "dry_run": False, "created": False, "record": _record_to_dict(existing, include_data=True)}

    record_id = f"sfdata_{hashlib.sha256((clean_idempotency_key + digest).encode('utf-8')).hexdigest()[:24]}"
    data_json = redacted_payload if isinstance(redacted_payload, (dict, list)) and size_bytes <= INLINE_JSON_MAX_BYTES and content_type != "binary" else None
    artifact_ref = None
    if data_json is None:
        artifact_ref = _artifact_ref(namespace=namespace, record_id=record_id, raw=raw, digest=digest, content_type=content_type)

    row = SfDataRecord(
        id=record_id,
        namespace=namespace,
        title=title,
        content_type=content_type,
        data_json=data_json,
        text_preview=preview,
        artifact_ref_json=artifact_ref,
        metadata_json=_redact_value(metadata) if isinstance(metadata, dict) else {},
        schema_json=schema,
        sha256=digest,
        size_bytes=size_bytes,
        source=effective_source,
        source_tool=effective_source_tool,
        source_ref=source_ref,
        skill_id=effective_skill_id,
        run_id=effective_run_id,
        user_id=str(getattr(user, "id", "") or "")[:50] or None,
        department=_clean_text(getattr(user, "department", None), limit=100) or None,
        visibility=visibility,
        idempotency_key=clean_idempotency_key,
        created_at=now_bjt(),
    )
    db.add(row)
    await db.flush()
    db.add(
        AuditLog(
            user_id=row.user_id,
            action="sf.data.write",
            target_type="sf_data",
            target_id=row.id,
            detail={
                "namespace": namespace,
                "content_type": content_type,
                "sha256": digest,
                "size_bytes": size_bytes,
                "skill_id": effective_skill_id,
                "run_id": effective_run_id,
                "source_tool": effective_source_tool,
                "visibility": visibility,
            },
        )
    )
    _register_post_commit(db, invalidate_sf_cache)
    return {"ok": True, "dry_run": False, "created": True, "record": _record_to_dict(row, include_data=True)}


async def list_sf_data_records(
    db: AsyncSession,
    user: User,
    *,
    page: int = 1,
    page_size: int = 50,
    namespace: str | None = None,
    content_type: str | None = None,
    skill_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), MAX_LIST_LIMIT))
    conditions = [_visibility_filter(user)]
    if namespace := _clean_text(namespace, limit=160):
        conditions.append(SfDataRecord.namespace == namespace)
    if content_type := _clean_text(content_type, limit=40):
        conditions.append(SfDataRecord.content_type == content_type)
    if skill_id := _clean_text(skill_id, limit=100):
        conditions.append(SfDataRecord.skill_id == skill_id)
    if run_id := _clean_text(run_id, limit=80):
        conditions.append(SfDataRecord.run_id == run_id)
    if source := _clean_text(source, limit=80):
        conditions.append(SfDataRecord.source == source)
    if q := _clean_text(q, limit=200):
        like = f"%{q}%"
        conditions.append(
            or_(
                SfDataRecord.namespace.ilike(like),
                SfDataRecord.title.ilike(like),
                SfDataRecord.text_preview.ilike(like),
                SfDataRecord.source_tool.ilike(like),
                SfDataRecord.skill_id.ilike(like),
                SfDataRecord.run_id.ilike(like),
            )
        )

    stmt = select(SfDataRecord).where(*conditions)
    count_stmt = select(func.count(SfDataRecord.id)).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    rows = list(
        (
            await db.execute(
                stmt.order_by(SfDataRecord.created_at.desc(), SfDataRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_record_to_dict(row, include_data=False) for row in rows],
    }


async def get_sf_data_record(db: AsyncSession, user: User, record_id: str) -> dict[str, Any]:
    row = await db.get(SfDataRecord, record_id)
    if not row:
        raise AppError("PARAM_INVALID", 404, {"detail": "sf data record not found"})
    if not _can_read_all(user):
        uid = str(getattr(user, "id", "") or "")
        department = str(getattr(user, "department", "") or "")
        same_department = row.visibility == "department" and row.department == department
        if row.visibility != "global" and row.user_id != uid and not same_department:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
    return _record_to_dict(row, include_data=True)
