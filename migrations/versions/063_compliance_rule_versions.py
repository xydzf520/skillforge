"""add compliance_rule_versions table

Wave 5 T16：合规规则版本历史。每次 update 写一条 snapshot，支持回滚到旧版本。

Revision ID: 063
Revises: 062
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "063"
down_revision = "062"


def upgrade():
    op.create_table(
        "compliance_rule_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.String(20), nullable=False, index=True),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("snapshot", postgresql.JSONB, nullable=False),
        sa.Column("author", sa.String(50), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        "ix_compliance_rule_versions_rule_created",
        "compliance_rule_versions",
        ["rule_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_compliance_rule_versions_rule_created", table_name="compliance_rule_versions")
    op.drop_table("compliance_rule_versions")
