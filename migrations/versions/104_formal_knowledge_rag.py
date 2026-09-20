"""formal knowledge rag

Revision ID: 104
Revises: 103
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "104"
down_revision = "103"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "department_knowledge_bases",
        "storage_backend",
        existing_type=sa.String(length=40),
        server_default="lightrag",
        existing_nullable=False,
    )
    op.execute("UPDATE department_knowledge_bases SET storage_backend = 'lightrag' WHERE storage_backend = 'postgres_jsonb_vector'")

    op.add_column("knowledge_documents", sa.Column("index_status", sa.String(length=20), nullable=False, server_default="pending"))
    op.add_column("knowledge_documents", sa.Column("index_error", sa.Text(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("index_version", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("knowledge_documents", sa.Column("content_mime", sa.String(length=120), nullable=True))
    op.add_column("knowledge_documents", sa.Column("source_version", sa.String(length=120), nullable=True))
    op.execute("UPDATE knowledge_documents SET index_status = 'indexed' WHERE indexed_at IS NOT NULL AND status = 'active'")
    op.execute("UPDATE knowledge_documents SET index_status = 'indexed' WHERE status <> 'active'")
    op.create_index("ix_knowledge_documents_index_status", "knowledge_documents", ["index_status"])

    op.add_column("knowledge_query_logs", sa.Column("retrieval_backend", sa.String(length=40), nullable=True))
    op.add_column("knowledge_query_logs", sa.Column("lightrag_mode", sa.String(length=20), nullable=True))
    op.add_column("knowledge_query_logs", sa.Column("rerank_used", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("knowledge_query_logs", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("knowledge_query_logs", sa.Column("source_trace_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_index("ix_knowledge_query_logs_retrieval_backend", "knowledge_query_logs", ["retrieval_backend"])

    op.create_table(
        "knowledge_index_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=50), nullable=False),
        sa.Column("document_id", sa.String(length=50), nullable=True),
        sa.Column("kb_id", sa.String(length=50), nullable=False),
        sa.Column("org_unit_id", sa.String(length=50), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_knowledge_index_jobs_job_id"),
    )
    op.create_index("ix_knowledge_index_jobs_document_id", "knowledge_index_jobs", ["document_id"])
    op.create_index("ix_knowledge_index_jobs_kb_id", "knowledge_index_jobs", ["kb_id"])
    op.create_index("ix_knowledge_index_jobs_status_created", "knowledge_index_jobs", ["status", "created_at"])


def downgrade():
    op.drop_index("ix_knowledge_index_jobs_status_created", table_name="knowledge_index_jobs")
    op.drop_index("ix_knowledge_index_jobs_kb_id", table_name="knowledge_index_jobs")
    op.drop_index("ix_knowledge_index_jobs_document_id", table_name="knowledge_index_jobs")
    op.drop_table("knowledge_index_jobs")

    op.drop_index("ix_knowledge_query_logs_retrieval_backend", table_name="knowledge_query_logs")
    op.drop_column("knowledge_query_logs", "source_trace_json")
    op.drop_column("knowledge_query_logs", "latency_ms")
    op.drop_column("knowledge_query_logs", "rerank_used")
    op.drop_column("knowledge_query_logs", "lightrag_mode")
    op.drop_column("knowledge_query_logs", "retrieval_backend")

    op.drop_index("ix_knowledge_documents_index_status", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "source_version")
    op.drop_column("knowledge_documents", "content_mime")
    op.drop_column("knowledge_documents", "index_version")
    op.drop_column("knowledge_documents", "index_error")
    op.drop_column("knowledge_documents", "index_status")

    op.execute("UPDATE department_knowledge_bases SET storage_backend = 'postgres_jsonb_vector' WHERE storage_backend = 'lightrag'")
    op.alter_column(
        "department_knowledge_bases",
        "storage_backend",
        existing_type=sa.String(length=40),
        server_default="postgres_jsonb_vector",
        existing_nullable=False,
    )
