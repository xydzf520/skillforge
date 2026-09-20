"""create codex gateway tables

Revision ID: 095
Revises: 094
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "095"
down_revision = "094"


def upgrade():
    op.create_table(
        "codex_cli_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("device_name", sa.String(length=64), nullable=True),
        sa.Column("scopes_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("permissions_rev_snapshot", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=120), nullable=True),
    )
    op.create_index("ix_codex_cli_sessions_user_id", "codex_cli_sessions", ["user_id"])
    op.create_index("ix_codex_cli_sessions_token_hash", "codex_cli_sessions", ["token_hash"], unique=True)

    op.create_table(
        "codex_login_intents",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("state", sa.String(length=160), nullable=False),
        sa.Column("nonce", sa.String(length=160), nullable=True),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(length=160), nullable=False),
        sa.Column("code_challenge_method", sa.String(length=16), nullable=False, server_default="S256"),
        sa.Column("device_name", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=True),
        sa.Column("auth_code_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_codex_login_intents_state", "codex_login_intents", ["state"])
    op.create_index("ix_codex_login_intents_user_id", "codex_login_intents", ["user_id"])
    op.create_index("ix_codex_login_intents_auth_code_hash", "codex_login_intents", ["auth_code_hash"], unique=True)

    op.create_table(
        "codex_debug_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("cli_session_id", sa.String(length=64), sa.ForeignKey("codex_cli_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=100), nullable=False),
        sa.Column("run_mode", sa.String(length=30), nullable=False, server_default="local_debug"),
        sa.Column("package_hash", sa.String(length=80), nullable=True),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("input_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requested_tools_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("output_mode", sa.String(length=30), nullable=False, server_default="preview_only"),
        sa.Column("limits_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_codex_debug_runs_cli_session_id", "codex_debug_runs", ["cli_session_id"])
    op.create_index("ix_codex_debug_runs_user_id", "codex_debug_runs", ["user_id"])
    op.create_index("ix_codex_debug_runs_skill_id", "codex_debug_runs", ["skill_id"])
    op.create_index("ix_codex_debug_runs_status", "codex_debug_runs", ["status"])

    op.create_table(
        "codex_run_tokens",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("cli_session_id", sa.String(length=64), sa.ForeignKey("codex_cli_sessions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("debug_run_id", sa.String(length=64), sa.ForeignKey("codex_debug_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=100), nullable=False),
        sa.Column("scopes_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("shop_ids_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_codex_run_tokens_token_hash", "codex_run_tokens", ["token_hash"], unique=True)
    op.create_index("ix_codex_run_tokens_cli_session_id", "codex_run_tokens", ["cli_session_id"])
    op.create_index("ix_codex_run_tokens_debug_run_id", "codex_run_tokens", ["debug_run_id"])
    op.create_index("ix_codex_run_tokens_user_id", "codex_run_tokens", ["user_id"])
    op.create_index("ix_codex_run_tokens_skill_id", "codex_run_tokens", ["skill_id"])

    op.create_table(
        "codex_mcp_call_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("proof_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("debug_run_id", sa.String(length=64), nullable=True),
        sa.Column("server", sa.String(length=80), nullable=False, server_default="skillforge"),
        sa.Column("tool", sa.String(length=120), nullable=False),
        sa.Column("data_scope", sa.String(length=160), nullable=True),
        sa.Column("shop_id", sa.String(length=120), nullable=True),
        sa.Column("run_mode", sa.String(length=30), nullable=False, server_default="local_debug"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("detail_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_codex_mcp_call_audit_request_id", "codex_mcp_call_audit", ["request_id"])
    op.create_index("ix_codex_mcp_call_audit_proof_id", "codex_mcp_call_audit", ["proof_id"], unique=True)
    op.create_index("ix_codex_mcp_call_audit_user_id", "codex_mcp_call_audit", ["user_id"])
    op.create_index("ix_codex_mcp_call_audit_skill_id", "codex_mcp_call_audit", ["skill_id"])
    op.create_index("ix_codex_mcp_call_audit_debug_run_id", "codex_mcp_call_audit", ["debug_run_id"])
    op.create_index("ix_codex_mcp_call_audit_tool", "codex_mcp_call_audit", ["tool"])
    op.create_index("ix_codex_mcp_call_audit_data_scope", "codex_mcp_call_audit", ["data_scope"])
    op.create_index("ix_codex_mcp_call_audit_shop_id", "codex_mcp_call_audit", ["shop_id"])

    op.create_table(
        "codex_skill_submissions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=100), nullable=False),
        sa.Column("package_hash", sa.String(length=80), nullable=False),
        sa.Column("base_commit", sa.String(length=80), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("git_commit", sa.String(length=80), nullable=True),
        sa.Column("checks_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_codex_skill_submissions_user_id", "codex_skill_submissions", ["user_id"])
    op.create_index("ix_codex_skill_submissions_skill_id", "codex_skill_submissions", ["skill_id"])
    op.create_index("ix_codex_skill_submissions_status", "codex_skill_submissions", ["status"])
    op.create_index("ix_codex_skill_submissions_review_id", "codex_skill_submissions", ["review_id"])


def downgrade():
    op.drop_table("codex_skill_submissions")
    op.drop_table("codex_mcp_call_audit")
    op.drop_table("codex_run_tokens")
    op.drop_table("codex_debug_runs")
    op.drop_table("codex_login_intents")
    op.drop_table("codex_cli_sessions")
