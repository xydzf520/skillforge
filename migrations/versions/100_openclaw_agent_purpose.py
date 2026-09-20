"""add openclaw agent purpose

Revision ID: 100
Revises: 099
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "100"
down_revision = "099"


JSONB = postgresql.JSONB(astext_type=sa.Text())


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return bool(_inspector().has_table(table_name))


def _columns(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {col["name"] for col in _inspector().get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {idx["name"] for idx in _inspector().get_indexes(table_name)}


def _unique_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {item["name"] for item in _inspector().get_unique_constraints(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], *, unique: bool = False, **kwargs) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=unique, **kwargs)


def _create_unique_if_missing(constraint_name: str, table_name: str, columns: list[str]) -> None:
    if constraint_name not in _unique_names(table_name):
        op.create_unique_constraint(constraint_name, table_name, columns)


def _ensure_097_to_099_baseline() -> None:
    """Repair production DBs whose legacy 099 predates current 097-099 files."""
    if not _has_table("user_skill_ui_preferences"):
        op.create_table(
            "user_skill_ui_preferences",
            sa.Column("id", sa.String(length=50), nullable=False),
            sa.Column("user_id", sa.String(length=50), nullable=False),
            sa.Column("skill_id", sa.String(length=50), nullable=False),
            sa.Column("surface", sa.String(length=30), nullable=False),
            sa.Column("base_skill_commit", sa.String(length=40), nullable=True),
            sa.Column("overlay_schema_version", sa.String(length=30), server_default="skill-ui/v1", nullable=False),
            sa.Column("overlay_json", JSONB, nullable=False),
            sa.Column("generated_by", sa.String(length=20), server_default="manual", nullable=False),
            sa.Column("prompt_summary", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_unique_if_missing(
        "uq_user_skill_ui_pref_version",
        "user_skill_ui_preferences",
        ["user_id", "skill_id", "surface", "version"],
    )
    _create_index_if_missing("ix_user_skill_ui_preferences_user_id", "user_skill_ui_preferences", ["user_id"])
    _create_index_if_missing("ix_user_skill_ui_preferences_skill_id", "user_skill_ui_preferences", ["skill_id"])
    _create_index_if_missing("ix_user_skill_ui_preferences_enabled", "user_skill_ui_preferences", ["enabled"])
    _create_index_if_missing(
        "uq_user_skill_ui_pref_active_enabled",
        "user_skill_ui_preferences",
        ["user_id", "skill_id", "surface"],
        unique=True,
        postgresql_where=sa.text("enabled = true"),
        sqlite_where=sa.text("enabled = 1"),
    )

    if _has_table("skill_submissions"):
        _add_column_if_missing("skill_submissions", sa.Column("ui_pref_id", sa.String(length=50), nullable=True))
        _add_column_if_missing("skill_submissions", sa.Column("ui_pref_version", sa.Integer(), nullable=True))
        _add_column_if_missing("skill_submissions", sa.Column("ui_surface", sa.String(length=30), nullable=True))
        _add_column_if_missing("skill_submissions", sa.Column("base_skill_commit", sa.String(length=40), nullable=True))
        _add_column_if_missing("skill_submissions", sa.Column("merged_ui_schema_hash", sa.String(length=80), nullable=True))
        _add_column_if_missing("skill_submissions", sa.Column("ui_snapshot_json", JSONB, nullable=True))
        _create_index_if_missing("ix_skill_submissions_ui_pref_id", "skill_submissions", ["ui_pref_id"])

    if not _has_table("training_jobs"):
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
            sa.Column("spec_json", JSONB, nullable=True),
            sa.Column("gateway_payload_json", JSONB, nullable=True),
            sa.Column("approval_required", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("approved_by", sa.String(length=50), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_by", sa.String(length=50), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_training_jobs_department", "training_jobs", ["department"])
    _create_index_if_missing("ix_training_jobs_created_by", "training_jobs", ["created_by"])
    _create_index_if_missing("ix_training_jobs_status", "training_jobs", ["status"])
    _create_index_if_missing("ix_training_jobs_target_skill_id", "training_jobs", ["target_skill_id"])
    _create_index_if_missing("ix_training_jobs_target_gateway_id", "training_jobs", ["target_gateway_id"])

    if not _has_table("training_job_tasks"):
        op.create_table(
            "training_job_tasks",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("job_id", sa.String(length=50), nullable=False),
            sa.Column("gateway_id", sa.String(length=50), nullable=True),
            sa.Column("worker_id", sa.String(length=100), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("progress", sa.Float(), nullable=False),
            sa.Column("metrics_json", JSONB, nullable=True),
            sa.Column("logs_url", sa.String(length=500), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_training_job_tasks_job_id", "training_job_tasks", ["job_id"])
    _create_index_if_missing("ix_training_job_tasks_gateway_id", "training_job_tasks", ["gateway_id"])
    _create_index_if_missing("ix_training_job_tasks_status", "training_job_tasks", ["status"])

    if not _has_table("model_deployments"):
        op.create_table(
            "model_deployments",
            sa.Column("id", sa.String(length=50), nullable=False),
            sa.Column("job_id", sa.String(length=50), nullable=False),
            sa.Column("department", sa.String(length=50), nullable=False),
            sa.Column("model_family", sa.String(length=100), nullable=False),
            sa.Column("artifact_id", sa.String(length=120), nullable=False),
            sa.Column("artifact_ref_json", JSONB, nullable=True),
            sa.Column("eval_task_id", sa.Integer(), nullable=True),
            sa.Column("target_skill_ids_json", JSONB, nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("rollout_percent", sa.Integer(), server_default="0", nullable=False),
            sa.Column("rollback_to", sa.String(length=50), nullable=True),
            sa.Column("requested_by", sa.String(length=50), nullable=False),
            sa.Column("request_reason", sa.Text(), nullable=True),
            sa.Column("approved_by", sa.String(length=50), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_model_deployments_job_id", "model_deployments", ["job_id"])
    _create_index_if_missing("ix_model_deployments_department", "model_deployments", ["department"])
    _create_index_if_missing("ix_model_deployments_model_family", "model_deployments", ["model_family"])
    _create_index_if_missing("ix_model_deployments_artifact_id", "model_deployments", ["artifact_id"])
    _create_index_if_missing("ix_model_deployments_eval_task_id", "model_deployments", ["eval_task_id"])
    _create_index_if_missing("ix_model_deployments_status", "model_deployments", ["status"])
    _create_index_if_missing("ix_model_deployments_requested_by", "model_deployments", ["requested_by"])


def upgrade():
    _ensure_097_to_099_baseline()
    _add_column_if_missing(
        "openclaw_instances",
        sa.Column("agent_purpose", sa.String(length=30), server_default="skill_runtime", nullable=False),
    )
    _create_index_if_missing(
        "ix_openclaw_instances_agent_purpose",
        "openclaw_instances",
        ["agent_purpose", "department", "is_active"],
    )


def downgrade():
    op.drop_index("ix_openclaw_instances_agent_purpose", table_name="openclaw_instances")
    op.drop_column("openclaw_instances", "agent_purpose")
