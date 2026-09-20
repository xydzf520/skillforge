"""用户与登录日志 ORM 模型"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # v2 角色体系（docs/spec/role-matrix-v2.md §4.1）：
    #   system_admin / dept_admin / aibp / observer
    # 旧值（admin/ai_engineer/biz_owner/operator/director）通过 app/users/role_matrix.py 的
    # ROLE_ALIASES 做兼容映射，数据库允许渐进式迁移。
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    can_view_all: Mapped[bool] = mapped_column(Boolean, default=False)
    # v2 用户状态机（docs/spec/role-matrix-v2.md §4.1）：
    #   pending  — 钉钉首登自动创建，需 admin 审批才能使用业务功能
    #   active   — 正常启用
    #   disabled — 禁用（被管理员禁用/离职）
    # 与 is_active 的兼容关系：state==disabled 一定对应 is_active=false；
    # is_active 保留作兜底，未来可以淘汰。
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    department: Mapped[str | None] = mapped_column(String(50))
    dingtalk_user_id: Mapped[str | None] = mapped_column(String(100))
    dingtalk_union_id: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # v2 权限版本号（docs/spec/role-matrix-v2.md §10）：
    # 每次 role/state/can_view_all/membership 变更时 +1，session token 携带快照，
    # 比对不一致即视为过期，实现“改完立即登出”效果。
    permissions_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class LoginLog(Base):
    __tablename__ = "login_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(50))
    login_method: Mapped[str | None] = mapped_column(String(20))  # password/dingtalk_scan
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool | None] = mapped_column(Boolean)
    failure_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
