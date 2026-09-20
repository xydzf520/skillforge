"""数据源 + 数据导入日志 + 数据访问授权 ORM 模型"""

from datetime import datetime

from sqlalchemy import Boolean, Computed, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.common.time_utils import now_bjt


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # csv_upload/api_pull/crawler
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)  # 字段映射/API地址/认证等
    schedule: Mapped[str | None] = mapped_column(String(50))  # Cron表达式
    stale_threshold_hours: Mapped[int] = mapped_column(Integer, default=24)
    quality_rules: Mapped[dict | None] = mapped_column(JSONB)
    related_skills: Mapped[list | None] = mapped_column(ARRAY(Text))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(50))
    # v2.7 大厅 v3：三级可发现性（与 Skill.visibility 对齐）
    # company   ：元数据全公司可发现（任意登录用户大厅可见）
    # department：仅本部门 + org_unit 树内可发现（默认）
    # private   ：仅 DataAccessGrant 名单 + owner 可发现
    visibility: Mapped[str] = mapped_column(String(20), default="department", index=True)
    description: Mapped[str | None] = mapped_column(Text)       # 大厅展示的一句话说明
    usage_hint: Mapped[str | None] = mapped_column(Text)        # 典型用法 / 适合场景
    owner_contact: Mapped[str | None] = mapped_column(String(50))  # 负责人 user_id（审批 / 联系）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class ConnectorApiKey(Base):
    """Chrome / Bridge connector API key metadata.

    New keys keep an encrypted copy for admin/operator copy flows, while
    verification still uses the SHA-256 hash. Older rows may not have
    encrypted_key populated.
    """

    __tablename__ = "connector_api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    encrypted_key: Mapped[str | None] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    key_last4: Mapped[str] = mapped_column(String(8), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(50), index=True)
    owner_user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    allowed_platforms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    allowed_shop_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_by: Mapped[str | None] = mapped_column(String(50))
    rotated_from_key_id: Mapped[str | None] = mapped_column(String(32), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_by: Mapped[str | None] = mapped_column(String(50))
    revoke_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class PlatformCookiePool(Base):
    """Owner-scoped platform cookie pool.

    New connector keys write here instead of mutating DataSource.config, so two
    owners can push cookies for the same platform/shop without overwriting each
    other.
    """

    __tablename__ = "platform_cookie_pool"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "platform",
            "shop_id",
            "owner_user_id",
            "connector_key_id",
            name="uq_platform_cookie_pool_source_platform_shop_owner_key",
        ),
        Index("ix_platform_cookie_pool_lookup", "source_id", "platform", "shop_id"),
        Index("ix_platform_cookie_pool_owner_active", "owner_user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    shop_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    account_login: Mapped[str | None] = mapped_column(String(150))
    owner_user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    connector_key_id: Mapped[str | None] = mapped_column(String(32), index=True)
    device_label: Mapped[str | None] = mapped_column(String(100))
    auth_source: Mapped[str] = mapped_column(String(40), nullable=False, default="connector_api_key")
    legacy_owner_user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    encrypted_cookies: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_cookie_details: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    capability_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    pushed_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime)
    disabled_by: Mapped[str | None] = mapped_column(String(50))
    disable_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class PlatformCookieAudit(Base):
    """Append-only audit trail for cookie pool writes and disabling."""

    __tablename__ = "platform_cookie_audit"
    __table_args__ = (
        Index("ix_platform_cookie_audit_context", "source_id", "platform", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cookie_pool_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    platform: Mapped[str | None] = mapped_column(String(40), index=True)
    shop_id: Mapped[str | None] = mapped_column(String(100), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    connector_key_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    auth_source: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(50))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)


class PlatformApiDriftAck(Base):
    """Persistent acknowledgement state for platform API drift alerts."""

    __tablename__ = "platform_api_drift_acks"

    alert_id: Mapped[str] = mapped_column(String(260), primary_key=True)
    acknowledged_by: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="acknowledged", index=True)
    review_task_id: Mapped[str | None] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class DataAccessGrant(Base):
    """数据访问授权模型。

    原始字段 grantee_user_id / grantee_org_unit_id 保留，
    grantee_type + grantee_id 为 PostgreSQL 计算列（GENERATED ALWAYS AS STORED），
    兼容 spec 中统一的 grantee_type + grantee_id 查询方式。
    """
    __tablename__ = "data_access_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    grantee_user_id: Mapped[str | None] = mapped_column(String(50))
    grantee_org_unit_id: Mapped[str | None] = mapped_column(String(50))
    permission: Mapped[str] = mapped_column(String(20), nullable=False, default="read")
    granted_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    # spec 兼容计算列（只读，由 PostgreSQL GENERATED ALWAYS AS STORED 计算）
    grantee_type: Mapped[str | None] = mapped_column(
        String(20),
        Computed(
            "CASE WHEN grantee_user_id IS NOT NULL THEN 'user' "
            "WHEN grantee_org_unit_id IS NOT NULL THEN 'org_unit' "
            "ELSE 'unknown' END",
            persisted=True,
        ),
    )
    grantee_id: Mapped[str | None] = mapped_column(
        String(100),
        Computed("COALESCE(grantee_user_id, grantee_org_unit_id)", persisted=True),
    )


class DataAccessRequest(Base):
    """数据访问申请单（v2.7 大厅 v3）。

    状态机：pending → approved / rejected / expired。

    申请流：用户在大厅点"申请访问"→ 写 pending → 钉钉推 owner_contact →
    owner/admin approve → 复用 create_grant 写 DataAccessGrant；reject → 仅写状态 + comment；
    14 天未决 → cron 置 expired。

    幂等：同 (source_id, requester_id, status=pending) 唯一。
    """

    __tablename__ = "data_access_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    requester_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_permission: Mapped[str] = mapped_column(String(20), default="read")

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(50))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_comment: Mapped[str | None] = mapped_column(Text)

    # 审批人可指定授权过期（不填 = 默认 90 天；None = 永久）
    approved_expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt)


class DataIngestionLog(Base):
    __tablename__ = "data_ingestion_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    ingestion_type: Mapped[str | None] = mapped_column(String(20))  # upload/api_pull/crawler
    uploaded_by: Mapped[str | None] = mapped_column(String(50))
    file_name: Mapped[str | None] = mapped_column(String(255))
    row_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing/success/failed/warning
    quality_report: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)
