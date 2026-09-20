"""create platform cookie audit trail

Revision ID: 083
Revises: 082
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "083"
down_revision = "082"


def upgrade():
    op.create_table(
        "platform_cookie_audit",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cookie_pool_id", sa.Integer, nullable=True),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("platform", sa.String(40), nullable=True),
        sa.Column("shop_id", sa.String(100), nullable=True),
        sa.Column("owner_user_id", sa.String(50), nullable=True),
        sa.Column("connector_key_id", sa.String(32), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("auth_source", sa.String(40), nullable=False),
        sa.Column("actor_user_id", sa.String(50), nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_platform_cookie_audit_cookie_pool_id", "platform_cookie_audit", ["cookie_pool_id"])
    op.create_index("ix_platform_cookie_audit_source_id", "platform_cookie_audit", ["source_id"])
    op.create_index("ix_platform_cookie_audit_platform", "platform_cookie_audit", ["platform"])
    op.create_index("ix_platform_cookie_audit_shop_id", "platform_cookie_audit", ["shop_id"])
    op.create_index("ix_platform_cookie_audit_owner_user_id", "platform_cookie_audit", ["owner_user_id"])
    op.create_index("ix_platform_cookie_audit_connector_key_id", "platform_cookie_audit", ["connector_key_id"])
    op.create_index("ix_platform_cookie_audit_action", "platform_cookie_audit", ["action"])
    op.create_index("ix_platform_cookie_audit_created_at", "platform_cookie_audit", ["created_at"])
    op.create_index(
        "ix_platform_cookie_audit_context",
        "platform_cookie_audit",
        ["source_id", "platform", "shop_id"],
    )


def downgrade():
    op.drop_index("ix_platform_cookie_audit_context", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_created_at", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_action", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_connector_key_id", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_owner_user_id", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_shop_id", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_platform", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_source_id", table_name="platform_cookie_audit")
    op.drop_index("ix_platform_cookie_audit_cookie_pool_id", table_name="platform_cookie_audit")
    op.drop_table("platform_cookie_audit")
