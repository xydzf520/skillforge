"""align data_access_grants runtime schema

Revision ID: 078
Revises: 077
"""

from alembic import op

revision = "078"
down_revision = "077"


def upgrade():
    op.execute("""
        ALTER TABLE data_access_grants
        ADD COLUMN IF NOT EXISTS permission VARCHAR(20) NOT NULL DEFAULT 'read'
    """)
    op.execute("""
        ALTER TABLE data_access_grants
        ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITHOUT TIME ZONE
    """)
    op.execute("""
        ALTER TABLE data_access_grants
        ALTER COLUMN grantee_user_id DROP NOT NULL
    """)

    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'data_access_grants'
              AND column_name = 'id'
              AND data_type <> 'integer'
        ) THEN
            CREATE SEQUENCE IF NOT EXISTS data_access_grants_id_seq;

            ALTER TABLE data_access_grants ADD COLUMN IF NOT EXISTS id_int INTEGER;
            UPDATE data_access_grants
            SET id_int = CASE
                WHEN id ~ '^[0-9]+$' THEN id::INTEGER
                ELSE nextval('data_access_grants_id_seq')::INTEGER
            END
            WHERE id_int IS NULL;

            ALTER TABLE data_access_grants DROP CONSTRAINT IF EXISTS data_access_grants_pkey;

            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'data_access_grants'
                  AND column_name = 'legacy_id'
            ) THEN
                ALTER TABLE data_access_grants RENAME COLUMN id TO legacy_id;
            ELSE
                ALTER TABLE data_access_grants DROP COLUMN id;
            END IF;

            ALTER TABLE data_access_grants RENAME COLUMN id_int TO id;
            ALTER TABLE data_access_grants ALTER COLUMN id SET NOT NULL;
            ALTER TABLE data_access_grants ALTER COLUMN id SET DEFAULT nextval('data_access_grants_id_seq');
            ALTER SEQUENCE data_access_grants_id_seq OWNED BY data_access_grants.id;
            PERFORM setval(
                'data_access_grants_id_seq',
                GREATEST(COALESCE((SELECT MAX(id) FROM data_access_grants), 0), 1),
                true
            );
            ALTER TABLE data_access_grants ADD CONSTRAINT data_access_grants_pkey PRIMARY KEY (id);
        ELSE
            CREATE SEQUENCE IF NOT EXISTS data_access_grants_id_seq OWNED BY data_access_grants.id;
            ALTER TABLE data_access_grants ALTER COLUMN id SET DEFAULT nextval('data_access_grants_id_seq');
            PERFORM setval(
                'data_access_grants_id_seq',
                GREATEST(COALESCE((SELECT MAX(id) FROM data_access_grants), 0), 1),
                true
            );
        END IF;
    END $$;
    """)


def downgrade():
    op.execute("ALTER TABLE data_access_grants DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE data_access_grants DROP COLUMN IF EXISTS permission")
