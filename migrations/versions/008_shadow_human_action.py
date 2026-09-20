"""add human_action fields to shadow_comparisons

Revision ID: 008
Revises: 007
Create Date: 2026-04-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shadow_comparisons", sa.Column("human_action", sa.String(20), nullable=True))
    op.add_column("shadow_comparisons", sa.Column("human_recorded_by", sa.String(50), nullable=True))
    op.add_column("shadow_comparisons", sa.Column("human_recorded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("shadow_comparisons", "human_recorded_at")
    op.drop_column("shadow_comparisons", "human_recorded_by")
    op.drop_column("shadow_comparisons", "human_action")
