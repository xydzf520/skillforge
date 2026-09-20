"""add training deployment rejection fields

Revision ID: 102
Revises: 101
"""

from alembic import op
import sqlalchemy as sa


revision = "102"
down_revision = "101"


def upgrade():
    op.add_column("model_deployments", sa.Column("rejected_by", sa.String(length=50), nullable=True))
    op.add_column("model_deployments", sa.Column("rejected_at", sa.DateTime(), nullable=True))
    op.add_column("model_deployments", sa.Column("reject_reason", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("model_deployments", "reject_reason")
    op.drop_column("model_deployments", "rejected_at")
    op.drop_column("model_deployments", "rejected_by")
