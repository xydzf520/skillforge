"""add line-level comment fields to review_comments

Revision ID: 004
Revises: 003
Create Date: 2026-04-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("review_comments", sa.Column("file_path", sa.String(255), nullable=True))
    op.add_column("review_comments", sa.Column("line_number", sa.Integer, nullable=True))
    op.add_column("review_comments", sa.Column("side", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("review_comments", "side")
    op.drop_column("review_comments", "line_number")
    op.drop_column("review_comments", "file_path")
