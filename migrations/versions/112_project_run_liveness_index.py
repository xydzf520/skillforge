"""project run liveness index

Revision ID: 112
Revises: 111
Create Date: 2026-06-03
"""

from alembic import op


revision = "112"
down_revision = "111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_project_runs_status_heartbeat", "project_runs", ["status", "last_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_project_runs_status_heartbeat", table_name="project_runs")
