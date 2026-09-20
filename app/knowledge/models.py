"""Department knowledge base ORM models."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class DepartmentKnowledgeBase(Base):
    """One automatically maintained knowledge base per department/org unit."""

    __tablename__ = "department_knowledge_bases"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    org_unit_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("org_units.id", ondelete="SET NULL"),
        unique=True,
        index=True,
    )
    department: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    storage_backend: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="lightrag",
        server_default="lightrag",
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active", index=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    stats_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)


class KnowledgeDocument(Base):
    """Raw knowledge document controlled by a department knowledge base."""

    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("department_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="manual", server_default="manual", index=True)
    source_ref: Mapped[str | None] = mapped_column(String(200), index=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active", index=True)
    index_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    index_error: Mapped[str | None] = mapped_column(Text)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=sa.text("1"))
    content_mime: Mapped[str | None] = mapped_column(String(120))
    source_version: Mapped[str | None] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tags_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    updated_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.Index("ix_knowledge_documents_kb_status_updated", "kb_id", "status", "updated_at"),
        sa.Index("ix_knowledge_documents_source", "kb_id", "source_type", "source_ref"),
    )


class KnowledgeMediaAsset(Base):
    """Binary media owned by a knowledge document and indexed through text proxy chunks."""

    __tablename__ = "knowledge_media_assets"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("department_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(240), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", server_default="pending", index=True)
    caption: Mapped[str | None] = mapped_column(Text)
    visible_text: Mapped[str | None] = mapped_column(Text)
    objects_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    embedding_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    index_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        sa.Index("ix_knowledge_media_kb_status", "kb_id", "status"),
        sa.Index("ix_knowledge_media_kb_sha", "kb_id", "sha256"),
    )


class KnowledgeChunk(Base):
    """Searchable chunk with deterministic vector and LightRAG-style graph hints."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    media_asset_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("knowledge_media_assets.id", ondelete="SET NULL"),
        index=True,
    )
    modality: Mapped[str] = mapped_column(String(20), nullable=False, default="text", server_default="text", index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    embedding_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    keywords_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    entities_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_doc_index"),
        sa.Index("ix_knowledge_chunks_kb_doc", "kb_id", "document_id"),
    )


class KnowledgeQueryLog(Base):
    """Append-only search/ask trace for audit and retrieval tuning."""

    __tablename__ = "knowledge_query_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[str | None] = mapped_column(String(50), index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="search", server_default="search", index=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=8, server_default=sa.text("8"))
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    answer_preview: Mapped[str | None] = mapped_column(Text)
    context_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    retrieval_backend: Mapped[str | None] = mapped_column(String(40), index=True)
    lightrag_mode: Mapped[str | None] = mapped_column(String(20))
    rerank_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa.text("false"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    source_trace_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, index=True)


class KnowledgeIndexJob(Base):
    """LightRAG indexing job trace. Jobs are processed synchronously today and can be moved to a worker later."""

    __tablename__ = "knowledge_index_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    document_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        index=True,
    )
    kb_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50))
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=sa.text("3"))
    error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_bjt, onupdate=now_bjt)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        sa.Index("ix_knowledge_index_jobs_status_created", "status", "created_at"),
    )
