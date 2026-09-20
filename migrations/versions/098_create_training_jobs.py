"""create training jobs

Revision ID: 098
Revises: 097
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "098"
down_revision = "097"


def upgrade():
    op.create_table(
        "training_jobs",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("job_type", sa.String(length=30), nullable=False),
        sa.Column("training_strategy", sa.String(length=80), nullable=True),
        sa.Column("target_skill_id", sa.String(length=50), nullable=True),
        sa.Column("target_gateway_id", sa.String(length=50), nullable=True),
        sa.Column("dataset_ref", sa.String(length=200), nullable=True),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("failure_stage", sa.String(length=30), nullable=True),
        sa.Column("spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("gateway_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("approval_required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("approved_by", sa.String(length=50), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_by", sa.String(length=50), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_jobs_department", "training_jobs", ["department"])
    op.create_index("ix_training_jobs_created_by", "training_jobs", ["created_by"])
    op.create_index("ix_training_jobs_status", "training_jobs", ["status"])
    op.create_index("ix_training_jobs_target_skill_id", "training_jobs", ["target_skill_id"])
    op.create_index("ix_training_jobs_target_gateway_id", "training_jobs", ["target_gateway_id"])

    op.create_table(
        "training_job_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=50), nullable=False),
        sa.Column("gateway_id", sa.String(length=50), nullable=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("logs_url", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_job_tasks_job_id", "training_job_tasks", ["job_id"])
    op.create_index("ix_training_job_tasks_gateway_id", "training_job_tasks", ["gateway_id"])
    op.create_index("ix_training_job_tasks_status", "training_job_tasks", ["status"])


def downgrade():
    op.drop_index("ix_training_job_tasks_status", table_name="training_job_tasks")
    op.drop_index("ix_training_job_tasks_gateway_id", table_name="training_job_tasks")
    op.drop_index("ix_training_job_tasks_job_id", table_name="training_job_tasks")
    op.drop_table("training_job_tasks")
    op.drop_index("ix_training_jobs_target_gateway_id", table_name="training_jobs")
    op.drop_index("ix_training_jobs_target_skill_id", table_name="training_jobs")
    op.drop_index("ix_training_jobs_status", table_name="training_jobs")
    op.drop_index("ix_training_jobs_created_by", table_name="training_jobs")
    op.drop_index("ix_training_jobs_department", table_name="training_jobs")
    op.drop_table("training_jobs")
