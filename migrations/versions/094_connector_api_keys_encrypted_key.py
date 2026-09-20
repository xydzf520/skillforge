"""connector api keys encrypted plaintext copy

Revision ID: 094
Revises: 093
"""

from alembic import op
import sqlalchemy as sa


revision = "094"
down_revision = "093"


def upgrade():
    op.add_column("connector_api_keys", sa.Column("encrypted_key", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("connector_api_keys", "encrypted_key")
