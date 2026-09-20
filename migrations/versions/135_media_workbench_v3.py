"""material workbench 3.0 exact prompt, strategy, and full replay

Revision ID: 135_media_workbench_v3
Revises: 134_ai_chat_preferences
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "135_media_workbench_v3"
down_revision = "134_ai_chat_preferences"
branch_labels = None
depends_on = None

JSON_OBJECT = sa.text("'{}'::jsonb")
JSON_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "media_creative_strategy_sessions",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50)),
        sa.Column("requested_by", sa.String(50)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("original_input_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("included_context_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("directions_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("selected_direction_ids_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("questions_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("answers_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("assumptions_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("compiled_plans_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("round_no", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_rounds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("model", sa.String(180)),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
    )
    for column in ("project_id", "project_run_id", "department_id", "requested_by", "status", "input_fingerprint"):
        op.create_index(f"ix_media_creative_strategy_sessions_{column}", "media_creative_strategy_sessions", [column])
    op.create_index(
        "ix_media_strategy_run_fingerprint", "media_creative_strategy_sessions",
        ["project_run_id", "input_fingerprint", "created_at"],
    )

    op.create_table(
        "media_replay_projects",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50)),
        sa.Column("requested_by", sa.String(50)),
        sa.Column("source_asset_id", sa.String(50), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source_duration_seconds", sa.Float(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("analysis_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("config_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("preview_asset_id", sa.String(50)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["project_run_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preview_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_replay_project_idempotency"),
    )
    for column in (
        "project_id", "project_run_id", "department_id", "requested_by", "source_asset_id",
        "mode", "status", "input_fingerprint", "preview_asset_id",
    ):
        op.create_index(f"ix_media_replay_projects_{column}", "media_replay_projects", [column])
    op.create_index(
        "ix_media_replay_run_status", "media_replay_projects",
        ["project_run_id", "status", "updated_at"],
    )

    op.create_table(
        "media_replay_segments",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("replay_project_id", sa.String(50), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source_clip_asset_id", sa.String(50)),
        sa.Column("media_job_id", sa.String(50)),
        sa.Column("prompt_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("dialogue_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("seam_analysis_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["replay_project_id"], ["media_replay_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_clip_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_job_id"], ["media_generation_jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("replay_project_id", "segment_index", name="uq_media_replay_project_segment"),
    )
    for column in ("replay_project_id", "status", "source_clip_asset_id", "media_job_id"):
        op.create_index(f"ix_media_replay_segments_{column}", "media_replay_segments", [column])
    op.create_index(
        "ix_media_replay_segments_project_status", "media_replay_segments",
        ["replay_project_id", "status", "segment_index"],
    )

    additions = (
        ("strategy_session_id", sa.String(50)),
        ("strategy_direction_id", sa.String(50)),
        ("replay_project_id", sa.String(50)),
        ("replay_segment_id", sa.String(50)),
        ("prompt_preview_sha256", sa.String(64)),
        ("execution_prompt_sha256", sa.String(64)),
    )
    for name, type_ in additions:
        op.add_column("media_generation_jobs", sa.Column(name, type_, nullable=True))
        op.create_index(f"ix_media_generation_jobs_{name}", "media_generation_jobs", [name])
    op.create_foreign_key(
        "fk_media_jobs_strategy_session", "media_generation_jobs", "media_creative_strategy_sessions",
        ["strategy_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_media_jobs_replay_project", "media_generation_jobs", "media_replay_projects",
        ["replay_project_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    for constraint in ("fk_media_jobs_replay_project", "fk_media_jobs_strategy_session"):
        op.drop_constraint(constraint, "media_generation_jobs", type_="foreignkey")
    for name in (
        "execution_prompt_sha256", "prompt_preview_sha256", "replay_segment_id",
        "replay_project_id", "strategy_direction_id", "strategy_session_id",
    ):
        op.drop_index(f"ix_media_generation_jobs_{name}", table_name="media_generation_jobs")
        op.drop_column("media_generation_jobs", name)
    op.drop_table("media_replay_segments")
    op.drop_table("media_replay_projects")
    op.drop_table("media_creative_strategy_sessions")
