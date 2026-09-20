"""增强 workbench 表以支持 Cursor 式 Studio 工作台。

messages 表增加上下文感知字段，patches 表增加 hunk 级操作字段。

Revision ID: 014
Revises: 013
Create Date: 2026-04-06
"""

revision = "014"
down_revision = "013"

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def upgrade():
    # ── skill_workbench_messages 增强 ──
    op.add_column(
        "skill_workbench_messages",
        sa.Column("module_id", sa.String(30), nullable=True, comment="消息关联的模块 ID"),
    )
    op.add_column(
        "skill_workbench_messages",
        sa.Column("selection_range", JSONB, nullable=True, comment="消息关联的选区"),
    )
    op.add_column(
        "skill_workbench_messages",
        sa.Column("draft_revision", sa.Integer, nullable=True, comment="消息发送时的文档版本号"),
    )

    # ── skill_workbench_patches 增强 ──
    op.add_column(
        "skill_workbench_patches",
        sa.Column("hunks_json", JSONB, nullable=True, comment="Hunk 级变更列表"),
    )
    op.add_column(
        "skill_workbench_patches",
        sa.Column("accepted_hunks", JSONB, nullable=True, comment="已接受的 hunk 索引"),
    )
    op.add_column(
        "skill_workbench_patches",
        sa.Column("rejected_hunks", JSONB, nullable=True, comment="已拒绝的 hunk 索引"),
    )
    op.add_column(
        "skill_workbench_patches",
        sa.Column("source_context", JSONB, nullable=True, comment="生成 patch 时的上下文快照"),
    )

    # ── 索引 ──
    op.create_index(
        "ix_workbench_messages_module",
        "skill_workbench_messages",
        ["session_id", "module_id"],
    )


def downgrade():
    op.drop_index("ix_workbench_messages_module")
    op.drop_column("skill_workbench_patches", "source_context")
    op.drop_column("skill_workbench_patches", "rejected_hunks")
    op.drop_column("skill_workbench_patches", "accepted_hunks")
    op.drop_column("skill_workbench_patches", "hunks_json")
    op.drop_column("skill_workbench_messages", "draft_revision")
    op.drop_column("skill_workbench_messages", "selection_range")
    op.drop_column("skill_workbench_messages", "module_id")
