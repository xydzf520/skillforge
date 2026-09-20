"""add bridge capabilities columns

bridge 启动后上报本机能力（gateway 类型 / 平台 / 可写 skills 目录），
SkillForge 用于 sync skill 时决定写到哪。

Revision ID: 022
Revises: 021
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("openclaw_instances", sa.Column("bridge_gateway_kind", sa.String(20), nullable=True))
    op.add_column("openclaw_instances", sa.Column("bridge_gateway_version", sa.String(50), nullable=True))
    op.add_column("openclaw_instances", sa.Column("bridge_platform", sa.String(30), nullable=True))
    op.add_column("openclaw_instances", sa.Column("bridge_skills_dir", sa.String(500), nullable=True))
    op.add_column("openclaw_instances", sa.Column("bridge_skills_dirs_json", sa.Text(), nullable=True))
    op.add_column("openclaw_instances", sa.Column("bridge_version", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("openclaw_instances", "bridge_version")
    op.drop_column("openclaw_instances", "bridge_skills_dirs_json")
    op.drop_column("openclaw_instances", "bridge_skills_dir")
    op.drop_column("openclaw_instances", "bridge_platform")
    op.drop_column("openclaw_instances", "bridge_gateway_version")
    op.drop_column("openclaw_instances", "bridge_gateway_kind")
