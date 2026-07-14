"""add agent identity, online eval and counterfactual governance

Revision ID: 3c4d5e6f7081
Revises: 2b3c4d5e6f70
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3c4d5e6f7081"
down_revision: Union[str, Sequence[str], None] = "2b3c4d5e6f70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_delegation_grants",
        sa.Column("grant_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("principal_id", sa.String(120), nullable=False),
        sa.Column("principal_role", sa.String(40), nullable=False),
        sa.Column("issued_by", sa.String(120), nullable=False),
        sa.Column("agent_id", sa.String(120), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("resource_scopes", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False, server_default=""),
        sa.Column("approval_id", sa.String(140), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("token_jti_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "token_jti_hash", name="uq_agent_delegation_jti"),
    )
    op.create_index("ix_agent_delegation_principal", "agent_delegation_grants", ["tenant_id", "principal_id", "status"])
    op.create_index("ix_agent_delegation_agent", "agent_delegation_grants", ["tenant_id", "agent_id", "expires_at"])
    _create_indexes(
        "agent_delegation_grants",
        ("tenant_id", "principal_id", "issued_by", "agent_id", "approval_id", "status", "token_jti_hash", "expires_at"),
    )

    op.create_table(
        "agent_feedback_records",
        sa.Column("feedback_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(120), nullable=True),
        sa.Column("task_id", sa.String(140), nullable=True),
        sa.Column("scenario_id", sa.String(100), nullable=False),
        sa.Column("submitted_by", sa.String(120), nullable=False),
        sa.Column("disposition", sa.String(30), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("correction", sa.JSON(), nullable=True),
        sa.Column("task_snapshot", sa.JSON(), nullable=False),
        sa.Column("version_context", sa.JSON(), nullable=False),
        sa.Column("eval_candidate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_feedback_window", "agent_feedback_records", ["tenant_id", "scenario_id", "created_at"])
    op.create_index("ix_agent_feedback_version", "agent_feedback_records", ["tenant_id", "created_at"])
    _create_indexes(
        "agent_feedback_records",
        ("tenant_id", "thread_id", "trace_id", "task_id", "scenario_id", "submitted_by", "disposition", "eval_candidate", "created_at"),
    )

    op.create_table(
        "online_eval_cases",
        sa.Column("case_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("source_feedback_id", sa.String(140), sa.ForeignKey("agent_feedback_records.feedback_id"), nullable=False),
        sa.Column("dataset_name", sa.String(120), nullable=False, server_default="production_feedback"),
        sa.Column("scenario_id", sa.String(100), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="candidate"),
        sa.Column("dataset_version", sa.String(80), nullable=True),
        sa.Column("reviewed_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "source_feedback_id", name="uq_online_eval_feedback"),
    )
    op.create_index("ix_online_eval_dataset", "online_eval_cases", ["tenant_id", "dataset_name", "status"])
    _create_indexes(
        "online_eval_cases",
        ("tenant_id", "source_feedback_id", "dataset_name", "scenario_id", "status", "dataset_version"),
    )

    op.create_table(
        "counterfactual_experiments",
        sa.Column("experiment_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=True),
        sa.Column("task_id", sa.String(140), nullable=True),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("baseline_snapshot", sa.JSON(), nullable=False),
        sa.Column("variants", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_counterfactual_task", "counterfactual_experiments", ["tenant_id", "thread_id", "created_at"])
    _create_indexes(
        "counterfactual_experiments",
        ("tenant_id", "thread_id", "task_id", "requested_by", "status"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY "tenant_isolation_{table}" ON "{table}" '
                "USING (tenant_id = current_setting('app.current_tenant', true)) "
                "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in reversed(_TABLES):
            op.execute(f'DROP POLICY IF EXISTS "tenant_isolation_{table}" ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    for table in reversed(_TABLES):
        op.drop_table(table)


def _create_indexes(table: str, columns: Sequence[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


_TABLES = (
    "agent_delegation_grants",
    "agent_feedback_records",
    "online_eval_cases",
    "counterfactual_experiments",
)
