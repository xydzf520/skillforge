"""execution_queue 表 — PostgreSQL 持久化执行任务队列

Revision ID: 046
Revises: 030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "046"
down_revision = "045"


def upgrade():
    op.create_table(
        "execution_queue",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(50), nullable=False),              # skill_run / playbook_run / test_run
        sa.Column("skill_id", sa.String(100)),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("priority", sa.Integer, default=5),                       # 1=最高, 10=最低
        sa.Column("status", sa.String(20), default="pending"),              # pending/claimed/running/completed/failed/cancelled
        sa.Column("queue_name", sa.String(50), default="default"),          # 队列分组
        sa.Column("claimed_by", sa.String(100)),                            # worker ID
        sa.Column("claimed_at", sa.DateTime),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("result", JSONB),
        sa.Column("error_message", sa.Text),
        sa.Column("retry_count", sa.Integer, default=0),
        sa.Column("max_retries", sa.Integer, default=2),
        sa.Column("timeout_seconds", sa.Integer, default=300),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    # 主查询索引：pending 任务按优先级 + 创建时间排序
    op.create_index(
        "ix_exec_queue_status",
        "execution_queue",
        ["status", "priority", "created_at"],
    )
    # 运行中任务按 worker 查询
    op.create_index(
        "ix_exec_queue_claimed",
        "execution_queue",
        ["claimed_by"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade():
    op.drop_index("ix_exec_queue_claimed", table_name="execution_queue")
    op.drop_index("ix_exec_queue_status", table_name="execution_queue")
    op.drop_table("execution_queue")
