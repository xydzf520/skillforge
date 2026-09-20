"""
MCP (Model Context Protocol) servers 配置管理。

数据模型:
    存在 system_config 表 key='coding_agent.mcp_servers' 的 JSONB value 中,
    格式: {server_name: server_config}, 例如:
        {
          "filesystem": {
            "enabled": true,
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {}
          },
          "github": {
            "enabled": false,
            "type": "http",
            "url": "https://api.example.com/mcp",
            "headers": {"Authorization": "Bearer xxx"}
          }
        }

`enabled` 是 SkillForge 加的, 用于临时禁用某个 server 而不删除配置。
spawn subprocess 时只把 enabled=true 的 server 转换成 aiclawcode 期望的 JSON
(去掉 enabled 字段) 注入 --mcp-config flag。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from loguru import logger
from app.common.time_utils import now_bjt


CONFIG_KEY = "coding_agent.mcp_servers"

# transport 类型白名单 (subset of fork's full schema, 选用户最常用的)
_VALID_TRANSPORTS = {"stdio", "http", "sse"}

# 缓存
_mcp_cache: dict[str, dict] | None = None
_mcp_cache_ts: float = 0
_CACHE_TTL = 30  # 30s


# ─────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────

class McpConfigError(ValueError):
    """配置校验失败。"""


_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")


def validate_server_name(name: str) -> None:
    """server name 必须小写字母开头, 字母数字下划线连字符, ≤50 字符。"""
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise McpConfigError(
            f"server name 非法: {name!r} "
            "(规则: ^[a-z][a-z0-9_-]{0,49}$)"
        )


def validate_server_config(config: dict) -> dict:
    """
    校验单个 server 配置, 返回规范化后的 dict (含 enabled 默认 True)。
    抛 McpConfigError 拒绝非法配置。
    """
    if not isinstance(config, dict):
        raise McpConfigError("server config 必须是 dict")

    # transport type
    transport = config.get("type") or "stdio"
    if transport not in _VALID_TRANSPORTS:
        raise McpConfigError(
            f"transport 不支持: {transport!r}, "
            f"仅支持: {sorted(_VALID_TRANSPORTS)}"
        )

    normalized: dict[str, Any] = {
        "type": transport,
        "enabled": bool(config.get("enabled", True)),
    }

    if transport == "stdio":
        command = config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError("stdio server 必须有 command 字段")
        normalized["command"] = command.strip()
        args = config.get("args") or []
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise McpConfigError("stdio server.args 必须是 list[str]")
        normalized["args"] = args
        env = config.get("env") or {}
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise McpConfigError("stdio server.env 必须是 dict[str, str]")
        normalized["env"] = env

    elif transport in ("http", "sse"):
        url = config.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise McpConfigError(
                f"{transport} server 必须有 url 字段 (http:// 或 https://)"
            )
        normalized["url"] = url
        headers = config.get("headers") or {}
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise McpConfigError(f"{transport} server.headers 必须是 dict[str, str]")
        normalized["headers"] = headers

    return normalized


def validate_servers_dict(servers: dict) -> dict:
    """校验整个 servers dict, 返回规范化版本。"""
    if not isinstance(servers, dict):
        raise McpConfigError("mcp_servers 必须是 dict")
    out: dict[str, dict] = {}
    for name, cfg in servers.items():
        validate_server_name(name)
        out[name] = validate_server_config(cfg)
    return out


# ─────────────────────────────────────────────────────────
# DB 读写 (system_config 表)
# ─────────────────────────────────────────────────────────

async def get_mcp_servers(*, force_refresh: bool = False) -> dict[str, dict]:
    """从 system_config 读 mcp servers 配置, 缓存 30s。"""
    global _mcp_cache, _mcp_cache_ts

    now = time.time()
    if not force_refresh and _mcp_cache is not None and (now - _mcp_cache_ts) < _CACHE_TTL:
        return _mcp_cache

    try:
        from sqlalchemy import select
        from app.common.models import SystemConfig
        from app.database import async_session_factory

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
                )
            ).scalar_one_or_none()

        servers = row.value if row and isinstance(row.value, dict) else {}
        _mcp_cache = servers
        _mcp_cache_ts = now
        return servers
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读 mcp_servers 配置失败: {e}")
        return {}


async def set_mcp_servers(servers: dict, *, updated_by: str | None = None) -> dict:
    """
    覆盖式写入 mcp_servers 配置, 写前会校验。
    返回规范化后的 dict。
    """
    normalized = validate_servers_dict(servers)
    try:
        from datetime import datetime
        from sqlalchemy import select
        from app.common.models import SystemConfig
        from app.database import async_session_factory

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
                )
            ).scalar_one_or_none()
            if row:
                row.value = normalized
                row.updated_by = updated_by
                row.updated_at = now_bjt()
            else:
                db.add(SystemConfig(
                    key=CONFIG_KEY, value=normalized, updated_by=updated_by,
                ))
            await db.commit()
    finally:
        invalidate_cache()
    return normalized


async def upsert_server(name: str, config: dict, *, updated_by: str | None = None) -> dict[str, dict]:
    """新增/更新单个 server, 返回更新后的完整 servers dict。"""
    validate_server_name(name)
    normalized_one = validate_server_config(config)
    current = await get_mcp_servers(force_refresh=True)
    current[name] = normalized_one
    return await set_mcp_servers(current, updated_by=updated_by)


async def delete_server(name: str, *, updated_by: str | None = None) -> dict[str, dict]:
    """删除单个 server, 返回剩余 servers dict。"""
    current = await get_mcp_servers(force_refresh=True)
    if name in current:
        current.pop(name)
        return await set_mcp_servers(current, updated_by=updated_by)
    return current


def invalidate_cache() -> None:
    """配置变更后立即失效缓存。"""
    global _mcp_cache, _mcp_cache_ts
    _mcp_cache = None
    _mcp_cache_ts = 0


# ─────────────────────────────────────────────────────────
# 给 subprocess 用的 JSON 构造
# ─────────────────────────────────────────────────────────

def build_mcp_config_json(servers: dict[str, dict]) -> str | None:
    """
    把 servers dict 转换成 aiclawcode --mcp-config 期望的 JSON 字符串。
    去掉 enabled=False 的 server 和我们加的 enabled 字段。
    返回 None 表示没有任何启用的 server (调用方应该不传 --mcp-config flag)。
    """
    enabled = {
        name: {k: v for k, v in cfg.items() if k != "enabled"}
        for name, cfg in servers.items()
        if cfg.get("enabled", True)
    }
    if not enabled:
        return None
    return json.dumps({"mcpServers": enabled}, ensure_ascii=False)
