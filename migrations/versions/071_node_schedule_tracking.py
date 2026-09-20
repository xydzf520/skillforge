"""node schedule tracking

Revision ID: 071
Revises: 070
"""

from alembic import op
import sqlalchemy as sa


revision = "071"
down_revision = "070"


def upgrade():
    op.add_column(
        "execution_runs",
        sa.Column("source_instance_id", sa.String(50), nullable=True),
    )
    op.create_index(
        "ix_execution_runs_source_instance",
        "execution_runs",
        ["source_instance_id", "skill_id", "started_at"],
    )

    op.create_table(
        "node_schedule_configs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.String(50), nullable=False),
        sa.Column("skill_id", sa.String(50), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("config_version", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("pushed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("ack_ok", sa.Boolean, nullable=True),
        sa.UniqueConstraint("instance_id", "skill_id", name="uq_node_schedule_inst_skill"),
    )
    op.create_index("ix_node_schedule_configs_instance", "node_schedule_configs", ["instance_id"])
    op.create_index("ix_node_schedule_configs_skill", "node_schedule_configs", ["skill_id"])


def downgrade():
    op.drop_index("ix_node_schedule_configs_skill", table_name="node_schedule_configs")
    op.drop_index("ix_node_schedule_configs_instance", table_name="node_schedule_configs")
    op.drop_table("node_schedule_configs")
    op.drop_index("ix_execution_runs_source_instance", table_name="execution_runs")
    op.drop_column("execution_runs", "source_instance_id")
