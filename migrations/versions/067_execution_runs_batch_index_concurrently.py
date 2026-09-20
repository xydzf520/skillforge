"""execution_runs batch_id 索引并发化

Revision ID: 067
Revises: 066
"""

from alembic import op


revision = "067"
down_revision = "066"


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_execution_runs_batch_id")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_execution_runs_batch_id "
            "ON execution_runs (batch_id)"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_execution_runs_batch_id")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_execution_runs_batch_id "
            "ON execution_runs (batch_id)"
        )
