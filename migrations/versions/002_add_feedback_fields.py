"""add feedback fields to decision_log

Revision ID: 002
Revises: 001
Create Date: 2026-04-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # decision_log: 运营结构化反馈字段
    op.add_column("decision_log", sa.Column("rating", sa.Integer, comment="1-5星评分"))
    op.add_column("decision_log", sa.Column("feedback_type", sa.String(50), comment="反馈类型"))
    op.add_column("decision_log", sa.Column("reject_reason", sa.String(50), comment="驳回原因"))


def downgrade() -> None:
    op.drop_column("decision_log", "reject_reason")
    op.drop_column("decision_log", "feedback_type")
    op.drop_column("decision_log", "rating")
