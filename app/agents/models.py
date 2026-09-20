"""Business-level Agent blueprints managed by SkillForge."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class DepartmentAnalysisAgent(Base):
    """A managed analysis Agent contract, separate from runtime terminals."""

    __tablename__ = "department_analysis_agents"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="analysis_v1")
    description: Mapped[str | None] = mapped_column(Text)
    dimensions_json: Mapped[list | None] = mapped_column(JSONB)
    default_params_json: Mapped[dict | None] = mapped_column(JSONB)
    editor_user_ids_json: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    updated_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (
        UniqueConstraint("department", "name", name="uq_department_analysis_agents_dept_name"),
    )
