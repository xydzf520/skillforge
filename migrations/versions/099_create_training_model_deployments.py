"""create training model deployments

Revision ID: 099
Revises: 098
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "099"
down_revision = "098"


def upgrade():
    op.create_table(
        "model_deployments",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("job_id", sa.String(length=50), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("model_family", sa.String(length=100), nullable=False),
        sa.Column("artifact_id", sa.String(length=120), nullable=False),
        sa.Column("artifact_ref_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("eval_task_id", sa.Integer(), nullable=True),
        sa.Column("target_skill_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
    op.create_index("ix_model_deployments_job_id", "model_deployments", ["job_id"])
    op.create_index("ix_model_deployments_department", "model_deployments", ["department"])
    op.create_index("ix_model_deployments_model_family", "model_deployments", ["model_family"])
    op.create_index("ix_model_deployments_artifact_id", "model_deployments", ["artifact_id"])
    op.create_index("ix_model_deployments_eval_task_id", "model_deployments", ["eval_task_id"])
    op.create_index("ix_model_deployments_status", "model_deployments", ["status"])
    op.create_index("ix_model_deployments_requested_by", "model_deployments", ["requested_by"])


def downgrade():
    op.drop_index("ix_model_deployments_requested_by", table_name="model_deployments")
    op.drop_index("ix_model_deployments_status", table_name="model_deployments")
    op.drop_index("ix_model_deployments_eval_task_id", table_name="model_deployments")
    op.drop_index("ix_model_deployments_artifact_id", table_name="model_deployments")
    op.drop_index("ix_model_deployments_model_family", table_name="model_deployments")
    op.drop_index("ix_model_deployments_department", table_name="model_deployments")
    op.drop_index("ix_model_deployments_job_id", table_name="model_deployments")
    op.drop_table("model_deployments")
