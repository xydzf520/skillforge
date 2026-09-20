"""skill draft lock state + fatigue mode (v7 C2/C3)

Revision ID: 026
Revises: 025
Create Date: 2026-04-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skill_drafts",
        sa.Column("lock_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "skill_drafts",
        sa.Column("confirmation_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "skill_drafts",
        sa.Column("trust_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_skill_drafts_branch_user",
        "skill_drafts",
        ["skill_id", "branch", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_drafts_branch_user", table_name="skill_drafts")
    op.drop_column("skill_drafts", "trust_mode")
    op.drop_column("skill_drafts", "confirmation_count")
    op.drop_column("skill_drafts", "lock_state")
