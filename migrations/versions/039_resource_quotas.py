"""resource quotas

Revision ID: 039
Revises: 038
"""

from alembic import op
import sqlalchemy as sa

revision = "039"
down_revision = "038"


def upgrade():
    op.create_table(
        "resource_quotas",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=True, server_default="monthly"),
        sa.Column("quota_limit", sa.Integer(), nullable=False),
        sa.Column("burst_limit", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resource_quotas_org_unit_id", "resource_quotas", ["org_unit_id"])
    op.create_index("ix_resource_quotas_resource_type", "resource_quotas", ["resource_type"])


def downgrade():
    op.drop_index("ix_resource_quotas_resource_type", table_name="resource_quotas")
    op.drop_index("ix_resource_quotas_org_unit_id", table_name="resource_quotas")
    op.drop_table("resource_quotas")
