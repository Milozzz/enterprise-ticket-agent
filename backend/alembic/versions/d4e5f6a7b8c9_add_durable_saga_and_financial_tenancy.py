"""add durable saga execution and tenant-protect financial records

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT = "TENANT-DEMO-COMMERCE"

ADDED_TENANT_COLUMNS = [
    "erp_access_requests",
    "erp_ap_invoices",
    "erp_asset_depreciation_runs",
    "erp_bill_of_material_lines",
    "erp_bills_of_material",
    "erp_change_documents",
    "erp_consolidation_runs",
    "erp_cost_centers",
    "erp_currency_rates",
    "erp_customer_complaints",
    "erp_customer_profiles",
    "erp_data_contract_versions",
    "erp_departments",
    "erp_document_flows",
    "erp_employee_profiles",
    "erp_fiscal_periods",
    "erp_fixed_assets",
    "erp_goods_receipt_lines",
    "erp_goods_receipts",
    "erp_inventory_batches",
    "erp_inventory_items",
    "erp_inventory_movements",
    "erp_inventory_serials",
    "erp_invoices",
    "erp_journal_lines",
    "erp_master_data_change_requests",
    "erp_master_data_duplicate_candidates",
    "erp_master_data_validation_results",
    "erp_master_data_versions",
    "erp_open_items",
    "erp_order_lines",
    "erp_payment_transactions",
    "erp_period_close_runs",
    "erp_plants",
    "erp_procurement_approval_requests",
    "erp_products",
    "erp_purchase_order_lines",
    "erp_purchase_orders",
    "erp_reimbursement_claims",
    "erp_return_authorizations",
    "erp_return_inspections",
    "erp_sales_organizations",
    "erp_shipments",
    "erp_stock_reservations",
    "erp_suppliers",
    "erp_tax_codes",
    "erp_warehouses",
    "erp_work_order_component_issues",
    "erp_work_orders",
    "erp_credit_memos",
    "erp_clearing_documents",
    "erp_reversal_documents",
    "erp_compensation_transactions",
    "erp_business_approvals",
    "erp_financial_ledger_entries",
    "erp_process_event_logs",
    "refund_logs",
    "approval_decisions",
]

EXISTING_UNPROTECTED_TENANT_TABLES = [
    "erp_approval_matrix_rules",
    "erp_business_partners",
    "erp_business_units",
    "erp_company_codes",
    "erp_consolidation_groups",
    "erp_data_quality_issues",
    "erp_data_quality_rules",
    "erp_journal_entries",
    "erp_policy_versions",
    "erp_support_tickets",
    "erp_tenant_organizations",
]

RLS_TABLES = (
    ADDED_TENANT_COLUMNS
    + EXISTING_UNPROTECTED_TENANT_TABLES
    + [
        "erp_saga_executions",
        "erp_saga_steps",
        "erp_execution_evidence",
        "a2a_tasks",
        "a2a_task_events",
        "tenant_onboarding",
        "tenant_subscriptions",
        "usage_events",
        "tenant_slos",
    ]
)


def upgrade() -> None:
    for table_name in ADDED_TENANT_COLUMNS:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "tenant_id",
                    sa.String(length=50),
                    nullable=False,
                    server_default=DEFAULT_TENANT,
                )
            )
            batch_op.create_index(f"ix_{table_name}_tenant_id", ["tenant_id"], unique=False)

    saga_status = sa.Enum(
        "PENDING",
        "RUNNING",
        "COMPENSATING",
        "COMPLETED",
        "FAILED",
        "MANUAL_REVIEW",
        name="erpsagastatus",
    )
    saga_step_status = sa.Enum(
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "SKIPPED",
        name="erpsagastepstatus",
    )
    op.create_table(
        "erp_saga_executions",
        sa.Column("saga_id", sa.String(length=120), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("saga_type", sa.String(length=80), nullable=False),
        sa.Column("business_key", sa.String(length=160), nullable=False),
        sa.Column("status", saga_status, nullable=False),
        sa.Column("current_step", sa.String(length=100), nullable=True),
        sa.Column("command_payload", sa.JSON(), nullable=False),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "saga_type", "business_key", name="uq_erp_saga_business_key"
        ),
    )
    op.create_index("ix_erp_saga_executions_tenant_id", "erp_saga_executions", ["tenant_id"])
    op.create_index("ix_erp_saga_executions_saga_type", "erp_saga_executions", ["saga_type"])
    op.create_index("ix_erp_saga_executions_business_key", "erp_saga_executions", ["business_key"])
    op.create_index(
        "ix_erp_saga_resume", "erp_saga_executions", ["tenant_id", "status", "updated_at"]
    )

    op.create_table(
        "erp_saga_steps",
        sa.Column("step_id", sa.String(length=140), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column(
            "saga_id",
            sa.String(length=120),
            sa.ForeignKey("erp_saga_executions.saga_id"),
            nullable=False,
        ),
        sa.Column("step_name", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", saga_step_status, nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("request_snapshot", sa.JSON(), nullable=True),
        sa.Column("response_snapshot", sa.JSON(), nullable=True),
        sa.Column("remote_object_id", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("saga_id", "step_name", name="uq_erp_saga_step_name"),
    )
    op.create_index("ix_erp_saga_steps_tenant_id", "erp_saga_steps", ["tenant_id"])
    op.create_index("ix_erp_saga_steps_saga_id", "erp_saga_steps", ["saga_id"])
    op.create_index("ix_erp_saga_step_sequence", "erp_saga_steps", ["saga_id", "sequence"])

    op.create_table(
        "erp_execution_evidence",
        sa.Column("evidence_id", sa.String(length=140), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column(
            "saga_id",
            sa.String(length=120),
            sa.ForeignKey("erp_saga_executions.saga_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=160), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("source_reference", sa.String(length=200), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=True),
        sa.Column("approval_id", sa.String(length=100), nullable=True),
        sa.Column("actor_id", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("saga_id", "sequence", name="uq_erp_evidence_saga_sequence"),
    )
    op.create_index("ix_erp_execution_evidence_tenant_id", "erp_execution_evidence", ["tenant_id"])
    op.create_index("ix_erp_execution_evidence_saga_id", "erp_execution_evidence", ["saga_id"])
    op.create_index("ix_erp_execution_evidence_evidence_type", "erp_execution_evidence", ["evidence_type"])
    op.create_index("ix_erp_execution_evidence_object_type", "erp_execution_evidence", ["object_type"])
    op.create_index("ix_erp_execution_evidence_object_id", "erp_execution_evidence", ["object_id"])
    op.create_index("ix_erp_execution_evidence_record_hash", "erp_execution_evidence", ["record_hash"])
    op.create_index(
        "ix_erp_evidence_object",
        "erp_execution_evidence",
        ["tenant_id", "object_type", "object_id"],
    )

    op.create_table(
        "a2a_tasks",
        sa.Column("task_id", sa.String(length=120), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("context_id", sa.String(length=120), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("scenario_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="submitted"),
        sa.Column("input_message", sa.JSON(), nullable=False),
        sa.Column("output_artifacts", sa.JSON(), nullable=True),
        sa.Column("task_metadata", sa.JSON(), nullable=True),
        sa.Column("push_notifications", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("callback_url", sa.String(length=500), nullable=True),
        sa.Column("callback_auth_ref", sa.String(length=120), nullable=True),
        sa.Column("callback_status", sa.String(length=30), nullable=True),
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("callback_next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("callback_last_error", sa.String(length=1000), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    for column in [
        "tenant_id",
        "context_id",
        "owner_user_id",
        "scenario_id",
        "status",
        "callback_status",
        "locked_at",
    ]:
        op.create_index(f"ix_a2a_tasks_{column}", "a2a_tasks", [column])
    op.create_index(
        "ix_a2a_task_claim", "a2a_tasks", ["status", "next_attempt_at", "created_at"]
    )
    op.create_index(
        "ix_a2a_task_context", "a2a_tasks", ["tenant_id", "context_id", "updated_at"]
    )

    op.create_table(
        "a2a_task_events",
        sa.Column("event_id", sa.String(length=140), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("task_id", sa.String(length=120), sa.ForeignKey("a2a_tasks.task_id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("message", sa.JSON(), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "sequence", name="uq_a2a_task_event_sequence"),
    )
    for column in ["tenant_id", "task_id", "state"]:
        op.create_index(f"ix_a2a_task_events_{column}", "a2a_task_events", [column])
    op.create_index(
        "ix_a2a_task_event_order",
        "a2a_task_events",
        ["tenant_id", "task_id", "sequence"],
    )

    op.create_table(
        "tenant_onboarding",
        sa.Column(
            "tenant_id",
            sa.String(length=50),
            sa.ForeignKey("erp_tenant_organizations.tenant_id"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="DRAFT"),
        sa.Column("environment", sa.String(length=40), nullable=False, server_default="sandbox"),
        sa.Column("primary_connector_id", sa.String(length=100), nullable=True),
        sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tenant_onboarding_status", "tenant_onboarding", ["status"])

    op.create_table(
        "tenant_subscriptions",
        sa.Column("subscription_id", sa.String(length=120), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=50),
            sa.ForeignKey("erp_tenant_organizations.tenant_id"),
            nullable=False,
        ),
        sa.Column("plan_code", sa.String(length=50), nullable=False, server_default="PILOT"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="TRIAL"),
        sa.Column("monthly_action_quota", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("billing_currency", sa.String(length=3), nullable=False, server_default="CNY"),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("trial_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_subscription_tenant"),
    )
    op.create_index("ix_tenant_subscriptions_tenant_id", "tenant_subscriptions", ["tenant_id"])
    op.create_index("ix_tenant_subscriptions_status", "tenant_subscriptions", ["status"])

    op.create_table(
        "usage_events",
        sa.Column("usage_event_id", sa.String(length=140), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="1.0000"),
        sa.Column("unit", sa.String(length=40), nullable=False, server_default="action"),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("usage_metadata", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "metric_name", "source_type", "source_id", name="uq_usage_source"
        ),
    )
    for column in ["tenant_id", "metric_name", "source_type", "source_id", "occurred_at"]:
        op.create_index(f"ix_usage_events_{column}", "usage_events", [column])
    op.create_index(
        "ix_usage_period", "usage_events", ["tenant_id", "occurred_at", "metric_name"]
    )

    op.create_table(
        "tenant_slos",
        sa.Column("slo_id", sa.String(length=120), primary_key=True),
        sa.Column("tenant_id", sa.String(length=50), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("target_operator", sa.String(length=10), nullable=False, server_default=">="),
        sa.Column("target_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="43200"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "metric_name", name="uq_tenant_slo_metric"),
    )
    op.create_index("ix_tenant_slos_tenant_id", "tenant_slos", ["tenant_id"])
    op.create_index("ix_tenant_slos_metric_name", "tenant_slos", ["metric_name"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in RLS_TABLES:
            policy_name = f"tenant_isolation_{table_name}"
            op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
            op.execute(
                sa.text(
                    f'CREATE POLICY "{policy_name}" ON "{table_name}" '
                    "USING (tenant_id = current_setting('app.current_tenant', true)) "
                    "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in reversed(RLS_TABLES):
            policy_name = f"tenant_isolation_{table_name}"
            op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
            op.execute(sa.text(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))

    op.drop_table("tenant_slos")
    op.drop_table("usage_events")
    op.drop_table("tenant_subscriptions")
    op.drop_table("tenant_onboarding")
    op.drop_table("a2a_task_events")
    op.drop_table("a2a_tasks")
    op.drop_table("erp_execution_evidence")
    op.drop_table("erp_saga_steps")
    op.drop_table("erp_saga_executions")

    for table_name in reversed(ADDED_TENANT_COLUMNS):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_tenant_id")
            batch_op.drop_column("tenant_id")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS erpsagastepstatus")
        op.execute("DROP TYPE IF EXISTS erpsagastatus")
