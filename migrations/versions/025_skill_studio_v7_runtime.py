"""skill studio v7 runtime tables

Revision ID: 025
Revises: 024
Create Date: 2026-04-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_drafts",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("skill_id", sa.String(length=255), nullable=True),
        sa.Column("branch", sa.String(length=50), nullable=False, server_default="main"),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("source_message", sa.Text(), nullable=True),
        sa.Column("intent_md", sa.Text(), nullable=True),
        sa.Column("skill_md", sa.Text(), nullable=True),
        sa.Column("policy_yaml", sa.Text(), nullable=True),
        sa.Column("contract_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_state_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_skill_drafts_skill_id", "skill_drafts", ["skill_id"])
    op.create_index("ix_skill_drafts_user_id", "skill_drafts", ["user_id"])

    op.create_table(
        "skill_runs",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("skill_id", sa.String(length=255), nullable=True),
        sa.Column("draft_id", sa.String(length=50), sa.ForeignKey("skill_drafts.id"), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="save"),
        sa.Column("contract_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("preview_cache_key", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
    )
    op.create_index("ix_skill_runs_skill_id", "skill_runs", ["skill_id"])
    op.create_index("ix_skill_runs_draft_id", "skill_runs", ["draft_id"])
    op.create_index("ix_skill_runs_status", "skill_runs", ["status"])
    op.create_index("ix_skill_runs_preview_cache_key", "skill_runs", ["preview_cache_key"])

    op.create_table(
        "skill_previews",
        sa.Column("cache_key", sa.String(length=64), primary_key=True),
        sa.Column("skill_id", sa.String(length=255), nullable=True),
        sa.Column("draft_id", sa.String(length=50), sa.ForeignKey("skill_drafts.id"), nullable=True),
        sa.Column("contract_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("adapter_name", sa.String(length=50), nullable=True),
        sa.Column("rendered_output", sa.Text(), nullable=True),
        sa.Column("card_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_skill_previews_skill_id", "skill_previews", ["skill_id"])
    op.create_index("ix_skill_previews_draft_id", "skill_previews", ["draft_id"])

    op.create_table(
        "skill_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(length=255), nullable=True),
        sa.Column("draft_id", sa.String(length=50), sa.ForeignKey("skill_drafts.id"), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("checkpoint", sa.String(length=50), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_skill_reviews_skill_id", "skill_reviews", ["skill_id"])
    op.create_index("ix_skill_reviews_draft_id", "skill_reviews", ["draft_id"])
    op.create_index("ix_skill_reviews_user_id", "skill_reviews", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_reviews_user_id", table_name="skill_reviews")
    op.drop_index("ix_skill_reviews_draft_id", table_name="skill_reviews")
    op.drop_index("ix_skill_reviews_skill_id", table_name="skill_reviews")
    op.drop_table("skill_reviews")

    op.drop_index("ix_skill_previews_draft_id", table_name="skill_previews")
    op.drop_index("ix_skill_previews_skill_id", table_name="skill_previews")
    op.drop_table("skill_previews")

    op.drop_index("ix_skill_runs_preview_cache_key", table_name="skill_runs")
    op.drop_index("ix_skill_runs_status", table_name="skill_runs")
    op.drop_index("ix_skill_runs_draft_id", table_name="skill_runs")
    op.drop_index("ix_skill_runs_skill_id", table_name="skill_runs")
    op.drop_table("skill_runs")

    op.drop_index("ix_skill_drafts_user_id", table_name="skill_drafts")
    op.drop_index("ix_skill_drafts_skill_id", table_name="skill_drafts")
    op.drop_table("skill_drafts")
