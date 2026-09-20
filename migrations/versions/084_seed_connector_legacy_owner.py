"""seed legacy connector owner marker

Revision ID: 084
Revises: 083
"""

from alembic import op


revision = "084"
down_revision = "083"


def upgrade():
    op.execute(
        """
        INSERT INTO system_config (key, value, updated_by, updated_at)
        VALUES (
            'connector_keys.legacy_owner_user_id',
            to_jsonb('legacy_connector'::text),
            'migration:084',
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE data_sources
        SET config = COALESCE(config, '{}'::jsonb) || jsonb_build_object(
            'auth_source',
            COALESCE(config->>'auth_source', 'legacy_connector_api_key'),
            'legacy_owner_user_id',
            COALESCE(NULLIF(config->>'legacy_owner_user_id', ''), NULLIF(created_by, ''), 'legacy_connector')
        )
        WHERE source_type = 'platform_cookies'
          AND COALESCE(config, '{}'::jsonb) ? 'encrypted_cookies'
        """
    )


def downgrade():
    op.execute("DELETE FROM system_config WHERE key = 'connector_keys.legacy_owner_user_id'")
    op.execute(
        """
        UPDATE data_sources
        SET config = config - 'auth_source' - 'legacy_owner_user_id'
        WHERE source_type = 'platform_cookies'
          AND config->>'auth_source' = 'legacy_connector_api_key'
        """
    )
