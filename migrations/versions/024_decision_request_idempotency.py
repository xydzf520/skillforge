"""decision_requests source 唯一约束 (幂等创建)

Revision ID: 024
Revises: 023
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 同一个 (source_type, source_id, kind, skill_id) 不能重复创建。
    # source_id 通常就是 SkillForge 的 run_id, 这样可以让 Skill 重试时幂等返回。
    # 在加约束前先把可能存在的重复行留存最新一份, 删掉旧的, 避免 migration 失败。
    op.execute(
        """
        DELETE FROM decision_requests a
        USING decision_requests b
        WHERE a.created_at < b.created_at
          AND a.source_type = b.source_type
          AND a.source_id = b.source_id
          AND a.kind = b.kind
          AND a.skill_id = b.skill_id
        """
    )
    op.create_unique_constraint(
        "uq_dr_source_kind_skill",
        "decision_requests",
        ["source_type", "source_id", "kind", "skill_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_dr_source_kind_skill", "decision_requests", type_="unique")
