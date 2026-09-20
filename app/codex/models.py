"""ORM models for Codex local SkillForge gateway."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import now_bjt
from app.database import Base


class CodexCliSession(Base):
    __tablename__ = "codex_cli_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    device_name: Mapped[str | None] = mapped_column(String(64))
    scopes_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    client_meta_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    permissions_rev_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_reason: Mapped[str | None] = mapped_column(String(120))


class CodexLoginIntent(Base):
    __tablename__ = "codex_login_intents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    nonce: Mapped[str | None] = mapped_column(String(160))
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(160), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(16), nullable=False, default="S256")
    device_name: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    auth_code_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class CodexDebugRun(Base):
    __tablename__ = "codex_debug_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cli_session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("codex_cli_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    run_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="local_debug")
    package_hash: Mapped[str | None] = mapped_column(String(80))
    manifest_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    requested_tools_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    output_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="preview_only")
    limits_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CodexRunToken(Base):
    __tablename__ = "codex_run_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    cli_session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("codex_cli_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    debug_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("codex_debug_runs.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scopes_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    shop_ids_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class CodexMcpCallAudit(Base):
    __tablename__ = "codex_mcp_call_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    proof_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    debug_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    server: Mapped[str] = mapped_column(String(80), nullable=False, default="skillforge")
    tool: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    data_scope: Mapped[str | None] = mapped_column(String(160), index=True)
    shop_id: Mapped[str | None] = mapped_column(String(120), index=True)
    run_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="local_debug")
    dry_run: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.true())
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    error_code: Mapped[str | None] = mapped_column(String(80))
    detail_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)


class CodexSkillSubmission(Base):
    __tablename__ = "codex_skill_submissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    package_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    base_commit: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    review_id: Mapped[int | None] = mapped_column(Integer, index=True)
    git_commit: Mapped[str | None] = mapped_column(String(80))
    checks_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    manifest_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, onupdate=now_bjt, nullable=False)


class CodexOutputPreview(Base):
    __tablename__ = "codex_output_previews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    cli_session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("codex_cli_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(30), nullable=False, default="json")
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class SfDataRecord(Base):
    __tablename__ = "sf_data_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(240))
    content_type: Mapped[str] = mapped_column(String(40), nullable=False, default="json", index=True)
    data_json: Mapped[dict | list | None] = mapped_column(JSONB)
    text_preview: Mapped[str | None] = mapped_column(Text)
    artifact_ref_json: Mapped[dict | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    schema_json: Mapped[dict | None] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=sa.text("0"))
    source: Mapped[str | None] = mapped_column(String(80), index=True)
    source_tool: Mapped[str | None] = mapped_column(String(160), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160))
    skill_id: Mapped[str | None] = mapped_column(String(100), index=True)
    run_id: Mapped[str | None] = mapped_column(String(80), index=True)
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    visibility: Mapped[str] = mapped_column(String(30), nullable=False, default="global", server_default="global", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt, nullable=False, index=True)

    __table_args__ = (
        sa.Index("ix_sf_data_records_namespace_created", "namespace", "created_at"),
        sa.Index("ix_sf_data_records_skill_created", "skill_id", "created_at"),
    )
