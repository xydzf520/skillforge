"""intelligence learning loop

Revision ID: 107
Revises: 106
Create Date: 2026-06-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "107"
down_revision = "106"
branch_labels = None
depends_on = None


def _jsonb(default: str):
    return postgresql.JSONB(astext_type=sa.Text()), sa.text(default)


def upgrade() -> None:
    jsonb_obj, obj_default = _jsonb("'{}'::jsonb")
    jsonb_arr, arr_default = _jsonb("'[]'::jsonb")

    op.create_table(
        "learning_events",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=True),
        sa.Column("run_id", sa.String(length=100), nullable=True),
        sa.Column("modality", sa.String(length=20), server_default="text", nullable=False),
        sa.Column("payload_ref", sa.String(length=240), nullable=True),
        sa.Column("redacted_summary", sa.Text(), nullable=True),
        sa.Column("sensitivity_level", sa.String(length=20), server_default="internal", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="captured", nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("policy_result_json", jsonb_obj, server_default=obj_default, nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_type", "source_type", "source_id", name="uq_learning_event_source"),
    )
    op.create_index("ix_learning_events_event_type", "learning_events", ["event_type"])
    op.create_index("ix_learning_events_source_type", "learning_events", ["source_type"])
    op.create_index("ix_learning_events_source_id", "learning_events", ["source_id"])
    op.create_index("ix_learning_events_source_hash", "learning_events", ["source_hash"])
    op.create_index("ix_learning_events_department", "learning_events", ["department"])
    op.create_index("ix_learning_events_org_unit_id", "learning_events", ["org_unit_id"])
    op.create_index("ix_learning_events_skill_id", "learning_events", ["skill_id"])
    op.create_index("ix_learning_events_user_id", "learning_events", ["user_id"])
    op.create_index("ix_learning_events_run_id", "learning_events", ["run_id"])
    op.create_index("ix_learning_events_modality", "learning_events", ["modality"])
    op.create_index("ix_learning_events_payload_ref", "learning_events", ["payload_ref"])
    op.create_index("ix_learning_events_sensitivity_level", "learning_events", ["sensitivity_level"])
    op.create_index("ix_learning_events_status", "learning_events", ["status"])
    op.create_index("ix_learning_events_created_at", "learning_events", ["created_at"])
    op.create_index("ix_learning_events_scope_created", "learning_events", ["department", "org_unit_id", "created_at"])
    op.create_index("ix_learning_events_skill_created", "learning_events", ["skill_id", "created_at"])

    op.create_table(
        "learning_artifacts",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("event_id", sa.String(length=50), nullable=False),
        sa.Column("artifact_kind", sa.String(length=50), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("labels_json", jsonb_arr, server_default=arr_default, nullable=False),
        sa.Column("quality_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("sensitivity_level", sa.String(length=20), server_default="internal", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="ready", nullable=False),
        sa.Column("review_status", sa.String(length=30), nullable=True),
        sa.Column("sink_type", sa.String(length=40), nullable=True),
        sa.Column("sink_id", sa.String(length=120), nullable=True),
        sa.Column("policy_result_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "artifact_kind", "artifact_hash", name="uq_learning_artifact_event_hash"),
    )
    for col in ["event_id", "artifact_kind", "artifact_hash", "target_type", "target_id", "department", "org_unit_id", "skill_id", "run_id", "sensitivity_level", "status", "review_status", "sink_type", "sink_id", "created_at"]:
        op.create_index(f"ix_learning_artifacts_{col}", "learning_artifacts", [col])
    op.create_index("ix_learning_artifacts_kind_status", "learning_artifacts", ["artifact_kind", "status"])
    op.create_index("ix_learning_artifacts_scope_created", "learning_artifacts", ["department", "org_unit_id", "created_at"])

    op.create_table(
        "learning_flow_edges",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("from_type", sa.String(length=50), nullable=False),
        sa.Column("from_id", sa.String(length=120), nullable=False),
        sa.Column("to_type", sa.String(length=50), nullable=False),
        sa.Column("to_id", sa.String(length=120), nullable=False),
        sa.Column("relation", sa.String(length=50), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.String(length=100), nullable=True),
        sa.Column("weight", sa.Float(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_type", "from_id", "to_type", "to_id", "relation", name="uq_learning_flow_edge"),
    )
    for col in ["from_type", "from_id", "to_type", "to_id", "relation", "department", "org_unit_id", "skill_id", "run_id", "status", "created_at"]:
        op.create_index(f"ix_learning_flow_edges_{col}", "learning_flow_edges", [col])
    op.create_index("ix_learning_flow_edges_scope_created", "learning_flow_edges", ["department", "org_unit_id", "created_at"])

    op.create_table(
        "learning_ingestion_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=50), nullable=False),
        sa.Column("event_id", sa.String(length=50), nullable=True),
        sa.Column("artifact_id", sa.String(length=50), nullable=True),
        sa.Column("sink_type", sa.String(length=40), nullable=False),
        sa.Column("sink_id", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("policy_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    for col in ["job_id", "event_id", "artifact_id", "sink_type", "sink_id", "department", "org_unit_id", "skill_id", "status", "created_by", "created_at"]:
        op.create_index(f"ix_learning_ingestion_jobs_{col}", "learning_ingestion_jobs", [col])
    op.create_index("ix_learning_ingestion_jobs_status_created", "learning_ingestion_jobs", ["status", "created_at"])

    op.create_table(
        "learning_automation_runs",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("trigger_type", sa.String(length=30), server_default="scheduled", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="running", nullable=False),
        sa.Column("days", sa.Integer(), server_default="7", nullable=False),
        sa.Column("limit", sa.Integer(), server_default="300", nullable=False),
        sa.Column("materialize", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("captured_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ["trigger_type", "status", "created_by", "started_at", "finished_at", "created_at"]:
        op.create_index(f"ix_learning_automation_runs_{col}", "learning_automation_runs", [col])
    op.create_index("ix_learning_automation_runs_status_started", "learning_automation_runs", ["status", "started_at"])
    op.create_index("ix_learning_automation_runs_trigger_started", "learning_automation_runs", ["trigger_type", "started_at"])

    op.create_table(
        "learning_governance_tasks",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("candidate_id", sa.String(length=50), nullable=False),
        sa.Column("queue_id", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("assignee", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("priority_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id"),
    )
    for col in ["candidate_id", "queue_id", "target_type", "target_id", "department", "org_unit_id", "assignee", "status", "created_by", "created_at", "completed_at"]:
        op.create_index(f"ix_learning_governance_tasks_{col}", "learning_governance_tasks", [col])
    op.create_index("ix_learning_governance_tasks_queue_status", "learning_governance_tasks", ["queue_id", "status", "created_at"])
    op.create_index("ix_learning_governance_tasks_scope_created", "learning_governance_tasks", ["department", "org_unit_id", "created_at"])

    op.create_table(
        "improvement_candidates",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("source_artifact_id", sa.String(length=50), nullable=True),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("proposal", sa.Text(), nullable=False),
        sa.Column("evidence_event_ids_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("expected_impact_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("priority_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="open", nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("todo_id", sa.String(length=50), nullable=True),
        sa.Column("git_commit", sa.String(length=80), nullable=True),
        sa.Column("external_ref_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("updated_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_artifact_id"),
    )
    for col in ["source_artifact_id", "target_type", "target_id", "department", "org_unit_id", "skill_id", "run_id", "risk_level", "status", "review_id", "todo_id", "git_commit", "created_by", "created_at"]:
        op.create_index(f"ix_improvement_candidates_{col}", "improvement_candidates", [col])
    op.create_index("ix_improvement_candidates_target_status", "improvement_candidates", ["target_type", "target_id", "status"])
    op.create_index("ix_improvement_candidates_scope_created", "improvement_candidates", ["department", "org_unit_id", "created_at"])


def downgrade() -> None:
    op.drop_table("improvement_candidates")
    op.drop_table("learning_governance_tasks")
    op.drop_table("learning_automation_runs")
    op.drop_table("learning_ingestion_jobs")
    op.drop_table("learning_flow_edges")
    op.drop_table("learning_artifacts")
    op.drop_table("learning_events")
