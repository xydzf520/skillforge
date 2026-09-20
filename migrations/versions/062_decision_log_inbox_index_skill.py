"""add skill_id to decision_log inbox partial index (M3)

原 index `ix_dl_inbox_listing` 只覆盖 `(created_at DESC)`，按 `skill_id` 过滤时
仍需 INDEX SCAN + FILTER。本次新增 `ix_dl_inbox_listing_skill` 把 `skill_id`
放进去形成复合索引，让 "某 Skill 的报告列表" 这种高频查询走纯索引命中。

保留原 index（按 created_at 倒序扫全量报告仍是高频访问模式），两个 partial
index 共存不相互干扰。

Revision ID: 062
Revises: 061
"""
from alembic import op


revision = "062"
down_revision = "061"


def upgrade():
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_dl_inbox_listing_skill
          ON decision_log (skill_id, created_at DESC)
          WHERE is_sandbox = false
            AND jsonb_typeof(output_result -> 'reports') = 'array'
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_dl_inbox_listing_skill")
