"""audit_log 表加 prompt_hash 字段。

支持 F3（Prompt Registry）：审计日志记录每次 LLM 调用使用的 prompt 版本 hash，
后续审核可追溯"本次结果基于哪版 prompt 生成"。

参考方案：docs/plans/aiclawcode-migration-plan.md §3.3

Revision ID: 016
Revises: 015
Create Date: 2026-04-06
"""

revision = "016"
down_revision = "015"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        "audit_log",
        sa.Column(
            "prompt_hash",
            sa.String(32),
            nullable=True,
            comment="本次操作使用的 prompt 版本 hash（PromptRegistry）",
        ),
    )
    op.create_index(
        "ix_audit_log_prompt_hash",
        "audit_log",
        ["prompt_hash"],
        postgresql_using="btree",
    )


def downgrade():
    op.drop_index("ix_audit_log_prompt_hash")
    op.drop_column("audit_log", "prompt_hash")
