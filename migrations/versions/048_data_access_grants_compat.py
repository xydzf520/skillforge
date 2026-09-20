"""data_access_grants spec 兼容计算列

Revision ID: 048
Revises: 047

在 migration 040 已创建的 data_access_grants 表上添加
grantee_type + grantee_id 计算列，兼容 spec 的查询方式。
"""
from alembic import op

revision = "048"
down_revision = "047"


def upgrade():
    # spec 兼容计算列：grantee_type（user / org_unit / unknown）
    op.execute("""
        ALTER TABLE data_access_grants ADD COLUMN IF NOT EXISTS grantee_type VARCHAR(20)
            GENERATED ALWAYS AS (
                CASE WHEN grantee_user_id IS NOT NULL THEN 'user'
                     WHEN grantee_org_unit_id IS NOT NULL THEN 'org_unit'
                     ELSE 'unknown' END
            ) STORED
    """)

    # spec 兼容计算列：grantee_id（取 user_id 或 org_unit_id）
    op.execute("""
        ALTER TABLE data_access_grants ADD COLUMN IF NOT EXISTS grantee_id VARCHAR(100)
            GENERATED ALWAYS AS (
                COALESCE(grantee_user_id, grantee_org_unit_id)
            ) STORED
    """)

    # 索引：加速按 grantee_type + grantee_id 查询
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_dag_grantee_type_id
        ON data_access_grants(grantee_type, grantee_id)
    """)

    # 约束：至少指定一个 grantee
    op.execute("""
        ALTER TABLE data_access_grants
        DROP CONSTRAINT IF EXISTS ck_dag_at_least_one_grantee
    """)
    op.execute("""
        ALTER TABLE data_access_grants ADD CONSTRAINT ck_dag_at_least_one_grantee
            CHECK (grantee_user_id IS NOT NULL OR grantee_org_unit_id IS NOT NULL)
    """)


def downgrade():
    op.execute("ALTER TABLE data_access_grants DROP CONSTRAINT IF EXISTS ck_dag_at_least_one_grantee")
    op.execute("DROP INDEX IF EXISTS ix_dag_grantee_type_id")
    op.execute("ALTER TABLE data_access_grants DROP COLUMN IF EXISTS grantee_id")
    op.execute("ALTER TABLE data_access_grants DROP COLUMN IF EXISTS grantee_type")
