"""add review_feedback JSONB column to reviews table

Revision ID: 013
Revises: 012
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reviews', sa.Column('review_feedback', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('reviews', 'review_feedback')
