"""decision_requests 增加 decision_log_id；todo_dispatch_tasks 增加 started_at

Revision ID: 032
Revises: 031
"""
from alembic import op
import sqlalchemy as sa

revision = "032"
down_revision = "031"


def upgrade():
    op.add_column("decision_requests", sa.Column("decision_log_id", sa.Integer))
    op.add_column("todo_dispatch_tasks", sa.Column("started_at", sa.DateTime))


def downgrade():
    op.drop_column("todo_dispatch_tasks", "started_at")
    op.drop_column("decision_requests", "decision_log_id")
