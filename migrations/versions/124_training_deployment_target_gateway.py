"""add deployment target gateway for training model deployments

Revision ID: 124_training_deploy_target
Revises: 123_exec_queue_next_attempt
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "124_training_deploy_target"
down_revision = "123_exec_queue_next_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_deployments",
        sa.Column("deployment_target_gateway_id", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "model_deployments",
        sa.Column("deployment_runtime_profile", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_model_deployments_deployment_target_gateway_id",
        "model_deployments",
        ["deployment_target_gateway_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_deployments_deployment_target_gateway_id", table_name="model_deployments")
    op.drop_column("model_deployments", "deployment_runtime_profile")
    op.drop_column("model_deployments", "deployment_target_gateway_id")
