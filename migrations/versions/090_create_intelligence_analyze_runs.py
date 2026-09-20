"""create intelligence analyze runs

Revision ID: 090
Revises: 089
"""

from alembic import op
import sqlalchemy as sa

revision = "090"
down_revision = "089"


def upgrade():
    op.create_table(
        "intelligence_analyze_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("cache_id", sa.BigInteger(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cache_hit_of_run_id", sa.String(length=100), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=False),
        sa.Column("skill_git_commit_full", sa.String(length=80), nullable=False),
        sa.Column("prompt_git_ref", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=True),
        sa.Column("instance_id", sa.String(length=100), nullable=True),
        sa.Column("department", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("data_health_ratio", sa.Numeric(5, 4), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("degraded_reason", sa.String(length=50), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("llm_output_hash", sa.String(length=64), nullable=True),
        sa.Column("evidence_passed", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(
            ["cache_id"],
            ["intelligence_analyze_cache.id"],
            name="fk_intelligence_analyze_runs_cache",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_intelligence_analyze_runs_skill_run",
        "intelligence_analyze_runs",
        ["skill_id", "run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_intelligence_analyze_runs_cache_id",
        "intelligence_analyze_runs",
        ["cache_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_intelligence_analyze_runs_cache_id",
        table_name="intelligence_analyze_runs",
    )
    op.drop_index(
        "ix_intelligence_analyze_runs_skill_run",
        table_name="intelligence_analyze_runs",
    )
    op.drop_table("intelligence_analyze_runs")
