"""Runtime platform policy switches stored in ``system_config``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import SystemConfig

SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH = "security.bypass_review_direct_publish"
SECURITY_PLATFORM_NODE_FALLBACK_ENABLED = "security.platform_node_fallback_enabled"
SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH_REASON = (
    "后台安全设置已开启免审核直接发布，提交后由系统自动通过 Review 状态机"
)


@dataclass(frozen=True)
class PlatformSecuritySettings:
    """Security switches that affect Skill publish and runtime placement."""

    bypass_review_direct_publish: bool = False
    platform_node_fallback_enabled: bool = True


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled", "enable"}:
            return True
        if text in {"0", "false", "no", "off", "disabled", "disable"}:
            return False
    return default


async def get_platform_security_settings(db: AsyncSession | None = None) -> PlatformSecuritySettings:
    """Read platform security switches.

    Defaults intentionally preserve current production behavior: Skill submit
    still needs review, and platform default nodes remain enabled as a fallback
    when a department has no runtime node.
    """
    owns_session = db is None
    session = db
    if session is None:
        from app.database import async_session_factory

        session = async_session_factory()

    try:
        rows = (
            await session.execute(
                select(SystemConfig).where(
                    SystemConfig.key.in_(
                        [
                            SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH,
                            SECURITY_PLATFORM_NODE_FALLBACK_ENABLED,
                        ]
                    )
                )
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取平台安全配置失败，使用默认值: {}", exc)
        rows = []
    finally:
        if owns_session and session is not None:
            await session.close()

    values = {row.key: row.value for row in rows}
    return PlatformSecuritySettings(
        bypass_review_direct_publish=_coerce_bool(
            values.get(SECURITY_BYPASS_REVIEW_DIRECT_PUBLISH),
            False,
        ),
        platform_node_fallback_enabled=_coerce_bool(
            values.get(SECURITY_PLATFORM_NODE_FALLBACK_ENABLED),
            True,
        ),
    )


async def is_platform_node_fallback_enabled(db: AsyncSession | None = None) -> bool:
    return (await get_platform_security_settings(db)).platform_node_fallback_enabled
