"""openclaw_instances 增加 agent_type 字段

Revision ID: 031
Revises: 030
"""
from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"


def upgrade():
    op.add_column(
        "openclaw_instances",
        sa.Column("agent_type", sa.String(20), server_default="aiclaw", nullable=False),
    )


def downgrade():
    op.drop_column("openclaw_instances", "agent_type")
