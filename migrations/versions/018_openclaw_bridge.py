"""openclaw bridge mode (Phase 1.7 AIClaw 工作区)

加 AIClaw bridge 认证相关字段，
支持 enrollment token + device pubkey 的反向连接模型。

Revision ID: 018
Revises: 017
Create Date: 2026-04-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "openclaw_instances",
        sa.Column("connection_mode", sa.String(20), server_default="bridge", nullable=False),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("bridge_connected_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("enrollment_token_hash", sa.String(128), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("enrollment_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("enrollment_consumed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("device_pubkey", sa.Text(), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("bridge_fingerprint", sa.String(200), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("pending_rotation", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("rotation_token_hash", sa.String(128), nullable=True),
    )
    op.add_column(
        "openclaw_instances",
        sa.Column("rotation_expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("openclaw_instances", "rotation_expires_at")
    op.drop_column("openclaw_instances", "rotation_token_hash")
    op.drop_column("openclaw_instances", "pending_rotation")
    op.drop_column("openclaw_instances", "bridge_fingerprint")
    op.drop_column("openclaw_instances", "device_pubkey")
    op.drop_column("openclaw_instances", "enrollment_consumed_at")
    op.drop_column("openclaw_instances", "enrollment_expires_at")
    op.drop_column("openclaw_instances", "enrollment_token_hash")
    op.drop_column("openclaw_instances", "bridge_connected_at")
    op.drop_column("openclaw_instances", "connection_mode")
