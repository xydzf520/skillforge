"""Skill 资产元模型：模板定义 / Skill 实例 / 发布记录 / 血缘关系

Revision ID: 043
Revises: 030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "043"
down_revision = "042"


def upgrade():
    # ── 模板定义（从 _templates/ 目录升级为 DB 管理）──
    op.create_table(
        "skill_templates",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.String(50)),
        sa.Column("owner_id", sa.String(50)),
        sa.Column("schema_json", JSONB),           # 参数 schema 模板
        sa.Column("ui_schema_json", JSONB),         # UI schema 模板
        sa.Column("runtime_profile", JSONB),        # 运行时配置（超时、并发等）
        sa.Column("is_published", sa.Boolean, server_default=sa.text("false")),
        sa.Column("version", sa.String(20)),        # 当前发布版本 e.g. "v1.2"
        sa.Column("source_skill_id", sa.String(100)),  # 从哪个 Skill 提取的模板（可 NULL）
        sa.Column("tags", ARRAY(sa.Text)),
        sa.Column("fork_count", sa.Integer, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ── Skill 实例（绑定模板 + 自定义配置）──
    op.create_table(
        "skill_instances",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(100), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.String(100), sa.ForeignKey("skill_templates.id")),
        sa.Column("custom_config", JSONB),          # 实例级覆盖配置
        sa.Column("status", sa.String(20), server_default=sa.text("'active'")),  # active/archived/suspended
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("skill_id", name="uq_skill_instances_skill_id"),
    )

    # ── 发布记录（每次审核通过 = 一条 release）──
    op.create_table(
        "skill_releases",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(100), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("git_ref", sa.String(100)),       # git tag or commit
        sa.Column("artifact_digest", sa.String(64)),  # SKILL.md 的 sha256
        sa.Column("review_id", sa.Integer),          # 关联的审核 ID
        sa.Column("release_notes", sa.Text),
        sa.Column("released_by", sa.String(50)),
        sa.Column("released_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_skill_releases_skill", "skill_releases", ["skill_id"])

    # ── 血缘关系（Fork/Derive 追踪）──
    op.create_table(
        "skill_lineage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(100), nullable=False),  # 源 skill 或 template
        sa.Column("target_id", sa.String(100), nullable=False),  # 目标 skill
        sa.Column("relation_type", sa.String(20), nullable=False),  # fork/derive/upgrade
        sa.Column("source_version", sa.String(20)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_skill_lineage_source", "skill_lineage", ["source_id"])
    op.create_index("ix_skill_lineage_target", "skill_lineage", ["target_id"])


def downgrade():
    op.drop_table("skill_lineage")
    op.drop_table("skill_releases")
    op.drop_table("skill_instances")
    op.drop_table("skill_templates")
