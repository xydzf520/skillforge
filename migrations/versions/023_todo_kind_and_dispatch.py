"""todo kind + dispatch tasks + callback fields

Revision ID: 023
Revises: 022
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. decision_requests 增加 kind / callback_payload / callback_status
    op.add_column(
        "decision_requests",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="review",
        ),
    )
    op.add_column(
        "decision_requests",
        sa.Column(
            "callback_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "decision_requests",
        sa.Column(
            "callback_status",
            sa.String(length=20),
            nullable=False,
            server_default="skipped",
        ),
    )
    op.add_column(
        "decision_requests",
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "decision_requests",
        sa.Column("callback_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "decision_requests",
        sa.Column("callback_completed_at", sa.DateTime(), nullable=True),
    )

    # 2. ai_todos 增加 kind 字段（与 request 一致，便于按 kind 过滤）
    op.add_column(
        "ai_todos",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="review",
        ),
    )
    op.create_index("ix_todos_assignee_kind_status", "ai_todos", ["assignee", "kind", "status"], unique=False)

    # 3. todo_dispatch_tasks: 派发子任务表
    op.create_table(
        "todo_dispatch_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=50), nullable=False),
        sa.Column("manager_todo_id", sa.Integer(), nullable=True),
        sa.Column("executor", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="awaiting_dispatch",
        ),
        sa.Column("dingtalk_msg_id", sa.String(length=100), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("ack_at", sa.DateTime(), nullable=True),
        sa.Column("ack_note", sa.Text(), nullable=True),
        sa.Column("ack_channel", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["decision_requests.id"]),
        sa.ForeignKeyConstraint(["manager_todo_id"], ["ai_todos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dispatch_request",
        "todo_dispatch_tasks",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "ix_dispatch_executor_status",
        "todo_dispatch_tasks",
        ["executor", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dispatch_executor_status", table_name="todo_dispatch_tasks")
    op.drop_index("ix_dispatch_request", table_name="todo_dispatch_tasks")
    op.drop_table("todo_dispatch_tasks")

    op.drop_index("ix_todos_assignee_kind_status", table_name="ai_todos")
    op.drop_column("ai_todos", "kind")

    op.drop_column("decision_requests", "callback_completed_at")
    op.drop_column("decision_requests", "callback_error")
    op.drop_column("decision_requests", "callback_attempts")
    op.drop_column("decision_requests", "callback_status")
    op.drop_column("decision_requests", "callback_payload")
    op.drop_column("decision_requests", "kind")
