"""platform_api_cache 表 — 全局平台 API 注册表

Revision ID: 030
Revises: 029
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "030"
down_revision = "029"


def upgrade():
    op.create_table(
        "platform_api_cache",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(200), nullable=False, index=True),
        sa.Column("page_path", sa.String(500), nullable=False),
        sa.Column("page_title", sa.String(200)),
        sa.Column("apis_json", JSONB, nullable=False),
        sa.Column("api_count", sa.Integer, default=0),
        sa.Column("discovery_method", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("domain", "page_path", name="uq_platform_api_domain_page"),
    )


def downgrade():
    op.drop_table("platform_api_cache")
