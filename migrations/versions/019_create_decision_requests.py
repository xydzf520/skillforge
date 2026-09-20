"""create decision_requests

Revision ID: 019
Revises: 018
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_requests",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("run_id", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decision_mode", sa.String(length=20), server_default="any_of", nullable=False),
        sa.Column("aggregate_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("aggregate_decision", sa.String(length=20), nullable=True),
        sa.Column("sla_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dr_skill_status", "decision_requests", ["skill_id", "aggregate_status"], unique=False)
    op.create_index("ix_dr_source", "decision_requests", ["source_type", "source_id"], unique=False)
    op.create_index("ix_dr_created_at", "decision_requests", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_dr_created_at", table_name="decision_requests")
    op.drop_index("ix_dr_source", table_name="decision_requests")
    op.drop_index("ix_dr_skill_status", table_name="decision_requests")
    op.drop_table("decision_requests")
