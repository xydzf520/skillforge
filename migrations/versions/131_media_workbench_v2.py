"""material workbench 2.0 production and review control plane

Revision ID: 131_media_workbench_v2
Revises: 130_media_workbench
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "131_media_workbench_v2"
down_revision = "130_media_workbench"
branch_labels = None
depends_on = None

JSON_OBJECT = sa.text("'{}'::jsonb")
JSON_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "media_library_assets",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("source_asset_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("media_type", sa.String(30), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("tags_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("rights_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("scan_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_asset_id"], ["project_run_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("department_id", "source_asset_id", name="uq_media_library_asset_department_source"),
    )
    for column in ("department_id", "source_asset_id", "media_type", "sha256", "status", "created_by"):
        op.create_index(f"ix_media_library_assets_{column}", "media_library_assets", [column])
    op.create_index("ix_media_library_assets_department_status", "media_library_assets", ["department_id", "status", "created_at"])

    op.create_table(
        "media_asset_groups",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("purpose", sa.String(500)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("items_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("history_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("created_by", sa.String(50)),
        sa.Column("published_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("department_id", "status", "created_by", "published_by"):
        op.create_index(f"ix_media_asset_groups_{column}", "media_asset_groups", [column])
    op.create_index("ix_media_asset_groups_department_status", "media_asset_groups", ["department_id", "status", "updated_at"])

    op.create_table(
        "media_workflow_definitions",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("purpose", sa.String(500)),
        sa.Column("platform", sa.String(80)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("workflow_kind", sa.String(40), nullable=False),
        sa.Column("h3_mode", sa.String(30), nullable=False),
        sa.Column("bridge_template_id", sa.String(80), nullable=False),
        sa.Column("required_roles_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("config_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("history_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("created_by", sa.String(50)),
        sa.Column("published_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("department_id", "platform", "status", "workflow_kind", "h3_mode", "bridge_template_id", "created_by", "published_by"):
        op.create_index(f"ix_media_workflow_definitions_{column}", "media_workflow_definitions", [column])
    op.create_index("ix_media_workflows_department_status", "media_workflow_definitions", ["department_id", "status", "updated_at"])

    op.create_table(
        "media_production_batches",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("theme", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("not_before_at", sa.DateTime()),
        sa.Column("deadline_at", sa.DateTime()),
        sa.Column("allowed_nodes_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("resource_policy_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("plan_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("counters_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("requested_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
    )
    for column in ("project_id", "project_run_id", "department_id", "status", "not_before_at", "deadline_at", "requested_by"):
        op.create_index(f"ix_media_production_batches_{column}", "media_production_batches", [column])
    op.create_index("ix_media_batches_project_status", "media_production_batches", ["project_id", "status", "created_at"])
    op.create_index("ix_media_batches_schedule", "media_production_batches", ["status", "not_before_at", "deadline_at"])

    op.create_table(
        "media_continuation_chains",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("source_asset_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("preview_asset_id", sa.String(50)),
        sa.Column("requested_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["project_run_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preview_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
    )
    for column in ("project_id", "project_run_id", "department_id", "source_asset_id", "status", "preview_asset_id", "requested_by"):
        op.create_index(f"ix_media_continuation_chains_{column}", "media_continuation_chains", [column])
    op.create_index("ix_media_continuation_project_status", "media_continuation_chains", ["project_id", "status", "created_at"])

    additions = (
        ("production_batch_id", sa.String(50)),
        ("workflow_definition_id", sa.String(50)),
        ("workflow_definition_version", sa.Integer()),
        ("continuation_chain_id", sa.String(50)),
        ("depends_on_job_id", sa.String(50)),
        ("sequence_index", sa.Integer()),
        ("not_before_at", sa.DateTime()),
        ("deadline_at", sa.DateTime()),
        ("review_assignee_id", sa.String(50)),
        ("review_tags_json", postgresql.JSONB()),
    )
    for name, type_ in additions:
        op.add_column("media_generation_jobs", sa.Column(name, type_, nullable=True))
    op.create_foreign_key("fk_media_jobs_batch", "media_generation_jobs", "media_production_batches", ["production_batch_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_media_jobs_workflow", "media_generation_jobs", "media_workflow_definitions", ["workflow_definition_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_media_jobs_chain", "media_generation_jobs", "media_continuation_chains", ["continuation_chain_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_media_jobs_dependency", "media_generation_jobs", "media_generation_jobs", ["depends_on_job_id"], ["id"], ondelete="SET NULL")
    for column in ("production_batch_id", "workflow_definition_id", "continuation_chain_id", "depends_on_job_id", "not_before_at", "deadline_at"):
        op.create_index(f"ix_media_generation_jobs_{column}", "media_generation_jobs", [column])
    op.execute("UPDATE media_generation_jobs SET review_tags_json = '[]'::jsonb WHERE review_tags_json IS NULL")
    op.alter_column("media_generation_jobs", "review_tags_json", nullable=False, server_default=JSON_ARRAY)
    op.create_index("ix_media_generation_jobs_review_assignee_id", "media_generation_jobs", ["review_assignee_id"])

    op.create_table(
        "media_continuation_segments",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("chain_id", sa.String(50), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("prompt_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("locks_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("anchor_asset_id", sa.String(50)),
        sa.Column("media_job_id", sa.String(50)),
        sa.Column("seam_analysis_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chain_id"], ["media_continuation_chains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["anchor_asset_id"], ["project_run_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_job_id"], ["media_generation_jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("chain_id", "segment_index", name="uq_media_continuation_chain_segment"),
    )
    for column in ("chain_id", "status", "anchor_asset_id", "media_job_id"):
        op.create_index(f"ix_media_continuation_segments_{column}", "media_continuation_segments", [column])
    op.create_index("ix_media_continuation_segments_chain_status", "media_continuation_segments", ["chain_id", "status", "segment_index"])

    op.create_table(
        "media_review_annotations",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("media_job_id", sa.String(50), nullable=False),
        sa.Column("department_id", sa.String(50), nullable=False),
        sa.Column("start_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("end_seconds", sa.Float()),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("created_by", sa.String(50)),
        sa.Column("resolved_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_job_id"], ["media_generation_jobs.id"], ondelete="CASCADE"),
    )
    for column in ("media_job_id", "department_id", "category", "severity", "status", "created_by", "resolved_by"):
        op.create_index(f"ix_media_review_annotations_{column}", "media_review_annotations", [column])
    op.create_index("ix_media_review_annotations_job_status", "media_review_annotations", ["media_job_id", "status", "start_seconds"])

    op.create_table(
        "media_workbench_stream_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(42), nullable=False),
        sa.Column("project_run_id", sa.String(50), nullable=False),
        sa.Column("previous_cursor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("changed_events_json", postgresql.JSONB(), server_default=JSON_ARRAY, nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), server_default=JSON_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_run_id"], ["project_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_run_id", "previous_cursor", "fingerprint", name="uq_media_workbench_stream_transition"),
    )
    op.create_index("ix_media_workbench_stream_events_project_id", "media_workbench_stream_events", ["project_id"])
    op.create_index("ix_media_workbench_stream_events_project_run_id", "media_workbench_stream_events", ["project_run_id"])
    op.create_index("ix_media_workbench_stream_run_cursor", "media_workbench_stream_events", ["project_run_id", "id"])


def downgrade() -> None:
    op.drop_table("media_workbench_stream_events")
    op.drop_table("media_review_annotations")
    op.drop_table("media_continuation_segments")
    for column in ("review_assignee_id", "deadline_at", "not_before_at", "depends_on_job_id", "continuation_chain_id", "workflow_definition_id", "production_batch_id"):
        op.drop_index(f"ix_media_generation_jobs_{column}", table_name="media_generation_jobs")
    for constraint in ("fk_media_jobs_dependency", "fk_media_jobs_chain", "fk_media_jobs_workflow", "fk_media_jobs_batch"):
        op.drop_constraint(constraint, "media_generation_jobs", type_="foreignkey")
    for name in ("review_tags_json", "review_assignee_id", "deadline_at", "not_before_at", "sequence_index", "depends_on_job_id", "continuation_chain_id", "workflow_definition_version", "workflow_definition_id", "production_batch_id"):
        op.drop_column("media_generation_jobs", name)
    op.drop_table("media_continuation_chains")
    op.drop_table("media_production_batches")
    op.drop_table("media_workflow_definitions")
    op.drop_table("media_asset_groups")
    op.drop_table("media_library_assets")
