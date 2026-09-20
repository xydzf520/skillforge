"""track direct capability image byte sizes

Revision ID: 128_image_history_size
Revises: 127_training_asset_registry
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "128_image_history_size"
down_revision = "127_training_asset_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "direct_capability_image_history",
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("direct_capability_image_history", "byte_size")
