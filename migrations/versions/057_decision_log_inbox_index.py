"""decision_log inbox listing partial index

Revision ID: 057
Revises: 056
"""

from alembic import op

revision = "057"
down_revision = "056"


def upgrade():
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_dl_inbox_listing
          ON decision_log (created_at DESC)
          WHERE is_sandbox = false
            AND jsonb_typeof(output_result -> 'reports') = 'array'
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_dl_inbox_listing")
