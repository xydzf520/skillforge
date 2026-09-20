"""add system_meta table for cross-worker cache invalidation

C3：多 worker 部署时，`_load_org_indexes` 进程级 60s TTL dict 缓存，
单进程清理后其它 worker 仍在 TTL 内读旧数据。

用一张轻量表 `system_meta(key, version, updated_at)` 作为 cache-bust 版本号源：
- 写入路径（create_org_unit / merge_org / sync_dingtalk_org 等）commit 成功后
  `UPDATE system_meta SET version = version + 1 WHERE key = 'org_units_cache_version'`
- 读缓存前，所有 worker 先 `SELECT version`（< 1ms），version 不匹配就 miss 重查

初始插入 `('org_units_cache_version', 1)` 一行，之后纯自增。

Revision ID: 061
Revises: 060
"""

from alembic import op
import sqlalchemy as sa


revision = "061"
down_revision = "060"


def upgrade():
    op.create_table(
        "system_meta",
        sa.Column("key", sa.String(64), primary_key=True, nullable=False),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    # 种一行初始数据，后续所有变更路径 UPDATE 而非 INSERT
    op.execute(
        "INSERT INTO system_meta (key, version, updated_at) "
        "VALUES ('org_units_cache_version', 1, NOW())"
    )


def downgrade():
    op.drop_table("system_meta")
