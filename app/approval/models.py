"""审批配置与实例模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class ApprovalRule(Base):
    __tablename__ = "approval_rules"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    business_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    condition_json: Mapped[dict | None] = mapped_column(JSONB)
    approval_chain: Mapped[list | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class ApprovalInstance(Base):
    __tablename__ = "approval_instances"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    rule_id: Mapped[str | None] = mapped_column(String(50), index=True)
    business_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    dingtalk_process_id: Mapped[str | None] = mapped_column(String(100))
    requester_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_reason: Mapped[str | None] = mapped_column(String(50))  # requester_disabled / user_request / manual / ...
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)


class ApprovalStep(Base):
    __tablename__ = "approval_steps"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_id: Mapped[str | None] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    comment: Mapped[str | None] = mapped_column(String(2000))
    acted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


class ResourceQuota(Base):
    __tablename__ = "resource_quotas"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    org_unit_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), default="monthly")
    quota_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    burst_limit: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)
