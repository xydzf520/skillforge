"""Skill 标签表。

Revision ID: 037
Revises: 036
"""

from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"


def upgrade():
    op.create_table(
        "skill_tags",
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("tag", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("skill_id", "tag"),
    )
    op.create_index("ix_skill_tags_tag", "skill_tags", ["tag"])


def downgrade():
    op.drop_index("ix_skill_tags_tag", table_name="skill_tags")
    op.drop_table("skill_tags")
