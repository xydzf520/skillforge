"""codex cli session client metadata

Revision ID: 105
Revises: 104
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "105"
down_revision = "104"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "codex_cli_sessions",
        sa.Column(
            "client_meta_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("codex_cli_sessions", "client_meta_json")
