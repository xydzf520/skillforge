"""ABAC 策略表 — 属性级访问控制

Revision ID: 031
Revises: 030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "045"
down_revision = "044"


def upgrade():
    op.create_table(
        "abac_policies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("effect", sa.String(10), server_default="deny", nullable=False),
        sa.Column("priority", sa.Integer, server_default="0", nullable=False),
        # 条件（复用 condition_engine 评估格式）
        sa.Column("subject_condition", JSONB),
        sa.Column("resource_condition", JSONB),
        sa.Column("environment_condition", JSONB),
        sa.Column("enabled", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_abac_policies_resource",
        "abac_policies",
        ["resource_type", "action"],
    )


def downgrade():
    op.drop_index("ix_abac_policies_resource", table_name="abac_policies")
    op.drop_table("abac_policies")
