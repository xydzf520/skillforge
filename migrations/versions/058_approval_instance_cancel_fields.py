"""approval_instances: cancelled_reason + cancelled_at (role-matrix-v2 §6.5)

Revision ID: 058
Revises: 057
"""
from alembic import op
import sqlalchemy as sa

revision = "058"
down_revision = "057"


def upgrade():
    op.execute("""
        ALTER TABLE approval_instances
            ADD COLUMN IF NOT EXISTS cancelled_reason VARCHAR(50),
            ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP
    """)
    # 把历史数据从 payload JSONB 回填到专属列（幂等）
    op.execute("""
        UPDATE approval_instances
           SET cancelled_reason = payload ->> 'cancelled_reason',
               cancelled_at = (payload ->> 'cancelled_at')::timestamp
         WHERE status = 'cancelled'
           AND cancelled_reason IS NULL
           AND payload ? 'cancelled_reason'
    """)
    op.create_index(
        "ix_approval_instances_cancelled_reason",
        "approval_instances",
        ["cancelled_reason"],
        postgresql_where=sa.text("cancelled_reason IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ix_approval_instances_cancelled_reason", table_name="approval_instances")
    op.drop_column("approval_instances", "cancelled_at")
    op.drop_column("approval_instances", "cancelled_reason")
