"""create department analysis agents

Revision ID: 120
Revises: 119
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "department_analysis_agents",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("department_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("owner_user_id", sa.String(length=50), nullable=True),
        sa.Column("skill_id", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=False, server_default="analysis_v1"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dimensions_json", JSONB, nullable=True),
        sa.Column("default_params_json", JSONB, nullable=True),
        sa.Column("editor_user_ids_json", JSONB, nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("updated_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("department", "name", name="uq_department_analysis_agents_dept_name"),
    )
    op.create_index("ix_department_analysis_agents_department_id", "department_analysis_agents", ["department_id"])
    op.create_index("ix_department_analysis_agents_department", "department_analysis_agents", ["department"])
    op.create_index("ix_department_analysis_agents_owner_user_id", "department_analysis_agents", ["owner_user_id"])
    op.create_index("ix_department_analysis_agents_skill_id", "department_analysis_agents", ["skill_id"])
    op.create_index("ix_department_analysis_agents_status", "department_analysis_agents", ["status"])
    op.create_index("ix_department_analysis_agents_is_active", "department_analysis_agents", ["is_active"])
    op.create_index("ix_department_analysis_agents_created_by", "department_analysis_agents", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_department_analysis_agents_created_by", table_name="department_analysis_agents")
    op.drop_index("ix_department_analysis_agents_is_active", table_name="department_analysis_agents")
    op.drop_index("ix_department_analysis_agents_status", table_name="department_analysis_agents")
    op.drop_index("ix_department_analysis_agents_skill_id", table_name="department_analysis_agents")
    op.drop_index("ix_department_analysis_agents_owner_user_id", table_name="department_analysis_agents")
    op.drop_index("ix_department_analysis_agents_department", table_name="department_analysis_agents")
    op.drop_index("ix_department_analysis_agents_department_id", table_name="department_analysis_agents")
    op.drop_table("department_analysis_agents")
