"""role matrix v2 core columns and role migration

Revision ID: 056
Revises: 055
"""

from alembic import op
import sqlalchemy as sa

revision = "056"
down_revision = "055"


def upgrade():
    op.add_column("users", sa.Column("state", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("permissions_rev", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_users_state", "users", ["state"])

    op.execute(
        """
        UPDATE users
        SET state = CASE
            WHEN is_active = TRUE THEN 'active'
            ELSE 'disabled'
        END
        """
    )
    op.alter_column("users", "state", existing_type=sa.String(length=20), nullable=False)

    op.execute(
        """
        UPDATE users
        SET role = CASE role
            WHEN 'admin' THEN 'system_admin'
            WHEN 'ai_engineer' THEN 'aibp'
            WHEN 'biz_owner' THEN 'dept_admin'
            WHEN 'director' THEN 'observer'
            WHEN 'operator' THEN 'observer'
            ELSE role
        END,
        can_view_all = CASE
            WHEN role = 'director' THEN TRUE
            ELSE can_view_all
        END
        """
    )

    op.add_column("audit_log", sa.Column("role_snapshot", sa.String(length=20), nullable=True))
    op.create_index("ix_audit_log_role_snapshot", "audit_log", ["role_snapshot"])


def downgrade():
    op.drop_index("ix_audit_log_role_snapshot", table_name="audit_log")
    op.drop_column("audit_log", "role_snapshot")

    op.execute(
        """
        UPDATE users
        SET role = CASE role
            WHEN 'system_admin' THEN 'admin'
            WHEN 'dept_admin' THEN 'biz_owner'
            WHEN 'aibp' THEN 'ai_engineer'
            WHEN 'observer' THEN 'operator'
            ELSE role
        END
        """
    )

    op.drop_index("ix_users_state", table_name="users")
    op.drop_column("users", "permissions_rev")
    op.drop_column("users", "state")
