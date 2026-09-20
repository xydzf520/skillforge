"""create platform api schema snapshots

Revision ID: 087
Revises: 086
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "087"
down_revision = "086"


def upgrade():
    op.create_table(
        "platform_api_schema_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("shop_id", sa.String(length=50), nullable=True),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("response_keys", postgresql.JSONB(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("source_run_id", sa.String(length=100), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "platform",
            "shop_id",
            "endpoint_hash",
            "source_run_id",
            name="uq_platform_api_schema_snapshot_run",
        ),
    )
    op.create_index(
        "ix_platform_api_schema_snapshots_lookup",
        "platform_api_schema_snapshots",
        ["platform", "endpoint_hash", "captured_at"],
    )
    op.create_index(
        "ix_platform_api_schema_snapshots_run",
        "platform_api_schema_snapshots",
        ["source_run_id"],
    )


def downgrade():
    op.drop_index("ix_platform_api_schema_snapshots_run", table_name="platform_api_schema_snapshots")
    op.drop_index("ix_platform_api_schema_snapshots_lookup", table_name="platform_api_schema_snapshots")
    op.drop_table("platform_api_schema_snapshots")
