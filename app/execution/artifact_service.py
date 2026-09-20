"""Persist raw execution data as platform artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.execution.models import ExecutionArtifact, ExecutionRunLog


MAX_SAMPLE_KEYS = 30
MAX_SUMMARY_ITEMS = 20
MAX_LOG_TAIL_CHARS = 64 * 1024
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s'\"<>]+"),
    re.compile(r"(?i)(access[_-]?token[\"'\s:=]+)[^,\"'\s<>]+"),
    re.compile(r"(?i)(cookie[\"'\s:=]+)[^\\n\\r]+"),
    re.compile(r"(?i)(secret[_-]?key[\"'\s:=]+)[^,\"'\s<>]+"),
)


def _artifact_root() -> Path:
    root = Path(settings.EXECUTION_ARTIFACT_ROOT).expanduser()
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root


def _safe_segment(value: str | None, fallback: str = "unknown") -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    return text.strip(".-")[:120] or fallback


def _extract_schema(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in (
        "collection_schema",
        "schema",
        "snapshot_schema",
        "dataset_schema",
        "data_schema",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:100]
    snapshot = value.get("数据采集快照")
    if isinstance(snapshot, dict):
        candidate = snapshot.get("schema")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:100]
    return None


def _summarize_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        keys = list(value.keys())
        summary: dict[str, Any] = {
            "type": "object",
            "key_count": len(keys),
            "sample_keys": [str(key) for key in keys[:MAX_SAMPLE_KEYS]],
        }
        for key in ("collection_schema", "schema", "summary", "status"):
            candidate = value.get(key)
            if isinstance(candidate, (str, int, float, bool)) or candidate is None:
                summary[key] = candidate
        reports = value.get("reports")
        todos = value.get("todos")
        if isinstance(reports, list):
            summary["report_count"] = len(reports)
        if isinstance(todos, list):
            summary["todo_count"] = len(todos)
        return summary
    if isinstance(value, list):
        item_types = sorted({type(item).__name__ for item in value[:MAX_SUMMARY_ITEMS]})
        return {
            "type": "array",
            "item_count": len(value),
            "sample_item_types": item_types,
        }
    return {"type": type(value).__name__}


async def persist_execution_artifact(
    session: AsyncSession,
    *,
    run_id: str,
    skill_id: str,
    kind: str,
    payload: Any,
    decision_log_id: int | None = None,
) -> dict[str, Any] | None:
    """Write a raw JSON payload to gzip storage and register it in DB.

    The function is idempotent for the same run/kind/content hash.
    """
    if not run_id or not skill_id:
        return None
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        existing = (
            await session.execute(
                select(ExecutionArtifact)
                .where(ExecutionArtifact.run_id == run_id)
                .where(ExecutionArtifact.kind == kind)
                .where(ExecutionArtifact.sha256 == digest)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if decision_log_id and not existing.decision_log_id:
                existing.decision_log_id = decision_log_id
            return artifact_ref(existing)

        compressed = gzip.compress(raw, compresslevel=6)
        root = _artifact_root()
        rel = Path(_safe_segment(skill_id)) / _safe_segment(run_id) / f"{_safe_segment(kind)}-{digest[:16]}.json.gz"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(compressed)

        row = ExecutionArtifact(
            run_id=run_id,
            skill_id=skill_id,
            decision_log_id=decision_log_id,
            kind=kind,
            schema_name=_extract_schema(payload),
            storage_backend="local",
            storage_path=str(rel),
            media_type="application/json+gzip",
            encoding="gzip",
            size_bytes=len(compressed),
            uncompressed_size_bytes=len(raw),
            sha256=digest,
            summary_json=_summarize_value(payload),
        )
        session.add(row)
        await session.flush()
        return artifact_ref(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "persist execution artifact failed skill={} run={} kind={} error={}",
            skill_id,
            run_id,
            kind,
            exc,
        )
        return None


def artifact_ref(row: ExecutionArtifact) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "decision_log_id": row.decision_log_id,
        "kind": row.kind,
        "schema": row.schema_name,
        "storage_backend": row.storage_backend,
        "storage_path": row.storage_path,
        "media_type": row.media_type,
        "encoding": row.encoding,
        "size_bytes": row.size_bytes,
        "uncompressed_size_bytes": row.uncompressed_size_bytes,
        "sha256": row.sha256,
        "summary": row.summary_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def artifact_abs_path(storage_path: str) -> Path:
    return (_artifact_root() / storage_path).resolve()


def _redact_log_text(value: Any) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


async def persist_execution_log(
    session: AsyncSession,
    *,
    run_id: str,
    skill_id: str,
    stream: str,
    content: Any,
    source: str | None = None,
    max_chars: int = MAX_LOG_TAIL_CHARS,
) -> ExecutionRunLog | None:
    """Persist a bounded, redacted execution log tail."""
    if not run_id or not skill_id:
        return None
    text = _redact_log_text(content)
    if not text:
        return None
    limit = max(1, int(max_chars or MAX_LOG_TAIL_CHARS))
    truncated = len(text) > limit
    row = ExecutionRunLog(
        run_id=run_id,
        skill_id=skill_id,
        stream=str(stream or "system")[:20],
        source=str(source or "")[:50] or None,
        content_tail=text[-limit:],
        truncated=truncated,
    )
    session.add(row)
    await session.flush()
    return row
