"""execution workers

Revision ID: 041
Revises: 040
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "041"
down_revision = "040"


def upgrade():
    op.create_table(
        "execution_workers",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("queue_name", sa.String(length=50), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("active_runs", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="online"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_workers_status", "execution_workers", ["status"])


def downgrade():
    op.drop_index("ix_execution_workers_status", table_name="execution_workers")
    op.drop_table("execution_workers")
