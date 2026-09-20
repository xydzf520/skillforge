"""knowledge multimodal media assets

Revision ID: 106
Revises: 105
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "106"
down_revision = "105"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledge_media_assets",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("document_id", sa.String(length=50), nullable=False),
        sa.Column("kb_id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("file_name", sa.String(length=240), nullable=False),
        sa.Column("mime_type", sa.String(length=80), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("visible_text", sa.Text(), nullable=True),
        sa.Column("objects_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("embedding_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("index_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["department_knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_media_assets_document_id", "knowledge_media_assets", ["document_id"])
    op.create_index("ix_knowledge_media_assets_kb_id", "knowledge_media_assets", ["kb_id"])
    op.create_index("ix_knowledge_media_assets_org_unit_id", "knowledge_media_assets", ["org_unit_id"])
    op.create_index("ix_knowledge_media_assets_department", "knowledge_media_assets", ["department"])
    op.create_index("ix_knowledge_media_assets_mime_type", "knowledge_media_assets", ["mime_type"])
    op.create_index("ix_knowledge_media_assets_sha256", "knowledge_media_assets", ["sha256"])
    op.create_index("ix_knowledge_media_assets_status", "knowledge_media_assets", ["status"])
    op.create_index("ix_knowledge_media_assets_created_by", "knowledge_media_assets", ["created_by"])
    op.create_index("ix_knowledge_media_assets_created_at", "knowledge_media_assets", ["created_at"])
    op.create_index("ix_knowledge_media_kb_status", "knowledge_media_assets", ["kb_id", "status"])
    op.create_index("ix_knowledge_media_kb_sha", "knowledge_media_assets", ["kb_id", "sha256"])

    op.add_column("knowledge_chunks", sa.Column("media_asset_id", sa.String(length=50), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("modality", sa.String(length=20), nullable=False, server_default="text"))
    op.create_foreign_key(
        "fk_knowledge_chunks_media_asset_id",
        "knowledge_chunks",
        "knowledge_media_assets",
        ["media_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_knowledge_chunks_media_asset_id", "knowledge_chunks", ["media_asset_id"])
    op.create_index("ix_knowledge_chunks_modality", "knowledge_chunks", ["modality"])


def downgrade():
    op.drop_index("ix_knowledge_chunks_modality", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_media_asset_id", table_name="knowledge_chunks")
    op.drop_constraint("fk_knowledge_chunks_media_asset_id", "knowledge_chunks", type_="foreignkey")
    op.drop_column("knowledge_chunks", "modality")
    op.drop_column("knowledge_chunks", "media_asset_id")

    op.drop_index("ix_knowledge_media_kb_sha", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_kb_status", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_created_at", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_created_by", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_status", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_sha256", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_mime_type", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_department", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_org_unit_id", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_kb_id", table_name="knowledge_media_assets")
    op.drop_index("ix_knowledge_media_assets_document_id", table_name="knowledge_media_assets")
    op.drop_table("knowledge_media_assets")
