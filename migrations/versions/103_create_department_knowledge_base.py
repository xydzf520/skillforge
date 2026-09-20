"""create department knowledge base

Revision ID: 103
Revises: 102
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "103"
down_revision = "102"


def upgrade():
    op.create_table(
        "department_knowledge_bases",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("storage_backend", sa.String(length=40), nullable=False, server_default="postgres_jsonb_vector"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("stats_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_unit_id"], ["org_units.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_unit_id"),
    )
    op.create_index("ix_department_knowledge_bases_department", "department_knowledge_bases", ["department"])
    op.create_index("ix_department_knowledge_bases_org_unit_id", "department_knowledge_bases", ["org_unit_id"])
    op.create_index("ix_department_knowledge_bases_status", "department_knowledge_bases", ["status"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("kb_id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("tags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("updated_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["department_knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_documents_content_hash", "knowledge_documents", ["content_hash"])
    op.create_index("ix_knowledge_documents_created_at", "knowledge_documents", ["created_at"])
    op.create_index("ix_knowledge_documents_created_by", "knowledge_documents", ["created_by"])
    op.create_index("ix_knowledge_documents_department", "knowledge_documents", ["department"])
    op.create_index("ix_knowledge_documents_kb_id", "knowledge_documents", ["kb_id"])
    op.create_index("ix_knowledge_documents_kb_status_updated", "knowledge_documents", ["kb_id", "status", "updated_at"])
    op.create_index("ix_knowledge_documents_org_unit_id", "knowledge_documents", ["org_unit_id"])
    op.create_index("ix_knowledge_documents_source", "knowledge_documents", ["kb_id", "source_type", "source_ref"])
    op.create_index("ix_knowledge_documents_source_hash", "knowledge_documents", ["source_hash"])
    op.create_index("ix_knowledge_documents_source_ref", "knowledge_documents", ["source_ref"])
    op.create_index("ix_knowledge_documents_source_type", "knowledge_documents", ["source_type"])
    op.create_index("ix_knowledge_documents_status", "knowledge_documents", ["status"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.String(length=50), nullable=False),
        sa.Column("kb_id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("embedding_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("keywords_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("entities_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_doc_index"),
    )
    op.create_index("ix_knowledge_chunks_content_hash", "knowledge_chunks", ["content_hash"])
    op.create_index("ix_knowledge_chunks_department", "knowledge_chunks", ["department"])
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_knowledge_chunks_kb_doc", "knowledge_chunks", ["kb_id", "document_id"])
    op.create_index("ix_knowledge_chunks_kb_id", "knowledge_chunks", ["kb_id"])
    op.create_index("ix_knowledge_chunks_org_unit_id", "knowledge_chunks", ["org_unit_id"])

    op.create_table(
        "knowledge_query_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kb_id", sa.String(length=50), nullable=True),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="search"),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default=sa.text("8")),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("answer_preview", sa.Text(), nullable=True),
        sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_query_logs_created_at", "knowledge_query_logs", ["created_at"])
    op.create_index("ix_knowledge_query_logs_department", "knowledge_query_logs", ["department"])
    op.create_index("ix_knowledge_query_logs_kb_id", "knowledge_query_logs", ["kb_id"])
    op.create_index("ix_knowledge_query_logs_mode", "knowledge_query_logs", ["mode"])
    op.create_index("ix_knowledge_query_logs_org_unit_id", "knowledge_query_logs", ["org_unit_id"])
    op.create_index("ix_knowledge_query_logs_user_id", "knowledge_query_logs", ["user_id"])


def downgrade():
    op.drop_index("ix_knowledge_query_logs_user_id", table_name="knowledge_query_logs")
    op.drop_index("ix_knowledge_query_logs_org_unit_id", table_name="knowledge_query_logs")
    op.drop_index("ix_knowledge_query_logs_mode", table_name="knowledge_query_logs")
    op.drop_index("ix_knowledge_query_logs_kb_id", table_name="knowledge_query_logs")
    op.drop_index("ix_knowledge_query_logs_department", table_name="knowledge_query_logs")
    op.drop_index("ix_knowledge_query_logs_created_at", table_name="knowledge_query_logs")
    op.drop_table("knowledge_query_logs")
    op.drop_index("ix_knowledge_chunks_org_unit_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_kb_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_kb_doc", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_department", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_content_hash", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_documents_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_source_type", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_source_ref", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_source_hash", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_source", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_org_unit_id", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_kb_status_updated", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_kb_id", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_department", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_created_by", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_created_at", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_content_hash", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_department_knowledge_bases_status", table_name="department_knowledge_bases")
    op.drop_index("ix_department_knowledge_bases_org_unit_id", table_name="department_knowledge_bases")
    op.drop_index("ix_department_knowledge_bases_department", table_name="department_knowledge_bases")
    op.drop_table("department_knowledge_bases")
