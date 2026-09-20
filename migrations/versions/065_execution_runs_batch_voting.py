"""execution_runs 加多次运行投票字段（v2.9.1 M2）

- batch_id: 同一批并发运行共享
- is_winner: 用户选中的冠军结果
- vote_count: 获得投票数（预留给多人多轮投票）

Revision ID: 065
Revises: 064
"""
from alembic import op
import sqlalchemy as sa


revision = "065"
down_revision = "064"


def upgrade():
    op.add_column(
        "execution_runs",
        sa.Column("batch_id", sa.String(50), nullable=True),
    )
    op.add_column(
        "execution_runs",
        sa.Column("is_winner", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "execution_runs",
        sa.Column("vote_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_execution_runs_batch_id", "execution_runs", ["batch_id"])


def downgrade():
    op.drop_index("ix_execution_runs_batch_id", table_name="execution_runs")
    op.drop_column("execution_runs", "vote_count")
    op.drop_column("execution_runs", "is_winner")
    op.drop_column("execution_runs", "batch_id")
