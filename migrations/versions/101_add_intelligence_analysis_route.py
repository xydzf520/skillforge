"""add intelligence analysis route trace

Revision ID: 101
Revises: 100
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "101"
down_revision = "100"


def upgrade():
    op.add_column(
        "intelligence_analyze_runs",
        sa.Column("analysis_backend", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "intelligence_analyze_runs",
        sa.Column("analysis_agent_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "intelligence_analyze_runs",
        sa.Column(
            "analysis_delegate_route",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_intelligence_analyze_runs_agent",
        "intelligence_analyze_runs",
        ["analysis_backend", "analysis_agent_id", "created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_intelligence_analyze_runs_agent", table_name="intelligence_analyze_runs")
    op.drop_column("intelligence_analyze_runs", "analysis_delegate_route")
    op.drop_column("intelligence_analyze_runs", "analysis_agent_id")
    op.drop_column("intelligence_analyze_runs", "analysis_backend")
