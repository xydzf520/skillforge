"""
Coding Agent 管理 API。

当前 Phase 2 仅提供"测试连接"端点（实际的 chat WS 端点在 Phase 3 接到 workbench/router.py）。
"""

from __future__ import annotations


from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.ai import (
    get_coding_agent_config,
    invalidate_coding_agent_config_cache,
)
from app.common.exceptions import AppError
from app.coding_agent.runtime_availability import RUNTIME_MESSAGE, RUNTIME_UNAVAILABLE, runtime_status
from app.common.time_utils import isoformat_bjt, now_bjt


router = APIRouter()


async def _builtin_mcp_servers() -> dict[str, dict]:
    """Return built-in MCP servers injected into Coding Agent sessions.

    These are not stored in system_config, but the admin page should still show
    them so operators can see what a session will actually receive.
    """
    from app.coding_agent.session_service import SessionService

    return await SessionService._build_builtin_mcp_servers("__builtin_preview__", "admin_preview")


def _mask_mcp_config(cfg: dict) -> dict:
    c = dict(cfg)
    if "env" in c and isinstance(c["env"], dict):
        c["env"] = {k: ("•" * 8 if v else "") for k, v in c["env"].items()}
    if "headers" in c and isinstance(c["headers"], dict):
        c["headers"] = {
            k: ("•" * 8 if v and ("auth" in k.lower() or "token" in k.lower() or "key" in k.lower()) else v)
            for k, v in c["headers"].items()
        }
    return c


class TestConnectionResponse(BaseModel):
    ok: bool
    session_id: str | None = None
    model: str | None = None
    tools_count: int | None = None
    prompt_hash: str | None = None
    config_dir: str | None = None
    bare_mode: bool | None = None
    vendor_version: str | None = None
    provider: str | None = None
    permission_strategy: str | None = None
    mcp_servers_count: int | None = None
    duration_ms: int
    error_code: str | None = None
    error: str | None = None


@router.post("/coding-agent/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    current_user: User = Depends(require_role("admin")),
):
    """Report the retired adapter without reading credentials or launching tools."""
    return TestConnectionResponse(
        ok=False, duration_ms=0, error_code=RUNTIME_UNAVAILABLE,
        error=RUNTIME_MESSAGE,
    )


