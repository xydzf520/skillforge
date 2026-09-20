"""widen execution trigger type

Revision ID: 075
Revises: 074
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "execution_runs",
        "trigger_type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=80),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "execution_runs",
        "trigger_type",
        existing_type=sa.String(length=80),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
