"""Department knowledge base API."""

import hashlib
import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import cached, invalidate_knowledge_cache
from app.config import settings
from app.database import get_db
from app.knowledge import service

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _body_cache_key(*args, **kwargs) -> str:
    body = kwargs.get("body")
    if hasattr(body, "model_dump"):
        payload = body.model_dump(mode="json")
    else:
        payload = body
    return _hash_payload(payload)


def _document_detail_cache_key(*args, **kwargs) -> str:
    return str(kwargs.get("document_id") or (args[0] if args else ""))


class KnowledgeDocumentCreateRequest(BaseModel):
    kb_id: str | None = Field(default=None, max_length=50)
    org_unit_id: str | None = Field(default=None, max_length=50)
    title: str = Field(..., min_length=1, max_length=240)
    content: str = Field(..., min_length=1, max_length=800_000)
    source_type: str = Field(default="manual", max_length=30)
    source_ref: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=30)
    metadata: dict[str, Any] | None = None
    upsert_source: bool = False


class KnowledgeDocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, min_length=1, max_length=800_000)
    source_type: str | None = Field(default=None, max_length=30)
    source_ref: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=30)
    metadata: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=20)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    kb_id: str | None = Field(default=None, max_length=50)
    org_unit_id: str | None = Field(default=None, max_length=50)
    top_k: int = Field(default=8, ge=1, le=20)
    modality: str = Field(default="all", max_length=20)


class KnowledgeAskRequest(KnowledgeSearchRequest):
    use_llm: bool = True


class KnowledgeContextRequest(KnowledgeSearchRequest):
    max_chars: int = Field(default=6000, ge=500, le=20_000)




class KnowledgeUrlImportRequest(BaseModel):
    kb_id: str | None = Field(default=None, max_length=50)
    org_unit_id: str | None = Field(default=None, max_length=50)
    url: str = Field(..., min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=240)
    tags: list[str] = Field(default_factory=list, max_length=30)
    upsert_source: bool = True


class KnowledgeSyncRequest(BaseModel):
    kb_id: str | None = Field(default=None, max_length=50)
    org_unit_id: str | None = Field(default=None, max_length=50)


class KnowledgeEmbeddingTestRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=40)
    api_base: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=160)
    dim: int | None = Field(default=None, ge=1, le=8192)
    timeout: int | None = Field(default=None, ge=5, le=120)


class KnowledgeLightRagServerTestRequest(BaseModel):
    api_base: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    flavor: str | None = Field(default=None, max_length=40)
    timeout: int | None = Field(default=None, ge=5, le=120)


class KnowledgeDoclingTestRequest(BaseModel):
    enabled: bool | None = None
    api_base: str | None = Field(default=None, max_length=300)
    timeout: int | None = Field(default=None, ge=5, le=120)


class KnowledgeVlmTestRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=40)
    api_base: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=160)
    timeout: int | None = Field(default=None, ge=5, le=120)


class KnowledgeLightRagServerSyncRequest(BaseModel):
    kb_id: str | None = Field(default=None, max_length=50)
    org_unit_id: str | None = Field(default=None, max_length=50)
    limit: int = Field(default=200, ge=1, le=1000)


