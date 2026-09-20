"""042: Skill Fork 关系追踪字段

Revision ID: 042
Revises: 041
"""

from alembic import op
import sqlalchemy as sa

revision = "042"
down_revision = "041"


def upgrade():
    op.add_column("skills", sa.Column("forked_from", sa.String(100), nullable=True))
    op.add_column("skills", sa.Column("fork_type", sa.String(20), nullable=True))  # 'template' | 'skill'
    op.add_column("skills", sa.Column("parent_version", sa.String(40), nullable=True))  # git commit hash
    op.create_index("ix_skills_forked_from", "skills", ["forked_from"])


def downgrade():
    op.drop_index("ix_skills_forked_from", "skills")
    op.drop_column("skills", "parent_version")
    op.drop_column("skills", "fork_type")
    op.drop_column("skills", "forked_from")
