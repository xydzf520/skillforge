"""ensure openclaw auth token column

Revision ID: 076
Revises: 075
Create Date: 2026-04-24
"""

from alembic import op


revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        'ALTER TABLE "openclaw_instances" '
        'ADD COLUMN IF NOT EXISTS "auth_token" VARCHAR(200)'
    )


def downgrade():
    op.execute(
        'ALTER TABLE "openclaw_instances" '
        'DROP COLUMN IF EXISTS "auth_token"'
    )
