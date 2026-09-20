"""dispatch task assignment fields

Revision ID: 028
Revises: 027
Create Date: 2026-04-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("todo_dispatch_tasks", "executor", existing_type=sa.String(length=50), nullable=True)
    op.add_column("todo_dispatch_tasks", sa.Column("assigned_by", sa.String(length=50), nullable=True))
    op.add_column("todo_dispatch_tasks", sa.Column("assigned_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("todo_dispatch_tasks", "assigned_at")
    op.drop_column("todo_dispatch_tasks", "assigned_by")
    op.alter_column("todo_dispatch_tasks", "executor", existing_type=sa.String(length=50), nullable=False)
