"""tasktree 查询优化索引

Revision ID: 049
Revises: 048

[codex-2026-04-14] 三个索引都落在在线热表上（execution_runs / decision_log /
decision_requests），用 CONCURRENTLY 避免阻塞写流量。CONCURRENTLY 不能跑在
事务里，所以包 autocommit_block。
"""

from alembic import op

revision = "049"
down_revision = "048"


def upgrade():
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_exec_runs_started_status "
            "ON execution_runs(started_at DESC, status)"
        )

        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_decision_log_created_skill "
            "ON decision_log(created_at DESC, skill_id)"
        )

        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_decision_requests_run_id "
            "ON decision_requests(run_id) "
            "WHERE run_id IS NOT NULL"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_exec_runs_started_status")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_decision_log_created_skill")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_decision_requests_run_id")
