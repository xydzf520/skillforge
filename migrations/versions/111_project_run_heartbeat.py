"""project run heartbeat

Revision ID: 111
Revises: 110
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "111"
down_revision = "110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_runs", sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True))
    op.create_index("ix_project_runs_last_heartbeat_at", "project_runs", ["last_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_project_runs_last_heartbeat_at", table_name="project_runs")
    op.drop_column("project_runs", "last_heartbeat_at")
