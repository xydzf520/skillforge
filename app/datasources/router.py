"""数据源管理API路由"""

import csv
import io
import json
import re

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import get_current_user, require_role, require_state_active
from app.auth.models import User
from app.common.cache import cache_delete_pattern, cache_get, cache_set
from app.common.cache_facade import NamespaceCache
from app.common.exceptions import AppError
from app.config import settings
from app.database import get_db
from app.datasources import service
from app.datasources.models import DataSource, PlatformCookiePool
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime

router = APIRouter()

DATASOURCE_LIST_CACHE = NamespaceCache("datasources:list", ttl=settings.CACHE_TTL_DATASOURCE)


async def _ensure_source_dept_access(db: AsyncSession, source_id: str, user: User) -> None:
    """数据源部门权限校验"""
    result = await db.execute(
        select(DataSource.department, DataSource.visibility).where(DataSource.id == source_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AppError("SOURCE_NOT_FOUND", 404)
    _, visibility = row
    if not await service.has_data_access(
        db,
        source_id,
        user_id=user.id,
        user_department=user.department,
        can_view_all=bool(user.can_view_all or role_matches_any(user, ("admin", "ai_engineer"))),
    ):
        if visibility == "department":
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        raise AppError("AUTH_PERMISSION_DENIED", 403)


class CreateSourceRequest(BaseModel):
    source_id: str
    name: str
    department: str
    source_type: str  # csv_upload / api_pull
    config: dict = {}
    quality_rules: dict | None = None
    related_skills: list[str] | None = None
    sensitivity: str = "L2"
    owner_org_unit_id: str | None = None
    # v2.7 大厅 v3
    visibility: str = "department"  # company / department / private
    description: str | None = None
    usage_hint: str | None = None
    owner_contact: str | None = None


class UpdateSourceRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    quality_rules: dict | None = None
    related_skills: list[str] | None = None
    schedule: str | None = None
    stale_threshold_hours: int | None = None
    sensitivity: str | None = None
    owner_org_unit_id: str | None = None
    # v2.7 大厅 v3
    visibility: str | None = None
    description: str | None = None
    usage_hint: str | None = None
    owner_contact: str | None = None


class DataAccessRequest(BaseModel):
    reason: str = ""


class ApproveRequestBody(BaseModel):
    expires_at: str | None = None  # ISO datetime；None = 默认 90 天
    comment: str | None = None


class RejectRequestBody(BaseModel):
    comment: str


@router.get("/")
async def list_sources(
    department: str | None = Query(None),
    source_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据源列表（部门隔离）"""
    # 非全局角色强制本部门
    if not role_matches_any(current_user, ("admin", "ai_engineer")) and not current_user.can_view_all:
        department = current_user.department

    scope = (
        f"user={current_user.id}|role={current_user.role}|dept={current_user.department}|"
        f"view_all={bool(current_user.can_view_all)}"
    )
    try:
        cached = await DATASOURCE_LIST_CACHE.get(
            scope,
            department or "all",
            source_type or "all",
            is_active if is_active is not None else "all",
            page,
            page_size,
        )
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)

    result = await service.list_sources(
        db,
        department=department,
        source_type=source_type,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )

    try:
        await DATASOURCE_LIST_CACHE.set(
            scope,
            department or "all",
            source_type or "all",
            is_active if is_active is not None else "all",
            page,
            page_size,
            value=result,
        )
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return result


# ═══ 平台连接端点（必须在 /{source_id} 之前，否则路径会被当成 source_id） ═══

class PlatformConnectRequest(BaseModel):
    platform: str
    name: str = ""
    department: str = ""


class PushCookiesRequest(BaseModel):
    cookies: str
    domain: str = ""
    user_agent: str = ""
    cookie_details: list[dict] | None = None
    platform: str | None = None
    shop_id: str | None = None
    account_login: str | None = None


class ApiDiscoveryRequest(BaseModel):
    page_url: str
    page_title: str = ""
    apis: list[dict] = []
    reported_at: str = ""


SUPPORTED_PLATFORMS = {
    "taobao": "淘宝/天猫千牛",
    "sycm": "生意参谋",
    "alimama": "阿里妈妈",
    "douyin": "抖店/抖音",
    "jd": "京东",
    "pdd": "拼多多",
}


@router.get("/connector-api-key")
async def get_or_create_api_key(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """获取/生成 Chrome 扩展用的 API Key。"""
    key = await service.get_or_create_connector_api_key(db)
    return {"api_key": key}


@router.post("/connector-api-key/rotate")
async def rotate_api_key(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """强制重新生成 Chrome 扩展用的 API Key。"""
    key = await service.rotate_connector_api_key(db)
    return {"api_key": key}


class CreateConnectorKeyRequest(BaseModel):
    name: str = ""
    device_label: str = ""
    source_id: str | None = None
    owner_user_id: str | None = None
    scopes: list[str] | str | None = None
    scope: list[str] | str | None = None
    platforms: list[str] | str | None = None
    platform: str | None = None
    shop_ids: list[str] | str | None = None
    shop_id: str | None = None


class RevokeConnectorKeyRequest(BaseModel):
    reason: str = "manual"


class DisableCookieCredentialRequest(BaseModel):
    reason: str = "manual_disable"


class CookieCredentialPriorityRequest(BaseModel):
    priority: int = 1


@router.post("/connector-keys")
async def create_connector_key(
    body: CreateConnectorKeyRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """创建新的授权 Connector Key。"""
    platforms = body.platforms if body.platforms is not None else ([body.platform] if body.platform else None)
    shop_ids = body.shop_ids if body.shop_ids is not None else ([body.shop_id] if body.shop_id else None)
    scopes = body.scopes if body.scopes is not None else body.scope
    can_create_for_others = role_matches_any(current_user, ("admin", "system_admin"))
    requested_scopes = service._normalize_str_list(scopes)
    if service.CONNECTOR_SYNC_BUNDLE_SCOPE in requested_scopes and not can_create_for_others:
        raise AppError("AUTH_PERMISSION_DENIED", 403, {"scope": service.CONNECTOR_SYNC_BUNDLE_SCOPE})
    key_name = body.name or body.device_label
    return await service.create_connector_api_key(
        db,
        name=key_name,
        source_id=body.source_id,
        owner_user_id=(body.owner_user_id if can_create_for_others and body.owner_user_id else current_user.id),
        scopes=scopes,
        platforms=platforms,
        shop_ids=shop_ids,
        created_by=current_user.id,
    )


@router.get("/connector-keys")
async def list_connector_keys(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出授权 Connector Key。新 Key 返回可复制的完整明文。"""
    owner_filter = None if role_matches_any(current_user, ("admin", "system_admin")) else current_user.id
    return await service.list_connector_api_keys(
        db,
        status=status,
        owner_user_id=owner_filter,
        current_user=current_user,
        page=page,
        page_size=page_size,
    )


@router.post("/connector-keys/{key_id}/rotate")
async def rotate_connector_key(
    key_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """轮换授权 Connector Key。新明文只在本次响应返回。"""
    if not role_matches_any(current_user, ("admin", "system_admin")):
        row = await db.get(service.ConnectorApiKey, key_id)
        if not row or row.owner_user_id != current_user.id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
    return await service.rotate_connector_api_key_record(
        db,
        key_id=key_id,
        actor_user_id=current_user.id,
    )


@router.post("/connector-keys/{key_id}/revoke")
async def revoke_connector_key(
    key_id: str,
    body: RevokeConnectorKeyRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """吊销 Connector Key，并禁用该 key 写入的活跃 cookie。"""
    if not role_matches_any(current_user, ("admin", "system_admin")):
        row = await db.get(service.ConnectorApiKey, key_id)
        if not row or row.owner_user_id != current_user.id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
    reason = (body.reason if body else "manual").strip()
    if not reason:
        raise AppError("COOKIE_REASON_REQUIRED", 400, {"min_length": 1})
    return await service.revoke_connector_api_key(
        db,
        key_id=key_id,
        actor_user_id=current_user.id,
        reason=reason,
    )


@router.get("/cookie-pools")
async def list_cookie_pools(
    platform: str | None = Query(None),
    shop_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出平台 Cookie 池摘要，不返回明文 cookie。"""
    return await service.list_cookie_pool_summary(
        db,
        platform=platform,
        shop_id=shop_id,
        current_user=current_user,
        page=page,
        page_size=page_size,
    )


@router.get("/collection-health")
async def get_collection_health(
    platform: str | None = Query(None),
    shop_id: str | None = Query(None),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """全店采集健康看板数据。"""
    return await service.collection_health_overview(
        db,
        platform=platform,
        shop_id=shop_id,
        current_user=current_user,
    )


@router.get("/platform-api-drift-alerts")
async def list_platform_api_drift_alerts(
    platform: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_platform_api_drift_alerts(
        db,
        platform=platform,
        limit=limit,
        current_user=current_user,
    )


@router.post("/platform-api-drift-alerts/{alert_id}/ack")
async def ack_platform_api_drift_alert(
    alert_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.ack_platform_api_drift_alert(
        db,
        alert_id=alert_id,
        current_user=current_user,
    )


@router.get("/cookie-pools/audit")
async def list_cookie_pool_audit(
    platform: str | None = Query(None),
    shop_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """查看 Cookie 池审计，不返回 cookie 明文。"""
    return await service.list_cookie_pool_audit(
        db,
        platform=platform,
        shop_id=shop_id,
        limit=limit,
        current_user=current_user,
    )


@router.post("/cookie-pools/credentials/{credential_id}/disable")
async def disable_cookie_pool_credential(
    credential_id: str,
    body: DisableCookieCredentialRequest | None = None,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.disable_cookie_pool_credential(
        db,
        credential_id=credential_id,
        actor_user_id=current_user.id,
        reason=(body.reason if body else "manual_disable"),
        current_user=current_user,
    )


@router.post("/cookie-pools/credentials/{credential_id}/verify")
async def verify_cookie_pool_credential(
    credential_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.verify_cookie_pool_credential(
        db,
        credential_id=credential_id,
        current_user=current_user,
    )


@router.patch("/cookie-pools/credentials/{credential_id}/priority")
async def update_cookie_pool_credential_priority(
    credential_id: str,
    body: CookieCredentialPriorityRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_cookie_pool_credential_priority(
        db,
        credential_id=credential_id,
        priority=body.priority,
        actor_user_id=current_user.id,
        current_user=current_user,
    )


@router.get("/cookie-pools/{platform}/{shop_id}")
async def get_cookie_pool_detail(
    platform: str,
    shop_id: str,
    include_audit: bool = Query(False),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """查看某平台/店铺的 Cookie 凭证池详情，不返回明文 cookie。"""
    return await service.get_cookie_pool_detail(
        db,
        platform=platform,
        shop_id=shop_id,
        include_audit=include_audit,
        current_user=current_user,
    )


@router.get("/platform-connections/export")
async def export_platform_connections(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """导出全部平台连接配置（含加密 cookies），用于跨环境同步。
    注意：目标环境需使用相同 COOKIE_ENCRYPT_KEY 才能解密。
    """
    result = await db.execute(
        select(DataSource)
        .where(DataSource.source_type == "platform_cookies")
        .order_by(DataSource.id)
    )
    rows = result.scalars().all()
    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "name": r.name,
            "department": r.department,
            "config": r.config,
            "is_active": r.is_active,
            "created_by": r.created_by,
        })
    return {
        "export_type": "platform_connections",
        "exported_at": isoformat_bjt(now_bjt()),
        "exported_by": current_user.username,
        "total": len(items),
        "data": items,
    }


class PlatformConnectionsImportRequest(BaseModel):
    export_type: str
    data: list[dict]


@router.post("/platform-connections/import")
async def import_platform_connections(
    body: PlatformConnectionsImportRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """导入平台连接配置（按 id 合并）。
    注意：cookies 是加密的，需与源环境使用相同 COOKIE_ENCRYPT_KEY。
    """
    if body.export_type != "platform_connections":
        raise AppError("PARAM_INVALID", 400, {"detail": "export_type 不匹配"})

    imported = 0
    for item in body.data:
        source_id = item.get("id", "")
        if not source_id:
            continue

        existing = await db.execute(
            select(DataSource).where(DataSource.id == source_id)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.config = item.get("config", row.config)
            row.name = item.get("name", row.name)
            row.is_active = item.get("is_active", row.is_active)
        else:
            row = DataSource(
                id=source_id,
                name=item.get("name", source_id),
                department=item.get("department", "EC"),
                source_type="platform_cookies",
                config=item.get("config", {}),
                is_active=item.get("is_active", True),
                created_by=current_user.id,
            )
            db.add(row)
        imported += 1

    await db.commit()
    return {"message": f"已导入 {imported} 个平台连接", "total": imported}


# ═══ 跨机器一键同步 ═══

# 平台主页 URL（同步后可直接在 Docker Chrome 中打开验证登录态）
PLATFORM_HOME_URLS: dict[str, str] = {
    "platform-taobao": "https://myseller.taobao.com",
    "platform-sycm": "https://sycm.taobao.com",
    "platform-alimama": "https://one.alimama.com/index.html#!/report/account?rptType=account",
    "platform-douyin": "https://compass.jinritemai.example.test",
    "platform-jd": "https://shop.jd.com",
    "platform-pdd": "https://mms.pinduoduo.com",
}


@router.get("/sync-bundle")
async def export_sync_bundle(
    request: "Request",
    db: AsyncSession = Depends(get_db),
):
    """导出全部可同步数据（cookies 解密后明文传输，仅限 API Key / admin 认证）。
    用于跨机器部署时一键拉取，不依赖相同 COOKIE_ENCRYPT_KEY。
    """
    api_key = request.headers.get("X-SF-API-Key", "")
    auth_context: service.ConnectorAuthContext | None = None
    if api_key:
        auth_context = await service.verify_connector_api_key(db, api_key)
        if not auth_context:
            raise AppError("AUTH_INVALID_CREDENTIALS", 401)
        if not auth_context.has_scope(service.CONNECTOR_SYNC_BUNDLE_SCOPE):
            raise AppError("CONNECTOR_SCOPE_DENIED", 403, {"required_scope": service.CONNECTOR_SYNC_BUNDLE_SCOPE})
    else:
        from app.auth.dependencies import get_current_user as _get_user
        user = await _get_user(request, db)
        if not role_matches_any(user, ("admin", "system_admin")):
            raise AppError("AUTH_PERMISSION_DENIED", 403)

    # 平台连接（解密 cookies）
    platforms = []
    stmt = select(DataSource).where(DataSource.source_type == "platform_cookies")
    if auth_context and auth_context.source_id:
        stmt = stmt.where(DataSource.id == auth_context.source_id)
    rows = (await db.execute(stmt.order_by(DataSource.id))).scalars().all()
    allowed_platforms = set(auth_context.allowed_platforms or []) if auth_context else set()
    allowed_shop_ids = set(auth_context.allowed_shop_ids or []) if auth_context else set()
    for ds in rows:
        platform = str((ds.config or {}).get("platform") or "").strip().lower()
        if not platform and ds.id.startswith("platform-"):
            platform = ds.id.removeprefix("platform-").strip().lower()
        pool_stmt = (
            select(PlatformCookiePool)
            .where(PlatformCookiePool.source_id == ds.id)
            .where(PlatformCookiePool.is_active == True)  # noqa: E712
            .where(PlatformCookiePool.status == "active")
            .order_by(PlatformCookiePool.platform, PlatformCookiePool.shop_id, PlatformCookiePool.owner_user_id)
        )
        all_pool_rows = (await db.execute(pool_stmt)).scalars().all()
        pool_rows = list(all_pool_rows)
        if auth_context and auth_context.owner_user_id:
            pool_rows = [row for row in pool_rows if row.owner_user_id == auth_context.owner_user_id]
        if allowed_platforms:
            pool_rows = [row for row in pool_rows if row.platform in allowed_platforms]
        if allowed_shop_ids:
            pool_rows = [row for row in pool_rows if row.shop_id in allowed_shop_ids]

        config_clean = {
            k: v for k, v in (ds.config or {}).items()
            if k not in ("encrypted_cookies", "encrypted_cookie_details")
        }
        if all_pool_rows:
            for pool in pool_rows:
                cookies = await service.get_cookies(
                    db,
                    ds.id,
                    platform=pool.platform,
                    shop_id=pool.shop_id,
                    owner_user_id=pool.owner_user_id,
                )
                cookies_details = await service.get_cookie_details(
                    db,
                    ds.id,
                    platform=pool.platform,
                    shop_id=pool.shop_id,
                    owner_user_id=pool.owner_user_id,
                )
                platforms.append({
                    "id": ds.id,
                    "name": ds.name,
                    "department": ds.department,
                    "config": {
                        **config_clean,
                        "platform": pool.platform,
                        "shop_id": pool.shop_id,
                        "account_login": pool.account_login,
                    },
                    "cookies": cookies or "",
                    "cookie_details": cookies_details or [],
                    "is_active": ds.is_active and pool.is_active,
                    "owner_user_id": pool.owner_user_id,
                    "account_login": pool.account_login,
                    "auth_source": pool.auth_source,
                    "home_url": PLATFORM_HOME_URLS.get(ds.id) or PLATFORM_HOME_URLS.get(f"platform-{pool.platform}", ""),
                })
            continue

        shop_id = str((ds.config or {}).get("shop_id") or "").strip()
        if allowed_platforms and platform not in allowed_platforms:
            continue
        if allowed_shop_ids and shop_id not in allowed_shop_ids:
            continue
        cookies = await service.get_cookies(db, ds.id)
        cookies_details = await service.get_cookie_details(db, ds.id)
        platforms.append({
            "id": ds.id,
            "name": ds.name,
            "department": ds.department,
            "config": config_clean,
            "cookies": cookies or "",
            "cookie_details": cookies_details or [],
            "is_active": ds.is_active,
            "home_url": PLATFORM_HOME_URLS.get(ds.id) or PLATFORM_HOME_URLS.get(f"platform-{platform}", ""),
        })

    # 平台 API 注册表
    from app.browser.models import PlatformApiCache
    api_rows = (await db.execute(
        select(PlatformApiCache).order_by(PlatformApiCache.domain, PlatformApiCache.page_path)
    )).scalars().all()
    apis = [{
        "domain": r.domain,
        "page_path": r.page_path,
        "page_title": r.page_title,
        "apis_json": r.apis_json,
        "api_count": r.api_count,
        "discovery_method": r.discovery_method,
    } for r in api_rows]

    # MCP servers
    mcp_servers: dict = {}
    try:
        from app.coding_agent.mcp_config import get_mcp_servers
        mcp_servers = await get_mcp_servers(force_refresh=True)
    except Exception:
        pass

    return {
        "exported_at": isoformat_bjt(now_bjt()),
        "platform_connections": platforms,
        "platform_apis": apis,
        "mcp_servers": mcp_servers,
    }


class PullRemoteRequest(BaseModel):
    remote_url: str
    api_key: str


@router.post("/pull-remote")
async def pull_from_remote(
    body: PullRemoteRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """从远程 SkillForge 拉取全部同步数据：平台连接(cookies)、平台 API、MCP servers。
    拉取后自动注入 Docker Chrome，平台 URL 可直接打开且为登录态。
    """
    from datetime import datetime
    from urllib.parse import urlsplit, urlunsplit

    import httpx

    from app.browser.service import DiscoverUrlError, guard_discover_url

    try:
        remote_url = guard_discover_url(body.remote_url, field="remote_url")
    except DiscoverUrlError as e:
        raise AppError("PARAM_INVALID", 400, {"detail": str(e)}) from e

    parsed = urlsplit(remote_url)
    base_path = parsed.path.rstrip("/")
    url = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        f"{base_path}/api/data-sources/sync-bundle",
        "",
        "",
    ))
    try:
        url = guard_discover_url(url, field="remote_url")
    except DiscoverUrlError as e:
        raise AppError("PARAM_INVALID", 400, {"detail": str(e)}) from e
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"X-SF-API-Key": body.api_key})
    except Exception as e:
        raise AppError("REMOTE_UNREACHABLE", 502, {"detail": f"连接远程服务器失败: {type(e).__name__}: {e}"})

    if resp.status_code == 401:
        raise AppError("AUTH_INVALID_CREDENTIALS", 401, {"detail": "远程 API Key 无效"})
    if resp.status_code != 200:
        raise AppError("REMOTE_SYNC_FAILED", 502, {"detail": f"远程返回 {resp.status_code}: {resp.text[:200]}"})

    bundle = resp.json()

    # ── 1. 导入平台连接 + 重新加密 cookies ──
    imported_platforms = 0
    connected_platforms: list[dict] = []
    for item in bundle.get("platform_connections", []):
        source_id = item.get("id", "")
        if not source_id:
            continue

        ds = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()
        if not ds:
            ds = DataSource(
                id=source_id,
                name=item.get("name", source_id),
                department=item.get("department", "EC"),
                source_type="platform_cookies",
                config={},
                is_active=True,
                created_by=current_user.id,
            )
            db.add(ds)

        config = dict(item.get("config") or {})
        cookies_plain = item.get("cookies", "")
        cookie_details = item.get("cookie_details") or []
        if cookies_plain:
            cipher = service._get_cookie_cipher()
            config["encrypted_cookies"] = cipher.encrypt(cookies_plain.encode("utf-8")).decode("utf-8")
            if cookie_details:
                config["encrypted_cookie_details"] = cipher.encrypt(
                    json.dumps(cookie_details, ensure_ascii=False).encode("utf-8")
                ).decode("utf-8")
            config["connected"] = True
            config["pushed_at"] = isoformat_bjt(now_bjt())
            config["pushed_by"] = f"remote_sync:{current_user.id}"

        ds.config = config
        ds.name = item.get("name", ds.name)
        ds.is_active = item.get("is_active", True)
        ds.updated_at = now_bjt()
        imported_platforms += 1

        if cookies_plain:
            connected_platforms.append({
                "id": source_id,
                "name": item.get("name", source_id),
                "home_url": item.get("home_url") or PLATFORM_HOME_URLS.get(source_id, ""),
            })

    # ── 2. 导入平台 API 注册表 ──
    imported_apis = 0
    for item in bundle.get("platform_apis", []):
        domain = item.get("domain", "")
        page_path = item.get("page_path", "")
        if not domain or not page_path:
            continue

        from app.browser.models import PlatformApiCache
        existing = (await db.execute(
            select(PlatformApiCache).where(
                PlatformApiCache.domain == domain,
                PlatformApiCache.page_path == page_path,
            )
        )).scalar_one_or_none()
        if existing:
            existing.apis_json = item.get("apis_json", existing.apis_json)
            existing.api_count = item.get("api_count", existing.api_count)
            existing.page_title = item.get("page_title") or existing.page_title
        else:
            db.add(PlatformApiCache(
                domain=domain,
                page_path=page_path,
                page_title=item.get("page_title"),
                apis_json=item.get("apis_json", {}),
                api_count=item.get("api_count", 0),
                discovery_method=item.get("discovery_method"),
                updated_by=current_user.username,
            ))
        imported_apis += 1

    # ── 3. 导入 MCP servers ──
    imported_mcp = 0
    mcp_data = bundle.get("mcp_servers") or {}
    if mcp_data:
        try:
            from app.coding_agent.mcp_config import get_mcp_servers, set_mcp_servers
            current_mcp = await get_mcp_servers(force_refresh=True)
            current_mcp.update(mcp_data)
            await set_mcp_servers(current_mcp, updated_by=current_user.id)
            imported_mcp = len(mcp_data)
        except Exception as e:
            logger.warning("MCP servers 导入失败: {}", e)

    await db.commit()
    try:
        await cache_delete_pattern("datasources:*")
    except Exception:
        pass

    # ── 4. 自动注入 cookies 到 Docker Chrome ──
    browser_synced = False
    browser_msg = ""
    try:
        from app.browser.service import inject_stored_cookies
        result = await inject_stored_cookies(db)
        browser_synced = not result.get("error")
        browser_msg = "cookies 已注入浏览器" if browser_synced else result.get("error", "")
    except Exception as e:
        browser_msg = f"浏览器未运行，稍后启动浏览器时会自动注入: {e}"

    return {
        "status": "ok",
        "imported": {
            "platform_connections": imported_platforms,
            "platform_apis": imported_apis,
            "mcp_servers": imported_mcp,
        },
        "connected_platforms": connected_platforms,
        "browser_synced": browser_synced,
        "browser_message": browser_msg,
    }


@router.post("/platform-connect")
async def create_platform_connection(
    body: PlatformConnectRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建一个平台连接类型的数据源。"""
    if body.platform not in SUPPORTED_PLATFORMS:
        raise AppError("PARAM_INVALID", 400, {
            "detail": f"不支持的平台: {body.platform}",
        })
    platform_name = SUPPORTED_PLATFORMS[body.platform]
    source_id = f"platform-{body.platform}"
    name = body.name or f"{platform_name}连接"
    dept = body.department or current_user.department or "EC"
    result = await service.create_or_update_platform_connection(
        db, source_id=source_id, name=name, department=dept,
        platform=body.platform, user_id=current_user.id,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/api-discovery")
async def report_api_discovery(
    body: ApiDiscoveryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Chrome 扩展上报页面 API 请求。"""
    api_key = request.headers.get("X-SF-API-Key", "")
    if api_key:
        valid = await service.verify_connector_api_key(db, api_key)
        if not valid:
            raise AppError("AUTH_INVALID_CREDENTIALS", 401)
    else:
        from app.auth.dependencies import get_current_user as _get_user
        await _get_user(request, db)

    from urllib.parse import urlparse
    from app.common.models import SystemConfig
    import json as _json
    import re as _re

    domain = urlparse(body.page_url).netloc
    # 安全：校验 domain 只允许字母数字点横线，防止 config key 注入
    if not domain or not _re.fullmatch(r"[a-zA-Z0-9._:-]+", domain) or len(domain) > 253:
        raise AppError("PARAM_INVALID", 400, {"detail": f"无效的域名: {domain}"})
    key = f"api_discovery.{domain}"
    existing = (await db.execute(select(SystemConfig).where(SystemConfig.key == key))).scalar_one_or_none()

    record = {"page_url": body.page_url, "page_title": body.page_title,
              "apis": body.apis[:50], "reported_at": body.reported_at}

    if existing:
        old = _json.loads(existing.value) if existing.value else {}
        old_apis = old.get("apis") or []
        seen = {a.get("method", "") + " " + a.get("url", "").split("?")[0] for a in old_apis}
        for api in record["apis"]:
            k = api.get("method", "") + " " + api.get("url", "").split("?")[0]
            if k not in seen:
                old_apis.append(api)
                seen.add(k)
        old["apis"] = old_apis[-100:]
        old.update({k: record[k] for k in ("page_url", "page_title", "reported_at")})
        existing.value = _json.dumps(old, ensure_ascii=False)
    else:
        db.add(SystemConfig(key=key, value=_json.dumps(record, ensure_ascii=False)))
    await db.commit()

    logger.info("[api-discovery] domain={} apis={}", domain, len(body.apis))
    return {"status": "ok", "domain": domain, "api_count": len(body.apis)}


@router.get("/api-discovery")
async def list_api_discoveries(
    domain: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """查看 Chrome 扩展上报的 API 发现记录。"""
    return await service.list_api_discoveries(
        db,
        domain=domain,
        page=page,
        page_size=page_size,
    )


@router.get("/{source_id}")
async def get_source(
    source_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据源详情（部门隔离）"""
    cache_key = f"datasources:detail:{source_id}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            await _ensure_source_dept_access(db, source_id, current_user)
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)

    data = await service.get_source(db, source_id)
    await _ensure_source_dept_access(db, source_id, current_user)

    try:
        await cache_set(cache_key, data, ttl=settings.CACHE_TTL_DATASOURCE)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return data


@router.post("/")
async def create_source(
    body: CreateSourceRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """添加数据源"""
    result = await service.create_source(
        db,
        source_id=body.source_id,
        name=body.name,
        department=body.department,
        source_type=body.source_type,
        config=body.config,
        quality_rules=body.quality_rules,
        related_skills=body.related_skills,
        sensitivity=body.sensitivity,
        owner_org_unit_id=body.owner_org_unit_id,
        user_id=current_user.id,
        # v2.7 大厅 v3
        visibility=body.visibility,
        description=body.description,
        usage_hint=body.usage_hint,
        owner_contact=body.owner_contact,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


def _is_number(v: str) -> bool:
    """判断字符串是否为数值"""
    try:
        float(v.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False


def _is_date(v: str) -> bool:
    """判断字符串是否为日期格式（YYYY-MM-DD 或 YYYY/MM/DD）"""
    return bool(re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", str(v)))


@router.post("/{source_id}/preview-upload")
async def preview_upload(
    source_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """上传预览：解析CSV前10行 + 字段类型推断，不写入数据库"""
    from app.config import settings
    if file.size and file.size > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk")

    reader = csv.DictReader(io.StringIO(text))
    columns = reader.fieldnames or []
    rows = []
    for i, row in enumerate(reader):
        if i >= 10:
            break
        rows.append(row)

    total_lines = text.count("\n")

    # 简单类型推断
    col_types = {}
    for col in columns:
        values = [r.get(col, "") for r in rows if r.get(col)]
        if all(_is_number(v) for v in values if v):
            col_types[col] = "number"
        elif all(_is_date(v) for v in values if v):
            col_types[col] = "date"
        else:
            col_types[col] = "text"

    return {
        "columns": columns,
        "col_types": col_types,
        "preview_rows": rows,
        "estimated_total": total_lines,
        "file_name": file.filename,
    }


@router.post("/{source_id}/upload")
async def upload_csv(
    source_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """上传CSV文件"""
    from app.config import settings
    if file.size and file.size > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise AppError("FILE_TOO_LARGE", 413, detail={"max_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})
    result = await service.upload_csv(
        db,
        source_id=source_id,
        file_content=content,
        file_name=file.filename or "upload.csv",
        user_id=current_user.id,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.put("/{source_id}")
async def update_source(
    source_id: str,
    body: UpdateSourceRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """编辑数据源配置"""
    result = await service.update_source(
        db,
        source_id=source_id,
        name=body.name,
        config=body.config,
        quality_rules=body.quality_rules,
        related_skills=body.related_skills,
        schedule=body.schedule,
        stale_threshold_hours=body.stale_threshold_hours,
        sensitivity=body.sensitivity,
        owner_org_unit_id=body.owner_org_unit_id,
        user_id=current_user.id,
        user_role=current_user.role,
        # v2.7 大厅 v3
        visibility=body.visibility,
        description=body.description,
        usage_hint=body.usage_hint,
        owner_contact=body.owner_contact,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/{source_id}/pull")
async def pull_api_source(
    source_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """手动触发API数据源拉取"""
    result = await service.pull_api_source(
        db,
        source_id=source_id,
        user_id=current_user.id,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.get("/{source_id}/preview")
async def preview_data(
    source_id: str,
    limit: int = Query(50, le=200),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """预览最新上传的数据（前N行）"""
    await _ensure_source_dept_access(db, source_id, current_user)
    cache_key = f"datasources:preview:{source_id}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)

    result = await service.preview_latest(db, source_id, limit)

    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_DATASOURCE)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return result


@router.get("/{source_id}/history")
async def ingestion_history(
    source_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """上传/拉取历史记录"""
    await _ensure_source_dept_access(db, source_id, current_user)
    cache_key = f"datasources:history:{source_id}:{page}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)

    result = await service.get_ingestion_history(db, source_id, page, page_size)

    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_DATASOURCE)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return result


@router.get("/{source_id}/quality")
async def quality_report(
    source_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """最近一次上传的质量校验报告"""
    await _ensure_source_dept_access(db, source_id, current_user)
    cache_key = f"datasources:quality:{source_id}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)

    result = await service.get_latest_quality_report(db, source_id)

    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_DATASOURCE)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return result


@router.post("/{source_id}/request-access")
async def request_access(
    source_id: str,
    body: DataAccessRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """v2.7 大厅 v3：用户申请数据访问。reason 最少 20 字；幂等。"""
    return await service.request_data_access(
        db,
        source_id=source_id,
        user_id=current_user.id,
        user_department=current_user.department,
        reason=body.reason,
    )


@router.get("/{source_id}/requests")
async def list_requests(
    source_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出某数据源的申请单。owner_contact / admin 看全量；其他人仅自己的。"""
    is_privileged = role_matches_any(current_user, ("admin",)) or bool(current_user.can_view_all)
    return await service.list_data_access_requests(
        db,
        source_id=source_id,
        current_user_id=current_user.id,
        current_user_role=current_user.role,
        is_privileged=is_privileged,
    )


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    body: ApproveRequestBody,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """审批通过：写 DataAccessGrant + 通知申请人。owner_contact 或 admin 可操作。"""
    expires_at = None
    if body.expires_at:
        try:
            expires_at = parse_bjt_datetime(body.expires_at)
        except ValueError as e:
            raise AppError(
                "EXPIRES_AT_INVALID",
                400,
                detail={"reason": "expires_at 必须为 ISO-8601 格式"},
            ) from e
    result = await service.approve_data_access_request(
        db,
        request_id=request_id,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
        expires_at=expires_at,
        comment=body.comment,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/requests/{request_id}/reject")
async def reject_request(
    request_id: int,
    body: RejectRequestBody,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """审批驳回：comment 必填；通知申请人。"""
    result = await service.reject_data_access_request(
        db,
        request_id=request_id,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
        comment=body.comment,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.get("/requests/my")
async def list_my_requests(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """当前用户发起的所有申请（给大厅卡片状态标记用）。"""
    return await service.list_my_pending_requests(db, current_user_id=current_user.id)


@router.get("/requests/pending")
async def list_owner_pending_requests(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """我作为 owner_contact 需要审批的 pending 请求（admin 返回全量）。"""
    is_privileged = (
        current_user.role == "admin" or bool(current_user.can_view_all)
    )
    return await service.list_pending_requests_for_owner(
        db, owner_user_id=current_user.id, is_privileged=is_privileged
    )


@router.get("/{source_id}/access-grants")
async def access_grants(
    source_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_data_access_grants(db, source_id)


# ===== B10: 数据源字段迁移预览 =====

class MigrationPreviewRequest(BaseModel):
    old_mapping: dict
    new_mapping: dict


@router.post("/{source_id}/migration-preview")
async def migration_preview(
    source_id: str,
    body: MigrationPreviewRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """B10: 数据源字段变更时，预览受影响的 Skill 脚本"""
    from app.skills.intelligence.ai_service import preview_field_migration
    return await preview_field_migration(db, source_id, body.old_mapping, body.new_mapping)


# ═══ push-cookies / cookies 端点（带 /{source_id} 前缀，不会冲突） ═══

@router.post("/{source_id}/push-cookies")
async def push_cookies(
    source_id: str,
    body: PushCookiesRequest,
    request: "Request",
    db: AsyncSession = Depends(get_db),
):
    """
    Chrome 扩展推送 cookies。

    认证方式：
    - X-SF-API-Key header（扩展用）
    - 或 session cookie（已登录 SkillForge 用）
    """
    # 认证
    api_key = request.headers.get("X-SF-API-Key", "")
    auth_context: service.ConnectorAuthContext | None = None
    if api_key:
        auth_context = await service.verify_connector_api_key(db, api_key)
        if not auth_context:
            raise AppError("AUTH_INVALID_CREDENTIALS", 401)
        user_id = auth_context.actor_user_id
    else:
        from app.auth.dependencies import get_current_user as _get_user
        user = await _get_user(request, db)
        user_id = user.id
        auth_context = service.ConnectorAuthContext(
            auth_source=service.CONNECTOR_AUTH_SOURCE_SESSION,
            owner_user_id=user.id,
        )

    result = await service.push_cookies(
        db, source_id=source_id, cookies=body.cookies,
        domain=body.domain, user_agent=body.user_agent, user_id=user_id,
        cookie_details=body.cookie_details, platform=body.platform,
        shop_id=body.shop_id, account_login=body.account_login,
        auth_context=auth_context,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.get("/{source_id}/cookies")
async def get_cookies(
    source_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Skill 执行时获取 cookies（内部调用）。"""
    cookies = await service.get_cookies(db, source_id)
    if not cookies:
        raise AppError("NOT_FOUND", 404, {"detail": "未连接或 cookies 已过期"})
    return {"source_id": source_id, "cookies": cookies}


@router.post("/{source_id}/test-connection")
async def test_platform_connection(
    source_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """测试平台连接状态，不返回明文 cookies。"""
    return await service.test_platform_connection(db, source_id=source_id)


@router.post("/{source_id}/disconnect")
async def disconnect_platform(
    source_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """断开平台连接，清除已同步的 cookies 信息。"""
    result = await service.disconnect_platform_connection(
        db,
        source_id=source_id,
        user_id=current_user.id,
    )
    try:
        await cache_delete_pattern("datasources:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result
