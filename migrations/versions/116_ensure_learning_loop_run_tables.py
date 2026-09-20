"""ensure learning loop run and governance tables

Revision ID: 116
Revises: 115
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "116"
down_revision = "115"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
OBJ_DEFAULT = sa.text("'{}'::jsonb")
ARR_DEFAULT = sa.text("'[]'::jsonb")


def _has_table(table_name: str) -> bool:
    return bool(sa.inspect(op.get_bind()).has_table(table_name))


def _index_names(table_name: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _unique_names(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints(table_name)}


def _has_unique_on_columns(table_name: str, columns: list[str]) -> bool:
    expected = tuple(columns)
    return any(tuple(item.get("column_names") or []) == expected for item in sa.inspect(op.get_bind()).get_unique_constraints(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns)


def _create_unique_if_missing(constraint_name: str, table_name: str, columns: list[str]) -> None:
    if constraint_name not in _unique_names(table_name) and not _has_unique_on_columns(table_name, columns):
        op.create_unique_constraint(constraint_name, table_name, columns)


def _ensure_learning_automation_runs() -> None:
    table_name = "learning_automation_runs"
    if not _has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=50), nullable=False),
            sa.Column("trigger_type", sa.String(length=30), server_default="scheduled", nullable=False),
            sa.Column("status", sa.String(length=30), server_default="running", nullable=False),
            sa.Column("days", sa.Integer(), server_default="7", nullable=False),
            sa.Column("limit", sa.Integer(), server_default="300", nullable=False),
            sa.Column("materialize", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("captured_json", JSONB, server_default=OBJ_DEFAULT, nullable=False),
            sa.Column("result_json", JSONB, server_default=OBJ_DEFAULT, nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("metadata_json", JSONB, server_default=OBJ_DEFAULT, nullable=False),
            sa.Column("created_by", sa.String(length=50), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    for col in ["trigger_type", "status", "created_by", "started_at", "finished_at", "created_at"]:
        _create_index_if_missing(f"ix_learning_automation_runs_{col}", table_name, [col])
    _create_index_if_missing("ix_learning_automation_runs_status_started", table_name, ["status", "started_at"])
    _create_index_if_missing("ix_learning_automation_runs_trigger_started", table_name, ["trigger_type", "started_at"])


def _ensure_learning_governance_tasks() -> None:
    table_name = "learning_governance_tasks"
    if not _has_table(table_name):
        op.create_table(
            table_name,
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
            sa.Column("payload_json", JSONB, server_default=OBJ_DEFAULT, nullable=False),
            sa.Column("created_by", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("candidate_id", name="uq_learning_governance_tasks_candidate_id"),
        )
    else:
        _create_unique_if_missing("uq_learning_governance_tasks_candidate_id", table_name, ["candidate_id"])
    for col in [
        "candidate_id",
        "queue_id",
        "target_type",
        "target_id",
        "department",
        "org_unit_id",
        "assignee",
        "status",
        "created_by",
        "created_at",
        "completed_at",
    ]:
        _create_index_if_missing(f"ix_learning_governance_tasks_{col}", table_name, [col])
    _create_index_if_missing("ix_learning_governance_tasks_queue_status", table_name, ["queue_id", "status", "created_at"])
    _create_index_if_missing("ix_learning_governance_tasks_scope_created", table_name, ["department", "org_unit_id", "created_at"])


def _ensure_improvement_candidates() -> None:
    table_name = "improvement_candidates"
    if not _has_table(table_name):
        op.create_table(
            table_name,
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
            sa.Column("evidence_event_ids_json", JSONB, server_default=ARR_DEFAULT, nullable=False),
            sa.Column("risk_level", sa.String(length=20), nullable=True),
            sa.Column("expected_impact_json", JSONB, server_default=OBJ_DEFAULT, nullable=False),
            sa.Column("priority_score", sa.Float(), server_default="0", nullable=False),
            sa.Column("status", sa.String(length=30), server_default="open", nullable=False),
            sa.Column("review_id", sa.Integer(), nullable=True),
            sa.Column("todo_id", sa.String(length=50), nullable=True),
            sa.Column("git_commit", sa.String(length=80), nullable=True),
            sa.Column("external_ref_json", JSONB, server_default=OBJ_DEFAULT, nullable=False),
            sa.Column("created_by", sa.String(length=50), nullable=True),
            sa.Column("updated_by", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_artifact_id", name="uq_improvement_candidates_source_artifact_id"),
        )
    else:
        _create_unique_if_missing("uq_improvement_candidates_source_artifact_id", table_name, ["source_artifact_id"])
    for col in [
        "source_artifact_id",
        "target_type",
        "target_id",
        "department",
        "org_unit_id",
        "skill_id",
        "run_id",
        "risk_level",
        "status",
        "review_id",
        "todo_id",
        "git_commit",
        "created_by",
        "created_at",
    ]:
        _create_index_if_missing(f"ix_improvement_candidates_{col}", table_name, [col])
    _create_index_if_missing("ix_improvement_candidates_target_status", table_name, ["target_type", "target_id", "status"])
    _create_index_if_missing("ix_improvement_candidates_scope_created", table_name, ["department", "org_unit_id", "created_at"])


def upgrade() -> None:
    _ensure_learning_automation_runs()
    _ensure_learning_governance_tasks()
    _ensure_improvement_candidates()


def downgrade() -> None:
    # Corrective repair only: these tables semantically belong to revision 107.
    # Downgrading 116 must not drop valid 107 objects on clean environments.
    pass
