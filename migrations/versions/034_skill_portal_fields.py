"""Skill 门户展示字段。

Revision ID: 034
Revises: 033
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "034"
down_revision = "033"


def upgrade():
    op.add_column("skills", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("skills", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("skills", sa.Column("category", sa.String(length=50), nullable=True))
    op.add_column("skills", sa.Column("icon", sa.String(length=50), nullable=True))
    op.add_column("skills", sa.Column("param_ui_schema", JSONB(), nullable=True))
    op.add_column("skills", sa.Column("result_ui_schema", JSONB(), nullable=True))
    op.add_column("skills", sa.Column("visibility", sa.String(length=20), nullable=True, server_default="department"))
    op.add_column("skills", sa.Column("usage_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("skills", sa.Column("success_rate", sa.Float(), nullable=True))
    op.add_column("skills", sa.Column("last_run_at", sa.DateTime(), nullable=True))
    op.create_index("ix_skills_category", "skills", ["category"])
    op.create_index("ix_skills_visibility", "skills", ["visibility"])
    op.execute("UPDATE skills SET display_name = name WHERE display_name IS NULL")


def downgrade():
    op.drop_index("ix_skills_visibility", table_name="skills")
    op.drop_index("ix_skills_category", table_name="skills")
    op.drop_column("skills", "last_run_at")
    op.drop_column("skills", "success_rate")
    op.drop_column("skills", "usage_count")
    op.drop_column("skills", "visibility")
    op.drop_column("skills", "result_ui_schema")
    op.drop_column("skills", "param_ui_schema")
    op.drop_column("skills", "icon")
    op.drop_column("skills", "category")
    op.drop_column("skills", "summary")
    op.drop_column("skills", "display_name")
