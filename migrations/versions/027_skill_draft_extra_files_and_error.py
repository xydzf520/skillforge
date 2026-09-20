"""skill draft 增加 extra_files / error_detail / generation_status 字段 (v7 一步到位 skill 创建流程)

Revision ID: 027
Revises: 026
Create Date: 2026-04-09

新流程把整个 skill (包括 scripts/main.py 和 tests/test_main.py) 一次性 aiclaw
生成后存到草稿，等用户在 4 必感知点确认完才落到 skill_repo + git。所以草稿
需要保存这些非标准文件 + 生成过程的状态/失败信息。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 草稿生成期间产出的额外文件 (scripts/main.py / tests/test_main.py 等)
    # 4 个标准字段 (skill_md / intent_md / policy_yaml / contract_json) 已有专列
    op.add_column(
        "skill_drafts",
        sa.Column("extra_files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # 生成阶段状态: pending / running / ready / failed
    op.add_column(
        "skill_drafts",
        sa.Column("generation_status", sa.String(20), nullable=False, server_default="ready"),
    )
    # 失败时保留的诊断信息 (含 scratch_dir 路径供管理后台 debug)
    op.add_column(
        "skill_drafts",
        sa.Column("error_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("skill_drafts", "error_detail")
    op.drop_column("skill_drafts", "generation_status")
    op.drop_column("skill_drafts", "extra_files")
