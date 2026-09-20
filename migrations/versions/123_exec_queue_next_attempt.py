"""add execution queue retry backoff schedule

Revision ID: 123_exec_queue_next_attempt
Revises: 122_create_sf_data_records
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "123_exec_queue_next_attempt"
down_revision = "122_create_sf_data_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_queue",
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
    )
    op.execute("UPDATE execution_queue SET next_attempt_at = COALESCE(started_at, claimed_at, created_at, now())")
    op.alter_column("execution_queue", "next_attempt_at", nullable=False)
    op.create_index(
        "ix_exec_queue_pending_due",
        "execution_queue",
        ["status", "queue_name", "next_attempt_at", "priority", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_exec_queue_pending_due", table_name="execution_queue")
    op.drop_column("execution_queue", "next_attempt_at")
