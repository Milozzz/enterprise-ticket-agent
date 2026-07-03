"""add llm usage ledger and approval inbox

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(120), nullable=True),
        sa.Column("node_name", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("output_cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fallback_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_llm_usage_records_tenant_id", "llm_usage_records", ["tenant_id"])
    op.create_index("ix_llm_usage_records_thread_id", "llm_usage_records", ["thread_id"])
    op.create_index("ix_llm_usage_records_trace_id", "llm_usage_records", ["trace_id"])
    op.create_index("ix_llm_usage_records_node_name", "llm_usage_records", ["node_name"])
    op.create_index("ix_llm_usage_records_provider", "llm_usage_records", ["provider"])
    op.create_index("ix_llm_usage_records_model", "llm_usage_records", ["model"])
    op.create_index("ix_llm_usage_tenant_created", "llm_usage_records", ["tenant_id", "created_at"])
    op.create_index("ix_llm_usage_thread_created", "llm_usage_records", ["thread_id", "created_at"])

    op.create_table(
        "approval_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("task_key", sa.String(180), nullable=False),
        sa.Column("request_id", sa.String(120), nullable=False),
        sa.Column("scenario_id", sa.String(80), nullable=False),
        sa.Column("approval_type", sa.String(80), nullable=False),
        sa.Column("stage_id", sa.String(100), nullable=False, server_default="human_review"),
        sa.Column("stage_name", sa.String(160), nullable=False, server_default="Human review"),
        sa.Column("requester_id", sa.String(120), nullable=False),
        sa.Column("requester_role", sa.String(50), nullable=False, server_default="USER"),
        sa.Column("assigned_roles", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("business_payload", sa.JSON(), nullable=True),
        sa.Column("thread_id", sa.String(120), nullable=True),
        sa.Column("current_assignee_id", sa.String(120), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalation_reason", sa.String(300), nullable=True),
        sa.Column("reviewer_comment", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "task_key", name="uq_approval_task_tenant_key"),
    )
    op.create_index("ix_approval_tasks_tenant_id", "approval_tasks", ["tenant_id"])
    op.create_index("ix_approval_tasks_task_key", "approval_tasks", ["task_key"])
    op.create_index("ix_approval_tasks_request_id", "approval_tasks", ["request_id"])
    op.create_index("ix_approval_tasks_scenario_id", "approval_tasks", ["scenario_id"])
    op.create_index("ix_approval_tasks_approval_type", "approval_tasks", ["approval_type"])
    op.create_index("ix_approval_tasks_requester_id", "approval_tasks", ["requester_id"])
    op.create_index("ix_approval_tasks_status", "approval_tasks", ["status"])
    op.create_index("ix_approval_tasks_thread_id", "approval_tasks", ["thread_id"])
    op.create_index("ix_approval_tasks_due_at", "approval_tasks", ["due_at"])
    op.create_index("ix_approval_tasks_queue", "approval_tasks", ["tenant_id", "status", "due_at"])
    op.create_index("ix_approval_tasks_requester", "approval_tasks", ["tenant_id", "requester_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in ("llm_usage_records", "approval_tasks"):
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
                "USING (tenant_id = current_setting('app.current_tenant', true)) "
                "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))"
            )


def downgrade() -> None:
    op.drop_table("approval_tasks")
    op.drop_table("llm_usage_records")
