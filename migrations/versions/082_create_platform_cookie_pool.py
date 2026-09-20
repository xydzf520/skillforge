"""create owner-scoped platform cookie pool

Revision ID: 082
Revises: 081
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "082"
down_revision = "081"


def upgrade():
    op.create_table(
        "platform_cookie_pool",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("shop_id", sa.String(100), nullable=False),
        sa.Column("account_login", sa.String(150), nullable=True),
        sa.Column("owner_user_id", sa.String(50), nullable=False),
        sa.Column("connector_key_id", sa.String(32), nullable=True),
        sa.Column("device_label", sa.String(100), nullable=True),
        sa.Column(
            "auth_source",
            sa.String(40),
            nullable=False,
            server_default="connector_api_key",
        ),
        sa.Column("legacy_owner_user_id", sa.String(50), nullable=True),
        sa.Column("encrypted_cookies", sa.Text(), nullable=False),
        sa.Column("encrypted_cookie_details", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("health_score", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("verification_status", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("capability_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("pushed_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_by", sa.String(50), nullable=True),
        sa.Column("disable_reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "source_id",
            "platform",
            "shop_id",
            "owner_user_id",
            name="uq_platform_cookie_pool_source_platform_shop_owner",
        ),
    )
    op.create_index("ix_platform_cookie_pool_source_id", "platform_cookie_pool", ["source_id"])
    op.create_index("ix_platform_cookie_pool_platform", "platform_cookie_pool", ["platform"])
    op.create_index("ix_platform_cookie_pool_shop_id", "platform_cookie_pool", ["shop_id"])
    op.create_index("ix_platform_cookie_pool_owner_user_id", "platform_cookie_pool", ["owner_user_id"])
    op.create_index("ix_platform_cookie_pool_connector_key_id", "platform_cookie_pool", ["connector_key_id"])
    op.create_index("ix_platform_cookie_pool_last_used_at", "platform_cookie_pool", ["last_used_at"])
    op.create_index("ix_platform_cookie_pool_priority", "platform_cookie_pool", ["priority"])
    op.create_index("ix_platform_cookie_pool_legacy_owner_user_id", "platform_cookie_pool", ["legacy_owner_user_id"])
    op.create_index("ix_platform_cookie_pool_status", "platform_cookie_pool", ["status"])
    op.create_index("ix_platform_cookie_pool_is_active", "platform_cookie_pool", ["is_active"])
    op.create_index("ix_platform_cookie_pool_pushed_at", "platform_cookie_pool", ["pushed_at"])
    op.create_index(
        "ix_platform_cookie_pool_lookup",
        "platform_cookie_pool",
        ["source_id", "platform", "shop_id"],
    )
    op.create_index(
        "ix_platform_cookie_pool_owner_active",
        "platform_cookie_pool",
        ["owner_user_id", "is_active"],
    )


def downgrade():
    op.drop_index("ix_platform_cookie_pool_owner_active", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_lookup", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_priority", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_last_used_at", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_pushed_at", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_is_active", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_status", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_legacy_owner_user_id", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_connector_key_id", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_owner_user_id", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_shop_id", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_platform", table_name="platform_cookie_pool")
    op.drop_index("ix_platform_cookie_pool_source_id", table_name="platform_cookie_pool")
    op.drop_table("platform_cookie_pool")
