"""drop legacy bridge_token column

Phase 1.7 v1.7.0 起 SkillForge 完全切换到 Ed25519 challenge-signature 认证，
不再需要长期 bridge_token；删除遗留列。

Revision ID: 021
Revises: 020
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 老环境 018 曾添加过 bridge_token，需要兜底删除；新环境若没有该列则跳过
    inspector = sa.inspect(op.get_bind())
    existing_cols = {col["name"] for col in inspector.get_columns("openclaw_instances")}
    if "bridge_token" in existing_cols:
        op.drop_column("openclaw_instances", "bridge_token")


def downgrade() -> None:
    op.add_column(
        "openclaw_instances",
        sa.Column("bridge_token", sa.String(200), nullable=True),
    )
