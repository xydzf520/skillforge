"""Skill 执行请求表。

Revision ID: 036
Revises: 035
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "036"
down_revision = "035"


def upgrade():
    op.create_table(
        "skill_submissions",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("skill_id", sa.String(length=50), nullable=False),
        sa.Column("requester_id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("params", JSONB(), nullable=True),
        sa.Column("execution_id", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column("result_summary", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skill_submissions_skill_id", "skill_submissions", ["skill_id"])
    op.create_index("ix_skill_submissions_requester_id", "skill_submissions", ["requester_id"])
    op.create_index("ix_skill_submissions_org_unit_id", "skill_submissions", ["org_unit_id"])
    op.create_index("ix_skill_submissions_status", "skill_submissions", ["status"])


def downgrade():
    op.drop_index("ix_skill_submissions_status", table_name="skill_submissions")
    op.drop_index("ix_skill_submissions_org_unit_id", table_name="skill_submissions")
    op.drop_index("ix_skill_submissions_requester_id", table_name="skill_submissions")
    op.drop_index("ix_skill_submissions_skill_id", table_name="skill_submissions")
    op.drop_table("skill_submissions")
