"""add structured ai todo feedback payload

Revision ID: 121_ai_todo_feedback_payload
Revises: 120
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "121_ai_todo_feedback_payload"
down_revision = "120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_todos",
        sa.Column("feedback_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_todos", "feedback_payload")
