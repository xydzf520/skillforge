"""Skill Auto-Optimizer 数据库表

Revision ID: 010
Revises: 009
Create Date: 2026-04-04

新增表:
- optimizer_sessions: 优化会话
- optimizer_candidates: 候选版本
- benchmark_packs: 评测包
- benchmark_cases: 评测用例
- benchmark_runs: 评测运行记录

扩展:
- decision_logs: 增加 candidate_id, benchmark_case_id
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 优化会话 ──
    op.create_table(
        "optimizer_sessions",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("skill_id", sa.String(100), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("goal", sa.String(200), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("baseline_commit", sa.String(50)),
        sa.Column("baseline_score", sa.JSON()),
        sa.Column("best_candidate_id", sa.String(50)),
        sa.Column("current_iteration", sa.Integer(), server_default="0"),
        sa.Column("max_iterations", sa.Integer(), server_default="20"),
        sa.Column("total_tokens", sa.Integer(), server_default="0"),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_optsess_skill", "optimizer_sessions", ["skill_id"])
    op.create_index("ix_optsess_status", "optimizer_sessions", ["status"])

    # ── 候选版本 ──
    op.create_table(
        "optimizer_candidates",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("session_id", sa.String(50), sa.ForeignKey("optimizer_sessions.id"), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(50)),
        sa.Column("skill_md_patch", sa.Text()),
        sa.Column("diff_summary", sa.Text()),
        sa.Column("generation_rationale", sa.Text()),
        sa.Column("changes", sa.JSON()),
        sa.Column("validation_status", sa.String(20), server_default="pending"),
        sa.Column("validation_errors", sa.JSON()),
        sa.Column("benchmark_status", sa.String(20), server_default="pending"),
        sa.Column("benchmark_score", sa.JSON()),
        sa.Column("shadow_status", sa.String(20), server_default="pending"),
        sa.Column("shadow_stats", sa.JSON()),
        sa.Column("decision", sa.String(20), server_default="pending"),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("model_name", sa.String(100)),
        sa.Column("token_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_optcand_session", "optimizer_candidates", ["session_id"])

    # ── 评测包 ──
    op.create_table(
        "benchmark_packs",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("skill_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("case_count", sa.Integer(), server_default="0"),
        sa.Column("frozen", sa.Boolean(), server_default="false"),
        sa.Column("manifest", sa.JSON()),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_bpack_skill", "benchmark_packs", ["skill_id"])

    # ── 评测用例 ──
    op.create_table(
        "benchmark_cases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pack_id", sa.String(50), sa.ForeignKey("benchmark_packs.id"), nullable=False),
        sa.Column("case_key", sa.String(200), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("expected_output", sa.JSON()),
        sa.Column("assertions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tags", sa.JSON(), server_default="[]"),
        sa.Column("weight", sa.Float(), server_default="1.0"),
        sa.Column("provenance", sa.JSON()),
        sa.UniqueConstraint("pack_id", "case_key", name="uq_bcase_pack_key"),
    )
    op.create_index("ix_bcase_pack", "benchmark_cases", ["pack_id"])

    # ── 评测运行记录 ──
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("pack_id", sa.String(50), sa.ForeignKey("benchmark_packs.id"), nullable=False),
        sa.Column("candidate_id", sa.String(50)),
        sa.Column("status", sa.String(20), server_default="running"),
        sa.Column("metrics", sa.JSON()),
        sa.Column("case_results", sa.JSON()),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_brun_pack", "benchmark_runs", ["pack_id"])
    op.create_index("ix_brun_cand", "benchmark_runs", ["candidate_id"])

    # ── DecisionLog 扩展 ──
    op.add_column("decision_log", sa.Column("candidate_id", sa.String(50)))
    op.add_column("decision_log", sa.Column("benchmark_case_id", sa.Integer()))


def downgrade() -> None:
    op.drop_column("decision_log", "benchmark_case_id")
    op.drop_column("decision_log", "candidate_id")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_cases")
    op.drop_table("benchmark_packs")
    op.drop_table("optimizer_candidates")
    op.drop_table("optimizer_sessions")
