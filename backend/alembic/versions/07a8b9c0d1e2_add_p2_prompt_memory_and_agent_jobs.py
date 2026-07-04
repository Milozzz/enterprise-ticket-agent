"""add P2 prompt audit, long-term memory, and agent jobs

Revision ID: 07a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "07a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_usage_records", sa.Column("prompt_version", sa.String(100), nullable=True))
    op.add_column("llm_usage_records", sa.Column("prompt_variant", sa.String(30), nullable=True))
    op.add_column("llm_usage_records", sa.Column("prompt_rollout_bucket", sa.Integer(), nullable=True))
    op.create_index("ix_llm_usage_records_prompt_version", "llm_usage_records", ["prompt_version"])

    op.create_table(
        "user_long_term_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("memory_key", sa.String(160), nullable=False),
        sa.Column("content", sa.String(1000), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("source_thread_id", sa.String(120), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "memory_type", "memory_key", name="uq_user_ltm_key"),
    )
    for column in ("tenant_id", "user_id", "memory_type", "source_thread_id", "status", "expires_at"):
        op.create_index(f"ix_user_long_term_memories_{column}", "user_long_term_memories", [column])
    op.create_index("ix_user_ltm_lookup", "user_long_term_memories", ["tenant_id", "user_id", "status", "importance"])

    op.create_table(
        "agent_execution_jobs",
        sa.Column("job_id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(120), nullable=True),
        sa.Column("requester_id", sa.String(120), nullable=False),
        sa.Column("requester_role", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_by", sa.String(120), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_job_idempotency"),
    )
    for column in ("tenant_id", "thread_id", "trace_id", "requester_id", "status", "available_at"):
        op.create_index(f"ix_agent_execution_jobs_{column}", "agent_execution_jobs", [column])
    op.create_index("ix_agent_jobs_claim", "agent_execution_jobs", ["status", "available_at", "priority"])
    op.create_index("ix_agent_jobs_tenant_created", "agent_execution_jobs", ["tenant_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in ("user_long_term_memories", "agent_execution_jobs"):
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
                "USING (tenant_id = current_setting('app.current_tenant', true)) "
                "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))"
            )


def downgrade() -> None:
    op.drop_table("agent_execution_jobs")
    op.drop_table("user_long_term_memories")
    op.drop_index("ix_llm_usage_records_prompt_version", table_name="llm_usage_records")
    op.drop_column("llm_usage_records", "prompt_rollout_bucket")
    op.drop_column("llm_usage_records", "prompt_variant")
    op.drop_column("llm_usage_records", "prompt_version")
