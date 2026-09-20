"""tasktree_repair_queue 双写补偿队列

Revision ID: 052
Revises: 051
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "052"
down_revision = "051"


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "tasktree_repair_queue",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=False),
        sa.Column("operation", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # 扫描：status + next_attempt_at（供 repair_worker 每 30s 取待执行）
    op.create_index(
        "ix_trq_scan",
        "tasktree_repair_queue",
        ["status", "next_attempt_at"],
    )
    # 幂等查找：source_type + source_ref（供 drift_scan/writer 去重入队）
    op.create_index(
        "ix_trq_source",
        "tasktree_repair_queue",
        ["source_type", "source_ref"],
    )
    # [codex-2026-04-14] 活跃任务唯一约束：同 (source_type, source_ref, operation) 在
    # pending/retrying 状态下最多 1 条，防止 enqueue_repair/drift_scan 并发堆等价任务。
    # 一旦状态变成 done/failed，就释放了约束，允许将来再入队（节点又丢了的场景）。
    op.execute(
        "CREATE UNIQUE INDEX ix_trq_active_unique "
        "ON tasktree_repair_queue (source_type, source_ref, operation) "
        "WHERE status IN ('pending', 'retrying')"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_trq_active_unique")
    op.drop_index("ix_trq_source", table_name="tasktree_repair_queue")
    op.drop_index("ix_trq_scan", table_name="tasktree_repair_queue")
    op.drop_table("tasktree_repair_queue")
