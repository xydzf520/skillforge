"""浏览器自动化 API 路由"""

import asyncio
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from loguru import logger
from fastapi import APIRouter, Depends, Request, WebSocket, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.auth.models import User
from app.browser.models import BrowserSlot, PlatformApiCache
from app.common.exceptions import AppError
from app.common.internal_request import is_internal_request
from app.common.ws_auth import get_current_user_ws, WS_CODE_AUTH_REQUIRED
from app.config import settings
from app.database import get_db
from app.browser import service
from app.browser.service import DiscoverUrlError
from app.common.time_utils import isoformat_bjt, now_bjt


def _is_local(request: Request) -> bool:
    """判断是否为本地调用（coding agent 子进程通过 localhost 调用）。
    禁止带转发头（X-Forwarded-For/X-Real-IP/Forwarded）以防反向代理伪造绕过。
    """
    return is_internal_request(request)

router = APIRouter()


def _serialize_browser_slot(row: BrowserSlot) -> dict:
    return {
        "slot_id": row.slot_id,
        "browser_session_id": row.browser_session_id,
        "cdp_url": row.cdp_url,
        "profile_dir": row.profile_dir,
        "node_id": row.node_id,
        "egress_group": row.egress_group,
        "egress_ip_hash": row.egress_ip_hash,
        "status": row.status,
        "current_pool_id": row.current_pool_id,
        "current_credential_alias": row.current_credential_alias,
        "last_used_at": isoformat_bjt(row.last_used_at) if row.last_used_at else None,
        "created_at": isoformat_bjt(row.created_at) if row.created_at else None,
        "updated_at": isoformat_bjt(row.updated_at) if row.updated_at else None,
        "run_success_rate": None,
        "daily_success_rate": None,
        "health": 1.0 if row.status in {"idle", "busy"} else 0.0,
        "current_run_id": None,
    }


class BrowserSlotMaintenanceRequest(BaseModel):
    maintenance: bool = True
    reason: str = ""


class BrowserSlotReleaseRequest(BaseModel):
    reason: str = ""


# ═══════════════════════════════════════════════
# 容器管理
# ═══════════════════════════════════════════════

