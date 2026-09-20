"""durable multi-model AI Chat

Revision ID: 133_ai_chat
Revises: 132_media_workbench_21
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "133_ai_chat"
down_revision = "132_media_workbench_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_threads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(160), nullable=False, server_default="新对话"),
        sa.Column("default_model", sa.String(80), nullable=False, server_default="deepseek-v4-pro"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_through_message_id", sa.String(36), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_chat_threads_user_id", "ai_chat_threads", ["user_id"])
    op.create_index("ix_ai_chat_threads_archived", "ai_chat_threads", ["archived"])
    op.create_index("ix_ai_chat_threads_last_message_at", "ai_chat_threads", ["last_message_at"])
    op.create_index(
        "ix_ai_chat_threads_user_recent",
        "ai_chat_threads",
        ["user_id", "archived", "last_message_at", "id"],
    )

    op.create_table(
        "ai_chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("provider_group", sa.String(32), nullable=True),
        sa.Column("vision_model", sa.String(80), nullable=True),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("reply_to_message_id", sa.String(36), nullable=True),
        sa.Column("variant_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("attachment_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("message_meta", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("event_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_ai_chat_message_user_idempotency"),
    )
    op.create_index("ix_ai_chat_messages_thread_id", "ai_chat_messages", ["thread_id"])
    op.create_index("ix_ai_chat_messages_user_id", "ai_chat_messages", ["user_id"])
    op.create_index("ix_ai_chat_messages_status", "ai_chat_messages", ["status"])
    op.create_index("ix_ai_chat_messages_turn_id", "ai_chat_messages", ["turn_id"])
    op.create_index("ix_ai_chat_messages_reply_to_message_id", "ai_chat_messages", ["reply_to_message_id"])
    op.create_index("ix_ai_chat_messages_created_at", "ai_chat_messages", ["created_at"])
    op.create_index(
        "ix_ai_chat_messages_thread_order",
        "ai_chat_messages",
        ["thread_id", "created_at", "id"],
    )
    op.create_index(
        "ix_ai_chat_messages_user_status",
        "ai_chat_messages",
        ["user_id", "status"],
    )

    op.create_table(
        "ai_chat_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("thread_id", sa.String(36), nullable=True),
        sa.Column("message_id", sa.String(36), nullable=True),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(80), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("bound_at", sa.DateTime(), nullable=True),
        sa.Column("orphan_expires_at", sa.DateTime(), nullable=True),
    )
    for column in ("user_id", "thread_id", "message_id", "sha256", "created_at", "orphan_expires_at"):
        op.create_index(f"ix_ai_chat_attachments_{column}", "ai_chat_attachments", [column])
    op.create_index(
        "ix_ai_chat_attachments_orphan",
        "ai_chat_attachments",
        ["message_id", "orphan_expires_at"],
    )


def downgrade() -> None:
    op.drop_table("ai_chat_attachments")
    op.drop_table("ai_chat_messages")
    op.drop_table("ai_chat_threads")
