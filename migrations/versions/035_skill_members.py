"""Skill 成员权限表。

Revision ID: 035
Revises: 034
"""

from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"


def upgrade():
    op.create_table(
        "skill_members",
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("granted_by", sa.String(length=50), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("skill_id", "user_id"),
    )
    op.create_index("ix_skill_members_user_id", "skill_members", ["user_id"])
    op.execute(
        """
        INSERT INTO skill_members (skill_id, user_id, role)
        SELECT id, owner, 'owner'
        FROM skills
        WHERE owner IS NOT NULL AND owner != ''
        ON CONFLICT DO NOTHING
        """
    )


def downgrade():
    op.drop_index("ix_skill_members_user_id", table_name="skill_members")
    op.drop_table("skill_members")
