"""大厅直接能力相关持久化模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class DirectCapabilityImageHistory(Base):
    __tablename__ = "direct_capability_image_history"
    __table_args__ = (
        UniqueConstraint("user_id", "capability_id", "image_url", name="uq_direct_capability_image_history_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    prompt: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(String(120), index=True)
    source: Mapped[str | None] = mapped_column(String(30))
    extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class DirectCapabilityTask(Base):
    """Direct capability async task tracked by SkillForge."""

    __tablename__ = "direct_capability_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(80), index=True)
    workspace_name: Mapped[str | None] = mapped_column(String(120))
    upstream_task_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    prompt: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class AIChatThread(Base):
    """A durable, user-owned AI Chat conversation."""

    __tablename__ = "ai_chat_threads"
    __table_args__ = (
        Index("ix_ai_chat_threads_user_recent", "user_id", "archived", "last_message_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="新对话")
    default_model: Mapped[str] = mapped_column(String(80), nullable=False, default="deepseek-v4-pro")
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_message_id: Mapped[str | None] = mapped_column(String(36))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False, index=True)


class AIChatUserPreference(Base):
    """Per-user AI Chat defaults that survive across browsers and sessions."""

    __tablename__ = "ai_chat_user_preferences"

    user_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_model: Mapped[str] = mapped_column(String(80), nullable=False, default="deepseek-v4-pro")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class AIChatMessage(Base):
    """One immutable user turn or persisted assistant response."""

    __tablename__ = "ai_chat_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_ai_chat_message_user_idempotency"),
        Index("ix_ai_chat_messages_thread_order", "thread_id", "created_at", "id"),
        Index("ix_ai_chat_messages_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed", index=True)
    model: Mapped[str | None] = mapped_column(String(80))
    provider_group: Mapped[str | None] = mapped_column(String(32))
    vision_model: Mapped[str | None] = mapped_column(String(80))
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    variant_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    attachment_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    message_meta: Mapped[dict | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class AIChatAttachment(Base):
    """Authenticated image asset owned by one AI Chat user."""

    __tablename__ = "ai_chat_attachments"
    __table_args__ = (
        Index("ix_ai_chat_attachments_orphan", "message_id", "orphan_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False, index=True)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime)
    orphan_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
