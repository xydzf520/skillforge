"""project trace pagination indexes

Revision ID: 115
Revises: 114
Create Date: 2026-06-03
"""

from alembic import op


revision = "115"
down_revision = "114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_project_ingress_run_created_id",
        "project_ingress_events",
        ["project_run_id", "created_at", "id"],
    )
    op.create_index(
        "ix_project_capability_run_created_id",
        "project_capability_calls",
        ["project_run_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_capability_run_created_id", table_name="project_capability_calls")
    op.drop_index("ix_project_ingress_run_created_id", table_name="project_ingress_events")
