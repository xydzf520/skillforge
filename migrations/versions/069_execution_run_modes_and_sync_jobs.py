"""execution run modes and skill sync jobs

Revision ID: 069
Revises: 068
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "069"
down_revision = "068"


def upgrade():
    op.add_column("execution_runs", sa.Column("skill_id", sa.String(length=50), nullable=True))
    op.add_column("execution_runs", sa.Column("run_mode", sa.String(length=30), nullable=True))
    op.add_column("execution_runs", sa.Column("parent_run_id", sa.String(length=50), nullable=True))
    op.add_column(
        "execution_runs",
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_execution_runs_skill_id", "execution_runs", ["skill_id"])
    op.create_index("ix_execution_runs_run_mode", "execution_runs", ["run_mode"])
    op.create_index("ix_execution_runs_parent_run_id", "execution_runs", ["parent_run_id"])

    op.create_table(
        "skill_sync_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("version_tag", sa.String(length=100), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_instance_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("target_department", sa.String(length=50), nullable=True),
        sa.Column("push_ok", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skill_sync_jobs_skill_id", "skill_sync_jobs", ["skill_id"])
    op.create_index("ix_skill_sync_jobs_version_tag", "skill_sync_jobs", ["version_tag"])
    op.create_index("ix_skill_sync_jobs_review_id", "skill_sync_jobs", ["review_id"])
    op.create_index("ix_skill_sync_jobs_status", "skill_sync_jobs", ["status"])

    op.create_table(
        "skill_sync_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("version_tag", sa.String(length=100), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("instance_id", sa.String(length=50), nullable=True),
        sa.Column("agent_type", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skill_sync_attempts_job_id", "skill_sync_attempts", ["job_id"])
    op.create_index("ix_skill_sync_attempts_skill_id", "skill_sync_attempts", ["skill_id"])
    op.create_index("ix_skill_sync_attempts_version_tag", "skill_sync_attempts", ["version_tag"])
    op.create_index("ix_skill_sync_attempts_review_id", "skill_sync_attempts", ["review_id"])
    op.create_index("ix_skill_sync_attempts_instance_id", "skill_sync_attempts", ["instance_id"])
    op.create_index("ix_skill_sync_attempts_status", "skill_sync_attempts", ["status"])


def downgrade():
    op.drop_index("ix_skill_sync_attempts_status", table_name="skill_sync_attempts")
    op.drop_index("ix_skill_sync_attempts_instance_id", table_name="skill_sync_attempts")
    op.drop_index("ix_skill_sync_attempts_review_id", table_name="skill_sync_attempts")
    op.drop_index("ix_skill_sync_attempts_version_tag", table_name="skill_sync_attempts")
    op.drop_index("ix_skill_sync_attempts_skill_id", table_name="skill_sync_attempts")
    op.drop_index("ix_skill_sync_attempts_job_id", table_name="skill_sync_attempts")
    op.drop_table("skill_sync_attempts")

    op.drop_index("ix_skill_sync_jobs_status", table_name="skill_sync_jobs")
    op.drop_index("ix_skill_sync_jobs_review_id", table_name="skill_sync_jobs")
    op.drop_index("ix_skill_sync_jobs_version_tag", table_name="skill_sync_jobs")
    op.drop_index("ix_skill_sync_jobs_skill_id", table_name="skill_sync_jobs")
    op.drop_table("skill_sync_jobs")

    op.drop_index("ix_execution_runs_parent_run_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_run_mode", table_name="execution_runs")
    op.drop_index("ix_execution_runs_skill_id", table_name="execution_runs")
    op.drop_column("execution_runs", "metadata_json")
    op.drop_column("execution_runs", "parent_run_id")
    op.drop_column("execution_runs", "run_mode")
    op.drop_column("execution_runs", "skill_id")
