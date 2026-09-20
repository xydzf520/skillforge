"""create project hosting tables

Revision ID: 108
Revises: 107
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "108"
down_revision = "107"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=42), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=30), nullable=False, server_default="external_web"),
        sa.Column("department_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("owner_user_id", sa.String(length=50), nullable=True),
        sa.Column("visibility", sa.String(length=20), nullable=False, server_default="department"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("entry_url", sa.Text(), nullable=True),
        sa.Column("current_version_id", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_department_id", "projects", ["department_id"])
    op.create_index("ix_projects_department", "projects", ["department"])
    op.create_index("ix_projects_owner_user_id", "projects", ["owner_user_id"])
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_type", "projects", ["type"])

    op.create_table(
        "project_versions",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("project_id", sa.String(length=42), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("artifact_type", sa.String(length=30), nullable=False, server_default="external_url"),
        sa.Column("artifact_ref", sa.Text(), nullable=True),
        sa.Column("sf_package_hash", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_project_versions_project_version"),
    )
    op.create_index("ix_project_versions_project_id", "project_versions", ["project_id"])
    op.create_index("ix_project_versions_created_by", "project_versions", ["created_by"])

    op.create_table(
        "project_runs",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.String(length=42), nullable=False),
        sa.Column("version_id", sa.String(length=80), nullable=True),
        sa.Column("execution_run_id", sa.String(length=50), nullable=True),
        sa.Column("decision_log_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=True),
        sa.Column("department_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="opening"),
        sa.Column("input_snapshot", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_result", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ai_summary", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("project_id", "version_id", "execution_run_id", "decision_log_id", "user_id", "department_id", "department", "status"):
        op.create_index(f"ix_project_runs_{column}", "project_runs", [column])

    op.create_table(
        "project_ingress_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_run_id", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False, server_default="output"),
        sa.Column("input_snapshot", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_result", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reports", JSONB, nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("todos", JSONB, nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("proofs", JSONB, nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata_json", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_ingress_events_project_run_id", "project_ingress_events", ["project_run_id"])
    op.create_index("ix_project_ingress_events_event_type", "project_ingress_events", ["event_type"])

    op.create_table(
        "project_capability_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_run_id", sa.String(length=50), nullable=False),
        sa.Column("capability_key", sa.String(length=120), nullable=False),
        sa.Column("capability_type", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("input_summary", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_summary", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("cost_json", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_capability_calls_project_run_id", "project_capability_calls", ["project_run_id"])
    op.create_index("ix_project_capability_calls_capability_key", "project_capability_calls", ["capability_key"])
    op.create_index("ix_project_capability_calls_capability_type", "project_capability_calls", ["capability_type"])
    op.create_index("ix_project_capability_calls_status", "project_capability_calls", ["status"])


def downgrade() -> None:
    op.drop_index("ix_project_capability_calls_status", table_name="project_capability_calls")
    op.drop_index("ix_project_capability_calls_capability_type", table_name="project_capability_calls")
    op.drop_index("ix_project_capability_calls_capability_key", table_name="project_capability_calls")
    op.drop_index("ix_project_capability_calls_project_run_id", table_name="project_capability_calls")
    op.drop_table("project_capability_calls")

    op.drop_index("ix_project_ingress_events_event_type", table_name="project_ingress_events")
    op.drop_index("ix_project_ingress_events_project_run_id", table_name="project_ingress_events")
    op.drop_table("project_ingress_events")

    for column in reversed(("project_id", "version_id", "execution_run_id", "decision_log_id", "user_id", "department_id", "department", "status")):
        op.drop_index(f"ix_project_runs_{column}", table_name="project_runs")
    op.drop_table("project_runs")

    op.drop_index("ix_project_versions_created_by", table_name="project_versions")
    op.drop_index("ix_project_versions_project_id", table_name="project_versions")
    op.drop_table("project_versions")

    op.drop_index("ix_projects_type", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_owner_user_id", table_name="projects")
    op.drop_index("ix_projects_department", table_name="projects")
    op.drop_index("ix_projects_department_id", table_name="projects")
    op.drop_table("projects")
