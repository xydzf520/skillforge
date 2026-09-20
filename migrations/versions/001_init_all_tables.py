"""init all tables

Revision ID: 001
Revises: None
Create Date: 2026-04-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Public clean-install baseline: this table was previously provisioned manually.
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", postgresql.JSONB),
        sa.Column("updated_by", sa.String(50)),
        sa.Column("updated_at", sa.DateTime),
    )
    # ===== users =====
    op.create_table(
        "users",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(128)),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("can_view_all", sa.Boolean, default=False),
        sa.Column("department", sa.String(50)),
        sa.Column("dingtalk_user_id", sa.String(100)),
        sa.Column("dingtalk_union_id", sa.String(100)),
        sa.Column("avatar_url", sa.String(500)),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("must_change_password", sa.Boolean, default=True),
        sa.Column("last_login_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== login_log =====
    op.create_table(
        "login_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(50)),
        sa.Column("login_method", sa.String(20)),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.Text),
        sa.Column("success", sa.Boolean),
        sa.Column("failure_reason", sa.String(100)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== audit_log =====
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(50)),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(30)),
        sa.Column("target_id", sa.String(50)),
        sa.Column("detail", postgresql.JSONB),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_audit_user", "audit_log", ["user_id", "created_at"])
    op.create_index("idx_audit_action", "audit_log", ["action", "created_at"])

    # ===== skills =====
    op.create_table(
        "skills",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("department", sa.String(50), nullable=False),
        sa.Column("role", sa.String(50)),
        sa.Column("trigger_type", sa.String(20)),
        sa.Column("trigger_expression", sa.String(100)),
        sa.Column("risk_level", sa.String(5)),
        sa.Column("owner", sa.String(50)),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("approval_level", sa.Integer, server_default="0"),
        sa.Column("target_users", postgresql.ARRAY(sa.Text)),
        sa.Column("approver", sa.String(50)),
        sa.Column("current_version", sa.String(20)),
        sa.Column("git_commit", sa.String(40)),
        sa.Column("playbook_ids", postgresql.ARRAY(sa.Text)),
        sa.Column("shadow_start_date", sa.Date),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_skills_dept", "skills", ["department"])
    op.create_index("idx_skills_status", "skills", ["status"])

    # ===== skill_locks =====
    op.create_table(
        "skill_locks",
        sa.Column("skill_id", sa.String(50), primary_key=True),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("locked_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== reviews =====
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(50), nullable=False),
        sa.Column("submitter", sa.String(50), nullable=False),
        sa.Column("reviewer", sa.String(50)),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("diff_summary", sa.Text),
        sa.Column("diff_content", postgresql.JSONB),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("git_commit_before", sa.String(40)),
        sa.Column("git_commit_after", sa.String(40)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime),
        sa.Column("dingtalk_msg_id", sa.String(100)),
    )
    op.create_index("idx_reviews_status", "reviews", ["status"])

    # ===== review_comments =====
    op.create_table(
        "review_comments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer, nullable=False),
        sa.Column("author", sa.String(50)),
        sa.Column("content", sa.Text),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== execution_runs =====
    op.create_table(
        "execution_runs",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("playbook_id", sa.String(50)),
        sa.Column("trigger_type", sa.String(20)),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("status", sa.String(20), server_default="running"),
        sa.Column("total_steps", sa.Integer),
        sa.Column("completed_steps", sa.Integer, server_default="0"),
    )
    op.create_index("idx_runs_playbook", "execution_runs", ["playbook_id", "started_at"])

    # ===== execution_steps =====
    op.create_table(
        "execution_steps",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(50), nullable=False),
        sa.Column("skill_id", sa.String(50), nullable=False),
        sa.Column("step_order", sa.Integer),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("input_data", postgresql.JSONB),
        sa.Column("output_data", postgresql.JSONB),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("error_message", sa.Text),
    )

    # ===== decision_log =====
    op.create_table(
        "decision_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(50)),
        sa.Column("skill_id", sa.String(50), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB),
        sa.Column("output_result", postgresql.JSONB),
        sa.Column("suggested_action", postgresql.JSONB),
        sa.Column("approval_level", sa.Integer),
        sa.Column("approval_status", sa.String(20)),
        sa.Column("approver", sa.String(50)),
        sa.Column("target_user", sa.String(50)),
        sa.Column("user_action", sa.String(20)),
        sa.Column("user_feedback", sa.Text),
        sa.Column("business_impact", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("dingtalk_msg_id", sa.String(100)),
        sa.Column("model_id", sa.String(50)),
        sa.Column("token_count", sa.Integer),
        sa.Column("is_sandbox", sa.Boolean, server_default="false"),
    )
    op.create_index("idx_decision_skill", "decision_log", ["skill_id", "created_at"])
    op.create_index("idx_decision_user", "decision_log", ["target_user", "created_at"])

    # ===== openclaw_instances =====
    op.create_table(
        "openclaw_instances",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("department", sa.String(50)),
        sa.Column("gateway_url", sa.String(200), nullable=False),
        sa.Column("reload_hook_url", sa.String(200), nullable=False),
        sa.Column("reload_token", sa.String(200), nullable=False),
        sa.Column("network_zone", sa.String(20), server_default="internal"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("last_heartbeat", sa.DateTime),
        sa.Column("last_sync_at", sa.DateTime),
        sa.Column("last_sync_ok", sa.Boolean),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== data_sources =====
    op.create_table(
        "data_sources",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("department", sa.String(50), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("schedule", sa.String(50)),
        sa.Column("stale_threshold_hours", sa.Integer, server_default="24"),
        sa.Column("quality_rules", postgresql.JSONB),
        sa.Column("related_skills", postgresql.ARRAY(sa.Text)),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== data_ingestion_log =====
    op.create_table(
        "data_ingestion_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("ingestion_type", sa.String(20)),
        sa.Column("uploaded_by", sa.String(50)),
        sa.Column("file_name", sa.String(255)),
        sa.Column("row_count", sa.Integer),
        sa.Column("status", sa.String(20), server_default="processing"),
        sa.Column("quality_report", postgresql.JSONB),
        sa.Column("error_message", sa.Text),
        sa.Column("storage_path", sa.String(500)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== dingtalk_outbox =====
    op.create_table(
        "dingtalk_outbox",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("message_type", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer, server_default="5"),
        sa.Column("recipient_user_id", sa.String(100)),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("attempts", sa.Integer, server_default="0"),
        sa.Column("max_attempts", sa.Integer, server_default="3"),
        sa.Column("next_retry_at", sa.DateTime),
        sa.Column("error_message", sa.Text),
        sa.Column("related_type", sa.String(30)),
        sa.Column("related_id", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime),
    )
    op.create_index("idx_outbox_status", "dingtalk_outbox", ["status", "priority", "created_at"])

    # ===== conversations =====
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("skill_id", sa.String(50), nullable=False),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("messages", postgresql.JSONB, server_default="[]"),
        sa.Column("model_id", sa.String(50)),
        sa.Column("total_tokens", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ===== compliance_rules =====
    op.create_table(
        "compliance_rules",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("surface", sa.String(20), nullable=False),
        sa.Column("category_scope", sa.String(50)),
        sa.Column("trigger_type", sa.String(20), nullable=False),
        sa.Column("pattern_value", sa.Text, nullable=False),
        sa.Column("severity", sa.String(5), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("rewrite_suggestion", sa.Text),
        sa.Column("required_evidence", sa.String(50)),
        sa.Column("effective_from", sa.Date),
        sa.Column("effective_to", sa.Date),
        sa.Column("source_url", sa.Text),
        sa.Column("owner", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_rules_platform", "compliance_rules", ["platform", "surface"])


def downgrade() -> None:
    tables = [
        "system_config", "compliance_rules", "conversations", "dingtalk_outbox",
        "data_ingestion_log", "data_sources", "openclaw_instances",
        "decision_log", "execution_steps", "execution_runs",
        "review_comments", "reviews", "skill_locks", "skills",
        "audit_log", "login_log", "users",
    ]
    for t in tables:
        op.drop_table(t)
