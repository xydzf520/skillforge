"""ensure codex production merge tables

Revision ID: 117
Revises: 116
Create Date: 2026-06-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "117"
down_revision = "116"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
OBJ_DEFAULT = sa.text("'{}'::jsonb")


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


def _has_unique_on_columns(table_name: str, columns: list[str]) -> bool:
    expected = tuple(columns)
    return any(tuple(item.get("column_names") or []) == expected for item in _inspector().get_unique_constraints(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], *, unique: bool = False, **kwargs) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=unique, **kwargs)


def _create_unique_if_missing(constraint_name: str, table_name: str, columns: list[str]) -> None:
    if constraint_name not in _unique_names(table_name) and not _has_unique_on_columns(table_name, columns):
        op.create_unique_constraint(constraint_name, table_name, columns)


def _ensure_execution_artifacts() -> None:
    table_name = "execution_artifacts"
    if not _has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(50), nullable=False),
            sa.Column("skill_id", sa.String(100), nullable=False),
            sa.Column("decision_log_id", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(50), nullable=False),
            sa.Column("schema_name", sa.String(100), nullable=True),
            sa.Column("storage_backend", sa.String(30), nullable=False, server_default="local"),
            sa.Column("storage_path", sa.String(1000), nullable=False),
            sa.Column("media_type", sa.String(100), nullable=False, server_default="application/json+gzip"),
            sa.Column("encoding", sa.String(30), nullable=False, server_default="gzip"),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("uncompressed_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("summary_json", JSONB, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        _add_column_if_missing(table_name, sa.Column("decision_log_id", sa.Integer(), nullable=True))
        _add_column_if_missing(table_name, sa.Column("schema_name", sa.String(100), nullable=True))
        _add_column_if_missing(table_name, sa.Column("storage_backend", sa.String(30), nullable=False, server_default="local"))
        _add_column_if_missing(table_name, sa.Column("storage_path", sa.String(1000), nullable=True))
        _add_column_if_missing(table_name, sa.Column("media_type", sa.String(100), nullable=False, server_default="application/json+gzip"))
        _add_column_if_missing(table_name, sa.Column("encoding", sa.String(30), nullable=False, server_default="gzip"))
        _add_column_if_missing(table_name, sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"))
        _add_column_if_missing(table_name, sa.Column("uncompressed_size_bytes", sa.BigInteger(), nullable=False, server_default="0"))
        _add_column_if_missing(table_name, sa.Column("sha256", sa.String(64), nullable=True))
        _add_column_if_missing(table_name, sa.Column("summary_json", JSONB, nullable=True))
        _add_column_if_missing(table_name, sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    _create_unique_if_missing("uq_execution_artifacts_run_kind_sha", table_name, ["run_id", "kind", "sha256"])
    _create_index_if_missing("ix_execution_artifacts_run_id", table_name, ["run_id"])
    _create_index_if_missing("ix_execution_artifacts_skill_id", table_name, ["skill_id"])
    _create_index_if_missing("ix_execution_artifacts_decision_log_id", table_name, ["decision_log_id"])
    _create_index_if_missing("ix_execution_artifacts_schema_name", table_name, ["schema_name"])
    _create_index_if_missing("ix_execution_artifacts_skill_kind_created", table_name, ["skill_id", "kind", "created_at"])


def _ensure_codex_output_previews() -> None:
    table_name = "codex_output_previews"
    if not _has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=50), nullable=False),
            sa.Column(
                "cli_session_id",
                sa.String(length=64),
                sa.ForeignKey("codex_cli_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("source_name", sa.String(length=255), nullable=True),
            sa.Column("content_type", sa.String(length=30), nullable=False, server_default="json"),
            sa.Column("content_text", sa.Text(), nullable=False),
            sa.Column("summary_json", JSONB, nullable=False, server_default=OBJ_DEFAULT),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing("ix_codex_output_previews_user_id", table_name, ["user_id"])
    _create_index_if_missing("ix_codex_output_previews_cli_session_id", table_name, ["cli_session_id"])
    _create_index_if_missing("ix_codex_output_previews_expires_at", table_name, ["expires_at"])


def _ensure_execution_run_logs() -> None:
    table_name = "execution_run_logs"
    if not _has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(length=50), nullable=False),
            sa.Column("skill_id", sa.String(length=100), nullable=False),
            sa.Column("stream", sa.String(length=20), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=True),
            sa.Column("content_tail", sa.Text(), nullable=False),
            sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        )
    _create_index_if_missing("ix_execution_run_logs_run_id", table_name, ["run_id"])
    _create_index_if_missing("ix_execution_run_logs_skill_id", table_name, ["skill_id"])
    _create_index_if_missing("ix_execution_run_logs_run_created", table_name, ["run_id", "created_at"])


def _ensure_market_ratings() -> None:
    table_name = "market_ratings"
    if not _has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("skill_id", sa.String(length=100), nullable=False),
            sa.Column("user_id", sa.String(length=50), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        )
    _create_unique_if_missing("uq_market_ratings_skill_user", table_name, ["skill_id", "user_id"])
    _create_index_if_missing("ix_market_ratings_skill_id", table_name, ["skill_id"])
    _create_index_if_missing("ix_market_ratings_user_id", table_name, ["user_id"])


def _ensure_user_skill_ui_preferences() -> None:
    table_name = "user_skill_ui_preferences"
    if not _has_table(table_name):
        op.create_table(
            table_name,
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
    _create_unique_if_missing("uq_user_skill_ui_pref_version", table_name, ["user_id", "skill_id", "surface", "version"])
    _create_index_if_missing("ix_user_skill_ui_preferences_user_id", table_name, ["user_id"])
    _create_index_if_missing("ix_user_skill_ui_preferences_skill_id", table_name, ["skill_id"])
    _create_index_if_missing("ix_user_skill_ui_preferences_enabled", table_name, ["enabled"])
    _create_index_if_missing(
        "uq_user_skill_ui_pref_active_enabled",
        table_name,
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


def _ensure_training_jobs() -> None:
    table_name = "training_jobs"
    if not _has_table(table_name):
        op.create_table(
            table_name,
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
    _create_index_if_missing("ix_training_jobs_department", table_name, ["department"])
    _create_index_if_missing("ix_training_jobs_created_by", table_name, ["created_by"])
    _create_index_if_missing("ix_training_jobs_status", table_name, ["status"])
    _create_index_if_missing("ix_training_jobs_target_skill_id", table_name, ["target_skill_id"])
    _create_index_if_missing("ix_training_jobs_target_gateway_id", table_name, ["target_gateway_id"])

    task_table = "training_job_tasks"
    if not _has_table(task_table):
        op.create_table(
            task_table,
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
    _create_index_if_missing("ix_training_job_tasks_job_id", task_table, ["job_id"])
    _create_index_if_missing("ix_training_job_tasks_gateway_id", task_table, ["gateway_id"])
    _create_index_if_missing("ix_training_job_tasks_status", task_table, ["status"])


def _ensure_model_deployments() -> None:
    table_name = "model_deployments"
    if not _has_table(table_name):
        op.create_table(
            table_name,
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
    _add_column_if_missing(table_name, sa.Column("rejected_by", sa.String(length=50), nullable=True))
    _add_column_if_missing(table_name, sa.Column("rejected_at", sa.DateTime(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("reject_reason", sa.Text(), nullable=True))
    _create_index_if_missing("ix_model_deployments_job_id", table_name, ["job_id"])
    _create_index_if_missing("ix_model_deployments_department", table_name, ["department"])
    _create_index_if_missing("ix_model_deployments_model_family", table_name, ["model_family"])
    _create_index_if_missing("ix_model_deployments_artifact_id", table_name, ["artifact_id"])
    _create_index_if_missing("ix_model_deployments_eval_task_id", table_name, ["eval_task_id"])
    _create_index_if_missing("ix_model_deployments_status", table_name, ["status"])
    _create_index_if_missing("ix_model_deployments_requested_by", table_name, ["requested_by"])


def upgrade() -> None:
    _ensure_user_skill_ui_preferences()
    _ensure_training_jobs()
    _ensure_model_deployments()
    _ensure_execution_artifacts()
    _ensure_codex_output_previews()
    _ensure_execution_run_logs()
    _ensure_market_ratings()


def downgrade() -> None:
    # Idempotent production merge repair. It may create tables originally owned by
    # revisions 097-099 or production-only migrations, so downgrade is intentionally
    # non-destructive.
    pass
