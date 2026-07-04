"""add pgvector policy knowledge base

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from app.db.vector_type import Vector


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.String(length=100), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=300), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False, server_default="1"),
        sa.Column("permission_roles", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])
    op.create_index("ix_knowledge_documents_enabled", "knowledge_documents", ["enabled"])
    op.create_index("ix_knowledge_documents_content_hash", "knowledge_documents", ["content_hash"])

    embedding_type = Vector(768) if is_postgres else sa.JSON()
    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.String(length=140), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("document_id", sa.String(length=100), nullable=False),
        sa.Column("paragraph_id", sa.String(length=100), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("embedding", embedding_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.document_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "document_id", "paragraph_id", name="uq_knowledge_chunk_paragraph"
        ),
    )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_knowledge_chunks_paragraph_id", "knowledge_chunks", ["paragraph_id"])
    op.create_index(
        "ix_knowledge_chunks_tenant_document",
        "knowledge_chunks",
        ["tenant_id", "document_id"],
    )
    if is_postgres:
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
            "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
        )
        for table_name in ("knowledge_documents", "knowledge_chunks"):
            policy_name = f"tenant_isolation_{table_name}"
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY "{policy_name}" ON "{table_name}" '
                "USING (tenant_id = current_setting('app.current_tenant', true)) "
                "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))"
            )


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