@router.post("/start")
async def start_browser(
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """启动浏览器容器"""
    return await service.start_browser(db, user_id=current_user.id)


@router.post("/stop")
async def stop_browser(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """停止浏览器容器"""
    return await service.stop_browser(db)


@router.get("/status")
async def browser_status(
    request: Request,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """查询浏览器容器状态"""
    return await service.get_browser_status()


@router.get("/status-local")
async def browser_status_local(request: Request):
    """浏览器状态（本地免认证，供 coding agent 调用）"""
    if not _is_local(request):
        return Response(status_code=403, content="仅限本地调用")
    return await service.get_browser_status()


@router.post("/sync-cookies")
async def sync_cookies(
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """将已存储的平台 cookies 同步到 Docker Chrome"""
    return await service.sync_cookies_to_browser(db)


@router.get("/slots")
async def list_browser_slots(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """列出浏览器采集 slot。首期只展示池状态，不暴露真实公网 IP。"""
    stmt = select(BrowserSlot)
    count_stmt = select(func.count()).select_from(BrowserSlot)
    if status:
        stmt = stmt.where(BrowserSlot.status == status)
        count_stmt = count_stmt.where(BrowserSlot.status == status)
    total = (await db.execute(count_stmt)).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(BrowserSlot.updated_at.desc().nullslast(), BrowserSlot.slot_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return {
        "items": [_serialize_browser_slot(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/slots/{slot_id}/restart")
async def restart_browser_slot(
    slot_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(BrowserSlot, slot_id)
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "browser slot 不存在"})
    row.status = "idle"
    row.updated_at = now_bjt()
    await db.commit()
    return _serialize_browser_slot(row)


@router.post("/slots/{slot_id}/maintenance")
async def set_browser_slot_maintenance(
    slot_id: str,
    body: BrowserSlotMaintenanceRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(BrowserSlot, slot_id)
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "browser slot 不存在"})
    row.status = "maintenance" if body.maintenance else "idle"
    row.updated_at = now_bjt()
    await db.commit()
    return _serialize_browser_slot(row)


@router.post("/slots/{slot_id}/release")
async def release_browser_slot(
    slot_id: str,
    body: BrowserSlotReleaseRequest | None = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(BrowserSlot, slot_id)
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "browser slot 不存在"})
    row.status = "idle"
    row.current_pool_id = None
    row.current_credential_alias = None
    row.updated_at = now_bjt()
    await db.commit()
    return _serialize_browser_slot(row)


class NavigateRequest(BaseModel):
    url: str


@router.post("/navigate")
async def navigate_browser(
    body: NavigateRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """在 Docker Chrome 中打开指定 URL（通过 CDP）"""
    try:
        return await service.navigate_to_url(body.url)
    except ValueError as e:
        raise AppError("PARAM_INVALID", 400, {"detail": str(e)})
    except RuntimeError as e:
        raise AppError("BROWSER_NOT_RUNNING", 503, {"detail": str(e)})


class VerifyLoginRequest(BaseModel):
    source_id: str  # platform-sycm / platform-douyin / ...


@router.post("/verify-login")
async def verify_login(
    body: VerifyLoginRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """主动调一次平台业务 API 验证登录态，结果写回 DataSource.config。

    返回 {source_id, verified, status: valid|expired|unknown|unsupported, detail, ...}
    """
    return await service.verify_login(
        db, source_id=body.source_id, user_id=current_user.id,
    )


class DiscoverRequest(BaseModel):
    url: str                    # 要抓包的页面 URL
    platform: str = "generic"   # sycm / generic（其它平台一律 generic）
    page_title: str = ""        # 可选页面标题，写到 PlatformApiCache.page_title
    wait_ms: int = 8000         # 抓包等待毫秒数


@router.post("/discover")
async def discover_apis(
    body: DiscoverRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """一键发现某平台某页面的 API 表，落到 PlatformApiCache。

    管理员/AI 工程师在新接入平台或平台改版时点一下，
    后续 Skill 创建直接复用 PlatformApiCache 即可。
    """
    try:
        return await service.run_discover(
            db,
            url=body.url,
            platform=body.platform,
            page_title=body.page_title,
            wait_ms=body.wait_ms,
            user_id=current_user.id,
        )
    except DiscoverUrlError as e:
        raise AppError("PARAM_INVALID", 400, detail={"url": body.url, "reason": str(e)}) from e


# ═══════════════════════════════════════════════
# noVNC HTTP + WebSocket 代理
# ═══════════════════════════════════════════════

@router.websocket("/novnc/websockify")
async def proxy_novnc_ws(websocket: WebSocket):
    """代理 noVNC WebSocket（iframe 内 VNC 协议流量，需登录且为 admin/ai_engineer）"""
    await websocket.accept(subprotocol="binary")
    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return
    from app.auth.access import role_matches_any
    if not role_matches_any(user, ("admin", "ai_engineer")):
        await websocket.close(code=4403, reason="forbidden")
        return

    import websockets
    novnc_ws_url = f"ws://{settings.BROWSER_NOVNC_HOST}:{settings.BROWSER_NOVNC_PORT}/websockify"

    try:
        async with websockets.connect(
            novnc_ws_url,
            subprotocols=["binary"],
            max_size=10 * 1024 * 1024,
        ) as vnc_ws:
            async def client_to_vnc():
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await vnc_ws.send(data)
                except Exception as e:
                    logger.debug("VNC 代理 client→vnc 方向结束: {}", e)

            async def vnc_to_client():
                try:
                    async for msg in vnc_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_bytes(msg.encode() if isinstance(msg, str) else msg)
                except Exception as e:
                    logger.debug("VNC 代理 vnc→client 方向结束: {}", e)

            done, pending = await asyncio.wait(
                [asyncio.create_task(client_to_vnc()), asyncio.create_task(vnc_to_client())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
    except Exception as e:
        logger.warning("VNC 代理连接建立/运行失败: {}", e)
    finally:
        # 仅在 WS 仍处于 CONNECTED / CONNECTING 状态时 close；否则 uvicorn ASGI send
        # 会抛 RuntimeError "Unexpected ASGI message 'websocket.close'"（重复关闭）
        from starlette.websockets import WebSocketState
        if websocket.application_state not in (WebSocketState.DISCONNECTED,):
            try:
                await websocket.close()
            except Exception as e:
                logger.debug("VNC 代理 WS close 失败: {}", e)


@router.get("/novnc/{path:path}")
async def proxy_novnc_http(path: str, request: Request):
    """代理 noVNC 静态文件（HTML/JS/CSS），供 iframe 嵌入"""
    novnc_base = f"http://{settings.BROWSER_NOVNC_HOST}:{settings.BROWSER_NOVNC_PORT}"
    url = f"{novnc_base}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            ct = resp.headers.get("content-type", "application/octet-stream")
            return Response(content=resp.content, status_code=resp.status_code, media_type=ct)
    except Exception as e:
        return Response(content=f"noVNC proxy error: {e}", status_code=502)


# ═══════════════════════════════════════════════
# 采集任务
# ═══════════════════════════════════════════════

class CollectRequest(BaseModel):
    platform: str       # sycm / douyin / xiaohongshu ...
    task_type: str      # dashboard / hot / orders ...
    params: dict = {}


class BrowserFetchJsonRequest(BaseModel):
    url: str | None = None
    registry_id: int | None = None
    api_index: int = 0
    method: str = "GET"
    headers: dict = {}
    body: dict | str | None = None
    page_url: str | None = None


@router.post("/collect")
async def collect(
    body: CollectRequest,
    request: Request,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """触发数据采集任务"""
    try:
        return await service.run_collection(
            db,
            platform=body.platform,
            task_type=body.task_type,
            params=body.params,
            user_id=current_user.id,
        )
    except DiscoverUrlError as e:
        raise AppError("PARAM_INVALID", 400, detail={"reason": str(e)}) from e


@router.post("/collect-local")
async def collect_local(
    body: CollectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """数据采集（本地免认证，供 coding agent 调用）"""
    if not _is_local(request):
        return Response(status_code=403, content="仅限本地调用")
    return await service.run_collection(
        db,
        platform=body.platform,
        task_type=body.task_type,
        params=body.params,
        user_id="coding_agent",
    )


@router.post("/collect-remote")
async def collect_remote(
    body: CollectRequest,
    request: Request,
    token: str = Query(..., description="BROWSER_REMOTE_TOKEN"),
    db: AsyncSession = Depends(get_db),
):
    """数据采集（远程调用，供 OpenClaw 上运行的 Skill 脚本使用）"""
    import hmac
    expected = settings.BROWSER_REMOTE_TOKEN
    if not expected:
        return Response(status_code=403, content="远程采集未启用，请设置 BROWSER_REMOTE_TOKEN")
    if not hmac.compare_digest(token, expected):
        return Response(status_code=401, content="token 无效")
    return await service.run_collection(
        db,
        platform=body.platform,
        task_type=body.task_type,
        params=body.params,
        user_id=f"remote:{request.client.host}" if request.client else "remote:unknown",
    )


@router.post("/fetch-json-local")
async def fetch_json_local(
    body: BrowserFetchJsonRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """浏览器 fetch-json（本地免认证，供 coding agent / MCP 调用）。

    SSRF 防御：body.url / body.page_url 都会先过 guard_discover_url，
    防 coding agent 被 prompt 诱导请求内网 / 元数据地址。
    """
    from app.browser.service import guard_discover_url
    if not _is_local(request):
        return Response(status_code=403, content="仅限本地调用")
    if body.registry_id is not None:
        return await service.replay_platform_api(
            db,
            registry_id=body.registry_id,
            api_index=body.api_index,
            method=body.method,
            headers=body.headers,
            body=body.body,
            page_url=body.page_url,
        )
    if not body.url:
        raise AppError("PARAM_INVALID", 400, detail={"reason": "缺少 url 或 registry_id"})
    try:
        guard_discover_url(body.url, field="url")
        if body.page_url:
            guard_discover_url(body.page_url, field="page_url")
    except DiscoverUrlError as e:
        raise AppError("PARAM_INVALID", 400, detail={"reason": str(e)}) from e
    return await service.fetch_json(
        url=body.url,
        method=body.method,
        headers=body.headers,
        body=body.body,
        page_url=body.page_url,
    )


@router.get("/tasks")
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """采集任务列表"""
    return await service.list_tasks(db, page=page, page_size=page_size)


# ═══════════════════════════════════════════════
# 平台 API 注册表（全局缓存，所有 Skill 共享）
# ═══════════════════════════════════════════════

@router.get("/platform-apis")
async def list_platform_apis(
    request: Request,
    domain: Optional[str] = Query(None, description="按域名筛选"),
    db: AsyncSession = Depends(get_db),
):
    """列出已发现的平台 API（本地免认证供 MCP 调用，远程需登录）"""
    if not _is_local(request):
        # 非本地调用需要登录
        from app.auth.dependencies import get_current_user as _get_user
        try:
            await _get_user(request, db)
        except Exception:
            return Response(status_code=401, content="未登录")

    query = select(PlatformApiCache)
    if domain:
        query = query.where(PlatformApiCache.domain == domain)
    query = query.order_by(PlatformApiCache.domain, PlatformApiCache.page_path)

    result = await db.execute(query)
    rows = result.scalars().all()
    return {
        "total": len(rows),
        "platforms": [
            {
                "id": r.id,
                "domain": r.domain,
                "page_path": r.page_path,
                "page_title": r.page_title,
                "api_count": r.api_count,
                "page_url": r.page_url,
                "last_verified_at": r.last_verified_at,
                "verification_status": r.verification_status,
                "discovery_method": r.discovery_method,
                "updated_by": r.updated_by,
                "updated_at": isoformat_bjt(r.updated_at),
            }
            for r in rows
        ],
    }


@router.get("/platform-apis/export")
async def export_platform_apis(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """导出全部平台 API 注册表（含完整 apis_json），用于跨环境同步。"""
    result = await db.execute(
        select(PlatformApiCache).order_by(PlatformApiCache.domain, PlatformApiCache.page_path)
    )
    rows = result.scalars().all()
    items = []
    for r in rows:
        items.append({
            "domain": r.domain,
            "page_path": r.page_path,
            "page_title": r.page_title,
            "apis_json": r.apis_json,
            "api_count": r.api_count,
            "discovery_method": r.discovery_method,
            "updated_by": r.updated_by,
            "updated_at": isoformat_bjt(r.updated_at),
        })
    return {
        "export_type": "platform_apis",
        "exported_at": isoformat_bjt(now_bjt()),
        "exported_by": current_user.username,
        "total": len(items),
        "data": items,
    }


class PlatformApisImportRequest(BaseModel):
    export_type: str
    data: list[dict]


@router.post("/platform-apis/import")
async def import_platform_apis(
    body: PlatformApisImportRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """导入平台 API 注册表（按 domain+page_path 合并）。"""
    if body.export_type != "platform_apis":
        raise AppError("PARAM_INVALID", 400, {"detail": "export_type 不匹配"})

    imported = 0
    for item in body.data:
        domain = item.get("domain", "")
        page_path = item.get("page_path", "")
        if not domain or not page_path:
            continue

        existing = await db.execute(
            select(PlatformApiCache).where(
                PlatformApiCache.domain == domain,
                PlatformApiCache.page_path == page_path,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.apis_json = item.get("apis_json", row.apis_json)
            row.api_count = item.get("api_count", row.api_count)
            row.page_title = item.get("page_title") or row.page_title
            row.discovery_method = item.get("discovery_method") or row.discovery_method
            row.updated_by = current_user.username
            row.updated_at = now_bjt()
        else:
            row = PlatformApiCache(
                domain=domain,
                page_path=page_path,
                page_title=item.get("page_title"),
                apis_json=item.get("apis_json", {}),
                api_count=item.get("api_count", 0),
                discovery_method=item.get("discovery_method"),
                updated_by=current_user.username,
            )
            db.add(row)
        imported += 1

    await db.commit()
    return {"message": f"已导入 {imported} 条", "total": imported}


@router.get("/platform-apis/{item_id}")
async def get_platform_api_detail(
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """读取一个平台页面的完整 API 发现结果"""
    if not _is_local(request):
        from app.auth.dependencies import get_current_user as _get_user
        try:
            await _get_user(request, db)
        except Exception:
            return Response(status_code=401, content="未登录")

    row = await db.get(PlatformApiCache, item_id)
    if not row:
        return Response(status_code=404, content="未找到")
    return {
        "id": row.id,
        "domain": row.domain,
        "page_path": row.page_path,
        "page_title": row.page_title,
        "api_count": row.api_count,
        "page_url": row.page_url,
        "capture_task_id": row.capture_task_id,
        "browser_session_id": row.browser_session_id,
        "login_source_id": row.login_source_id,
        "response_hashes": row.response_hashes,
        "last_verified_at": row.last_verified_at,
        "verification_status": row.verification_status,
        "apis_json": row.apis_json,
        "discovery_method": row.discovery_method,
        "updated_by": row.updated_by,
        "updated_at": isoformat_bjt(row.updated_at),
    }


class PlatformApiSaveRequest(BaseModel):
    url: str                # 完整页面 URL，自动解析 domain + page_path
    page_title: str = ""
    apis_json: dict         # capture_apis 的完整结果
    discovery_method: str = "browser_capture_apis"


@router.post("/platform-apis")
async def save_platform_api(
    body: PlatformApiSaveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """保存/更新平台 API 发现结果（capture_apis 后自动调用）"""
    if not _is_local(request):
        return Response(status_code=403, content="仅限本地调用")

    parsed = urlparse(body.url)
    domain = parsed.hostname or ""
    page_path = parsed.path or "/"

    # upsert
    existing = await db.execute(
        select(PlatformApiCache).where(
            PlatformApiCache.domain == domain,
            PlatformApiCache.page_path == page_path,
        )
    )
    row = existing.scalar_one_or_none()

    apis_json = service.attach_capture_proof(body.apis_json, page_url=body.url)
    api_count = len(apis_json.get("data_apis", []))

    if row:
        row.apis_json = apis_json
        row.api_count = api_count
        row.page_title = body.page_title or row.page_title
        row.discovery_method = body.discovery_method
        row.updated_by = "mcp_server"
        row.updated_at = now_bjt()
    else:
        row = PlatformApiCache(
            domain=domain,
            page_path=page_path,
            page_title=body.page_title,
            apis_json=apis_json,
            api_count=api_count,
            discovery_method=body.discovery_method,
            updated_by="mcp_server",
        )
        db.add(row)

    await db.commit()
    await db.refresh(row)
    return {
        "id": row.id,
        "domain": domain,
        "page_path": page_path,
        "api_count": api_count,
        "proof": apis_json.get("_proof"),
        "registry_meta": apis_json.get("_registry_meta"),
    }


class PlatformApiReplayRequest(BaseModel):
    api_index: int = 0
    method: str | None = None
    headers: dict = {}
    body: dict | str | None = None
    page_url: str | None = None


@router.post("/platform-apis/{item_id}/replay")
async def replay_platform_api(
    item_id: int,
    body: PlatformApiReplayRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """replay 注册表 API 并回写 verification proof（本地供 MCP 使用）。"""
    if not _is_local(request):
        return Response(status_code=403, content="仅限本地调用")
    return await service.replay_platform_api(
        db,
        registry_id=item_id,
        api_index=body.api_index,
        method=body.method,
        headers=body.headers,
        body=body.body,
        page_url=body.page_url,
    )
