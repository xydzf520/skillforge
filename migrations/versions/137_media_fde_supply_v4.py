"""FDE material supply requests, candidates, editorial decisions and attribution

Revision ID: 137_media_fde_supply_v4
Revises: 136_media_workbench_agents
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "137_media_fde_supply_v4"
down_revision = "136_media_workbench_agents"
branch_labels = None
depends_on = None

JSON_OBJECT = sa.text("'{}'::jsonb")
JSON_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "media_material_requests",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("requested_by", sa.String(50), nullable=False),
        sa.Column("assignee_id", sa.String(50)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("deadline_at", sa.DateTime()),
        sa.Column("business_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("generation_participation_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("visual_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("script", sa.Text(), server_default="", nullable=False),
        sa.Column("source_roles_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("production_mode", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("ratio", sa.String(20), nullable=False),
        sa.Column("output_preset_id", sa.String(50), nullable=False),
        sa.Column("allow_ai_optimization", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("allow_script_changes", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("submitted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_material_requests_project_idempotency"),
    )
    for column in (
        "project_id", "project_run_id", "department_id", "requested_by", "assignee_id",
        "source", "status", "priority", "deadline_at", "production_mode",
    ):
        op.create_index(f"ix_media_material_requests_{column}", "media_material_requests", [column])
    op.create_index(
        "ix_media_material_requests_department_status", "media_material_requests",
        ["department_id", "status", "priority", "created_at"],
    )
    op.create_index(
        "ix_media_material_requests_requester_created", "media_material_requests",
        ["requested_by", "created_at"],
    )

    op.create_table(
        "media_candidates",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("request_id", sa.String(50)),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("media_job_id", sa.String(50), nullable=False),
        sa.Column("generation_method", sa.String(40), nullable=False),
        sa.Column("direction_id", sa.String(20)),
        sa.Column("variant_index", sa.Integer(), server_default="1", nullable=False),
        sa.Column("business_title", sa.String(240), nullable=False),
        sa.Column("result_asset_id", sa.String(50)),
        sa.Column("poster_asset_id", sa.String(50)),
        sa.Column("technical_status", sa.String(30), nullable=False),
        sa.Column("technical_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("editorial_status", sa.String(30), nullable=False),
        sa.Column("delivery_status", sa.String(30), nullable=False),
        sa.Column("attribution_status", sa.String(30), nullable=False),
        sa.Column("cumulative_spend", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("performance_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("tags_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("entered_selection_at", sa.DateTime()),
        sa.Column("selected_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["media_material_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_job_id"], ["media_generation_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["poster_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("media_job_id", name="uq_media_candidates_media_job_id"),
    )
    for column in (
        "request_id", "project_id", "project_run_id", "department_id", "media_job_id",
        "generation_method", "direction_id", "result_asset_id", "poster_asset_id",
        "technical_status", "editorial_status", "delivery_status", "attribution_status",
        "entered_selection_at", "selected_at",
    ):
        op.create_index(f"ix_media_candidates_{column}", "media_candidates", [column])
    op.create_index(
        "ix_media_candidates_selection_queue", "media_candidates",
        ["department_id", "technical_status", "editorial_status", "entered_selection_at"],
    )
    op.create_index("ix_media_candidates_request_created", "media_candidates", ["request_id", "created_at"])
    op.create_index(
        "ix_media_candidates_delivery_attribution", "media_candidates",
        ["delivery_status", "attribution_status", "updated_at"],
    )

    op.create_table(
        "media_editorial_decisions",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("candidate_id", sa.String(50), nullable=False),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("decided_by", sa.String(50), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("usage_direction", sa.String(240)),
        sa.Column("edit_notes", sa.Text()),
        sa.Column("problem_types_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("annotation_ids_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("history_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["media_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("candidate_id", "decided_by", name="uq_media_editorial_decision_candidate_user"),
    )
    for column in ("candidate_id", "project_id", "department_id", "decided_by", "decision"):
        op.create_index(f"ix_media_editorial_decisions_{column}", "media_editorial_decisions", [column])
    op.create_index(
        "ix_media_editorial_decisions_department_time", "media_editorial_decisions",
        ["department_id", "decision", "decided_at"],
    )

    op.create_table(
        "media_deliveries",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("candidate_id", sa.String(50), nullable=False),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("requested_by", sa.String(50), nullable=False),
        sa.Column("video_sha256", sa.String(64), nullable=False),
        sa.Column("team", sa.String(120), nullable=False),
        sa.Column("category", sa.String(180), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("cloud_outbox_id", sa.String(50)),
        sa.Column("remote_video_id", sa.String(180)),
        sa.Column("ad_asset_id", sa.String(180)),
        sa.Column("payload_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("delivered_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["media_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cloud_outbox_id"], ["cloud_video_sync_outbox.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_media_deliveries_idempotency_key"),
    )
    for column in (
        "candidate_id", "project_id", "department_id", "requested_by", "video_sha256",
        "status", "idempotency_key", "cloud_outbox_id", "remote_video_id", "ad_asset_id",
    ):
        op.create_index(f"ix_media_deliveries_{column}", "media_deliveries", [column])
    op.create_index("ix_media_deliveries_candidate_status", "media_deliveries", ["candidate_id", "status", "updated_at"])
    op.create_index("ix_media_deliveries_remote_link", "media_deliveries", ["remote_video_id", "ad_asset_id"])

    op.create_table(
        "media_performance_daily",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("candidate_id", sa.String(50), nullable=False),
        sa.Column("delivery_id", sa.String(50)),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("remote_video_id", sa.String(180), nullable=False),
        sa.Column("ad_asset_id", sa.String(180), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("spend", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("impressions", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("clicks", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("conversions", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("gmv", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("roi", sa.Numeric(18, 6)),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["media_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_id"], ["media_deliveries.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("ad_asset_id", "metric_date", name="uq_media_performance_ad_asset_date"),
    )
    for column in (
        "candidate_id", "delivery_id", "department_id", "remote_video_id", "ad_asset_id", "metric_date",
    ):
        op.create_index(f"ix_media_performance_daily_{column}", "media_performance_daily", [column])
    op.create_index("ix_media_performance_candidate_date", "media_performance_daily", ["candidate_id", "metric_date"])
    op.create_index("ix_media_performance_department_date", "media_performance_daily", ["department_id", "metric_date"])

    op.create_table(
        "media_review_preferences",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("preference_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_media_review_preferences_project_user"),
    )
    for column in ("project_id", "user_id"):
        op.create_index(f"ix_media_review_preferences_{column}", "media_review_preferences", [column])


def downgrade() -> None:
    op.drop_table("media_review_preferences")
    op.drop_table("media_performance_daily")
    op.drop_table("media_deliveries")
    op.drop_table("media_editorial_decisions")
    op.drop_table("media_candidates")
    op.drop_table("media_material_requests")
