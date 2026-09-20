"""市场分层可见性 + 认证流程 + 复用指标

Revision ID: 044
Revises: 030
"""
from alembic import op
import sqlalchemy as sa

revision = "044"
down_revision = "043"


def upgrade():
    # 在 skills 表添加市场状态
    op.add_column(
        "skills",
        sa.Column("market_status", sa.String(30), server_default="private", nullable=False),
    )

    # 市场认证记录
    op.create_table(
        "market_certifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(100), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("reviewer_id", sa.String(50)),
        sa.Column("review_notes", sa.Text),
        sa.Column("certified_at", sa.DateTime),
        sa.Column("submitted_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("submitted_by", sa.String(50), nullable=False),
    )
    op.create_index("ix_market_certifications_skill", "market_certifications", ["skill_id"])
    op.create_index("ix_market_certifications_status", "market_certifications", ["status"])


def downgrade():
    op.drop_table("market_certifications")
    op.drop_column("skills", "market_status")
