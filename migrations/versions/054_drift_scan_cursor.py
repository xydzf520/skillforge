"""drift_scan 持久化游标

Revision ID: 054
Revises: 053

[codex-2026-04-14] 旧 drift_scan 固定扫 "now 往前 window_hours"，worker 停摆
超过窗口后旧缺口永久脱离扫描面。新增单例游标表 drift_scan_cursor，
last_scanned_at 持久化，重启后能继续追赶老数据；失败状态也不再永久跳过。
"""

from alembic import op
import sqlalchemy as sa

revision = "054"
down_revision = "053"


def upgrade():
    op.create_table(
        "drift_scan_cursor",
        sa.Column("id", sa.SmallInteger(), primary_key=True, server_default="1"),
        sa.Column("last_scanned_at", sa.DateTime()),
        sa.Column("last_run_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="drift_scan_cursor_singleton"),
    )
    # 插入单例行；幂等：若已存在则跳过
    op.execute(
        "INSERT INTO drift_scan_cursor (id) VALUES (1) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade():
    op.drop_table("drift_scan_cursor")
