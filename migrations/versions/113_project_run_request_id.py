"""project run request id

Revision ID: 113
Revises: 112
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "113"
down_revision = "112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_runs", sa.Column("request_id", sa.String(length=80), nullable=True))
    op.create_index("ix_project_runs_request_id", "project_runs", ["request_id"])
    op.create_unique_constraint(
        "uq_project_runs_project_user_request",
        "project_runs",
        ["project_id", "user_id", "request_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_project_runs_project_user_request", "project_runs", type_="unique")
    op.drop_index("ix_project_runs_request_id", table_name="project_runs")
    op.drop_column("project_runs", "request_id")
