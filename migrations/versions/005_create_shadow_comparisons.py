"""create shadow_comparisons table

Revision ID: 005
Revises: 004
Create Date: 2026-04-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shadow_comparisons",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(50), nullable=False, index=True),
        sa.Column("run_id", sa.String(50), nullable=False),
        sa.Column("old_version", sa.String(20)),
        sa.Column("new_version", sa.String(20)),
        sa.Column("old_output", sa.dialects.postgresql.JSONB),
        sa.Column("new_output", sa.dialects.postgresql.JSONB),
        sa.Column("is_divergent", sa.Boolean, default=False),
        sa.Column("divergence_detail", sa.dialects.postgresql.JSONB),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("shadow_comparisons")
