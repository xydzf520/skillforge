"""add DataSource visibility + DataAccessRequest table (Hall v3)

大厅 v3 · 阶段 1：数据源加三级可发现性 + 申请访问审批单表。

Revision ID: 064
Revises: 063
"""
from alembic import op
import sqlalchemy as sa


revision = "064"
down_revision = "063"


def upgrade():
    # --- 1) DataSource 加 4 个字段 ---
    op.add_column(
        "data_sources",
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'department'"),
        ),
    )
    op.add_column("data_sources", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("data_sources", sa.Column("usage_hint", sa.Text(), nullable=True))
    op.add_column(
        "data_sources", sa.Column("owner_contact", sa.String(50), nullable=True)
    )
    op.create_index(
        "ix_data_sources_visibility", "data_sources", ["visibility"]
    )

    # 回填 owner_contact = created_by（历史数据 owner 默认等同创建人）
    op.execute(
        "UPDATE data_sources SET owner_contact = created_by "
        "WHERE owner_contact IS NULL AND created_by IS NOT NULL"
    )

    # --- 2) 申请访问单表 ---
    op.create_table(
        "data_access_requests",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("requester_id", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "requested_permission",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'read'"),
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("decided_by", sa.String(50), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("approved_expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_dar_source_status",
        "data_access_requests",
        ["source_id", "status"],
    )
    op.create_index(
        "ix_dar_requester", "data_access_requests", ["requester_id"]
    )


def downgrade():
    op.drop_index("ix_dar_requester", table_name="data_access_requests")
    op.drop_index("ix_dar_source_status", table_name="data_access_requests")
    op.drop_table("data_access_requests")

    op.drop_index("ix_data_sources_visibility", table_name="data_sources")
    op.drop_column("data_sources", "owner_contact")
    op.drop_column("data_sources", "usage_hint")
    op.drop_column("data_sources", "description")
    op.drop_column("data_sources", "visibility")
