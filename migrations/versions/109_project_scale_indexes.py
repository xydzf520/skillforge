"""project scale indexes and run counters

Revision ID: 109
Revises: 108
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_runs", sa.Column("report_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("project_runs", sa.Column("todo_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("project_runs", sa.Column("capability_call_count", sa.Integer(), nullable=False, server_default="0"))

    op.create_index("ix_projects_type_updated", "projects", ["type", "updated_at"])
    op.create_index("ix_projects_visibility_status_updated", "projects", ["visibility", "status", "updated_at"])
    op.create_index("ix_projects_owner_updated", "projects", ["owner_user_id", "updated_at"])
    op.create_index("ix_projects_department_updated", "projects", ["department_id", "updated_at"])
    op.create_index("ix_project_runs_project_latest", "project_runs", ["project_id", "created_at"])
    op.create_index("ix_project_runs_status_updated", "project_runs", ["status", "updated_at"])
    op.create_index("ix_project_runs_department_updated", "project_runs", ["department_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_project_runs_department_updated", table_name="project_runs")
    op.drop_index("ix_project_runs_status_updated", table_name="project_runs")
    op.drop_index("ix_project_runs_project_latest", table_name="project_runs")
    op.drop_index("ix_projects_department_updated", table_name="projects")
    op.drop_index("ix_projects_owner_updated", table_name="projects")
    op.drop_index("ix_projects_visibility_status_updated", table_name="projects")
    op.drop_index("ix_projects_type_updated", table_name="projects")

    op.drop_column("project_runs", "capability_call_count")
    op.drop_column("project_runs", "todo_count")
    op.drop_column("project_runs", "report_count")
