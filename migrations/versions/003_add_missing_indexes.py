"""add missing indexes for frequently queried columns

Revision ID: 003
Revises: 002
Create Date: 2026-04-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # review_comments.review_id — 按审核ID查评论
    op.create_index("idx_review_comments_review_id", "review_comments", ["review_id"])
    # execution_steps.run_id — 按执行ID查步骤
    op.create_index("idx_execution_steps_run_id", "execution_steps", ["run_id"])
    # decision_log.run_id — 按执行ID查决策记录
    op.create_index("idx_decision_log_run_id", "decision_log", ["run_id"])
    # decision_log.skill_id + created_at — 按Skill查近期决策
    op.create_index("idx_decision_log_skill_time", "decision_log", ["skill_id", "created_at"])
    # data_ingestion_log.source_id — 按数据源查导入记录
    op.create_index("idx_ingestion_log_source_id", "data_ingestion_log", ["source_id"])
    # dingtalk_outbox.status + priority — 消费队列查询
    op.create_index("idx_outbox_status_priority", "dingtalk_outbox", ["status", "priority"])


def downgrade() -> None:
    op.drop_index("idx_outbox_status_priority")
    op.drop_index("idx_ingestion_log_source_id")
    op.drop_index("idx_decision_log_skill_time")
    op.drop_index("idx_decision_log_run_id")
    op.drop_index("idx_execution_steps_run_id")
    op.drop_index("idx_review_comments_review_id")
