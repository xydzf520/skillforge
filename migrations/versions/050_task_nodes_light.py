"""task_nodes_light 轻量投影表

Revision ID: 050
Revises: 049
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "050"
down_revision = "049"


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "task_nodes_light",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("root_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task_nodes_light.id")),
        sa.Column("source_run_id", sa.String(100)),
        sa.Column("source_instance_id", sa.String(100)),
        sa.Column("department_id", sa.String(100), nullable=False),
        sa.Column("node_type", sa.String(30), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sort_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("last_heartbeat_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_index(
        "ix_tnl_tree",
        "task_nodes_light",
        ["department_id", "root_id", "parent_id", "sort_key"],
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tnl_updated ON task_nodes_light (department_id, updated_at DESC)"
    )
    op.create_index("ix_tnl_run", "task_nodes_light", ["source_run_id"])
    op.create_index(
        "ix_tnl_active",
        "task_nodes_light",
        ["department_id", "status"],
        postgresql_where=sa.text("status IN ('queued', 'running', 'stale')"),
    )
    # [codex-2026-04-14] 幂等约束：同一 run_id 的 execution/run 节点最多一条，
    # 保证 replay_create_execution_node 在并发/重复消费时不会插出多条重复。
    op.execute(
        "CREATE UNIQUE INDEX ix_tnl_source_run_unique "
        "ON task_nodes_light (source_run_id) "
        "WHERE node_type IN ('execution', 'run') AND source_run_id IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_tnl_source_run_unique")
    op.drop_index("ix_tnl_active", table_name="task_nodes_light")
    op.drop_index("ix_tnl_run", table_name="task_nodes_light")
    op.drop_index("ix_tnl_updated", table_name="task_nodes_light")
    op.drop_index("ix_tnl_tree", table_name="task_nodes_light")
    op.drop_table("task_nodes_light")
