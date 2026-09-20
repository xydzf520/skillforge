"""create connector API key metadata

Revision ID: 081
Revises: 080
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "081"
down_revision = "080"


def upgrade():
    op.create_table(
        "connector_api_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(24), nullable=False),
        sa.Column("key_last4", sa.String(8), nullable=False),
        sa.Column("source_id", sa.String(50), nullable=True),
        sa.Column("owner_user_id", sa.String(50), nullable=False),
        sa.Column(
            "scopes",
            JSONB,
            nullable=False,
            server_default=sa.text("'[\"cookies:push\"]'::jsonb"),
        ),
        sa.Column(
            "allowed_platforms",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "allowed_shop_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(50), nullable=True),
        sa.Column("rotated_from_key_id", sa.String(32), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(50), nullable=True),
        sa.Column("revoke_reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_connector_api_keys_key_hash", "connector_api_keys", ["key_hash"], unique=True)
    op.create_index("ix_connector_api_keys_source_id", "connector_api_keys", ["source_id"])
    op.create_index("ix_connector_api_keys_owner_user_id", "connector_api_keys", ["owner_user_id"])
    op.create_index("ix_connector_api_keys_status", "connector_api_keys", ["status"])
    op.create_index(
        "ix_connector_api_keys_rotated_from_key_id",
        "connector_api_keys",
        ["rotated_from_key_id"],
    )


def downgrade():
    op.drop_index("ix_connector_api_keys_rotated_from_key_id", table_name="connector_api_keys")
    op.drop_index("ix_connector_api_keys_status", table_name="connector_api_keys")
    op.drop_index("ix_connector_api_keys_owner_user_id", table_name="connector_api_keys")
    op.drop_index("ix_connector_api_keys_source_id", table_name="connector_api_keys")
    op.drop_index("ix_connector_api_keys_key_hash", table_name="connector_api_keys")
    op.drop_table("connector_api_keys")
