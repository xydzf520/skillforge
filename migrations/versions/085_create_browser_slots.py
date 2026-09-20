"""create browser slots

Revision ID: 085
Revises: 084
"""

from alembic import op
import sqlalchemy as sa

revision = "085"
down_revision = "084"


def upgrade():
    op.create_table(
        "browser_slots",
        sa.Column("slot_id", sa.String(length=80), primary_key=True),
        sa.Column(
            "browser_session_id",
            sa.Integer(),
            sa.ForeignKey("browser_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cdp_url", sa.String(length=255), nullable=False),
        sa.Column("profile_dir", sa.String(length=500), nullable=True),
        sa.Column("node_id", sa.String(length=100), nullable=True),
        sa.Column("egress_group", sa.String(length=100), nullable=True),
        sa.Column("egress_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
        sa.Column("current_pool_id", sa.BigInteger(), nullable=True),
        sa.Column("current_credential_alias", sa.String(length=80), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_browser_slots_node_status", "browser_slots", ["node_id", "status"])
    op.create_index("ix_browser_slots_egress_status", "browser_slots", ["egress_group", "status"])


def downgrade():
    op.drop_index("ix_browser_slots_egress_status", table_name="browser_slots")
    op.drop_index("ix_browser_slots_node_status", table_name="browser_slots")
    op.drop_table("browser_slots")
