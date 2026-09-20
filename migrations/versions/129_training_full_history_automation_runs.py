"""persist full-history finetune automation runs

Revision ID: 129_full_history_auto_runs
Revises: 128_image_history_size
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "129_full_history_auto_runs"
down_revision = "128_image_history_size"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_full_history_automation_runs",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("trigger_type", sa.String(length=40), nullable=False),
        sa.Column("current_step", sa.String(length=80), nullable=True),
        sa.Column("current_cycle", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_job_id", sa.String(length=50), nullable=True),
        sa.Column("target_skill_id", sa.String(length=100), nullable=False),
        sa.Column("model_profile", sa.String(length=100), nullable=False),
        sa.Column("training_gateway_id", sa.String(length=80), nullable=True),
        sa.Column("deployment_gateway_id", sa.String(length=80), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("data_preparation_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("actions_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_training_full_history_automation_runs_status"), "training_full_history_automation_runs", ["status"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_trigger_type"), "training_full_history_automation_runs", ["trigger_type"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_current_step"), "training_full_history_automation_runs", ["current_step"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_current_job_id"), "training_full_history_automation_runs", ["current_job_id"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_target_skill_id"), "training_full_history_automation_runs", ["target_skill_id"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_training_gateway_id"), "training_full_history_automation_runs", ["training_gateway_id"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_deployment_gateway_id"), "training_full_history_automation_runs", ["deployment_gateway_id"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_created_by"), "training_full_history_automation_runs", ["created_by"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_started_at"), "training_full_history_automation_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_finished_at"), "training_full_history_automation_runs", ["finished_at"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_last_heartbeat_at"), "training_full_history_automation_runs", ["last_heartbeat_at"], unique=False)
    op.create_index(op.f("ix_training_full_history_automation_runs_created_at"), "training_full_history_automation_runs", ["created_at"], unique=False)
    op.create_index(
        "ix_training_full_history_runs_target_status",
        "training_full_history_automation_runs",
        ["target_skill_id", "status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_training_full_history_runs_target_status", table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_created_at"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_last_heartbeat_at"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_finished_at"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_started_at"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_created_by"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_deployment_gateway_id"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_training_gateway_id"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_target_skill_id"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_current_job_id"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_current_step"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_trigger_type"), table_name="training_full_history_automation_runs")
    op.drop_index(op.f("ix_training_full_history_automation_runs_status"), table_name="training_full_history_automation_runs")
    op.drop_table("training_full_history_automation_runs")
