"""bridge capabilities json

Revision ID: 072
Revises: 071
"""

from alembic import op
import sqlalchemy as sa


revision = "072"
down_revision = "071"


def upgrade():
    op.add_column(
        "openclaw_instances",
        sa.Column("bridge_capabilities_json", sa.Text, nullable=True),
    )


def downgrade():
    op.drop_column("openclaw_instances", "bridge_capabilities_json")