@router.get("/coding-agent/config-status")
async def config_status(
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """
    返回当前生效的 coding_agent 配置（脱敏后），供管理页展示"实际生效值"。
    api_key 只显示最后 4 位。
    """
    config = await get_coding_agent_config()
    api_key = str(config.get("coding_agent.api_key") or "")
    masked_key = ("•" * 8 + api_key[-4:]) if len(api_key) > 4 else ("配置但过短" if api_key else "未配置")
    return {
        "provider": config.get("coding_agent.provider") or "",
        "api_base": config.get("coding_agent.api_base") or "",
        "api_key_masked": masked_key,
        "api_key_set": bool(api_key),
        "model": config.get("coding_agent.model") or "",
        "max_tokens": config.get("coding_agent.max_tokens"),
        "permission_strategy": config.get("coding_agent.permission_strategy") or "balanced",
        "bare_mode": bool(config.get("coding_agent.bare_mode", True)),
        "vendor_version": None,
        "requested_enabled": bool(config.get("coding_agent.enabled", False)),
        "enabled": False,
        "runtime": runtime_status(),
    }


@router.post("/coding-agent/invalidate-cache")
async def invalidate_cache(
    current_user: User = Depends(require_role("admin")),
):
    """配置改完后立即失效 60s 缓存（不必等过期）。"""
    invalidate_coding_agent_config_cache()
    return {"message": "缓存已失效"}


# ═══════════════════════════════════════════════════════════════
# MCP servers 管理
# ═══════════════════════════════════════════════════════════════

class McpServerUpsertRequest(BaseModel):
    """新增/更新一个 MCP server 的请求体, 字段按 transport 不同。"""
    enabled: bool = True
    type: str = "stdio"  # stdio / http / sse
    # stdio
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    # http / sse
    url: str | None = None
    headers: dict[str, str] | None = None


@router.get("/coding-agent/mcp-servers")
async def list_mcp_servers(
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """列出全部 MCP servers (含 enabled=False 的)。"""
    from app.coding_agent.mcp_config import get_mcp_servers

    servers = await get_mcp_servers(force_refresh=True)
    masked: dict[str, dict] = {}
    builtin = await _builtin_mcp_servers()
    for name, cfg in builtin.items():
        c = _mask_mcp_config(cfg)
        c["builtin"] = True
        c["managed"] = "code"
        c.setdefault("description", "代码内置，自动注入 Coding Agent 会话")
        masked[name] = c

    # 脱敏: 隐藏敏感 header / env value (用 ••• 代替)
    for name, cfg in servers.items():
        c = _mask_mcp_config(cfg)
        c["builtin"] = False
        c["managed"] = "config"
        masked[name] = c
    return {"servers": masked, "count": len(masked), "configured_count": len(servers), "builtin_count": len(builtin)}


@router.put("/coding-agent/mcp-servers/{name}")
async def upsert_mcp_server(
    name: str,
    body: McpServerUpsertRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """新增/更新一个 MCP server。"""
    from app.coding_agent.mcp_config import McpConfigError, upsert_server

    if name in await _builtin_mcp_servers():
        raise AppError("MCP_SERVER_BUILTIN", 400, {"detail": "内置 MCP server 由代码管理，不能在后台编辑"})
    raw = body.model_dump(exclude_none=True)
    try:
        servers = await upsert_server(name, raw, updated_by=current_user.id)
    except McpConfigError as e:
        raise AppError("MCP_CONFIG_INVALID", 400, {"detail": str(e)})
    return {"message": "保存成功", "name": name, "count": len(servers)}


@router.delete("/coding-agent/mcp-servers/{name}")
async def delete_mcp_server(
    name: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """删除一个 MCP server。"""
    from app.coding_agent.mcp_config import delete_server

    if name in await _builtin_mcp_servers():
        raise AppError("MCP_SERVER_BUILTIN", 400, {"detail": "内置 MCP server 由代码管理，不能删除"})
    servers = await delete_server(name, updated_by=current_user.id)
    return {"message": "已删除", "remaining_count": len(servers)}


@router.post("/coding-agent/mcp-servers/{name}/toggle")
async def toggle_mcp_server(
    name: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """切换 enabled 状态。"""
    from app.coding_agent.mcp_config import McpConfigError, get_mcp_servers, upsert_server

    if name in await _builtin_mcp_servers():
        raise AppError("MCP_SERVER_BUILTIN", 400, {"detail": "内置 MCP server 固定启用，不能切换"})
    servers = await get_mcp_servers(force_refresh=True)
    if name not in servers:
        raise AppError("MCP_SERVER_NOT_FOUND", 404, {"name": name})
    cfg = dict(servers[name])
    cfg["enabled"] = not bool(cfg.get("enabled", True))
    try:
        await upsert_server(name, cfg, updated_by=current_user.id)
    except McpConfigError as e:
        raise AppError("MCP_CONFIG_INVALID", 400, {"detail": str(e)})
    return {"name": name, "enabled": cfg["enabled"]}


class McpTestResponse(BaseModel):
    ok: bool
    name: str
    duration_ms: int
    tools: list[str] = []
    error: str | None = None


@router.get("/coding-agent/mcp-servers/export")
async def export_mcp_servers(
    current_user: User = Depends(require_role("admin")),
):
    """导出全部 MCP servers 配置（含完整密钥），用于跨环境同步。"""
    from app.coding_agent.mcp_config import get_mcp_servers

    servers = await get_mcp_servers(force_refresh=True)
    return {
        "export_type": "mcp_servers",
        "exported_at": isoformat_bjt(now_bjt()),
        "exported_by": current_user.username,
        "data": servers,
    }


class McpImportRequest(BaseModel):
    export_type: str
    data: dict


@router.post("/coding-agent/mcp-servers/import")
async def import_mcp_servers(
    body: McpImportRequest,
    current_user: User = Depends(require_role("admin")),
):
    """导入 MCP servers 配置（合并模式：同名覆盖，不删已有）。"""
    if body.export_type != "mcp_servers":
        raise AppError("PARAM_INVALID", 400, {"detail": "export_type 不匹配"})

    from app.coding_agent.mcp_config import get_mcp_servers, set_mcp_servers, McpConfigError

    current = await get_mcp_servers(force_refresh=True)
    current.update(body.data)
    try:
        await set_mcp_servers(current, updated_by=current_user.id)
    except McpConfigError as e:
        raise AppError("MCP_CONFIG_INVALID", 400, {"detail": str(e)})
    return {"message": f"已导入 {len(body.data)} 个 server", "total": len(current)}


@router.post("/coding-agent/mcp-servers/{name}/test", response_model=McpTestResponse)
async def test_mcp_server(
    name: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """Runtime tool discovery is unavailable; saved MCP definitions are preserved."""
    return McpTestResponse(
        ok=False, name=name, duration_ms=0, error=RUNTIME_MESSAGE,
    )
