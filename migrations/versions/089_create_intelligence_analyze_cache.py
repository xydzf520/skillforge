"""create intelligence analyze cache

Revision ID: 089
Revises: 088
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "089"
down_revision = "088"


def upgrade():
    op.create_table(
        "intelligence_analyze_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("evidence_passed", sa.Boolean(), nullable=True),
        sa.Column("first_seen_run_id", sa.String(length=100), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_hit_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "cache_key",
            "model",
            "prompt_hash",
            "context_hash",
            name="uq_intelligence_analyze_cache",
        ),
    )
    op.create_index(
        "ix_intelligence_analyze_cache_last_hit",
        "intelligence_analyze_cache",
        ["last_hit_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_intelligence_analyze_cache_last_hit",
        table_name="intelligence_analyze_cache",
    )
    op.drop_table("intelligence_analyze_cache")
