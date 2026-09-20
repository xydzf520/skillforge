"""add governed H3 media workbench control-plane tables

Revision ID: 130_media_workbench
Revises: 129_full_history_auto_runs
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "130_media_workbench"
down_revision = "129_full_history_auto_runs"
branch_labels = None
depends_on = None

JSON_OBJECT = sa.text("'{}'::jsonb")
JSON_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "media_plan_comparisons",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.String(length=42), nullable=False),
        sa.Column("project_run_id", sa.String(length=50), nullable=False),
        sa.Column("department_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("requested_by", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("brief_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("baseline_model", sa.String(length=180), nullable=False),
        sa.Column("baseline_output_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("candidate_deployment_id", sa.String(length=120), nullable=True),
        sa.Column("candidate_model", sa.String(length=180), nullable=True),
        sa.Column("candidate_output_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("selected_source", sa.String(length=30), nullable=True),
        sa.Column("final_output_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("edits_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("template_version", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("project_id", "project_run_id", "department_id", "department", "requested_by", "status", "candidate_deployment_id", "selected_source", "template_version"):
        op.create_index(f"ix_media_plan_comparisons_{column}", "media_plan_comparisons", [column])
    op.create_index("ix_media_plan_comparisons_run_created", "media_plan_comparisons", ["project_run_id", "created_at"])
    op.create_index("ix_media_plan_comparisons_department_created", "media_plan_comparisons", ["department_id", "created_at"])

    op.create_table(
        "media_generation_jobs",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.String(length=42), nullable=False),
        sa.Column("project_run_id", sa.String(length=50), nullable=False),
        sa.Column("plan_comparison_id", sa.String(length=50), nullable=True),
        sa.Column("cloned_from_job_id", sa.String(length=50), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("department_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("requested_by", sa.String(length=50), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("assigned_instance_id", sa.String(length=50), nullable=True),
        sa.Column("current_attempt_no", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("params_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("reference_assets_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("workflow_template_id", sa.String(length=80), nullable=False),
        sa.Column("workflow_version", sa.String(length=80), nullable=True),
        sa.Column("model_version", sa.String(length=180), nullable=True),
        sa.Column("model_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_asset_id", sa.String(length=50), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("review_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("rights_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("training_eligibility", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.String(length=50), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_comparison_id"], ["media_plan_comparisons.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cloned_from_job_id"], ["media_generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_generation_jobs_project_idempotency"),
    )
    for column in (
        "project_id", "project_run_id", "plan_comparison_id", "cloned_from_job_id", "department_id", "department",
        "requested_by", "mode", "status", "priority", "assigned_instance_id", "workflow_template_id", "workflow_version",
        "model_version", "model_sha256", "result_asset_id", "result_sha256", "training_eligibility", "approved_by",
    ):
        op.create_index(f"ix_media_generation_jobs_{column}", "media_generation_jobs", [column])
    op.create_index("ix_media_generation_jobs_queue", "media_generation_jobs", ["status", "priority", "created_at"])
    op.create_index("ix_media_generation_jobs_run_created", "media_generation_jobs", ["project_run_id", "created_at"])

    op.create_table(
        "media_generation_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=50), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(length=50), nullable=True),
        sa.Column("bridge_job_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("capability_snapshot_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["media_generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_media_generation_attempts_job_attempt"),
    )
    for column in ("job_id", "instance_id", "bridge_job_id", "status"):
        op.create_index(f"ix_media_generation_attempts_{column}", "media_generation_attempts", [column])
    op.create_index("ix_media_generation_attempts_instance_status", "media_generation_attempts", ["instance_id", "status"])

    op.create_table(
        "cloud_video_sync_outbox",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("media_job_id", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("team", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("remote_video_id", sa.String(length=180), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["media_job_id"], ["media_generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("media_job_id", "idempotency_key", "status", "remote_video_id", "next_attempt_at"):
        op.create_index(f"ix_cloud_video_sync_outbox_{column}", "cloud_video_sync_outbox", [column], unique=column == "idempotency_key")
    op.create_index("ix_cloud_video_sync_outbox_pending", "cloud_video_sync_outbox", ["status", "next_attempt_at", "created_at"])


def downgrade() -> None:
    op.drop_table("cloud_video_sync_outbox")
    op.drop_table("media_generation_attempts")
    op.drop_table("media_generation_jobs")
    op.drop_table("media_plan_comparisons")
