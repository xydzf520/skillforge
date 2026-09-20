"""组织架构：org_units 组织树 + user_org_memberships 人员归属"""

from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"


def upgrade():
    op.create_table(
        "org_units",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(20), nullable=False, server_default=sa.text("'department'")),
        sa.Column("parent_id", sa.String(50), sa.ForeignKey("org_units.id"), nullable=True),
        sa.Column("path", sa.Text, nullable=True),
        sa.Column("dingtalk_dept_id", sa.String(50), nullable=True),
        sa.Column("manager_user_id", sa.String(50), nullable=True),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )
    op.create_index("ix_org_units_parent_id", "org_units", ["parent_id"])
    op.create_index("ix_org_units_type", "org_units", ["type"])
    op.create_index("ix_org_units_dingtalk_dept_id", "org_units", ["dingtalk_dept_id"])

    op.create_table(
        "user_org_memberships",
        sa.Column("user_id", sa.String(50), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_unit_id", sa.String(50), sa.ForeignKey("org_units.id"), nullable=False),
        sa.Column("membership_type", sa.String(20), server_default=sa.text("'primary'")),
        sa.Column("is_manager", sa.Boolean, server_default=sa.text("false")),
        sa.Column("joined_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.PrimaryKeyConstraint("user_id", "org_unit_id"),
    )
    op.create_index("ix_user_org_memberships_org", "user_org_memberships", ["org_unit_id"])

    op.add_column("skills", sa.Column("org_unit_id", sa.String(50), nullable=True))
    op.create_index("ix_skills_org_unit_id", "skills", ["org_unit_id"])

    op.execute(
        """
        INSERT INTO org_units (id, name, type, path)
        SELECT DISTINCT department, department, 'department', '/' || department
        FROM users
        WHERE department IS NOT NULL AND department != ''
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO user_org_memberships (user_id, org_unit_id, membership_type)
        SELECT id, department, 'primary'
        FROM users
        WHERE department IS NOT NULL AND department != ''
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE skills
        SET org_unit_id = department
        WHERE department IS NOT NULL AND department != ''
        """
    )


def downgrade():
    op.drop_index("ix_skills_org_unit_id", table_name="skills")
    op.drop_column("skills", "org_unit_id")
    op.drop_index("ix_user_org_memberships_org", table_name="user_org_memberships")
    op.drop_table("user_org_memberships")
    op.drop_index("ix_org_units_dingtalk_dept_id", table_name="org_units")
    op.drop_index("ix_org_units_type", table_name="org_units")
    op.drop_index("ix_org_units_parent_id", table_name="org_units")
    op.drop_table("org_units")
