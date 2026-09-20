"""创建 contract_drifts 表

Revision ID: 055
Revises: 054

Skill runtime 输出与 contract.output_schema 不一致时写入这张表。
平台 warn-only（不 reject），owner 通过钉钉和看板感知 drift 并修 contract 或 main.py。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "055"
down_revision = "054"


def upgrade():
    op.create_table(
        "contract_drifts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(50), nullable=False),
        sa.Column("run_id", sa.String(50), nullable=False),
        sa.Column("schema_errors", JSONB, nullable=False),
        sa.Column("schema_hash", sa.String(64)),
        sa.Column("detected_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_by", sa.String(50)),
        sa.Column("acknowledged_at", sa.DateTime()),
    )
    op.create_index(
        "ix_contract_drifts_skill_ack",
        "contract_drifts",
        ["skill_id", "acknowledged", "detected_at"],
    )
    op.create_index(
        "ix_contract_drifts_run_id",
        "contract_drifts",
        ["run_id"],
    )


def downgrade():
    op.drop_index("ix_contract_drifts_run_id", "contract_drifts")
    op.drop_index("ix_contract_drifts_skill_ack", "contract_drifts")
    op.drop_table("contract_drifts")
