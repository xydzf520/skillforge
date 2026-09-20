"""创建 usage_logs 表用于 LLM 用量与成本追踪。

支持 F4（成本追踪 Dashboard）：每次 LLM 调用完成后写一条记录，
按 user/skill/department/model 维度聚合查询。

参考方案：docs/plans/aiclawcode-migration-plan.md §3.4

Revision ID: 017
Revises: 016
Create Date: 2026-04-06
"""

revision = "017"
down_revision = "016"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.String(50)),
        sa.Column("department", sa.String(50)),
        sa.Column("skill_id", sa.String(50)),
        sa.Column("conversation_id", sa.String(50)),
        sa.Column("call_source", sa.String(30)),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.DECIMAL(12, 6)),
        sa.Column("prompt_hash", sa.String(32)),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
    )

    # 4 个常用查询索引
    op.create_index("ix_usage_logs_ts", "usage_logs", ["ts"], postgresql_using="btree")
    op.create_index("ix_usage_logs_user_ts", "usage_logs", ["user_id", "ts"])
    op.create_index("ix_usage_logs_skill_ts", "usage_logs", ["skill_id", "ts"])
    op.create_index("ix_usage_logs_dept_ts", "usage_logs", ["department", "ts"])


def downgrade():
    op.drop_index("ix_usage_logs_dept_ts")
    op.drop_index("ix_usage_logs_skill_ts")
    op.drop_index("ix_usage_logs_user_ts")
    op.drop_index("ix_usage_logs_ts")
    op.drop_table("usage_logs")
