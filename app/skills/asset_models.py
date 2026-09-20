"""Skill 资产元模型 ORM：模板 / 实例 / 发布记录 / 血缘关系"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class SkillTemplate(Base):
    """模板定义 — 从 _templates/ 目录升级为 DB 管理"""
    __tablename__ = "skill_templates"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))
    owner_id: Mapped[str | None] = mapped_column(String(50))
    schema_json: Mapped[dict | None] = mapped_column(JSONB)          # 参数 schema 模板
    ui_schema_json: Mapped[dict | None] = mapped_column(JSONB)       # UI schema 模板
    runtime_profile: Mapped[dict | None] = mapped_column(JSONB)      # 运行时配置（超时、并发等）
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str | None] = mapped_column(String(20))          # 当前发布版本 e.g. "v1.2"
    source_skill_id: Mapped[str | None] = mapped_column(String(100)) # 从哪个 Skill 提取的模板
    tags: Mapped[list | None] = mapped_column(ARRAY(Text))
    fork_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class SkillInstance(Base):
    """Skill 实例 — 绑定模板 + 自定义配置"""
    __tablename__ = "skill_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False,
    )
    template_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("skill_templates.id"),
    )
    custom_config: Mapped[dict | None] = mapped_column(JSONB)       # 实例级覆盖配置
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/archived/suspended
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)

    __table_args__ = (UniqueConstraint("skill_id", name="uq_skill_instances_skill_id"),)


class SkillRelease(Base):
    """发布记录 — 每次审核通过 = 一条 release"""
    __tablename__ = "skill_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False,
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    git_ref: Mapped[str | None] = mapped_column(String(100))        # git tag or commit
    artifact_digest: Mapped[str | None] = mapped_column(String(64)) # SKILL.md 的 sha256
    review_id: Mapped[int | None] = mapped_column(Integer)           # 关联的审核 ID
    release_notes: Mapped[str | None] = mapped_column(Text)
    released_by: Mapped[str | None] = mapped_column(String(50))
    released_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class SkillLineage(Base):
    """血缘关系 — Fork/Derive/Upgrade 追踪"""
    __tablename__ = "skill_lineage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # 源 skill 或 template
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # 目标 skill
    relation_type: Mapped[str] = mapped_column(String(20), nullable=False)  # fork/derive/upgrade
    source_version: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
