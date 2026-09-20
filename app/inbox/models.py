"""收件中心用户偏好 ORM 模型（GAP-5/6）。

服务端记录每个用户查看过报告列表的时间戳，取代 localStorage `sf-inbox-reports-last-seen`，
以便换浏览器 / 换设备仍然保持一致的未读计数。

对应 migration：`migrations/versions/059_user_inbox_preferences.py`。
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class UserInboxPreference(Base):
    __tablename__ = "user_inbox_preferences"

    user_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    # 最近一次"查看报告列表"的时间，用于算未读红点
    reports_last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 预留：将来可能引入"查看待办列表"的已读时间
    todos_last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_bjt,
        onupdate=now_bjt,
    )


class InboxReportCard(Base):
    """Flattened report-card projection for inbox list queries."""

    __tablename__ = "inbox_report_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_log_id: Mapped[int] = mapped_column(Integer, nullable=False)
    report_index: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(50))
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    channel: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[list | None] = mapped_column(JSONB)
    tags: Mapped[list | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_bjt,
        onupdate=now_bjt,
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("decision_log_id", "report_index", name="uq_inbox_report_cards_log_index"),
        sa.Index("ix_inbox_report_cards_created", "created_at"),
        sa.Index("ix_inbox_report_cards_skill_created", "skill_id", "created_at"),
        sa.Index("ix_inbox_report_cards_channel_created", "channel", "created_at"),
    )
