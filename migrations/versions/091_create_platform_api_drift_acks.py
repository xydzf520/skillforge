"""create platform api drift ack state

Revision ID: 091
Revises: 090
"""

from alembic import op
import sqlalchemy as sa


revision = "091"
down_revision = "090"


def upgrade():
    op.create_table(
        "platform_api_drift_acks",
        sa.Column("alert_id", sa.String(260), primary_key=True),
        sa.Column("acknowledged_by", sa.String(50), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("status", sa.String(20), nullable=False, server_default="acknowledged"),
        sa.Column("review_task_id", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_platform_api_drift_acks_acknowledged_by", "platform_api_drift_acks", ["acknowledged_by"])
    op.create_index("ix_platform_api_drift_acks_acknowledged_at", "platform_api_drift_acks", ["acknowledged_at"])
    op.create_index("ix_platform_api_drift_acks_status", "platform_api_drift_acks", ["status"])
    op.create_index("ix_platform_api_drift_acks_review_task_id", "platform_api_drift_acks", ["review_task_id"])


def downgrade():
    op.drop_index("ix_platform_api_drift_acks_review_task_id", table_name="platform_api_drift_acks")
    op.drop_index("ix_platform_api_drift_acks_status", table_name="platform_api_drift_acks")
    op.drop_index("ix_platform_api_drift_acks_acknowledged_at", table_name="platform_api_drift_acks")
    op.drop_index("ix_platform_api_drift_acks_acknowledged_by", table_name="platform_api_drift_acks")
    op.drop_table("platform_api_drift_acks")
