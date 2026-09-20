"""create ai_todos

Revision ID: 020
Revises: 019
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_todos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=50), nullable=False),
        sa.Column("assignee", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(length=50), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decision_channel", sa.String(length=20), nullable=True),
        sa.Column("dingtalk_msg_id", sa.String(length=100), nullable=True),
        sa.Column("dingtalk_msg_type", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["decision_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_todos_assignee_status", "ai_todos", ["assignee", "status", "created_at"], unique=False)
    op.create_index("ix_todos_request_id", "ai_todos", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_todos_request_id", table_name="ai_todos")
    op.drop_index("ix_todos_assignee_status", table_name="ai_todos")
    op.drop_table("ai_todos")
