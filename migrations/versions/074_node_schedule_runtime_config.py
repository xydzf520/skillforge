"""node schedule runtime config

Revision ID: 074
Revises: 073
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "node_schedule_configs",
        sa.Column("runtime_backend", sa.String(30), nullable=True),
    )
    op.add_column(
        "node_schedule_configs",
        sa.Column("runtime_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade():
    op.drop_column("node_schedule_configs", "runtime_config")
    op.drop_column("node_schedule_configs", "runtime_backend")
