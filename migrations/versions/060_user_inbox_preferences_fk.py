"""add FK from user_inbox_preferences.user_id to users.id

059 创建 user_inbox_preferences 时漏了 user_id → users.id 外键，
导致用户被删除后偏好记录会成为孤儿。本迁移补上 ON DELETE CASCADE 约束。

Revision ID: 060
Revises: 059
"""

from alembic import op


revision = "060"
down_revision = "059"


def upgrade():
    # 先清理可能已存在的孤儿（user_id 在 users 表里找不到对应行的偏好记录）
    # 否则 create_foreign_key 会因为约束被违反而失败
    op.execute(
        """
        DELETE FROM user_inbox_preferences
         WHERE user_id NOT IN (SELECT id FROM users)
        """
    )
    op.create_foreign_key(
        "fk_user_inbox_preferences_user_id",
        "user_inbox_preferences",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade():
    op.drop_constraint(
        "fk_user_inbox_preferences_user_id",
        "user_inbox_preferences",
        type_="foreignkey",
    )
