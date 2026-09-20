"""create training model transfer tasks

Revision ID: 125_training_model_transfers
Revises: 124_training_deploy_target
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "125_training_model_transfers"
down_revision = "124_training_deploy_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_model_transfers",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("profile", sa.String(length=100), nullable=False),
        sa.Column("source_gateway_id", sa.String(length=80), nullable=False),
        sa.Column("target_gateway_id", sa.String(length=80), nullable=False),
        sa.Column("source_path", sa.String(length=1000), nullable=False),
        sa.Column("source_public_host", sa.String(length=120), nullable=True),
        sa.Column("internal_model_ref", sa.String(length=1000), nullable=True),
        sa.Column("transfer_mode", sa.String(length=30), nullable=True),
        sa.Column("progress", sa.Float(), server_default="0", nullable=False),
        sa.Column("transferred_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("imported_files", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_file", sa.String(length=500), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=128), nullable=True),
        sa.Column("hash_files", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("prepare", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("force", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="21600", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_model_transfers_status", "training_model_transfers", ["status"])
    op.create_index("ix_training_model_transfers_profile", "training_model_transfers", ["profile"])
    op.create_index("ix_training_model_transfers_source_gateway_id", "training_model_transfers", ["source_gateway_id"])
    op.create_index("ix_training_model_transfers_target_gateway_id", "training_model_transfers", ["target_gateway_id"])
    op.create_index("ix_training_model_transfers_created_by", "training_model_transfers", ["created_by"])
    op.create_index(
        "ix_training_model_transfers_fingerprint",
        "training_model_transfers",
        ["profile", "source_gateway_id", "target_gateway_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_training_model_transfers_fingerprint", table_name="training_model_transfers")
    op.drop_index("ix_training_model_transfers_created_by", table_name="training_model_transfers")
    op.drop_index("ix_training_model_transfers_target_gateway_id", table_name="training_model_transfers")
    op.drop_index("ix_training_model_transfers_source_gateway_id", table_name="training_model_transfers")
    op.drop_index("ix_training_model_transfers_profile", table_name="training_model_transfers")
    op.drop_index("ix_training_model_transfers_status", table_name="training_model_transfers")
    op.drop_table("training_model_transfers")
