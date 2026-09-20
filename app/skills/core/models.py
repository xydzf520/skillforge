"""Skill 元数据 + 编辑锁 ORM 模型"""

from datetime import datetime, date

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)

    # 从 SKILL.md frontmatter 同步的字段
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    role: Mapped[str | None] = mapped_column(String(50))
    trigger_type: Mapped[str | None] = mapped_column(String(20))  # cron/event/manual
    trigger_expression: Mapped[str | None] = mapped_column(String(100))
    risk_level: Mapped[str | None] = mapped_column(String(5))  # R1/R2/R3/R4

    # SkillForge 独有的管理字段
    owner: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/shadow/active/deprecated
    org_unit_id: Mapped[str | None] = mapped_column(String(50), index=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50), index=True)
    icon: Mapped[str | None] = mapped_column(String(50))
    param_ui_schema: Mapped[dict | None] = mapped_column(JSONB)
    result_ui_schema: Mapped[dict | None] = mapped_column(JSONB)
    # 访问控制（谁能看到这个 Skill）：private / department / company
    # 与 market_status 是正交维度 —— 此字段决定"可见性"，market_status 决定"是否上架市场"
    # 例：visibility=company + market_status=private 表示公司内可见但未上架
    # 消费者：hall_service（跨部门浏览）、access.py（权限判断）、portal.service（门户筛选）
    visibility: Mapped[str] = mapped_column(String(20), default="department", index=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    success_rate: Mapped[float | None] = mapped_column(Float)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    approval_level: Mapped[int] = mapped_column(Integer, default=0)  # 0=L0 / 1=L1 / 2=L2 / 3=L3
    target_users: Mapped[list | None] = mapped_column(ARRAY(Text))
    approver: Mapped[str | None] = mapped_column(String(50))
    current_version: Mapped[str | None] = mapped_column(String(20))
    git_commit: Mapped[str | None] = mapped_column(String(40))
    playbook_ids: Mapped[list | None] = mapped_column(ARRAY(Text))
    shadow_start_date: Mapped[date | None] = mapped_column(Date)

    # 市场上架状态：private / company_public / marketplace_listed / marketplace_certified
    # 与 visibility 正交 —— 决定是否出现在企业市场 / 跨公司市场；
    # 消费者仅限 portal.market_service，不做访问控制用
    market_status: Mapped[str] = mapped_column(String(30), default="private")
    default_baseline_minutes: Mapped[float | None] = mapped_column(Float)

    # Fork 关系追踪
    forked_from: Mapped[str | None] = mapped_column(String(100), index=True)  # 来源 template_id 或 skill_id
    fork_type: Mapped[str | None] = mapped_column(String(20))  # template / skill
    parent_version: Mapped[str | None] = mapped_column(String(40))  # git commit hash

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class UserSkillPin(Base):
    """用户 Skill 置顶"""
    __tablename__ = "user_skill_pins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)

    __table_args__ = (UniqueConstraint('user_id', 'skill_id', name='uq_user_skill_pin'),)


class SkillLock(Base):
    """Skill编辑锁，防止并发编辑冲突"""
    __tablename__ = "skill_locks"

    skill_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
