"""add canonical data model and production data governance

Revision ID: c3d4e5f6a7b8
Revises: a902ebde4f49
Create Date: 2026-06-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a902ebde4f49"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_TENANT = "TENANT-DEMO-COMMERCE"

MONEY_COLUMNS: dict[str, list[str]] = {
    "orders": ["amount"],
    "refund_logs": ["amount"],
    "erp_cost_centers": ["budget_amount"],
    "erp_customer_profiles": ["lifetime_value"],
    "erp_employee_profiles": ["approval_limit"],
    "erp_products": ["price"],
    "erp_order_lines": ["unit_price", "discount_amount", "tax_amount"],
    "erp_payment_transactions": ["amount"],
    "erp_invoices": ["amount"],
    "erp_refund_requests": ["requested_amount", "approved_amount"],
    "erp_journal_entries": ["total_debit", "total_credit"],
    "erp_journal_lines": ["debit", "credit"],
    "erp_approval_matrix_rules": ["min_amount", "max_amount"],
    "erp_open_items": ["debit", "credit", "balance"],
    "erp_purchase_orders": ["total_amount"],
    "erp_purchase_order_lines": ["unit_price"],
    "erp_ap_invoices": ["amount", "tax_amount"],
    "erp_credit_memos": ["amount", "tax_amount"],
    "erp_clearing_documents": ["amount"],
    "erp_reversal_documents": ["amount"],
    "erp_procurement_approval_requests": ["amount"],
    "erp_reimbursement_claims": ["amount"],
    "erp_fixed_assets": ["acquisition_cost"],
    "erp_asset_depreciation_runs": ["depreciation_amount", "accumulated_depreciation"],
    "erp_period_close_runs": ["revenue_total", "expense_total"],
    "erp_consolidation_runs": ["total_revenue", "total_expense", "elimination_amount"],
    "erp_business_approvals": ["approval_limit"],
    "erp_financial_ledger_entries": ["amount"],
}

TENANT_TABLES = [
    "orders",
    "tickets",
    "audit_logs",
    "erp_refund_requests",
    "erp_idempotency_records",
    "erp_external_system_connectors",
    "erp_webhook_subscriptions",
    "erp_outbox_events",
    "erp_system_reconciliation_issues",
]


def _add_tenant_column(table_name: str) -> None:
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


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'FINANCE'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SECURITY'")

    for table_name in TENANT_TABLES:
        _add_tenant_column(table_name)

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("source_system", sa.String(length=50), nullable=False, server_default="MINI_ERP"))
        batch_op.add_column(sa.Column("external_order_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"))
        batch_op.create_index("ix_orders_source_system", ["source_system"], unique=False)
        batch_op.create_index("ix_orders_external_order_id", ["external_order_id"], unique=False)
        batch_op.create_index("ix_orders_tenant_created_at", ["tenant_id", "created_at"], unique=False)
        batch_op.create_unique_constraint(
            "uq_orders_tenant_source_external",
            ["tenant_id", "source_system", "external_order_id"],
        )
        batch_op.create_check_constraint("ck_orders_amount_non_negative", "amount >= 0")

    op.execute(
        "UPDATE orders SET external_order_id = id, "
        "source_system = CASE WHEN id LIKE 'ERP-%' THEN 'MINI_ERP' ELSE 'LEGACY_DEMO' END "
        "WHERE external_order_id IS NULL"
    )

    with op.batch_alter_table("erp_refund_requests") as batch_op:
        batch_op.add_column(sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"))
    with op.batch_alter_table("erp_journal_entries") as batch_op:
        batch_op.add_column(sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"))

    for table_name, columns in MONEY_COLUMNS.items():
        with op.batch_alter_table(table_name) as batch_op:
            for column_name in columns:
                batch_op.alter_column(
                    column_name,
                    existing_type=sa.Float(),
                    type_=sa.Numeric(18, 2),
                    existing_nullable=True,
                )

    with op.batch_alter_table("erp_tax_codes") as batch_op:
        batch_op.alter_column("tax_rate", existing_type=sa.Float(), type_=sa.Numeric(9, 6), existing_nullable=False)
    with op.batch_alter_table("erp_currency_rates") as batch_op:
        batch_op.alter_column("rate", existing_type=sa.Float(), type_=sa.Numeric(18, 8), existing_nullable=False)

    with op.batch_alter_table("erp_payment_transactions") as batch_op:
        batch_op.create_check_constraint("ck_erp_payments_amount_non_negative", "amount >= 0")
    with op.batch_alter_table("erp_invoices") as batch_op:
        batch_op.create_check_constraint("ck_erp_invoices_amount_non_negative", "amount >= 0")
    with op.batch_alter_table("erp_refund_requests") as batch_op:
        batch_op.create_check_constraint("ck_erp_refunds_requested_non_negative", "requested_amount >= 0")
        batch_op.create_check_constraint(
            "ck_erp_refunds_approved_non_negative", "approved_amount IS NULL OR approved_amount >= 0"
        )
        batch_op.create_check_constraint(
            "ck_erp_refunds_approved_lte_requested",
            "approved_amount IS NULL OR approved_amount <= requested_amount",
        )
    with op.batch_alter_table("erp_journal_entries") as batch_op:
        batch_op.create_check_constraint("ck_erp_journal_debit_non_negative", "total_debit >= 0")
        batch_op.create_check_constraint("ck_erp_journal_credit_non_negative", "total_credit >= 0")
        batch_op.create_check_constraint("ck_erp_journal_balanced", "total_debit = total_credit")
    with op.batch_alter_table("erp_journal_lines") as batch_op:
        batch_op.create_check_constraint("ck_erp_journal_lines_debit_non_negative", "debit >= 0")
        batch_op.create_check_constraint("ck_erp_journal_lines_credit_non_negative", "credit >= 0")
        batch_op.create_check_constraint("ck_erp_journal_lines_single_side", "NOT (debit > 0 AND credit > 0)")

    with op.batch_alter_table("erp_outbox_events") as batch_op:
        batch_op.add_column(sa.Column("last_error", sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column("locked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("locked_by", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("dead_lettered_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_index("ix_erp_outbox_events_locked_at", ["locked_at"], unique=False)
        batch_op.create_index(
            "ix_erp_outbox_claim", ["status", "next_attempt_at", "created_at"], unique=False
        )
    with op.batch_alter_table("erp_system_reconciliation_issues") as batch_op:
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(), nullable=True))

    op.create_table(
        "erp_business_object_aliases",
        sa.Column("alias_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("object_type", sa.String(50), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("canonical_id", sa.String(120), nullable=False),
        sa.Column("alias_metadata", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "object_type", "source_system", "external_id", name="uq_erp_alias_source_external"
        ),
    )
    op.create_index("ix_erp_alias_canonical_lookup", "erp_business_object_aliases", ["tenant_id", "object_type", "canonical_id"])
    for column in ["tenant_id", "object_type", "source_system", "external_id", "canonical_id"]:
        op.create_index(f"ix_erp_business_object_aliases_{column}", "erp_business_object_aliases", [column])
    op.execute(
        "INSERT INTO erp_business_object_aliases "
        "(alias_id, tenant_id, object_type, source_system, external_id, canonical_id, active, created_at) "
        "SELECT 'ALIAS-MIGRATED-' || id, tenant_id, 'ORDER', source_system, "
        "external_order_id, id, TRUE, CURRENT_TIMESTAMP FROM orders "
        "WHERE external_order_id IS NOT NULL"
    )
    for legacy_id, canonical_id in [
        ("123456", "ERP-ORD-1001"),
        ("789012", "ERP-ORD-1002"),
        ("456789", "ERP-ORD-1003"),
    ]:
        op.execute(
            sa.text(
                "UPDATE erp_business_object_aliases SET canonical_id = :canonical_id "
                "WHERE object_type = 'ORDER' AND external_id = :legacy_id "
                "AND EXISTS (SELECT 1 FROM orders WHERE id = :canonical_id)"
            ).bindparams(legacy_id=legacy_id, canonical_id=canonical_id)
        )

    op.create_table(
        "erp_pii_records",
        sa.Column("pii_record_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("subject_type", sa.String(80), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=False),
        sa.Column("field_name", sa.String(80), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(120), nullable=False),
        sa.Column("legal_basis", sa.String(120), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "subject_type", "subject_id", "field_name", name="uq_erp_pii_subject_field"),
    )
    op.create_index("ix_erp_pii_retention", "erp_pii_records", ["expires_at", "deleted_at"])
    for column in ["tenant_id", "subject_type", "subject_id", "expires_at"]:
        op.create_index(f"ix_erp_pii_records_{column}", "erp_pii_records", [column])

    op.create_table(
        "erp_cdc_events",
        sa.Column("cdc_event_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("source_system", sa.String(80), nullable=False),
        sa.Column("source_position", sa.String(200), nullable=False),
        sa.Column("object_type", sa.String(80), nullable=False),
        sa.Column("object_id", sa.String(120), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("source_system", "source_position", name="uq_erp_cdc_source_position"),
    )
    op.create_index("ix_erp_cdc_unpublished", "erp_cdc_events", ["published_at", "occurred_at"])
    for column in ["tenant_id", "source_system", "object_type", "object_id", "occurred_at"]:
        op.create_index(f"ix_erp_cdc_events_{column}", "erp_cdc_events", [column])

    op.create_table(
        "erp_cdc_checkpoints",
        sa.Column("checkpoint_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("source_system", sa.String(80), nullable=False),
        sa.Column("stream_name", sa.String(120), nullable=False),
        sa.Column("source_position", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_erp_cdc_checkpoints_tenant_id", "erp_cdc_checkpoints", ["tenant_id"])
    op.create_index("ix_erp_cdc_checkpoints_source_system", "erp_cdc_checkpoints", ["source_system"])

    op.create_table(
        "erp_data_contracts",
        sa.Column("contract_id", sa.String(120), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("contract_name", sa.String(160), nullable=False),
        sa.Column("object_type", sa.String(80), nullable=False),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("compatibility_mode", sa.String(30), nullable=False),
        sa.Column("active_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "contract_name", name="uq_erp_data_contract_name"),
    )
    for column in ["tenant_id", "contract_name", "object_type"]:
        op.create_index(f"ix_erp_data_contracts_{column}", "erp_data_contracts", [column])

    op.create_table(
        "erp_data_contract_versions",
        sa.Column("contract_version_id", sa.String(140), primary_key=True),
        sa.Column("contract_id", sa.String(120), sa.ForeignKey("erp_data_contracts.contract_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_definition", sa.JSON(), nullable=False),
        sa.Column("schema_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("contract_id", "version", name="uq_erp_data_contract_version"),
    )
    op.create_index("ix_erp_data_contract_versions_contract_id", "erp_data_contract_versions", ["contract_id"])
    op.create_index("ix_erp_data_contract_versions_schema_fingerprint", "erp_data_contract_versions", ["schema_fingerprint"])

    op.create_table(
        "erp_data_lineage_edges",
        sa.Column("lineage_edge_id", sa.String(140), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("source_asset", sa.String(200), nullable=False),
        sa.Column("target_asset", sa.String(200), nullable=False),
        sa.Column("transformation", sa.String(200), nullable=False),
        sa.Column("job_name", sa.String(160), nullable=True),
        sa.Column("column_mapping", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "source_asset", "target_asset", "transformation", name="uq_erp_lineage_edge"
        ),
    )
    for column in ["tenant_id", "source_asset", "target_asset"]:
        op.create_index(f"ix_erp_data_lineage_edges_{column}", "erp_data_lineage_edges", [column])

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TABLE erp_event_archive (
                archive_event_id VARCHAR(140) NOT NULL,
                occurred_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                tenant_id VARCHAR(50) NOT NULL,
                source_table VARCHAR(120) NOT NULL,
                event_type VARCHAR(120) NOT NULL,
                aggregate_id VARCHAR(140) NOT NULL,
                payload JSON NOT NULL,
                PRIMARY KEY (archive_event_id, occurred_at)
            ) PARTITION BY RANGE (occurred_at)
            """
        )
        op.execute(
            "CREATE TABLE erp_event_archive_2026_06 PARTITION OF erp_event_archive "
            "FOR VALUES FROM ('2026-06-01') TO ('2026-07-01')"
        )
        op.execute(
            "CREATE TABLE erp_event_archive_default PARTITION OF erp_event_archive DEFAULT"
        )
    else:
        op.create_table(
            "erp_event_archive",
            sa.Column("archive_event_id", sa.String(140), primary_key=True),
            sa.Column("occurred_at", sa.DateTime(), primary_key=True),
            sa.Column("tenant_id", sa.String(50), nullable=False),
            sa.Column("source_table", sa.String(120), nullable=False),
            sa.Column("event_type", sa.String(120), nullable=False),
            sa.Column("aggregate_id", sa.String(140), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
        )
    op.create_index("ix_erp_event_archive_tenant_id", "erp_event_archive", ["tenant_id"])
    op.create_index("ix_erp_event_archive_event_type", "erp_event_archive", ["event_type"])
    op.create_index("ix_erp_event_archive_aggregate_id", "erp_event_archive", ["aggregate_id"])
    op.create_index(
        "ix_erp_event_archive_tenant_occurred", "erp_event_archive", ["tenant_id", "occurred_at"]
    )
    op.create_index(
        "ix_erp_event_archive_type_occurred", "erp_event_archive", ["event_type", "occurred_at"]
    )

    if bind.dialect.name == "postgresql":
        rls_tables = TENANT_TABLES + [
            "erp_business_object_aliases",
            "erp_pii_records",
            "erp_cdc_events",
            "erp_cdc_checkpoints",
            "erp_data_contracts",
            "erp_data_lineage_edges",
            "erp_event_archive",
        ]
        for table_name in rls_tables:
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
        rls_tables = TENANT_TABLES + [
            "erp_business_object_aliases",
            "erp_pii_records",
            "erp_cdc_events",
            "erp_cdc_checkpoints",
            "erp_data_contracts",
            "erp_data_lineage_edges",
            "erp_event_archive",
        ]
        for table_name in rls_tables:
            op.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_isolation_{table_name}" ON "{table_name}"'))
            op.execute(sa.text(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))

    op.drop_table("erp_event_archive")
    op.drop_table("erp_data_lineage_edges")
    op.drop_table("erp_data_contract_versions")
    op.drop_table("erp_data_contracts")
    op.drop_table("erp_cdc_checkpoints")
    op.drop_table("erp_cdc_events")
    op.drop_table("erp_pii_records")
    op.drop_table("erp_business_object_aliases")

    with op.batch_alter_table("erp_system_reconciliation_issues") as batch_op:
        batch_op.drop_column("resolved_at")
    with op.batch_alter_table("erp_outbox_events") as batch_op:
        batch_op.drop_index("ix_erp_outbox_claim")
        batch_op.drop_index("ix_erp_outbox_events_locked_at")
        for column in ["replay_count", "dead_lettered_at", "locked_by", "locked_at", "last_error"]:
            batch_op.drop_column(column)

    with op.batch_alter_table("erp_journal_lines") as batch_op:
        for name in [
            "ck_erp_journal_lines_single_side",
            "ck_erp_journal_lines_credit_non_negative",
            "ck_erp_journal_lines_debit_non_negative",
        ]:
            batch_op.drop_constraint(name, type_="check")
    with op.batch_alter_table("erp_journal_entries") as batch_op:
        for name in ["ck_erp_journal_balanced", "ck_erp_journal_credit_non_negative", "ck_erp_journal_debit_non_negative"]:
            batch_op.drop_constraint(name, type_="check")
    with op.batch_alter_table("erp_refund_requests") as batch_op:
        for name in [
            "ck_erp_refunds_approved_lte_requested",
            "ck_erp_refunds_approved_non_negative",
            "ck_erp_refunds_requested_non_negative",
        ]:
            batch_op.drop_constraint(name, type_="check")
    with op.batch_alter_table("erp_invoices") as batch_op:
        batch_op.drop_constraint("ck_erp_invoices_amount_non_negative", type_="check")
    with op.batch_alter_table("erp_payment_transactions") as batch_op:
        batch_op.drop_constraint("ck_erp_payments_amount_non_negative", type_="check")

    with op.batch_alter_table("erp_currency_rates") as batch_op:
        batch_op.alter_column("rate", existing_type=sa.Numeric(18, 8), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table("erp_tax_codes") as batch_op:
        batch_op.alter_column("tax_rate", existing_type=sa.Numeric(9, 6), type_=sa.Float(), existing_nullable=False)
    for table_name, columns in reversed(list(MONEY_COLUMNS.items())):
        with op.batch_alter_table(table_name) as batch_op:
            for column_name in columns:
                batch_op.alter_column(
                    column_name,
                    existing_type=sa.Numeric(18, 2),
                    type_=sa.Float(),
                    existing_nullable=True,
                )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("ck_orders_amount_non_negative", type_="check")
        batch_op.drop_constraint("uq_orders_tenant_source_external", type_="unique")
        batch_op.drop_index("ix_orders_tenant_created_at")
        batch_op.drop_index("ix_orders_external_order_id")
        batch_op.drop_index("ix_orders_source_system")
        batch_op.drop_column("currency")
        batch_op.drop_column("external_order_id")
        batch_op.drop_column("source_system")

    with op.batch_alter_table("erp_journal_entries") as batch_op:
        batch_op.drop_column("currency")
    with op.batch_alter_table("erp_refund_requests") as batch_op:
        batch_op.drop_column("currency")

    for table_name in reversed(TENANT_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_tenant_id")
            batch_op.drop_column("tenant_id")
