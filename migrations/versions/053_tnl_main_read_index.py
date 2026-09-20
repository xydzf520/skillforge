"""task_nodes_light 主读路径索引

Revision ID: 053
Revises: 052

M2：_get_tree_from_tnl 查询形状是 (department_id, status, started_at DESC)
加 source_run_id IS NOT NULL 的部分索引，更贴合主查询且体积更小。

[codex-2026-04-14] task_nodes_light 是 Phase 1.5 引入的主读表，升级时可能
已经有写流量。用 CONCURRENTLY 避免阻塞；需要 autocommit_block 脱事务。
"""

from alembic import op
import sqlalchemy as sa

revision = "053"
down_revision = "052"


def upgrade():
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tnl_main_read "
            "ON task_nodes_light (department_id, status, started_at DESC) "
            "WHERE source_run_id IS NOT NULL"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tnl_main_read")
