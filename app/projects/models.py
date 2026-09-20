"""Project hosting ORM models.

Projects are platform-hosted frontends or external pages. They do not own
runtime containers; SkillForge records launches, capability calls and ingress
outputs so project data can enter Run Trace / Inbox / Learning Loop.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(42), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="external_web")
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="department")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    entry_url: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[str | None] = mapped_column(String(80), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.Index("ix_projects_type_updated", "type", "updated_at"),
        sa.Index("ix_projects_visibility_status_updated", "visibility", "status", "updated_at"),
        sa.Index("ix_projects_owner_updated", "owner_user_id", "updated_at"),
        sa.Index("ix_projects_department_updated", "department_id", "updated_at"),
    )


class ProjectVersion(Base):
    __tablename__ = "project_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(30), nullable=False, default="external_url")
    artifact_ref: Mapped[str | None] = mapped_column(Text)
    sf_package_hash: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "version", name="uq_project_versions_project_version"),
    )


class ProjectRun(Base):
    __tablename__ = "project_runs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[str | None] = mapped_column(String(80), index=True)
    execution_run_id: Mapped[str | None] = mapped_column(String(50), index=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    decision_log_id: Mapped[int | None] = mapped_column(Integer, index=True)
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="opening", index=True)
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    output_result: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    ai_summary: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    todo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capability_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.Index("ix_project_runs_project_latest", "project_id", "created_at"),
        sa.Index("ix_project_runs_status_updated", "status", "updated_at"),
        sa.Index("ix_project_runs_status_heartbeat", "status", "last_heartbeat_at"),
        sa.Index("ix_project_runs_department_updated", "department_id", "updated_at"),
        sa.UniqueConstraint("project_id", "user_id", "request_id", name="uq_project_runs_project_user_request"),
    )


class ProjectRunAsset(Base):
    __tablename__ = "project_run_assets"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_run_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("project_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department_id: Mapped[str | None] = mapped_column(String(50), index=True)
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="file", index=True)
    file_name: Mapped[str] = mapped_column(String(240), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_backend: Mapped[str] = mapped_column(String(30), nullable=False, default="local")
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.Index("ix_project_run_assets_run_created", "project_run_id", "created_at"),
        sa.Index("ix_project_run_assets_project_created", "project_id", "created_at"),
        sa.UniqueConstraint("project_run_id", "sha256", name="uq_project_run_assets_run_sha256"),
    )


class ProjectSdkToken(Base):
    __tablename__ = "project_sdk_tokens"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    scopes_json: Mapped[list | None] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(50), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.Index("ix_project_sdk_tokens_project_created", "project_id", "created_at"),
        sa.Index("ix_project_sdk_tokens_project_active", "project_id", "revoked_at", "expires_at"),
    )


class ProjectIngressEvent(Base):
    __tablename__ = "project_ingress_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_run_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("project_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, default="output", index=True)
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    output_result: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    reports: Mapped[list | None] = mapped_column(JSONB, default=list)
    todos: Mapped[list | None] = mapped_column(JSONB, default=list)
    proofs: Mapped[list | None] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)

    __table_args__ = (
        sa.Index("ix_project_ingress_run_created_id", "project_run_id", "created_at", "id"),
        sa.UniqueConstraint("project_run_id", "event_type", "request_id", name="uq_project_ingress_run_event_request"),
    )


class ProjectCapabilityCall(Base):
    __tablename__ = "project_capability_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_run_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("project_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    capability_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    capability_type: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    input_payload: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    input_summary: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    output_result: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    output_summary: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    cost_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        sa.Index("ix_project_capability_run_created_id", "project_run_id", "created_at", "id"),
        sa.UniqueConstraint("project_run_id", "request_id", name="uq_project_capability_run_request"),
    )
