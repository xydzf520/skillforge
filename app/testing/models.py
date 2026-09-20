"""Agent对话会话 ORM 模型"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class Conversation(Base):
    """Agent对话测试的会话记录"""
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    messages: Mapped[dict] = mapped_column(JSONB, default=list)  # [{role, content, timestamp}]
    model_id: Mapped[str | None] = mapped_column(String(50))
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # F1 上下文压缩字段（migration 015）
    compact_boundary_idx: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    compact_failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    usage_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)