@router.get("/summary")
@cached(
    "knowledge:summary",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_knowledge_summary(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_summary(db, current_user)


@router.get("/bases")
@cached(
    "knowledge:bases",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_knowledge_bases(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_bases(db, current_user)


@router.post("/rag/test-embedding")
async def test_knowledge_embedding_config(
    body: KnowledgeEmbeddingTestRequest | None = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.test_embedding_config(
        db,
        current_user,
        overrides=body.model_dump(exclude_none=True) if body else None,
    )


@router.post("/rag/test-server")
async def test_knowledge_lightrag_server_config(
    body: KnowledgeLightRagServerTestRequest | None = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.test_lightrag_server_config(
        db,
        current_user,
        overrides=body.model_dump(exclude_none=True) if body else None,
    )


@router.post("/rag/test-docling")
async def test_knowledge_docling_config(
    body: KnowledgeDoclingTestRequest | None = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.test_docling_config(
        db,
        current_user,
        overrides=body.model_dump(exclude_none=True) if body else None,
    )


@router.post("/rag/test-vlm")
async def test_knowledge_vlm_config(
    body: KnowledgeVlmTestRequest | None = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.test_vlm_config(
        db,
        current_user,
        overrides=body.model_dump(exclude_none=True) if body else None,
    )


@router.post("/rag/sync-server")
async def sync_knowledge_lightrag_server(
    body: KnowledgeLightRagServerSyncRequest | None = None,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.sync_lightrag_server(
        db,
        current_user,
        kb_id=body.kb_id if body else None,
        org_unit_id=body.org_unit_id if body else None,
        limit=body.limit if body else 200,
    )
    await invalidate_knowledge_cache()
    return result


@router.get("/rag/health")
@cached(
    "knowledge:rag:health",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_knowledge_rag_health(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_rag_health(db, current_user)


@router.get("/index-jobs")
@cached(
    "knowledge:index-jobs",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_knowledge_index_jobs(
    kb_id: str | None = Query(None),
    org_unit_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_index_jobs(
        db,
        current_user,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        status=status,
        limit=limit,
    )


@router.post("/sync")
async def sync_knowledge_assets(
    body: KnowledgeSyncRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    result = await service.sync_department_assets(
        db,
        current_user,
        kb_id=body.kb_id,
        org_unit_id=body.org_unit_id,
    )
    await invalidate_knowledge_cache()
    return result


@router.get("/documents")
@cached(
    "knowledge:documents:list",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_knowledge_documents(
    kb_id: str | None = Query(None),
    org_unit_id: str | None = Query(None),
    status: str = Query("active"),
    query: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_documents(
        db,
        current_user,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        status=status,
        query=query,
        limit=limit,
    )


@router.post("/documents")
async def create_knowledge_document(
    body: KnowledgeDocumentCreateRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    result = await service.create_document(
        db,
        current_user,
        kb_id=body.kb_id,
        org_unit_id=body.org_unit_id,
        title=body.title,
        content=body.content,
        source_type=body.source_type,
        source_ref=body.source_ref,
        tags=body.tags,
        metadata=body.metadata,
        upsert_source=body.upsert_source,
    )
    await invalidate_knowledge_cache()
    return result


@router.post("/documents/upload")
async def upload_knowledge_document(
    kb_id: str | None = Form(default=None),
    org_unit_id: str | None = Form(default=None),
    title: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    raw = await file.read()
    tag_list = [item.strip() for item in (tags or "").replace("，", ",").split(",") if item.strip()]
    result = await service.upload_document(
        db,
        current_user,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        filename=file.filename or "document.txt",
        content_type=file.content_type,
        data=raw,
        title=title,
        tags=tag_list,
    )
    await invalidate_knowledge_cache()
    return result


@router.get("/media/{asset_id}/content")
async def get_knowledge_media_content(
    asset_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data, mime_type, filename = await service.get_media_asset_content(db, current_user, asset_id)
    safe_name = quote(filename or f"{asset_id}.img")
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{safe_name}",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.post("/documents/import-url")
async def import_knowledge_url(
    body: KnowledgeUrlImportRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    result = await service.import_url_document(
        db,
        current_user,
        kb_id=body.kb_id,
        org_unit_id=body.org_unit_id,
        url=body.url,
        title=body.title,
        tags=body.tags,
        upsert_source=body.upsert_source,
    )
    await invalidate_knowledge_cache()
    return result


@router.get("/documents/{document_id}")
@cached(
    "knowledge:documents:detail",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_builder=_document_detail_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_knowledge_document(
    document_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_document(db, current_user, document_id)


@router.put("/documents/{document_id}")
async def update_knowledge_document(
    document_id: str,
    body: KnowledgeDocumentUpdateRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    result = await service.update_document(
        db,
        current_user,
        document_id,
        title=body.title,
        content=body.content,
        source_type=body.source_type,
        source_ref=body.source_ref,
        tags=body.tags,
        status=body.status,
        metadata=body.metadata,
    )
    await invalidate_knowledge_cache()
    return result


@router.delete("/documents/{document_id}")
async def delete_knowledge_document(
    document_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    result = await service.delete_document(db, current_user, document_id)
    await invalidate_knowledge_cache()
    return result


@router.post("/documents/{document_id}/reindex")
async def reindex_knowledge_document(
    document_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    result = await service.reindex_document(db, current_user, document_id)
    await invalidate_knowledge_cache()
    return result


@router.post("/search")
@cached(
    "knowledge:search",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_builder=_body_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def search_knowledge(
    body: KnowledgeSearchRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.search_knowledge(
        db,
        current_user,
        query=body.query,
        kb_id=body.kb_id,
        org_unit_id=body.org_unit_id,
        top_k=body.top_k,
        modality=body.modality,
    )


@router.post("/search/upload")
async def search_knowledge_by_image(
    kb_id: str | None = Form(default=None),
    org_unit_id: str | None = Form(default=None),
    top_k: int = Form(default=8),
    modality: str = Form(default="all"),
    file: UploadFile = File(...),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    raw = await file.read()
    return await service.search_knowledge_by_image(
        db,
        current_user,
        filename=file.filename or "query.png",
        content_type=file.content_type,
        data=raw,
        kb_id=kb_id,
        org_unit_id=org_unit_id,
        top_k=top_k,
        modality=modality,
    )


@router.post("/ask")
async def ask_knowledge(
    body: KnowledgeAskRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.ask_knowledge(
        db,
        current_user,
        query=body.query,
        kb_id=body.kb_id,
        org_unit_id=body.org_unit_id,
        top_k=body.top_k,
        use_llm=body.use_llm,
        modality=body.modality,
    )


@router.post("/context")
@cached(
    "knowledge:context",
    ttl=settings.CACHE_TTL_KNOWLEDGE,
    scope_by=_CACHE_SCOPE,
    key_builder=_body_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def build_knowledge_context(
    body: KnowledgeContextRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.build_knowledge_context(
        db,
        current_user,
        query=body.query,
        kb_id=body.kb_id,
        org_unit_id=body.org_unit_id,
        top_k=body.top_k,
        max_chars=body.max_chars,
        modality=body.modality,
    )
