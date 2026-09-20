"""create workbench tables

Revision ID: 012
Revises: 011
Create Date: 2026-04-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_workbench_sessions",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="pro"),
        sa.Column("current_module", sa.String(length=30)),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("context_snapshot", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_wbskill_skill", "skill_workbench_sessions", ["skill_id"])
    op.create_index("ix_wbskill_user", "skill_workbench_sessions", ["user_id"])
    op.create_index("ix_wbskill_status", "skill_workbench_sessions", ["status"])

    op.create_table(
        "skill_workbench_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=50), sa.ForeignKey("skill_workbench_sessions.id"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_wbmsg_session", "skill_workbench_messages", ["session_id"])

    op.create_table(
        "skill_workbench_patches",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("session_id", sa.String(length=50), sa.ForeignKey("skill_workbench_sessions.id"), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("target_module", sa.String(length=30), nullable=False),
        sa.Column("intent", sa.String(length=50)),
        sa.Column("summary", sa.Text()),
        sa.Column("patch_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diff_preview_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("applied_at", sa.DateTime()),
    )
    op.create_index("ix_wbpatch_session", "skill_workbench_patches", ["session_id"])
    op.create_index("ix_wbpatch_skill", "skill_workbench_patches", ["skill_id"])
    op.create_index("ix_wbpatch_status", "skill_workbench_patches", ["status"])

    op.create_table(
        "skill_workbench_validation_runs",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("patch_id", sa.String(length=50), sa.ForeignKey("skill_workbench_patches.id"), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("structural_report", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("sample_case_report", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("replay_report", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("impact_summary", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("can_apply", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_wbval_patch", "skill_workbench_validation_runs", ["patch_id"])
    op.create_index("ix_wbval_skill", "skill_workbench_validation_runs", ["skill_id"])

    op.create_table(
        "skill_workbench_references",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patch_id", sa.String(length=50), sa.ForeignKey("skill_workbench_patches.id"), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("source_module", sa.String(length=30)),
        sa.Column("reference_mode", sa.String(length=30)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_wbref_patch", "skill_workbench_references", ["patch_id"])

    op.create_table(
        "skill_workbench_ai_runs",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("session_id", sa.String(length=50), sa.ForeignKey("skill_workbench_sessions.id"), nullable=False),
        sa.Column("patch_id", sa.String(length=50), sa.ForeignKey("skill_workbench_patches.id")),
        sa.Column("run_type", sa.String(length=30), nullable=False),
        sa.Column("model_id", sa.String(length=100)),
        sa.Column("prompt_key", sa.String(length=100)),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_wbai_session", "skill_workbench_ai_runs", ["session_id"])
    op.create_index("ix_wbai_patch", "skill_workbench_ai_runs", ["patch_id"])


def downgrade() -> None:
    op.drop_table("skill_workbench_ai_runs")
    op.drop_table("skill_workbench_references")
    op.drop_table("skill_workbench_validation_runs")
    op.drop_table("skill_workbench_patches")
    op.drop_table("skill_workbench_messages")
    op.drop_table("skill_workbench_sessions")
