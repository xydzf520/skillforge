"""openclaw platform default flag

Revision ID: 070
Revises: 069
"""

from alembic import op
import sqlalchemy as sa


revision = "070"
down_revision = "069"


def upgrade():
    op.add_column(
        "openclaw_instances",
        sa.Column(
            "is_platform_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_openclaw_instances_platform_default",
        "openclaw_instances",
        ["is_platform_default", "is_active"],
    )


def downgrade():
    op.drop_index("ix_openclaw_instances_platform_default", table_name="openclaw_instances")
    op.drop_column("openclaw_instances", "is_platform_default")
