"""execution_runs winner 唯一约束

为多次运行投票增加 batch 级唯一冠军约束：
- 同一个 batch 最多只能有一条 is_winner=true

Revision ID: 066
Revises: 065
"""

from alembic import op
import sqlalchemy as sa


revision = "066"
down_revision = "065"


def upgrade():
    op.create_index(
        "uq_execution_runs_batch_winner",
        "execution_runs",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("is_winner IS TRUE"),
    )


def downgrade():
    op.drop_index("uq_execution_runs_batch_winner", table_name="execution_runs")
