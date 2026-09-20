"""data access grants and datasource sensitivity

Revision ID: 040
Revises: 039
"""

from alembic import op
import sqlalchemy as sa

revision = "040"
down_revision = "039"


def upgrade():
    op.add_column("data_sources", sa.Column("sensitivity", sa.String(length=20), nullable=True, server_default="L2"))
    op.add_column("data_sources", sa.Column("owner_org_unit_id", sa.String(length=50), nullable=True))
    op.create_table(
        "data_access_grants",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=50), nullable=False),
        sa.Column("grantee_user_id", sa.String(length=50), nullable=False),
        sa.Column("grantee_org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approval_instance_id", sa.String(length=50), nullable=True),
        sa.Column("granted_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_access_grants_source_id", "data_access_grants", ["source_id"])
    op.create_index("ix_data_access_grants_grantee_user_id", "data_access_grants", ["grantee_user_id"])
    op.create_index("ix_data_access_grants_grantee_org_unit_id", "data_access_grants", ["grantee_org_unit_id"])
    op.create_index("ix_data_access_grants_status", "data_access_grants", ["status"])
    op.create_index("ix_data_access_grants_approval_instance_id", "data_access_grants", ["approval_instance_id"])


def downgrade():
    op.drop_index("ix_data_access_grants_approval_instance_id", table_name="data_access_grants")
    op.drop_index("ix_data_access_grants_status", table_name="data_access_grants")
    op.drop_index("ix_data_access_grants_grantee_org_unit_id", table_name="data_access_grants")
    op.drop_index("ix_data_access_grants_grantee_user_id", table_name="data_access_grants")
    op.drop_index("ix_data_access_grants_source_id", table_name="data_access_grants")
    op.drop_table("data_access_grants")
    op.drop_column("data_sources", "owner_org_unit_id")
    op.drop_column("data_sources", "sensitivity")
