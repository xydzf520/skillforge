"""alter usage_logs call_source for intelligence analyze

Revision ID: 088
Revises: 087
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "088"
down_revision = "087"


def upgrade():
    op.alter_column(
        "usage_logs",
        "call_source",
        existing_type=sa.String(length=30),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "usage_logs",
        "prompt_hash",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.add_column(
        "usage_logs",
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("usage_logs", "metadata_json")
    op.alter_column(
        "usage_logs",
        "prompt_hash",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
    op.alter_column(
        "usage_logs",
        "call_source",
        existing_type=sa.String(length=64),
        type_=sa.String(length=30),
        existing_nullable=True,
    )
