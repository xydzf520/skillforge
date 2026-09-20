"""组织架构 ORM 模型：OrgUnit（部门/团队）+ UserOrgMembership（用户归属）"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class OrgUnit(Base):
    """组织单元（部门/团队），支持多级嵌套"""
    __tablename__ = "org_units"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="department")
    parent_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("org_units.id"), nullable=True, index=True
    )
    # 物化路径：/root-id/parent-id/self-id，用于快速子树查询
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    dingtalk_dept_id: Mapped[str | None] = mapped_column(String(50))
    manager_user_id: Mapped[str | None] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class UserOrgMembership(Base):
    """用户与组织单元的归属关系（复合主键）"""
    __tablename__ = "user_org_memberships"

    user_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.id"), primary_key=True
    )
    org_unit_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("org_units.id"), primary_key=True, index=True
    )
    membership_type: Mapped[str] = mapped_column(String(20), default="primary")
    is_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
