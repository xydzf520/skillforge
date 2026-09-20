"""AI自动化与交互改进数据库变更

Revision ID: 009
Revises: 008
Create Date: 2026-04-04

变更:
- execution_runs: 增加 summary 字段 (B5 AI摘要)
- review_comments: 增加 resolved/resolved_by/resolved_at (C5 行内评论)
- notifications: 新建表 (C7 通知中心)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # B5: 执行摘要
    op.add_column("execution_runs", sa.Column("summary", sa.Text(), nullable=True))

    # C5: 行内评论解决标记
    op.add_column("review_comments", sa.Column("resolved", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("review_comments", sa.Column("resolved_by", sa.String(50), nullable=True))
    op.add_column("review_comments", sa.Column("resolved_at", sa.DateTime(), nullable=True))

    # C7: 通知中心
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("read", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("idx_notifications_user", "notifications", ["user_id", "read", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_notifications_user", table_name="notifications")
    op.drop_table("notifications")
    op.drop_column("review_comments", "resolved_at")
    op.drop_column("review_comments", "resolved_by")
    op.drop_column("review_comments", "resolved")
    op.drop_column("execution_runs", "summary")
