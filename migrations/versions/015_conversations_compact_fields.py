"""conversations 表加上下文压缩与预算字段。

支持 F1（分层上下文压缩）：
- compact_boundary_idx：压缩边界消息在 messages 数组中的下标，用于前端展示"已压缩 N 轮历史"提示
- compact_failure_count：连续压缩失败次数，达到阈值触发熔断
- usage_snapshot：最近一次 LLM 调用的 usage 信息（input/output/cache 分桶），用于成本统计与预算判断

参考方案：docs/plans/aiclawcode-migration-plan.md §3.1

Revision ID: 015
Revises: 014
Create Date: 2026-04-06
"""

revision = "015"
down_revision = "014"

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def upgrade():
    # ── conversations 加压缩与预算字段 ──
    op.add_column(
        "conversations",
        sa.Column(
            "compact_boundary_idx",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="压缩边界消息在 messages 数组中的下标，0 表示未压缩",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "compact_failure_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="连续压缩失败次数，达到阈值触发熔断",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "usage_snapshot",
            JSONB,
            nullable=True,
            comment="最近一次 LLM 调用的 usage 快照（input/output/cache_read/cache_write）",
        ),
    )


def downgrade():
    op.drop_column("conversations", "usage_snapshot")
    op.drop_column("conversations", "compact_failure_count")
    op.drop_column("conversations", "compact_boundary_idx")
