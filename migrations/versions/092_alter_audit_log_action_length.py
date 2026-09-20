"""alter audit log action length

Revision ID: 092
Revises: 091
"""

from alembic import op
import sqlalchemy as sa


revision = "092"
down_revision = "091"


def upgrade():
    op.alter_column(
        "audit_log",
        "action",
        existing_type=sa.String(50),
        type_=sa.String(160),
        existing_nullable=False,
    )


def downgrade():
    op.alter_column(
        "audit_log",
        "action",
        existing_type=sa.String(160),
        type_=sa.String(50),
        existing_nullable=False,
    )
