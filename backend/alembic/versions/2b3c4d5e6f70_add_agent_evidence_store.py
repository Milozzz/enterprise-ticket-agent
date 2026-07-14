"""add persistent task evidence graph and cited decisions

Revision ID: 2b3c4d5e6f70
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2b3c4d5e6f70"
down_revision: Union[str, Sequence[str], None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_evidence_records",
        sa.Column("record_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("task_id", sa.String(140), nullable=False),
        sa.Column("evidence_id", sa.String(140), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.String(80), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=True),
        sa.Column("trace_id", sa.String(120), nullable=True),
        sa.Column("predicate", sa.String(120), nullable=False),
        sa.Column("claim", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(180), nullable=False),
        sa.Column("observed_value", sa.JSON(), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("source_object", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(180), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("data_version", sa.String(100), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=True),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("evidence_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "task_id", "evidence_id", name="uq_agent_evidence_task_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "task_id", "sequence", name="uq_agent_evidence_task_sequence"
        ),
    )
    op.create_index(
        "ix_agent_evidence_task",
        "agent_evidence_records",
        ["tenant_id", "task_id", "sequence"],
    )
    op.create_index(
        "ix_agent_evidence_entity",
        "agent_evidence_records",
        ["tenant_id", "source_system", "source_object", "entity_id"],
    )
    op.create_index(
        "ix_agent_evidence_trace",
        "agent_evidence_records",
        ["tenant_id", "trace_id"],
    )
    for column in ("tenant_id", "task_id", "evidence_id", "scenario_id", "thread_id", "trace_id", "predicate", "subject", "source_system", "source_object", "entity_id", "expires_at", "record_hash"):
        op.create_index(
            f"ix_agent_evidence_records_{column}",
            "agent_evidence_records",
            [column],
        )

    op.create_table(
        "agent_evidence_relations",
        sa.Column("relation_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("task_id", sa.String(140), nullable=False),
        sa.Column(
            "source_record_id",
            sa.String(140),
            sa.ForeignKey("agent_evidence_records.record_id"),
            nullable=False,
        ),
        sa.Column(
            "target_record_id",
            sa.String(140),
            sa.ForeignKey("agent_evidence_records.record_id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "source_record_id",
            "target_record_id",
            "relation_type",
            name="uq_agent_evidence_relation",
        ),
    )
    op.create_index(
        "ix_agent_evidence_relation_task",
        "agent_evidence_relations",
        ["tenant_id", "task_id"],
    )
    for column in ("tenant_id", "task_id", "source_record_id", "target_record_id", "relation_type"):
        op.create_index(
            f"ix_agent_evidence_relations_{column}",
            "agent_evidence_relations",
            [column],
        )

    op.create_table(
        "agent_decision_records",
        sa.Column("record_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("task_id", sa.String(140), nullable=False),
        sa.Column("decision_id", sa.String(140), nullable=False),
        sa.Column("decision_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("cited_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("decision_payload", sa.JSON(), nullable=False),
        sa.Column("verifier", sa.String(100), nullable=False),
        sa.Column("trace_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "task_id", "decision_id", name="uq_agent_decision_task_id"
        ),
    )
    op.create_index(
        "ix_agent_decision_task",
        "agent_decision_records",
        ["tenant_id", "task_id", "created_at"],
    )
    for column in ("tenant_id", "task_id", "decision_id", "decision_type", "status", "trace_id"):
        op.create_index(
            f"ix_agent_decision_records_{column}",
            "agent_decision_records",
            [column],
        )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (
            "agent_evidence_records",
            "agent_evidence_relations",
            "agent_decision_records",
        ):
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
        for table in (
            "agent_decision_records",
            "agent_evidence_relations",
            "agent_evidence_records",
        ):
            op.execute(f'DROP POLICY IF EXISTS "tenant_isolation_{table}" ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_table("agent_decision_records")
    op.drop_table("agent_evidence_relations")
    op.drop_table("agent_evidence_records")
