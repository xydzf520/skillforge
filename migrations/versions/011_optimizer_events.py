"""Optimizer 审计事件表

Revision ID: 011
Revises: 010
Create Date: 2026-04-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "optimizer_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(50), nullable=False),
        sa.Column("candidate_id", sa.String(50)),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("detail", sa.JSON()),
        sa.Column("user_id", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_optevt_session", "optimizer_events", ["session_id"])
    op.create_index("ix_optevt_type", "optimizer_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("optimizer_events")
