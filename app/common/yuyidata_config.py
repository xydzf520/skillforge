"""YuyiData platform configuration helpers.

Production credentials are stored in system_config with the yuyidata.* prefix.
Environment variables remain a fallback for local development only.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import SystemConfig
from app.config import settings


YUYIDATA_CONFIG_KEYS = (
    "yuyidata.base_url",
    "yuyidata.app_key",
    "yuyidata.app_secret",
)


def _value_as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("value", "app_key", "appSecret", "api_key", "base_url"):
            if value.get(key):
                return str(value[key]).strip()
        return ""
    return str(value).strip()


async def get_yuyidata_config(db: AsyncSession | None = None) -> dict[str, str]:
    """Read YuyiData config from project backend, falling back to settings."""

    config = {
        "base_url": str(settings.YUYIDATA_BASE_URL or "https://openapi.yuyidata.com").rstrip("/"),
        "app_key": str(settings.YUYIDATA_APP_KEY or "").strip(),
        "app_secret": str(settings.YUYIDATA_APP_SECRET or "").strip(),
    }

    close_session = False
    if db is None:
        try:
            from app.database import async_session_factory

            db = async_session_factory()
            close_session = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("open yuyidata config session failed: {}", exc)
            return config

    try:
        rows = (
            await db.execute(select(SystemConfig).where(SystemConfig.key.in_(YUYIDATA_CONFIG_KEYS)))
        ).scalars().all()
        values = {row.key: _value_as_str(row.value) for row in rows}
        if values.get("yuyidata.base_url"):
            config["base_url"] = values["yuyidata.base_url"].rstrip("/")
        if values.get("yuyidata.app_key"):
            config["app_key"] = values["yuyidata.app_key"]
        if values.get("yuyidata.app_secret"):
            config["app_secret"] = values["yuyidata.app_secret"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("read yuyidata system_config failed: {}", exc)
    finally:
        if close_session and db is not None:
            await db.close()
    return config


async def yuyidata_mcp_env(db: AsyncSession | None = None) -> dict[str, str]:
    """Build environment variables for YuyiData MCP subprocesses."""

    config = await get_yuyidata_config(db)
    env: dict[str, str] = {
        "YUYIDATA_BASE_URL": config["base_url"] or "https://openapi.yuyidata.com",
    }
    if config["app_key"]:
        env["YUYIDATA_APP_KEY"] = config["app_key"]
    if config["app_secret"]:
        env["YUYIDATA_APP_SECRET"] = config["app_secret"]
    return env


async def inject_yuyidata_arguments(db: AsyncSession, tool: str, arguments: dict) -> dict:
    """Inject backend-stored credentials into YuyiData MCP arguments."""

    if not tool.startswith("yuyidata_"):
        return arguments
    config = await get_yuyidata_config(db)
    injected = dict(arguments or {})
    if config["base_url"] and not injected.get("baseUrl"):
        injected["baseUrl"] = config["base_url"]
    if config["app_key"] and not injected.get("appKey"):
        injected["appKey"] = config["app_key"]
    if config["app_secret"] and not injected.get("appSecret"):
        injected["appSecret"] = config["app_secret"]
    return injected
