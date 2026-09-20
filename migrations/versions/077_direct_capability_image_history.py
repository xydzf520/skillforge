"""direct capability image history

Revision ID: 077
Revises: 076
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "direct_capability_image_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("capability_id", sa.String(length=100), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("task_id", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", "capability_id", "image_url", name="uq_direct_capability_image_history_url"),
    )
    op.create_index(
        "ix_dcih_user_capability_created",
        "direct_capability_image_history",
        ["user_id", "capability_id", "created_at"],
    )
    op.create_index(
        "ix_dcih_task_id",
        "direct_capability_image_history",
        ["task_id"],
    )


def downgrade():
    op.drop_index("ix_dcih_task_id", table_name="direct_capability_image_history")
    op.drop_index("ix_dcih_user_capability_created", table_name="direct_capability_image_history")
    op.drop_table("direct_capability_image_history")
