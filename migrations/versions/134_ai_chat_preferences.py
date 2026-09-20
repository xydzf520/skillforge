"""AI Chat per-user preferences

Revision ID: 134_ai_chat_preferences
Revises: 133_ai_chat
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "134_ai_chat_preferences"
down_revision = "133_ai_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_user_preferences",
        sa.Column("user_id", sa.String(50), primary_key=True),
        sa.Column("last_model", sa.String(80), nullable=False, server_default="deepseek-v4-pro"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_chat_user_preferences")
