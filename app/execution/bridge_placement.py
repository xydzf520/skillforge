"""Bridge placement policy helpers.

平台主节点可以装在平台机；业务节点必须装在自己的节点机器上。
这里用 bridge_fingerprint 识别同一台机器，避免非平台节点误挂到平台机。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import OpenClawInstance


BRIDGE_PLACEMENT_CONFLICT = "BRIDGE_PLACEMENT_CONFLICT"


def normalize_bridge_fingerprint(value: str | None) -> str:
    return str(value or "").strip().lower()


def _bridge_fingerprint_parts(value: str | None) -> tuple[str, str | None, str | None]:
    clean = normalize_bridge_fingerprint(value)
    if not clean:
        return "", None, None
    host: str | None = None
    machine: str | None = None
    for part in clean.split("|"):
        if ":" not in part and "=" not in part:
            continue
        key, raw = part.replace("=", ":", 1).split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if key == "host" and raw:
            host = raw
        elif key == "machine" and raw:
            machine = raw
    if host is None and machine is None:
        host = clean
    return clean, host, machine


def bridge_fingerprints_match(left: str | None, right: str | None) -> bool:
    """Compare legacy hostname fingerprints with v2.4.8 structured fingerprints."""
    left_clean, left_host, left_machine = _bridge_fingerprint_parts(left)
    right_clean, right_host, right_machine = _bridge_fingerprint_parts(right)
    if not left_clean or not right_clean:
        return False
    if left_machine and right_machine:
        return left_machine == right_machine
    if left_clean == right_clean:
        return True
    return bool(left_host and right_host and left_host == right_host)


def is_platform_instance(inst: Any) -> bool:
    return bool(getattr(inst, "is_platform_default", False))


def bridge_has_platform_fingerprint_conflict(
    inst: Any,
    platform_fingerprints: Iterable[str],
) -> bool:
    """Return True when a non-platform instance is on the platform host."""
    if inst is None or is_platform_instance(inst):
        return False
    fingerprint = normalize_bridge_fingerprint(getattr(inst, "bridge_fingerprint", None))
    if not fingerprint:
        return False
    return any(bridge_fingerprints_match(fingerprint, item) for item in platform_fingerprints)


async def active_platform_fingerprints(
    db: AsyncSession,
    *,
    exclude_instance_id: str | None = None,
) -> set[str]:
    stmt = (
        select(OpenClawInstance.bridge_fingerprint)
        .where(OpenClawInstance.is_active == True)  # noqa: E712
        .where(OpenClawInstance.is_platform_default == True)  # noqa: E712
        .where(OpenClawInstance.bridge_fingerprint.isnot(None))
    )
    if exclude_instance_id:
        stmt = stmt.where(OpenClawInstance.id != exclude_instance_id)
    rows = (await db.execute(stmt)).scalars().all()
    return {item for item in (normalize_bridge_fingerprint(row) for row in rows) if item}


async def has_active_platform_fingerprint_conflict(
    db: AsyncSession,
    *,
    instance_id: str,
    fingerprint: str | None,
    is_platform_default: bool,
) -> bool:
    if is_platform_default:
        return False
    clean = normalize_bridge_fingerprint(fingerprint)
    if not clean:
        return False
    platform_fingerprints = await active_platform_fingerprints(db, exclude_instance_id=instance_id)
    return any(bridge_fingerprints_match(clean, item) for item in platform_fingerprints)
