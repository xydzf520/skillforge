"""user_inbox_preferences table (GAP-5/6 服务端未读红点)

Revision ID: 059
Revises: 058
"""

from alembic import op
import sqlalchemy as sa

revision = "059"
down_revision = "058"


def upgrade():
    op.create_table(
        "user_inbox_preferences",
        sa.Column("user_id", sa.String(length=50), primary_key=True),
        sa.Column("reports_last_viewed_at", sa.DateTime(), nullable=True),
        sa.Column("todos_last_viewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade():
    op.drop_table("user_inbox_preferences")
