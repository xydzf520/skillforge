"""SF dashboard API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.cache import cached, invalidate_sf_cache
from app.config import settings
from app.database import get_db
from app.sf.data_service import get_sf_data_record, list_sf_data_records, write_sf_data
from app.sf.service import build_sf_catalog, build_sf_overview, build_sf_trace, sf_mcp_dry_run

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


class SfMcpDryRunRequest(BaseModel):
    tool: str = Field(..., max_length=160)
    arguments: dict = Field(default_factory=dict)
    skill_id: str | None = Field(None, max_length=100)
    shop_id: str | None = Field(None, max_length=120)


class SfDataWriteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    namespace: str = Field("default", max_length=160)
    title: str | None = Field(None, max_length=240)
    content_type: str | None = Field(None, max_length=40)
    data: object | None = None
    content: object | None = None
    text: str | None = None
    rows: list | None = None
    base64: str | None = None
    filename: str | None = Field(None, max_length=240)
    metadata: dict = Field(default_factory=dict)
    source: str | None = Field(None, max_length=80)
    source_tool: str | None = Field(None, max_length=160)
    source_ref: str | None = Field(None, max_length=160)
    skill_id: str | None = Field(None, max_length=100)
    run_id: str | None = Field(None, max_length=80)
    visibility: str = Field("global", max_length=30)
    dry_run: bool = True
    idempotency_key: str | None = Field(None, max_length=160)


@router.get("/overview")
@cached(
    "sf:overview",
    ttl=settings.CACHE_TTL_GENERATED_DATA,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def sf_overview(
    days: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    kind: str | None = Query(None, pattern="^(api|mcp)?$"),
    tool: str | None = Query(None, max_length=160),
    user_id: str | None = Query(None, max_length=50),
    skill_id: str | None = Query(None, max_length=100),
    q: str | None = Query(None, max_length=200),
    report_q: str | None = Query(None, max_length=200),
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    return await build_sf_overview(
        db,
        current_user,
        days=days,
        page=page,
        page_size=page_size,
        kind=kind,
        tool=tool,
        user_id=user_id,
        skill_id=skill_id,
        q=q,
        report_q=report_q,
    )


@router.get("/catalog")
@cached(
    "sf:catalog",
    ttl=settings.CACHE_TTL_SF_CATALOG,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def sf_catalog(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    return await build_sf_catalog(db, current_user, days=days)


@router.get("/data")
async def sf_data_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    namespace: str | None = Query(None, max_length=160),
    content_type: str | None = Query(None, max_length=40),
    skill_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=80),
    source: str | None = Query(None, max_length=80),
    q: str | None = Query(None, max_length=200),
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    return await list_sf_data_records(
        db,
        current_user,
        page=page,
        page_size=page_size,
        namespace=namespace,
        content_type=content_type,
        skill_id=skill_id,
        run_id=run_id,
        source=source,
        q=q,
    )


@router.get("/data/{record_id}")
async def sf_data_detail(
    record_id: str,
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    return await get_sf_data_record(db, current_user, record_id)


@router.post("/data")
async def sf_data_write(
    body: SfDataWriteRequest,
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    payload = body.model_dump(by_alias=True, exclude_none=True)
    result = await write_sf_data(
        db,
        current_user,
        payload,
        dry_run=body.dry_run,
        idempotency_key=body.idempotency_key,
        source_tool=body.source_tool,
        source=body.source or "sf_api",
        skill_id=body.skill_id,
        run_id=body.run_id,
    )
    return result


@router.get("/trace")
@cached(
    "sf:trace",
    ttl=settings.CACHE_TTL_TRACE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def sf_trace(
    event_id: str | None = Query(None, max_length=100),
    run_id: str | None = Query(None, max_length=100),
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    return await build_sf_trace(db, current_user, event_id=event_id, run_id=run_id)


@router.post("/mcp/dry-run")
async def sf_mcp_dry_run_endpoint(
    body: SfMcpDryRunRequest,
    current_user: User = Depends(require_role("admin", "system_admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    result = await sf_mcp_dry_run(
        db,
        current_user,
        tool=body.tool,
        arguments=body.arguments,
        skill_id=body.skill_id,
        shop_id=body.shop_id,
    )
    try:
        from sqlalchemy import select
        from app.codex.models import CodexMcpCallAudit
        from app.learning.service import capture_sf_mcp_call

        proof_id = (result.get("proof") or {}).get("proof_id") if isinstance(result, dict) else None
        stmt = select(CodexMcpCallAudit).where(CodexMcpCallAudit.user_id == current_user.id)
        if proof_id:
            stmt = stmt.where(CodexMcpCallAudit.proof_id == proof_id)
        else:
            stmt = stmt.where(CodexMcpCallAudit.tool == body.tool).order_by(CodexMcpCallAudit.created_at.desc()).limit(1)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row:
            await capture_sf_mcp_call(db, row)
    except Exception:
        pass
    await invalidate_sf_cache()
    return result
