"""门户执行请求与个人 UI 偏好模型。"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class SkillSubmission(Base):
    __tablename__ = "skill_submissions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    requester_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    params: Mapped[dict | None] = mapped_column(JSONB)
    ui_pref_id: Mapped[str | None] = mapped_column(String(50), index=True)
    ui_pref_version: Mapped[int | None] = mapped_column(Integer)
    ui_surface: Mapped[str | None] = mapped_column(String(30))
    base_skill_commit: Mapped[str | None] = mapped_column(String(40))
    merged_ui_schema_hash: Mapped[str | None] = mapped_column(String(80))
    ui_snapshot_json: Mapped[dict | None] = mapped_column(JSONB)
    execution_id: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserSkillUIPreference(Base):
    __tablename__ = "user_skill_ui_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "skill_id",
            "surface",
            "version",
            name="uq_user_skill_ui_pref_version",
        ),
        sa.Index(
            "uq_user_skill_ui_pref_active_enabled",
            "user_id",
            "skill_id",
            "surface",
            unique=True,
            postgresql_where=sa.text("enabled = true"),
            sqlite_where=sa.text("enabled = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(30), nullable=False)
    base_skill_commit: Mapped[str | None] = mapped_column(String(40))
    overlay_schema_version: Mapped[str] = mapped_column(
        String(30),
        default="skill-ui/v1",
        nullable=False,
        server_default="skill-ui/v1",
    )
    overlay_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    generated_by: Mapped[str] = mapped_column(
        String(20),
        default="manual",
        nullable=False,
        server_default="manual",
    )
    prompt_summary: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        server_default=sa.true(),
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        server_default=sa.text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)
