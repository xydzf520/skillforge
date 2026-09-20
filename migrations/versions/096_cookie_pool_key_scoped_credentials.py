"""scope cookie pool credentials by connector key

Revision ID: 096
Revises: 095
"""

from alembic import op
import sqlalchemy as sa


revision = "096"
down_revision = "095"


def upgrade():
    op.drop_constraint(
        "uq_platform_cookie_pool_source_platform_shop_owner",
        "platform_cookie_pool",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_platform_cookie_pool_source_platform_shop_owner_key",
        "platform_cookie_pool",
        ["source_id", "platform", "shop_id", "owner_user_id", "connector_key_id"],
    )
    op.execute(
        sa.text(
            """
            UPDATE platform_cookie_pool AS pool
            SET shop_id = 'default',
                updated_at = NOW()
            FROM connector_api_keys AS key
            WHERE pool.connector_key_id = key.id
              AND pool.shop_id = key.id
              AND COALESCE(key.allowed_shop_ids, '[]'::jsonb) = '[]'::jsonb
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE data_sources AS ds
            SET config = jsonb_set(ds.config, '{shop_id}', '"default"', true),
                updated_at = NOW()
            FROM connector_api_keys AS key
            WHERE ds.source_type = 'platform_cookies'
              AND ds.config->>'auth_source' = 'connector_api_key'
              AND ds.config->>'shop_id' = key.id
              AND COALESCE(key.allowed_shop_ids, '[]'::jsonb) = '[]'::jsonb
            """
        )
    )


def downgrade():
    op.drop_constraint(
        "uq_platform_cookie_pool_source_platform_shop_owner_key",
        "platform_cookie_pool",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_platform_cookie_pool_source_platform_shop_owner",
        "platform_cookie_pool",
        ["source_id", "platform", "shop_id", "owner_user_id"],
    )
