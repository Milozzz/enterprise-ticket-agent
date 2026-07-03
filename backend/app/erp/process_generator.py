"""Process-first Mini ERP data generation.

The goal is to create ERP-like data that is internally consistent across
orders, payments, shipments, refund requests, approvals, ledger entries, and
process event logs. This is deliberately deterministic so demos, evals, and
tests can share the same business cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccessRequestRecord,
    APInvoice,
    ApprovalMatrixRule,
    AssetDepreciationRun,
    BillOfMaterial,
    BillOfMaterialLine,
    BusinessApprovalRecord,
    BusinessPartner,
    BusinessUnit,
    ChangeDocument,
    ClearingDocument,
    CompanyCode,
    CompensationTransaction,
    ConsolidationGroup,
    ConsolidationRun,
    CostCenter,
    CreditMemoDocument,
    CurrencyRate,
    CustomerProfile,
    CustomerComplaint,
    DataQualityIssue,
    DataQualityRule,
    Department,
    DocumentFlow,
    EmployeeProfile,
    ErpApInvoiceStatus,
    ErpAssetStatus,
    ErpBomStatus,
    ErpBusinessRequestStatus,
    ErpCompensationStatus,
    ErpConsolidationStatus,
    ErpConnectorStatus,
    ErpDocumentStatus,
    ErpFiscalPeriodStatus,
    ErpFulfillmentStatus,
    ErpGoodsReceiptStatus,
    ErpIdempotencyStatus,
    ErpInspectionResult,
    ErpInventoryMovementType,
    ErpJournalStatus,
    ErpLedgerEntryType,
    ErpMasterDataRequestStatus,
    ErpOutboxStatus,
    ErpOrderLine,
    ErpPartnerType,
    ErpPaymentStatus,
    ErpPolicyStatus,
    ErpProcurementStatus,
    ErpRefundStatus,
    ErpReservationStatus,
    ErpReturnStatus,
    ErpSupportTicketStatus,
    ErpWorkOrderStatus,
    FiscalPeriod,
    FinancialLedgerEntry,
    FixedAsset,
    GoodsReceipt,
    GoodsReceiptLine,
    IdempotencyRecord,
    InventoryItem,
    InventoryBatch,
    InventoryMovement,
    InventorySerial,
    InvoiceDocument,
    JournalEntry,
    JournalLine,
    MasterDataChangeRequest,
    MasterDataDuplicateCandidate,
    MasterDataValidationResult,
    MasterDataVersion,
    OpenItem,
    Order,
    OutboxEvent,
    PaymentTransaction,
    PeriodCloseRun,
    Plant,
    PolicyVersion,
    ProcessEventLog,
    ProductCatalog,
    PurchaseOrder,
    PurchaseOrderLine,
    ProcurementApprovalRequest,
    RefundRequest,
    ReimbursementClaim,
    ReversalDocument,
    ReturnAuthorization,
    ReturnInspection,
    SalesOrganization,
    ShipmentDocument,
    StockReservation,
    SupportTicket,
    Supplier,
    SystemReconciliationIssue,
    TaxCode,
    TenantOrganization,
    User,
    UserRole,
    Warehouse,
    WebhookSubscription,
    WorkOrder,
    WorkOrderComponentIssue,
    ExternalSystemConnector,
)
from app.erp.identity import seed_legacy_order_aliases
from app.erp.financial_validation import (
    FinancialInvariantError,
    require_balanced_journal,
    require_currency_consistency,
    require_non_negative,
)


@dataclass(frozen=True)
class ReturnToRefundCase:
    case_id: str
    order_id: str
    refund_request_id: str
    variant: str
    expected_risk_level: str
    expected_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "order_id": self.order_id,
            "refund_request_id": self.refund_request_id,
            "variant": self.variant,
            "expected_risk_level": self.expected_risk_level,
            "expected_status": self.expected_status,
        }


BASE_TIME = datetime(2026, 5, 13, 9, 0, 0)


async def seed_return_to_refund_demo(session: AsyncSession) -> dict[str, Any]:
    """Seed a deterministic ERP-like return-to-refund demo dataset."""

    cases = [
        await seed_return_to_refund_case(session, variant="standard_low_risk"),
        await seed_return_to_refund_case(session, variant="high_value_hitl"),
        await seed_return_to_refund_case(session, variant="non_returnable_exception"),
        await seed_return_to_refund_case(session, variant="duplicate_refund_attempt"),
    ]
    await seed_legacy_order_aliases(session)
    await session.commit()
    return {
        "dataset": "return_to_refund_demo",
        "case_count": len(cases),
        "cases": [case.to_dict() for case in cases],
        "invariants": [
            "captured payment amount equals order amount",
            "approved refund amount never exceeds order amount",
            "high-value refund requires manager approval",
            "non-returnable products are rejected before refund execution",
            "duplicate refund attempts are recorded and skipped",
            "journal entries are debit-credit balanced",
            "return authorization and inspection exist for returnable refund cases",
            "inventory movement records every shipment and restock side effect",
            "policy version and approval matrix are linked to refund decisions",
            "document flow links order, payment, invoice, shipment, refund, return, and finance postings",
            "open items show AR clearing and refund/AP liabilities",
            "change documents record policy and refund status transitions",
            "procurement, batch, serial, and stock reservation data make inventory traceable",
            "manufacturing, assets, FX, period close, and consolidation samples make the data layer ERP-like",
            "executed refunds create credit memos, clearing documents, and outbox events",
            "duplicate or rejected refund paths record reversal or compensation transactions",
            "procurement, reimbursement, and access requests have approval depth and timeout examples",
            "master data governance covers field validation, duplicate detection, change approval, and version history",
            "exception datasets include missing fields, duplicate customers, abnormal tax codes, stock mismatch, invoice mismatch, messy history, and cross-system inconsistency",
        ],
    }


async def seed_return_to_refund_case(session: AsyncSession, *, variant: str) -> ReturnToRefundCase:
    await _seed_master_data(session)
    spec = _case_spec(variant)
    user = User(
        id=spec["user_id"],
        name=spec["customer_name"],
        email=spec["customer_email"],
        role=UserRole.USER,
    )
    customer = CustomerProfile(
        customer_id=spec["customer_id"],
        user_id=spec["user_id"],
        segment=spec["segment"],
        region=spec["region"],
        lifetime_value=spec["lifetime_value"],
        risk_score=spec["customer_risk_score"],
        refund_count_90d=spec["refund_count_90d"],
        complaint_count_90d=spec["complaint_count_90d"],
        tags=spec["customer_tags"],
    )
    order = Order(
        id=spec["order_id"],
        tenant_id="TENANT-DEMO-COMMERCE",
        source_system="MINI_ERP",
        external_order_id=spec["order_id"],
        user_id=spec["user_id"],
        amount=spec["amount"],
        currency="CNY",
        status=spec["order_status"],
        items=[{"sku": spec["sku"], "name": spec["product_name"], "qty": 1, "price": spec["amount"]}],
        shipping_address=spec["shipping_address"],
        created_at=spec["created_at"],
    )
    line = ErpOrderLine(
        line_id=f"LINE-{spec['order_id']}",
        order_id=spec["order_id"],
        product_id=spec["product_id"],
        sku=spec["sku"],
        quantity=1,
        unit_price=spec["amount"],
        discount_amount=0.0,
        tax_amount=round(spec["amount"] * 0.06, 2),
        fulfillment_status=ErpFulfillmentStatus.DELIVERED,
        returnable=spec["returnable"],
    )
    payment = PaymentTransaction(
        payment_id=f"PAY-{spec['order_id']}",
        order_id=spec["order_id"],
        provider="mockpay",
        method="card",
        amount=spec["amount"],
        currency="CNY",
        status=ErpPaymentStatus.CAPTURED,
        captured_at=spec["created_at"] + timedelta(minutes=2),
        external_reference=f"MP-{spec['order_id']}",
    )
    invoice = InvoiceDocument(
        invoice_id=f"INV-{spec['order_id']}",
        order_id=spec["order_id"],
        amount=spec["amount"],
        currency="CNY",
        status=ErpDocumentStatus.POSTED,
        tax_snapshot={"tax_rate": 0.06, "tax_amount": round(spec["amount"] * 0.06, 2)},
        issued_at=spec["created_at"] + timedelta(minutes=3),
    )
    shipment = ShipmentDocument(
        shipment_id=f"SHP-{spec['order_id']}",
        order_id=spec["order_id"],
        warehouse_id="WH-SH-01",
        carrier="SF Express",
        tracking_number=f"SF{spec['order_id'].replace('-', '')}",
        status=ErpFulfillmentStatus.DELIVERED,
        shipped_at=spec["created_at"] + timedelta(hours=4),
        delivered_at=spec["created_at"] + timedelta(days=2),
    )
    refund = RefundRequest(
        refund_request_id=spec["refund_request_id"],
        order_id=spec["order_id"],
        customer_id=spec["customer_id"],
        requested_amount=spec["requested_amount"],
        approved_amount=spec["approved_amount"],
        currency="CNY",
        reason_code=spec["reason_code"],
        description=spec["description"],
        risk_level=spec["risk_level"],
        status=spec["refund_status"],
        policy_snapshot=spec["policy_snapshot"],
        duplicate_key=f"refund:{spec['order_id']}:{spec['requested_amount']:.2f}",
        created_at=spec["created_at"] + timedelta(days=3),
    )

    records: list[Any] = [user, *_customer_partner_records(spec), customer, order, line, payment, invoice, shipment, refund]
    records.extend(_support_records(spec))
    records.extend(_return_records(spec))
    records.extend(_inventory_movements(spec))
    records.extend(_stock_reservations(spec))
    records.extend(_approval_records(spec))
    records.extend(_ledger_entries(spec))
    records.extend(_journal_records(spec))
    records.extend(_open_items(spec))
    records.extend(_refund_finance_documents(spec))
    records.extend(_refund_compensation_records(spec))
    records.extend(_case_outbox_events(spec))
    records.extend(_document_flows(spec))
    records.extend(_change_documents(spec))
    records.extend(_data_quality_issues(spec))
    records.extend(_event_logs(spec))
    for record in records:
        await session.merge(record)
    return ReturnToRefundCase(
        case_id=spec["case_id"],
        order_id=spec["order_id"],
        refund_request_id=spec["refund_request_id"],
        variant=variant,
        expected_risk_level=spec["risk_level"],
        expected_status=spec["refund_status"].value,
    )


async def validate_return_to_refund_case(session: AsyncSession, order_id: str) -> dict[str, Any]:
    """Return business invariant checks for a seeded return-to-refund case."""

    order = await session.scalar(select(Order).where(Order.id == order_id))
    payment = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.order_id == order_id))
    invoice = await session.scalar(select(InvoiceDocument).where(InvoiceDocument.order_id == order_id))
    refund = await session.scalar(select(RefundRequest).where(RefundRequest.order_id == order_id))
    ledger_rows = (
        await session.execute(select(FinancialLedgerEntry).where(FinancialLedgerEntry.order_id == order_id))
    ).scalars().all()
    source_documents = [f"INV-{order_id}", f"PAY-{order_id}", refund.refund_request_id] if refund else []
    journal_rows = (
        await session.execute(select(JournalEntry).where(JournalEntry.source_document.in_(source_documents)))
    ).scalars().all()
    return_authorization = await session.scalar(select(ReturnAuthorization).where(ReturnAuthorization.order_id == order_id))
    inventory_movements = (
        await session.execute(select(InventoryMovement).where(InventoryMovement.order_id == order_id))
    ).scalars().all()
    document_flows = (
        await session.execute(select(DocumentFlow).where(DocumentFlow.order_id == order_id))
    ).scalars().all()
    open_items = (
        await session.execute(select(OpenItem).where(OpenItem.source_document.in_(source_documents)))
    ).scalars().all()
    change_documents = (
        await session.execute(
            select(ChangeDocument).where(
                ChangeDocument.object_id.in_([order_id, refund.refund_request_id] if refund else [order_id])
            )
        )
    ).scalars().all()
    stock_reservations = (
        await session.execute(select(StockReservation).where(StockReservation.order_id == order_id))
    ).scalars().all()
    credit_memos: list[CreditMemoDocument] = []
    clearing_documents: list[ClearingDocument] = []
    reversal_documents: list[ReversalDocument] = []
    compensation_transactions: list[CompensationTransaction] = []
    outbox_events: list[OutboxEvent] = []
    if refund:
        credit_memos = (
            await session.execute(select(CreditMemoDocument).where(CreditMemoDocument.refund_request_id == refund.refund_request_id))
        ).scalars().all()
        credit_memo_ids = [memo.credit_memo_id for memo in credit_memos]
        if credit_memo_ids:
            clearing_documents = (
                await session.execute(select(ClearingDocument).where(ClearingDocument.source_document.in_(credit_memo_ids)))
            ).scalars().all()
        reversal_documents = (
            await session.execute(select(ReversalDocument).where(ReversalDocument.source_document == refund.refund_request_id))
        ).scalars().all()
        compensation_transactions = (
            await session.execute(select(CompensationTransaction).where(CompensationTransaction.object_id == refund.refund_request_id))
        ).scalars().all()
        outbox_aggregate_ids = [refund.refund_request_id, *credit_memo_ids, *[item.compensation_id for item in compensation_transactions]]
        outbox_events = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id.in_(outbox_aggregate_ids)))
        ).scalars().all()
    fiscal_period = await session.scalar(
        select(FiscalPeriod).where(FiscalPeriod.company_code_id == "CCODE-CN01", FiscalPeriod.status == ErpFiscalPeriodStatus.OPEN)
    )
    tax_code = await session.scalar(select(TaxCode).where(TaxCode.code == "O6", TaxCode.active.is_(True)))
    policy = await session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.policy_id == "REFUND-POLICY",
            PolicyVersion.status == ErpPolicyStatus.ACTIVE,
        )
    )
    if not order or not payment or not refund:
        return {"valid": False, "errors": [f"case '{order_id}' is incomplete"]}

    errors: list[str] = []
    currency_documents: dict[str, str | None] = {
        "order": order.currency,
        "payment": payment.currency,
        "refund": refund.currency,
    }
    if invoice:
        currency_documents["invoice"] = invoice.currency
    currency_documents.update(
        {f"ledger:{row.ledger_entry_id}": row.currency for row in ledger_rows}
    )
    currency_documents.update(
        {f"open_item:{row.open_item_id}": row.currency for row in open_items}
    )
    currency_documents.update(
        {f"credit_memo:{row.credit_memo_id}": row.currency for row in credit_memos}
    )
    currency_documents.update(
        {f"clearing:{row.clearing_document_id}": row.currency for row in clearing_documents}
    )
    try:
        require_currency_consistency(currency_documents)
        require_non_negative(
            {
                "order": order.amount,
                "payment": payment.amount,
                "refund_requested": refund.requested_amount,
                **{
                    f"ledger:{row.ledger_entry_id}": row.amount
                    for row in ledger_rows
                },
            }
        )
    except FinancialInvariantError as exc:
        errors.append(str(exc))
    if round(payment.amount, 2) != round(order.amount, 2):
        errors.append("captured payment amount does not match order amount")
    if refund.approved_amount is not None and refund.approved_amount > order.amount:
        errors.append("approved refund amount exceeds order amount")
    if refund.risk_level == "high" and refund.status == ErpRefundStatus.EXECUTED:
        has_approval = any(entry.entry_type == ErpLedgerEntryType.REFUND for entry in ledger_rows)
        if not has_approval:
            errors.append("executed high-risk refund has no refund ledger entry")
    for journal in journal_rows:
        try:
            require_balanced_journal(journal.total_debit, journal.total_credit)
            require_currency_consistency(
                {"order": order.currency, f"journal:{journal.journal_entry_id}": journal.currency}
            )
        except FinancialInvariantError as exc:
            errors.append(f"journal entry {journal.journal_entry_id}: {exc}")
    if refund.status != ErpRefundStatus.DUPLICATE_SKIPPED and not return_authorization:
        errors.append("non-duplicate refund case has no return authorization record")
    if not inventory_movements:
        errors.append("order has no inventory movement")
    if not document_flows:
        errors.append("order has no ERP document flow")
    if not open_items:
        errors.append("order has no AR/refund open item record")
    if not change_documents:
        errors.append("order has no change document")
    if not stock_reservations:
        errors.append("order has no stock reservation")
    if not fiscal_period:
        errors.append("company code has no open fiscal period")
    if not tax_code:
        errors.append("active output tax code is missing")
    if not policy:
        errors.append("active refund policy version is missing")
    if refund.status == ErpRefundStatus.EXECUTED:
        if not credit_memos:
            errors.append("executed refund has no credit memo document")
        if not clearing_documents:
            errors.append("executed refund has no clearing document")
        if not outbox_events:
            errors.append("executed refund has no outbox event")
    if refund.status in {ErpRefundStatus.REJECTED, ErpRefundStatus.DUPLICATE_SKIPPED} and not compensation_transactions:
        errors.append("non-executed refund path has no compensation transaction")
    if refund.status == ErpRefundStatus.DUPLICATE_SKIPPED and not reversal_documents:
        errors.append("duplicate refund path has no reversal document")
    return {
        "valid": not errors,
        "order_id": order_id,
        "errors": errors,
        "checks": {
            "payment_amount": payment.amount,
            "order_amount": order.amount,
            "approved_refund_amount": refund.approved_amount,
            "currency": order.currency,
            "ledger_entry_count": len(ledger_rows),
            "journal_entry_count": len(journal_rows),
            "inventory_movement_count": len(inventory_movements),
            "document_flow_count": len(document_flows),
            "open_item_count": len(open_items),
            "credit_memo_count": len(credit_memos),
            "clearing_document_count": len(clearing_documents),
            "reversal_document_count": len(reversal_documents),
            "compensation_transaction_count": len(compensation_transactions),
            "outbox_event_count": len(outbox_events),
            "change_document_count": len(change_documents),
            "stock_reservation_count": len(stock_reservations),
            "has_return_authorization": bool(return_authorization),
            "has_open_fiscal_period": bool(fiscal_period),
            "has_active_tax_code": bool(tax_code),
            "has_active_policy": bool(policy),
        },
    }


async def _seed_master_data(session: AsyncSession) -> None:
    records: list[Any] = [
        TenantOrganization(
            tenant_id="TENANT-DEMO-COMMERCE",
            name="Demo Commerce Group",
            industry="ecommerce",
            region="CN",
            plan="enterprise_trial",
        ),
        BusinessUnit(
            business_unit_id="BU-COMMERCE-CN",
            tenant_id="TENANT-DEMO-COMMERCE",
            name="China Commerce Business Unit",
            region="CN-East",
        ),
        CompanyCode(
            company_code_id="CCODE-CN01",
            tenant_id="TENANT-DEMO-COMMERCE",
            code="1000",
            legal_name="Demo Commerce China Ltd.",
            country="CN",
            currency="CNY",
            fiscal_variant="K4",
        ),
        CompanyCode(
            company_code_id="CCODE-CN02",
            tenant_id="TENANT-DEMO-COMMERCE",
            code="2000",
            legal_name="Demo Commerce Services Ltd.",
            country="CN",
            currency="CNY",
            fiscal_variant="K4",
        ),
        SalesOrganization(
            sales_org_id="SORG-CN-ONLINE",
            company_code_id="CCODE-CN01",
            code="CN10",
            name="China Online Sales",
            distribution_channel="online",
            division="consumer_electronics",
        ),
        FiscalPeriod(
            fiscal_period_id="FP-1000-2026-05",
            company_code_id="CCODE-CN01",
            fiscal_year=2026,
            period=5,
            start_at=datetime(2026, 5, 1),
            end_at=datetime(2026, 5, 31, 23, 59, 59),
            status=ErpFiscalPeriodStatus.OPEN,
        ),
        FiscalPeriod(
            fiscal_period_id="FP-2000-2026-05",
            company_code_id="CCODE-CN02",
            fiscal_year=2026,
            period=5,
            start_at=datetime(2026, 5, 1),
            end_at=datetime(2026, 5, 31, 23, 59, 59),
            status=ErpFiscalPeriodStatus.OPEN,
        ),
        TaxCode(
            tax_code_id="TAX-CN-OUT-6",
            company_code_id="CCODE-CN01",
            code="O6",
            description="China output VAT 6%",
            tax_rate=0.06,
            category="output_vat",
        ),
        TaxCode(
            tax_code_id="TAX-CN-IN-13",
            company_code_id="CCODE-CN01",
            code="I13",
            description="China input VAT 13%",
            tax_rate=0.13,
            category="input_vat",
        ),
        Department(
            department_id="DEPT-SUPPORT",
            business_unit_id="BU-COMMERCE-CN",
            name="Customer Operations",
            manager_employee_id="EMP-OPS-001",
        ),
        Department(
            department_id="DEPT-FINANCE",
            business_unit_id="BU-COMMERCE-CN",
            name="Finance Operations",
            manager_employee_id="EMP-FIN-001",
        ),
        CostCenter(
            cost_center_id="CC-SUPPORT",
            department_id="DEPT-SUPPORT",
            name="Customer Support Cost Center",
            budget_amount=300000.0,
        ),
        CostCenter(
            cost_center_id="CC-FINANCE",
            department_id="DEPT-FINANCE",
            name="Finance Operations Cost Center",
            budget_amount=500000.0,
        ),
        BusinessPartner(
            partner_id="BP-SUP-POWER-01",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.SUPPLIER,
            display_name="Shenzhen Power Components",
            legal_name="Shenzhen Power Components Co., Ltd.",
            country="CN",
            tax_registration_number="CN-TAX-SUP-POWER-01",
            email="sales@power.example.com",
            risk_rating="low",
            payment_terms="NET30",
            currency="CNY",
        ),
        BusinessPartner(
            partner_id="BP-SUP-VISION-02",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.SUPPLIER,
            display_name="Hangzhou Vision Devices",
            legal_name="Hangzhou Vision Devices Co., Ltd.",
            country="CN",
            tax_registration_number="CN-TAX-SUP-VISION-02",
            email="finance@vision.example.com",
            risk_rating="normal",
            payment_terms="NET45",
            currency="CNY",
        ),
        BusinessPartner(
            partner_id="BP-SUP-GIFT-03",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.SUPPLIER,
            display_name="Suzhou Custom Gift Workshop",
            legal_name="Suzhou Custom Gift Workshop Co., Ltd.",
            country="CN",
            tax_registration_number=None,
            email="ops@gift.example.com",
            risk_rating="medium",
            payment_terms="NET30",
            currency="CNY",
        ),
        BusinessPartner(
            partner_id="BP-EMP-OPS-001",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.EMPLOYEE,
            display_name="Ops Manager",
            legal_name="Ops Manager",
            country="CN",
            email="ops.manager@example.com",
            risk_rating="low",
            payment_terms="payroll",
            currency="CNY",
        ),
        BusinessPartner(
            partner_id="BP-INTERCO-SVC",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.INTERCOMPANY,
            display_name="Demo Commerce Services",
            legal_name="Demo Commerce Services Ltd.",
            country="CN",
            tax_registration_number="CN-TAX-INTERCO-SVC",
            risk_rating="low",
            payment_terms="NET30",
            currency="CNY",
        ),
        Supplier(
            supplier_id="SUP-POWER-01",
            partner_id="BP-SUP-POWER-01",
            supplier_code="SUPPWR01",
            category="battery_components",
            rating="A",
            lead_time_days=5,
            preferred=True,
        ),
        Supplier(
            supplier_id="SUP-VISION-02",
            partner_id="BP-SUP-VISION-02",
            supplier_code="SUPVIS02",
            category="display_devices",
            rating="B",
            lead_time_days=10,
            preferred=True,
        ),
        Supplier(
            supplier_id="SUP-GIFT-03",
            partner_id="BP-SUP-GIFT-03",
            supplier_code="SUPGFT03",
            category="customized_goods",
            rating="C",
            lead_time_days=14,
            preferred=False,
        ),
        User(id=8001, name="Ops Manager", email="ops.manager@example.com", role=UserRole.MANAGER),
        User(id=8002, name="Finance Reviewer", email="finance.reviewer@example.com", role=UserRole.FINANCE),
        EmployeeProfile(
            employee_id="EMP-OPS-001",
            user_id=8001,
            department="Customer Operations",
            title="Support Manager",
            manager_employee_id=None,
            cost_center="CC-SUPPORT",
            approval_limit=5000.0,
        ),
        EmployeeProfile(
            employee_id="EMP-FIN-001",
            user_id=8002,
            department="Finance",
            title="Refund Accountant",
            manager_employee_id=None,
            cost_center="CC-FINANCE",
            approval_limit=20000.0,
        ),
        ProductCatalog(
            product_id="PROD-PB-20000",
            sku="PB-20000",
            name="Wireless Power Bank 20000mAh",
            category="electronics",
            price=299.0,
            returnable=True,
            warranty_days=7,
            supplier_id="SUP-POWER-01",
        ),
        ProductCatalog(
            product_id="PROD-PROJECTOR-4K",
            sku="PJ-4K-PRO",
            name="4K Smart Projector Pro",
            category="electronics",
            price=1299.0,
            returnable=True,
            warranty_days=15,
            supplier_id="SUP-VISION-02",
        ),
        ProductCatalog(
            product_id="PROD-CUSTOM-GIFT",
            sku="CUSTOM-GIFT-01",
            name="Customized Gift Box",
            category="customized_goods",
            price=199.0,
            returnable=False,
            warranty_days=0,
            supplier_id="SUP-GIFT-03",
        ),
        ProductCatalog(
            product_id="PROD-CELL-5000",
            sku="CELL-5000",
            name="Lithium Battery Cell 5000mAh",
            category="raw_material",
            price=28.0,
            returnable=False,
            warranty_days=0,
            supplier_id="SUP-POWER-01",
        ),
        ProductCatalog(
            product_id="PROD-PCBA-PB",
            sku="PCBA-PB-01",
            name="Power Bank Control Board",
            category="raw_material",
            price=18.0,
            returnable=False,
            warranty_days=0,
            supplier_id="SUP-POWER-01",
        ),
        Warehouse(warehouse_id="WH-SH-01", code="SH01", name="Shanghai Fulfillment Center", region="CN-East"),
        Plant(
            plant_id="PLANT-SH-01",
            company_code_id="CCODE-CN01",
            sales_org_id="SORG-CN-ONLINE",
            warehouse_id="WH-SH-01",
            code="SH01",
            name="Shanghai Fulfillment and Light Assembly Plant",
            region="CN-East",
        ),
        InventoryItem(
            inventory_id="INV-PB-20000-SH01",
            product_id="PROD-PB-20000",
            warehouse_id="WH-SH-01",
            quantity_on_hand=180,
            quantity_reserved=12,
            reorder_point=40,
        ),
        InventoryItem(
            inventory_id="INV-PJ-4K-SH01",
            product_id="PROD-PROJECTOR-4K",
            warehouse_id="WH-SH-01",
            quantity_on_hand=20,
            quantity_reserved=4,
            reorder_point=8,
        ),
        InventoryItem(
            inventory_id="INV-CUSTOM-GIFT-SH01",
            product_id="PROD-CUSTOM-GIFT",
            warehouse_id="WH-SH-01",
            quantity_on_hand=7,
            quantity_reserved=2,
            reorder_point=5,
        ),
        InventoryItem(
            inventory_id="INV-CELL-5000-SH01",
            product_id="PROD-CELL-5000",
            warehouse_id="WH-SH-01",
            quantity_on_hand=1000,
            quantity_reserved=120,
            reorder_point=200,
        ),
        InventoryItem(
            inventory_id="INV-PCBA-PB-SH01",
            product_id="PROD-PCBA-PB",
            warehouse_id="WH-SH-01",
            quantity_on_hand=420,
            quantity_reserved=80,
            reorder_point=100,
        ),
        PurchaseOrder(
            purchase_order_id="PO-2026-0001",
            company_code_id="CCODE-CN01",
            supplier_id="SUP-POWER-01",
            plant_id="PLANT-SH-01",
            status=ErpProcurementStatus.INVOICED,
            currency="CNY",
            total_amount=5600.0,
            ordered_at=BASE_TIME - timedelta(days=10),
            expected_delivery_at=BASE_TIME - timedelta(days=5),
        ),
        PurchaseOrderLine(
            po_line_id="POL-2026-0001-1",
            purchase_order_id="PO-2026-0001",
            product_id="PROD-CELL-5000",
            quantity=200,
            unit_price=28.0,
            tax_code_id="TAX-CN-IN-13",
            received_quantity=200,
        ),
        GoodsReceipt(
            goods_receipt_id="GR-2026-0001",
            purchase_order_id="PO-2026-0001",
            plant_id="PLANT-SH-01",
            warehouse_id="WH-SH-01",
            status=ErpGoodsReceiptStatus.POSTED,
            received_by="EMP-OPS-001",
            notes="Inbound raw material batch for power bank assembly.",
            received_at=BASE_TIME - timedelta(days=5),
        ),
        GoodsReceiptLine(
            gr_line_id="GRL-2026-0001-1",
            goods_receipt_id="GR-2026-0001",
            po_line_id="POL-2026-0001-1",
            product_id="PROD-CELL-5000",
            batch_id="BATCH-CELL-202605",
            quantity_received=200,
        ),
        APInvoice(
            ap_invoice_id="APINV-2026-0001",
            purchase_order_id="PO-2026-0001",
            supplier_id="SUP-POWER-01",
            company_code_id="CCODE-CN01",
            amount=5600.0,
            tax_amount=728.0,
            currency="CNY",
            status=ErpApInvoiceStatus.POSTED,
            invoice_date=BASE_TIME - timedelta(days=4),
            due_at=BASE_TIME + timedelta(days=26),
            source_document="GR-2026-0001",
        ),
        InventoryBatch(
            batch_id="BATCH-CELL-202605",
            product_id="PROD-CELL-5000",
            warehouse_id="WH-SH-01",
            supplier_id="SUP-POWER-01",
            batch_number="CELL202605",
            manufacture_at=BASE_TIME - timedelta(days=40),
            expiry_at=BASE_TIME + timedelta(days=720),
            quantity_on_hand=200,
            quality_status="released",
        ),
        InventoryBatch(
            batch_id="BATCH-PJ-202605",
            product_id="PROD-PROJECTOR-4K",
            warehouse_id="WH-SH-01",
            supplier_id="SUP-VISION-02",
            batch_number="PJ202605",
            manufacture_at=BASE_TIME - timedelta(days=25),
            expiry_at=None,
            quantity_on_hand=20,
            quality_status="released",
        ),
        InventorySerial(
            serial_id="SER-PJ-ERP-ORD-1002",
            product_id="PROD-PROJECTOR-4K",
            batch_id="BATCH-PJ-202605",
            warehouse_id="WH-SH-01",
            serial_number="PJ4K2026050002",
            status="assigned",
            assigned_order_id="ERP-ORD-1002",
        ),
        DataQualityRule(
            rule_id="DQR-BP-TAX-ID",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="business_partner",
            field_name="tax_registration_number",
            rule_type="required_when_supplier",
            expression="partner_type == SUPPLIER implies tax_registration_number is not null",
            severity="high",
            owner_department_id="DEPT-FINANCE",
        ),
        DataQualityRule(
            rule_id="DQR-REFUND-AMOUNT",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="refund_request",
            field_name="approved_amount",
            rule_type="range_check",
            expression="approved_amount <= order.amount",
            severity="critical",
            owner_department_id="DEPT-SUPPORT",
        ),
        BillOfMaterial(
            bom_id="BOM-PB-20000-V1",
            product_id="PROD-PB-20000",
            version="1",
            status=ErpBomStatus.ACTIVE,
            output_quantity=1,
            valid_from=BASE_TIME - timedelta(days=60),
        ),
        BillOfMaterialLine(
            bom_line_id="BOML-PB-CELL",
            bom_id="BOM-PB-20000-V1",
            component_product_id="PROD-CELL-5000",
            quantity=4.0,
            scrap_rate=0.02,
        ),
        BillOfMaterialLine(
            bom_line_id="BOML-PB-PCBA",
            bom_id="BOM-PB-20000-V1",
            component_product_id="PROD-PCBA-PB",
            quantity=1.0,
            scrap_rate=0.01,
        ),
        WorkOrder(
            work_order_id="WO-PB-2026-0501",
            bom_id="BOM-PB-20000-V1",
            plant_id="PLANT-SH-01",
            product_id="PROD-PB-20000",
            status=ErpWorkOrderStatus.COMPLETED,
            planned_quantity=50,
            completed_quantity=50,
            scheduled_start_at=BASE_TIME - timedelta(days=8),
            scheduled_end_at=BASE_TIME - timedelta(days=7),
            actual_start_at=BASE_TIME - timedelta(days=8),
            actual_end_at=BASE_TIME - timedelta(days=7, hours=-2),
        ),
        WorkOrderComponentIssue(
            issue_id="WOI-PB-2026-0501-CELL",
            work_order_id="WO-PB-2026-0501",
            product_id="PROD-CELL-5000",
            batch_id="BATCH-CELL-202605",
            quantity=200.0,
            issued_at=BASE_TIME - timedelta(days=8, hours=-1),
        ),
        FixedAsset(
            asset_id="FA-PACK-ROBOT-01",
            company_code_id="CCODE-CN01",
            plant_id="PLANT-SH-01",
            asset_tag="SH01-PACK-ROBOT-01",
            name="Packing Line Robot 01",
            category="warehouse_equipment",
            acquisition_cost=180000.0,
            currency="CNY",
            status=ErpAssetStatus.IN_SERVICE,
            acquired_at=BASE_TIME - timedelta(days=360),
            useful_life_months=60,
        ),
        AssetDepreciationRun(
            depreciation_run_id="DEP-FA-PACK-ROBOT-01-202605",
            asset_id="FA-PACK-ROBOT-01",
            fiscal_period_id="FP-1000-2026-05",
            depreciation_amount=3000.0,
            accumulated_depreciation=36000.0,
            posted_journal_entry_id="JE-DEP-202605",
            posted_at=BASE_TIME + timedelta(days=18),
        ),
        CurrencyRate(
            currency_rate_id="FX-USD-CNY-20260513",
            from_currency="USD",
            to_currency="CNY",
            rate=7.12,
            provider="demo_fx",
            valid_at=BASE_TIME,
        ),
        PeriodCloseRun(
            close_run_id="PCR-1000-2026-05",
            company_code_id="CCODE-CN01",
            fiscal_period_id="FP-1000-2026-05",
            status=ErpConsolidationStatus.VALIDATED,
            revenue_total=3096.0,
            expense_total=3000.0,
            open_item_count=1,
            closed_by="EMP-FIN-001",
            notes="Demo soft close with open AP liability.",
            closed_at=BASE_TIME + timedelta(days=19),
        ),
        ConsolidationGroup(
            group_id="CONSOL-DEMO-COMMERCE",
            tenant_id="TENANT-DEMO-COMMERCE",
            name="Demo Commerce Consolidation Group",
            reporting_currency="CNY",
        ),
        ConsolidationRun(
            consolidation_run_id="CONSOL-RUN-2026-05",
            group_id="CONSOL-DEMO-COMMERCE",
            fiscal_period_id="FP-1000-2026-05",
            status=ErpConsolidationStatus.VALIDATED,
            total_revenue=3096.0,
            total_expense=3000.0,
            elimination_amount=180.0,
            reporting_currency="CNY",
            created_at=BASE_TIME + timedelta(days=20),
        ),
        ExternalSystemConnector(
            connector_id="CONN-MOCK-ERP",
            name="Mock ERP Connector",
            system_type="mock_erp",
            base_url="/api/erp",
            auth_type="internal",
            status=ErpConnectorStatus.ACTIVE,
            capabilities={
                "read": ["sales_order", "invoice", "open_item", "document_flow"],
                "write": ["credit_memo", "clearing_document", "approval_request"],
                "query": ["metadata", "odata_like_filter", "expand_document_flow"],
            },
            config={"connector_class": "MockERPConnector", "timeout_seconds": 5},
            last_health_check_at=BASE_TIME,
        ),
        ExternalSystemConnector(
            connector_id="CONN-SAP-ODATA-DEMO",
            name="SAP OData Demo Connector",
            system_type="sap_odata",
            base_url="https://sap.example.local/odata",
            auth_type="oauth2_client_credentials",
            status=ErpConnectorStatus.DRAFT,
            capabilities={
                "read": ["A_SalesOrder", "A_BusinessPartner", "A_JournalEntry"],
                "write": ["A_CreditMemoRequest"],
            },
            config={"metadata_url": "$metadata", "dry_run_only": True},
        ),
        WebhookSubscription(
            subscription_id="WH-REFUND-CREATED",
            connector_id="CONN-MOCK-ERP",
            event_type="refund.credit_memo.created",
            target_url="https://webhook.site/demo-refund-credit-memo",
            secret_ref="secret://webhook/refund",
            active=True,
        ),
        WebhookSubscription(
            subscription_id="WH-MDG-APPROVED",
            connector_id="CONN-MOCK-ERP",
            event_type="master_data.change.approved",
            target_url="https://webhook.site/demo-mdg-approved",
            secret_ref="secret://webhook/mdg",
            active=True,
        ),
        ProcurementApprovalRequest(
            procurement_request_id="PROC-APR-2026-0001",
            purchase_order_id="PO-2026-0001",
            requester_employee_id="EMP-OPS-001",
            supplier_id="SUP-POWER-01",
            amount=5600.0,
            risk_level="medium",
            status=ErpBusinessRequestStatus.APPROVED,
            approval_due_at=BASE_TIME - timedelta(days=9),
            policy_snapshot={"policy_id": "PROCUREMENT-APPROVAL", "threshold": 5000, "required_role": "MANAGER"},
            created_at=BASE_TIME - timedelta(days=10, minutes=30),
        ),
        ProcurementApprovalRequest(
            procurement_request_id="PROC-APR-2026-0002",
            purchase_order_id="PO-2026-0001",
            requester_employee_id="EMP-OPS-001",
            supplier_id="SUP-POWER-01",
            amount=92000.0,
            risk_level="high",
            status=ErpBusinessRequestStatus.TIMEOUT,
            approval_due_at=BASE_TIME - timedelta(days=2),
            policy_snapshot={"policy_id": "PROCUREMENT-APPROVAL", "threshold": 50000, "required_role": "FINANCE"},
            created_at=BASE_TIME - timedelta(days=4),
        ),
        ReimbursementClaim(
            reimbursement_id="REIM-2026-0001",
            requester_employee_id="EMP-OPS-001",
            cost_center_id="CC-SUPPORT",
            amount=860.0,
            currency="CNY",
            category="travel",
            description="Customer site visit transport reimbursement.",
            receipt_count=3,
            status=ErpBusinessRequestStatus.APPROVED,
            approval_due_at=BASE_TIME + timedelta(days=2),
            policy_snapshot={"policy_id": "REIMBURSEMENT-POLICY", "auto_limit": 1000},
            created_at=BASE_TIME + timedelta(hours=6),
        ),
        ReimbursementClaim(
            reimbursement_id="REIM-2026-0002",
            requester_employee_id="EMP-OPS-001",
            cost_center_id="CC-SUPPORT",
            amount=4200.0,
            currency="CNY",
            category="client_meal",
            description="Large client meal with missing receipt attachment.",
            receipt_count=0,
            status=ErpBusinessRequestStatus.PENDING_APPROVAL,
            approval_due_at=BASE_TIME - timedelta(hours=2),
            policy_snapshot={"policy_id": "REIMBURSEMENT-POLICY", "receipt_required": True},
            created_at=BASE_TIME - timedelta(days=1),
        ),
        AccessRequestRecord(
            access_request_id="ACC-REQ-2026-0001",
            requester_employee_id="EMP-OPS-001",
            target_system="SAP_PRD_FINANCE",
            permission_level="read_only",
            business_reason="Investigate refund clearing status for customer complaint.",
            risk_level="medium",
            status=ErpBusinessRequestStatus.APPROVED,
            approval_due_at=BASE_TIME + timedelta(hours=12),
            granted_at=BASE_TIME + timedelta(hours=2),
            policy_snapshot={"policy_id": "ACCESS-POLICY", "requires_security_review": False},
            created_at=BASE_TIME + timedelta(hours=1),
        ),
        AccessRequestRecord(
            access_request_id="ACC-REQ-2026-0002",
            requester_employee_id="EMP-OPS-001",
            target_system="SAP_PRD_FINANCE",
            permission_level="journal_posting_admin",
            business_reason="Emergency access request without proper SoD justification.",
            risk_level="high",
            status=ErpBusinessRequestStatus.PENDING_APPROVAL,
            approval_due_at=BASE_TIME - timedelta(minutes=30),
            granted_at=None,
            policy_snapshot={"policy_id": "ACCESS-POLICY", "requires_security_review": True, "sod_check": "conflict"},
            created_at=BASE_TIME - timedelta(hours=3),
        ),
        BusinessApprovalRecord(
            approval_id="APR-PROC-2026-0001",
            scenario="procurement_approval",
            object_type="procurement_approval_request",
            object_id="PROC-APR-2026-0001",
            refund_request_id=None,
            stage_id="manager_review",
            status=ErpDocumentStatus.APPROVED,
            required_role="MANAGER",
            approver_employee_id="EMP-OPS-001",
            approval_limit=10000.0,
            decision_reason="Preferred supplier and budget available.",
            decided_at=BASE_TIME - timedelta(days=9, hours=20),
            created_at=BASE_TIME - timedelta(days=10, minutes=15),
        ),
        BusinessApprovalRecord(
            approval_id="APR-PROC-2026-0002-TIMEOUT",
            scenario="procurement_approval",
            object_type="procurement_approval_request",
            object_id="PROC-APR-2026-0002",
            refund_request_id=None,
            stage_id="finance_review",
            status=ErpDocumentStatus.SUBMITTED,
            required_role="FINANCE",
            approver_employee_id=None,
            approval_limit=100000.0,
            decision_reason="Approval timeout example for SLA monitoring.",
            decided_at=None,
            created_at=BASE_TIME - timedelta(days=4),
        ),
        BusinessApprovalRecord(
            approval_id="APR-REIM-2026-0002",
            scenario="reimbursement",
            object_type="reimbursement_claim",
            object_id="REIM-2026-0002",
            refund_request_id=None,
            stage_id="finance_review",
            status=ErpDocumentStatus.SUBMITTED,
            required_role="FINANCE",
            approver_employee_id=None,
            approval_limit=5000.0,
            decision_reason="Receipt missing; waiting for finance review.",
            decided_at=None,
            created_at=BASE_TIME - timedelta(days=1),
        ),
        BusinessApprovalRecord(
            approval_id="APR-ACCESS-2026-0002",
            scenario="permission_request",
            object_type="access_request",
            object_id="ACC-REQ-2026-0002",
            refund_request_id=None,
            stage_id="security_review",
            status=ErpDocumentStatus.SUBMITTED,
            required_role="SECURITY",
            approver_employee_id=None,
            approval_limit=0.0,
            decision_reason="Privileged finance access requires SoD review.",
            decided_at=None,
            created_at=BASE_TIME - timedelta(hours=3),
        ),
        DataQualityRule(
            rule_id="DQR-INVOICE-ORDER-MATCH",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="invoice",
            field_name="amount",
            rule_type="cross_document_match",
            expression="invoice.amount == sales_order.amount",
            severity="critical",
            owner_department_id="DEPT-FINANCE",
        ),
        DataQualityRule(
            rule_id="DQR-INVENTORY-BOOK-PHYSICAL",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="inventory_item",
            field_name="quantity_on_hand",
            rule_type="reconciliation",
            expression="book_quantity == physical_quantity",
            severity="high",
            owner_department_id="DEPT-SUPPORT",
        ),
        BusinessPartner(
            partner_id="BP-CUST-DUP-8101",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.CUSTOMER,
            display_name="Demo Buyer Low Risk",
            legal_name="Demo Buyer Low Risk",
            country="CN",
            email="buyer.low@example.com",
            risk_rating="normal",
            payment_terms="immediate",
            currency="CNY",
        ),
        MasterDataVersion(
            version_id="MDV-BP-SUP-POWER-01-V1",
            object_type="business_partner",
            object_id="BP-SUP-POWER-01",
            version_number=1,
            data_snapshot={"display_name": "Shenzhen Power Components", "payment_terms": "NET30", "risk_rating": "low"},
            changed_by="EMP-FIN-001",
            change_reason="Initial supplier master data onboarding.",
            valid_from=BASE_TIME - timedelta(days=90),
        ),
        MasterDataVersion(
            version_id="MDV-TAX-CN-OUT-6-V1",
            object_type="tax_code",
            object_id="TAX-CN-OUT-6",
            version_number=1,
            data_snapshot={"code": "O6", "tax_rate": 0.06, "category": "output_vat"},
            changed_by="EMP-FIN-001",
            change_reason="Initial tax code version.",
            valid_from=BASE_TIME - timedelta(days=120),
        ),
        MasterDataChangeRequest(
            mdg_request_id="MDG-REQ-2026-0001",
            object_type="business_partner",
            object_id="BP-SUP-POWER-01",
            change_type="UPDATE",
            proposed_change={"payment_terms": {"from": "NET30", "to": "NET45"}},
            validation_summary={"passed": True, "critical_issues": 0, "warnings": 0},
            status=ErpMasterDataRequestStatus.APPROVED,
            requested_by="EMP-FIN-001",
            reviewer_employee_id="EMP-OPS-001",
            decision_reason="Supplier contract renewal approved.",
            created_at=BASE_TIME - timedelta(days=2),
            decided_at=BASE_TIME - timedelta(days=1, hours=20),
        ),
        MasterDataChangeRequest(
            mdg_request_id="MDG-REQ-2026-0002",
            object_type="business_partner",
            object_id="BP-CUST-DUP-8101",
            change_type="MERGE",
            proposed_change={"merge_into": "BP-CUST-8101", "reason": "duplicate email and legal name"},
            validation_summary={"passed": False, "critical_issues": 1, "warnings": 1},
            status=ErpMasterDataRequestStatus.PENDING_APPROVAL,
            requested_by="agent_mdg",
            reviewer_employee_id=None,
            decision_reason="Duplicate candidate requires human review.",
            created_at=BASE_TIME + timedelta(hours=2),
        ),
        MasterDataValidationResult(
            validation_result_id="MDVRES-REQ-0002-EMAIL",
            mdg_request_id="MDG-REQ-2026-0002",
            rule_id="DQR-BP-TAX-ID",
            object_type="business_partner",
            object_id="BP-CUST-DUP-8101",
            field_name="email",
            severity="high",
            passed=False,
            message="Duplicate email detected against BP-CUST-8101.",
            checked_at=BASE_TIME + timedelta(hours=2, minutes=1),
        ),
        MasterDataDuplicateCandidate(
            duplicate_id="DUP-BP-CUST-8101",
            object_type="business_partner",
            left_object_id="BP-CUST-8101",
            right_object_id="BP-CUST-DUP-8101",
            match_score=0.97,
            matched_fields={"email": True, "display_name": True, "country": True},
            status="open",
            detected_at=BASE_TIME + timedelta(hours=2),
        ),
        DataQualityIssue(
            issue_id="DQ-MISSING-BP-TAX-SUP-GIFT",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="business_partner",
            object_id="BP-SUP-GIFT-03",
            severity="high",
            issue_type="missing_required_field",
            description="Supplier master record is missing tax registration number.",
            detected_at=BASE_TIME + timedelta(hours=5),
        ),
        DataQualityIssue(
            issue_id="DQ-DUP-CUSTOMER-8101",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="business_partner",
            object_id="BP-CUST-DUP-8101",
            severity="high",
            issue_type="duplicate_customer",
            description="Potential duplicate customer detected by email and legal name.",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=5),
        ),
        DataQualityIssue(
            issue_id="DQ-ABNORMAL-TAX-O6",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="tax_code",
            object_id="TAX-CN-OUT-6",
            severity="medium",
            issue_type="abnormal_tax_code_usage",
            description="Output tax code used on a reimbursement AP-like flow; review mapping.",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=10),
        ),
        DataQualityIssue(
            issue_id="DQ-STOCK-MISMATCH-PB",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="inventory_item",
            object_id="INV-PB-20000-SH01",
            severity="high",
            issue_type="stock_book_physical_mismatch",
            description="Book quantity is 180 while physical cycle count is 177.",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=15),
        ),
        DataQualityIssue(
            issue_id="DQ-INVOICE-ORDER-MISMATCH-1002",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="invoice",
            object_id="INV-ERP-ORD-1002",
            severity="critical",
            issue_type="invoice_order_amount_mismatch",
            description="Invoice amount does not reconcile with the latest order amount snapshot.",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=20),
        ),
        DataQualityIssue(
            issue_id="DQ-APPROVAL-TIMEOUT-PROC-0002",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="procurement_approval_request",
            object_id="PROC-APR-2026-0002",
            severity="high",
            issue_type="approval_timeout",
            description="Procurement approval exceeded SLA and remains unassigned.",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=25),
        ),
        DataQualityIssue(
            issue_id="DQ-MESSY-HISTORY-REF-1003",
            tenant_id="TENANT-DEMO-COMMERCE",
            object_type="refund_request",
            object_id="ERP-REF-1003",
            severity="medium",
            issue_type="messy_change_history",
            description="Refund request has multiple policy and status changes requiring replay before action.",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=30),
        ),
        SystemReconciliationIssue(
            reconciliation_issue_id="REC-ERP-CRM-REF-1002",
            object_type="refund_request",
            object_id="ERP-REF-1002",
            source_system="mock_erp",
            target_system="crm",
            mismatch_type="status_mismatch",
            source_snapshot={"status": "EXECUTED", "amount": 1299.0},
            target_snapshot={"status": "PENDING", "amount": 1299.0},
            severity="high",
            status="open",
            detected_at=BASE_TIME + timedelta(hours=5, minutes=35),
        ),
        OpenItem(
            open_item_id="OI-AP-APINV-2026-0001",
            company_code_id="CCODE-CN01",
            business_partner_id="BP-SUP-POWER-01",
            source_document="APINV-2026-0001",
            account_code="AccountsPayable",
            debit=0.0,
            credit=5600.0,
            balance=-5600.0,
            currency="CNY",
            due_at=BASE_TIME + timedelta(days=26),
            cleared=False,
        ),
        DocumentFlow(
            document_flow_id="DF-PO-GR-2026-0001",
            case_id="PROCURE-CASE-2026-0001",
            order_id=None,
            source_doctype="purchase_order",
            source_id="PO-2026-0001",
            target_doctype="goods_receipt",
            target_id="GR-2026-0001",
            relation_type="receives",
            sequence=1,
            created_at=BASE_TIME - timedelta(days=5),
        ),
        DocumentFlow(
            document_flow_id="DF-GR-APINV-2026-0001",
            case_id="PROCURE-CASE-2026-0001",
            order_id=None,
            source_doctype="goods_receipt",
            source_id="GR-2026-0001",
            target_doctype="ap_invoice",
            target_id="APINV-2026-0001",
            relation_type="three_way_match",
            sequence=2,
            created_at=BASE_TIME - timedelta(days=4),
        ),
        DocumentFlow(
            document_flow_id="DF-BOM-WO-PB-202605",
            case_id="MFG-CASE-PB-202605",
            order_id=None,
            source_doctype="bom",
            source_id="BOM-PB-20000-V1",
            target_doctype="work_order",
            target_id="WO-PB-2026-0501",
            relation_type="produces",
            sequence=1,
            created_at=BASE_TIME - timedelta(days=8),
        ),
        ChangeDocument(
            change_id="CHG-POLICY-REFUND-2026-V1",
            object_type="policy_version",
            object_id="POLICY-REFUND-2026-V1",
            change_type="CREATE",
            changed_by="EMP-OPS-001",
            before_state=None,
            after_state={"status": "ACTIVE", "version": "2026.1"},
            reason="Annual refund policy activation.",
            changed_at=BASE_TIME - timedelta(days=30),
        ),
        ChangeDocument(
            change_id="CHG-MDG-REQ-2026-0001",
            object_type="master_data_change_request",
            object_id="MDG-REQ-2026-0001",
            change_type="APPROVAL",
            changed_by="EMP-OPS-001",
            before_state={"status": "PENDING_APPROVAL"},
            after_state={"status": "APPROVED", "payment_terms": "NET45"},
            reason="Approved supplier payment terms update.",
            changed_at=BASE_TIME - timedelta(days=1, hours=20),
        ),
        ChangeDocument(
            change_id="CHG-MESSY-REF-1003-A",
            object_type="refund_request",
            object_id="ERP-REF-1003",
            change_type="STATUS_CHANGE",
            changed_by="agent_runtime",
            before_state={"status": "REQUESTED"},
            after_state={"status": "POLICY_CHECKED"},
            reason="Policy evaluated non-returnable item.",
            changed_at=BASE_TIME + timedelta(hours=5, minutes=31),
        ),
        ChangeDocument(
            change_id="CHG-MESSY-REF-1003-B",
            object_type="refund_request",
            object_id="ERP-REF-1003",
            change_type="STATUS_CHANGE",
            changed_by="agent_runtime",
            before_state={"status": "POLICY_CHECKED"},
            after_state={"status": "REJECTED"},
            reason="Rejected by non-returnable customized product policy.",
            changed_at=BASE_TIME + timedelta(hours=5, minutes=32),
        ),
        OutboxEvent(
            outbox_event_id="OUTBOX-PROC-APR-TIMEOUT-0002",
            aggregate_type="procurement_approval_request",
            aggregate_id="PROC-APR-2026-0002",
            event_type="approval.timeout",
            payload={"request_id": "PROC-APR-2026-0002", "sla": "expired", "required_role": "FINANCE"},
            status=ErpOutboxStatus.PENDING,
            attempts=0,
            idempotency_key="outbox:approval-timeout:PROC-APR-2026-0002",
            next_attempt_at=BASE_TIME + timedelta(minutes=10),
            created_at=BASE_TIME + timedelta(hours=5, minutes=25),
        ),
        OutboxEvent(
            outbox_event_id="OUTBOX-MDG-APPROVED-0001",
            aggregate_type="master_data_change_request",
            aggregate_id="MDG-REQ-2026-0001",
            event_type="master_data.change.approved",
            payload={"object_type": "business_partner", "object_id": "BP-SUP-POWER-01", "change": "payment_terms"},
            status=ErpOutboxStatus.DISPATCHED,
            attempts=1,
            idempotency_key="outbox:mdg-approved:MDG-REQ-2026-0001",
            created_at=BASE_TIME - timedelta(days=1, hours=20),
            dispatched_at=BASE_TIME - timedelta(days=1, hours=19, minutes=59),
        ),
        IdempotencyRecord(
            idempotency_key="seed:return-to-refund:demo",
            scope="erp_seed",
            request_hash="return_to_refund_demo_v2",
            response_snapshot={"dataset": "return_to_refund_demo", "case_count": 4},
            status=ErpIdempotencyStatus.COMPLETED,
            created_at=BASE_TIME,
            expires_at=BASE_TIME + timedelta(days=30),
        ),
        JournalEntry(
            journal_entry_id="JE-AP-APINV-2026-0001",
            tenant_id="TENANT-DEMO-COMMERCE",
            source_document="APINV-2026-0001",
            description="Post supplier AP invoice for raw material receipt",
            status=ErpJournalStatus.POSTED,
            total_debit=5600.0,
            total_credit=5600.0,
            posted_at=BASE_TIME - timedelta(days=4),
        ),
        JournalLine(
            journal_line_id="JL-AP-INV-EXP-2026-0001",
            journal_entry_id="JE-AP-APINV-2026-0001",
            account_code="InventoryRawMaterial",
            debit=5600.0,
            credit=0.0,
            cost_center_id="CC-FINANCE",
            memo="Raw material goods receipt accrual",
        ),
        JournalLine(
            journal_line_id="JL-AP-INV-PAY-2026-0001",
            journal_entry_id="JE-AP-APINV-2026-0001",
            account_code="AccountsPayable",
            debit=0.0,
            credit=5600.0,
            cost_center_id="CC-FINANCE",
            memo="Supplier payable recognized",
        ),
        JournalEntry(
            journal_entry_id="JE-DEP-202605",
            tenant_id="TENANT-DEMO-COMMERCE",
            source_document="DEP-FA-PACK-ROBOT-01-202605",
            description="Monthly depreciation posting for packing robot",
            status=ErpJournalStatus.POSTED,
            total_debit=3000.0,
            total_credit=3000.0,
            posted_at=BASE_TIME + timedelta(days=18),
        ),
        JournalLine(
            journal_line_id="JL-DEP-EXP-202605",
            journal_entry_id="JE-DEP-202605",
            account_code="DepreciationExpense",
            debit=3000.0,
            credit=0.0,
            cost_center_id="CC-FINANCE",
            memo="Monthly fixed asset depreciation",
        ),
        JournalLine(
            journal_line_id="JL-DEP-ACC-202605",
            journal_entry_id="JE-DEP-202605",
            account_code="AccumulatedDepreciation",
            debit=0.0,
            credit=3000.0,
            cost_center_id="CC-FINANCE",
            memo="Accumulated depreciation",
        ),
        DocumentFlow(
            document_flow_id="DF-FA-DEP-202605",
            case_id="ASSET-CASE-2026-05",
            order_id=None,
            source_doctype="fixed_asset",
            source_id="FA-PACK-ROBOT-01",
            target_doctype="asset_depreciation_run",
            target_id="DEP-FA-PACK-ROBOT-01-202605",
            relation_type="depreciates",
            sequence=1,
            created_at=BASE_TIME + timedelta(days=18),
        ),
        PolicyVersion(
            policy_version_id="POLICY-REFUND-2026-V1",
            tenant_id="TENANT-DEMO-COMMERCE",
            policy_id="REFUND-POLICY",
            version="2026.1",
            title="Refund and Return Policy 2026",
            scenario="return_to_refund",
            content=(
                "Refunds within 7 days are allowed for returnable products. "
                "Refunds above 500 CNY require manager approval. Customized goods are non-returnable. "
                "Duplicate refund attempts must be skipped by idempotency controls."
            ),
            status=ErpPolicyStatus.ACTIVE,
            effective_from=BASE_TIME - timedelta(days=30),
            owner_department_id="DEPT-SUPPORT",
        ),
        ApprovalMatrixRule(
            approval_rule_id="APR-RULE-REFUND-LOW",
            tenant_id="TENANT-DEMO-COMMERCE",
            scenario="return_to_refund",
            stage_id="auto_approval",
            min_amount=0.0,
            max_amount=500.0,
            required_role="AGENT",
            required_department_id="DEPT-SUPPORT",
            escalation_after_hours=0,
        ),
        ApprovalMatrixRule(
            approval_rule_id="APR-RULE-REFUND-HIGH",
            tenant_id="TENANT-DEMO-COMMERCE",
            scenario="return_to_refund",
            stage_id="manager_review",
            min_amount=500.01,
            max_amount=5000.0,
            required_role="MANAGER",
            required_department_id="DEPT-SUPPORT",
            escalation_after_hours=24,
        ),
        ApprovalMatrixRule(
            approval_rule_id="APR-RULE-REFUND-FINANCE",
            tenant_id="TENANT-DEMO-COMMERCE",
            scenario="return_to_refund",
            stage_id="finance_posting",
            min_amount=0.0,
            max_amount=None,
            required_role="FINANCE",
            required_department_id="DEPT-FINANCE",
            escalation_after_hours=24,
        ),
    ]
    for record in records:
        await session.merge(record)


def _case_spec(variant: str) -> dict[str, Any]:
    specs = {
        "standard_low_risk": {
            "case_id": "RTR-CASE-1001",
            "user_id": 8101,
            "customer_id": "CUST-8101",
            "customer_name": "Demo Buyer Low Risk",
            "customer_email": "buyer.low@example.com",
            "segment": "silver",
            "region": "Shanghai",
            "lifetime_value": 1880.0,
            "customer_risk_score": 8,
            "refund_count_90d": 0,
            "complaint_count_90d": 0,
            "customer_tags": {"loyalty": "normal"},
            "order_id": "ERP-ORD-1001",
            "amount": 299.0,
            "sku": "PB-20000",
            "product_id": "PROD-PB-20000",
            "product_name": "Wireless Power Bank 20000mAh",
            "returnable": True,
            "order_status": "delivered",
            "shipping_address": "Shanghai Pudong Demo Road 202",
            "refund_request_id": "ERP-REF-1001",
            "requested_amount": 299.0,
            "approved_amount": 299.0,
            "reason_code": "damaged_item",
            "description": "Item arrived damaged and photo evidence is attached.",
            "risk_level": "low",
            "refund_status": ErpRefundStatus.EXECUTED,
            "policy_snapshot": {"policy_id": "REFUND-7D", "clause_id": "REFUND-7D-LOW-RISK"},
            "created_at": BASE_TIME,
        },
        "high_value_hitl": {
            "case_id": "RTR-CASE-1002",
            "user_id": 8102,
            "customer_id": "CUST-8102",
            "customer_name": "Demo Buyer High Value",
            "customer_email": "buyer.high@example.com",
            "segment": "gold",
            "region": "Hangzhou",
            "lifetime_value": 7800.0,
            "customer_risk_score": 28,
            "refund_count_90d": 1,
            "complaint_count_90d": 1,
            "customer_tags": {"loyalty": "gold"},
            "order_id": "ERP-ORD-1002",
            "amount": 1299.0,
            "sku": "PJ-4K-PRO",
            "product_id": "PROD-PROJECTOR-4K",
            "product_name": "4K Smart Projector Pro",
            "returnable": True,
            "order_status": "delivered",
            "shipping_address": "Hangzhou Binjiang Demo Road 77",
            "refund_request_id": "ERP-REF-1002",
            "requested_amount": 1299.0,
            "approved_amount": 1299.0,
            "reason_code": "quality_issue",
            "description": "High-value item has display defects and requires manager approval.",
            "risk_level": "high",
            "refund_status": ErpRefundStatus.EXECUTED,
            "policy_snapshot": {"policy_id": "REFUND-HITL", "clause_id": "REFUND-HITL-500"},
            "created_at": BASE_TIME + timedelta(hours=1),
        },
        "non_returnable_exception": {
            "case_id": "RTR-CASE-1003",
            "user_id": 8103,
            "customer_id": "CUST-8103",
            "customer_name": "Demo Buyer Non Returnable",
            "customer_email": "buyer.exception@example.com",
            "segment": "standard",
            "region": "Suzhou",
            "lifetime_value": 450.0,
            "customer_risk_score": 35,
            "refund_count_90d": 2,
            "complaint_count_90d": 2,
            "customer_tags": {"watchlist": True},
            "order_id": "ERP-ORD-1003",
            "amount": 199.0,
            "sku": "CUSTOM-GIFT-01",
            "product_id": "PROD-CUSTOM-GIFT",
            "product_name": "Customized Gift Box",
            "returnable": False,
            "order_status": "delivered",
            "shipping_address": "Suzhou Industrial Park Demo Street 18",
            "refund_request_id": "ERP-REF-1003",
            "requested_amount": 199.0,
            "approved_amount": None,
            "reason_code": "changed_mind",
            "description": "Customized item return request violates non-returnable product policy.",
            "risk_level": "high",
            "refund_status": ErpRefundStatus.REJECTED,
            "policy_snapshot": {"policy_id": "REFUND-NONRETURNABLE", "clause_id": "CUSTOM-ITEM-NO-RETURN"},
            "created_at": BASE_TIME + timedelta(hours=2),
        },
        "duplicate_refund_attempt": {
            "case_id": "RTR-CASE-1004",
            "user_id": 8104,
            "customer_id": "CUST-8104",
            "customer_name": "Demo Buyer Duplicate",
            "customer_email": "buyer.duplicate@example.com",
            "segment": "standard",
            "region": "Nanjing",
            "lifetime_value": 650.0,
            "customer_risk_score": 72,
            "refund_count_90d": 4,
            "complaint_count_90d": 3,
            "customer_tags": {"watchlist": True, "duplicate_refund": True},
            "order_id": "ERP-ORD-1004",
            "amount": 299.0,
            "sku": "PB-20000",
            "product_id": "PROD-PB-20000",
            "product_name": "Wireless Power Bank 20000mAh",
            "returnable": True,
            "order_status": "delivered",
            "shipping_address": "Nanjing Xuanwu Demo Avenue 8",
            "refund_request_id": "ERP-REF-1004",
            "requested_amount": 299.0,
            "approved_amount": None,
            "reason_code": "duplicate_request",
            "description": "Duplicate refund attempt detected for the same order and amount.",
            "risk_level": "high",
            "refund_status": ErpRefundStatus.DUPLICATE_SKIPPED,
            "policy_snapshot": {"policy_id": "REFUND-IDEMPOTENCY", "clause_id": "DUPLICATE-SKIP"},
            "created_at": BASE_TIME + timedelta(hours=3),
        },
    }
    if variant not in specs:
        raise ValueError(f"Unknown return-to-refund variant: {variant}")
    return specs[variant]


def _customer_partner_records(spec: dict[str, Any]) -> list[BusinessPartner]:
    return [
        BusinessPartner(
            partner_id=f"BP-{spec['customer_id']}",
            tenant_id="TENANT-DEMO-COMMERCE",
            partner_type=ErpPartnerType.CUSTOMER,
            display_name=spec["customer_name"],
            legal_name=spec["customer_name"],
            country="CN",
            tax_registration_number=None,
            email=spec["customer_email"],
            phone=None,
            risk_rating=spec["risk_level"],
            payment_terms="immediate",
            currency="CNY",
        )
    ]


def _support_records(spec: dict[str, Any]) -> list[Any]:
    priority = "high" if spec["risk_level"] == "high" else "normal"
    status = ErpSupportTicketStatus.RESOLVED
    if spec["refund_status"] == ErpRefundStatus.REJECTED:
        status = ErpSupportTicketStatus.CLOSED
    elif spec["refund_status"] == ErpRefundStatus.DUPLICATE_SKIPPED:
        status = ErpSupportTicketStatus.ESCALATED
    support_ticket = SupportTicket(
        support_ticket_id=f"CS-{spec['order_id']}",
        tenant_id="TENANT-DEMO-COMMERCE",
        order_id=spec["order_id"],
        customer_id=spec["customer_id"],
        channel="web",
        subject=f"Refund request for {spec['product_name']}",
        message=spec["description"],
        intent="refund",
        priority=priority,
        sla_due_at=spec["created_at"] + timedelta(days=3, hours=8),
        assigned_employee_id="EMP-OPS-001",
        status=status,
        resolution_summary=f"Refund request handled as {spec['refund_status'].value}.",
        created_at=spec["created_at"] + timedelta(days=3, minutes=-10),
    )
    complaint = CustomerComplaint(
        complaint_id=f"CMP-{spec['order_id']}",
        support_ticket_id=support_ticket.support_ticket_id,
        customer_id=spec["customer_id"],
        complaint_type=spec["reason_code"],
        severity="high" if spec["risk_level"] == "high" else "medium",
        sentiment="negative",
        escalation_required=spec["risk_level"] == "high",
        evidence={"photos": spec["reason_code"] in {"damaged_item", "quality_issue"}, "source": "demo_seed"},
        created_at=spec["created_at"] + timedelta(days=3, minutes=-8),
    )
    return [support_ticket, complaint]


def _stock_reservations(spec: dict[str, Any]) -> list[StockReservation]:
    return [
        StockReservation(
            reservation_id=f"RES-{spec['order_id']}",
            product_id=spec["product_id"],
            warehouse_id="WH-SH-01",
            order_id=spec["order_id"],
            work_order_id=None,
            quantity=1,
            status=ErpReservationStatus.CONSUMED,
            reserved_at=spec["created_at"] - timedelta(minutes=15),
            expires_at=spec["created_at"] + timedelta(hours=2),
        )
    ]


def _return_records(spec: dict[str, Any]) -> list[Any]:
    if spec["refund_status"] == ErpRefundStatus.DUPLICATE_SKIPPED:
        return []
    rma_status = ErpReturnStatus.RESTOCKED if spec["refund_status"] == ErpRefundStatus.EXECUTED else ErpReturnStatus.REJECTED
    inspection_result = ErpInspectionResult.DAMAGED if spec["reason_code"] in {"damaged_item", "quality_issue"} else ErpInspectionResult.PASS
    if not spec["returnable"]:
        inspection_result = ErpInspectionResult.NOT_RECEIVED
    rma = ReturnAuthorization(
        rma_id=f"RMA-{spec['order_id']}",
        refund_request_id=spec["refund_request_id"],
        order_id=spec["order_id"],
        warehouse_id="WH-SH-01",
        status=rma_status,
        return_label_url=f"https://demo.local/rma/{spec['order_id']}",
        received_at=spec["created_at"] + timedelta(days=4) if spec["refund_status"] == ErpRefundStatus.EXECUTED else None,
        created_at=spec["created_at"] + timedelta(days=3, minutes=5),
    )
    inspection = ReturnInspection(
        inspection_id=f"INS-{spec['order_id']}",
        rma_id=rma.rma_id,
        inspector_employee_id="EMP-OPS-001",
        result=inspection_result,
        restockable=spec["refund_status"] == ErpRefundStatus.EXECUTED and spec["returnable"],
        notes="Generated from deterministic return-to-refund seed.",
        inspected_at=spec["created_at"] + timedelta(days=4, minutes=30),
    )
    return [rma, inspection]


def _open_items(spec: dict[str, Any]) -> list[OpenItem]:
    items = [
        OpenItem(
            open_item_id=f"OI-AR-{spec['order_id']}",
            company_code_id="CCODE-CN01",
            business_partner_id=f"BP-{spec['customer_id']}",
            source_document=f"INV-{spec['order_id']}",
            account_code="AccountsReceivable",
            debit=spec["amount"],
            credit=0.0,
            balance=0.0,
            currency="CNY",
            due_at=spec["created_at"] + timedelta(days=7),
            cleared=True,
            clearing_document_id=f"PAY-{spec['order_id']}",
            created_at=spec["created_at"] + timedelta(minutes=5),
        )
    ]
    if spec["refund_status"] == ErpRefundStatus.EXECUTED and spec["approved_amount"]:
        items.append(
            OpenItem(
                open_item_id=f"OI-REFUND-{spec['refund_request_id']}",
                company_code_id="CCODE-CN01",
                business_partner_id=f"BP-{spec['customer_id']}",
                source_document=spec["refund_request_id"],
                account_code="RefundLiability",
                debit=0.0,
                credit=spec["approved_amount"],
                balance=0.0,
                currency="CNY",
                due_at=spec["created_at"] + timedelta(days=5),
                cleared=True,
                clearing_document_id=f"LED-REFUND-{spec['refund_request_id']}",
                created_at=spec["created_at"] + timedelta(days=3, minutes=50),
            )
        )
    return items


def _refund_finance_documents(spec: dict[str, Any]) -> list[Any]:
    if spec["refund_status"] != ErpRefundStatus.EXECUTED or not spec["approved_amount"]:
        return []
    amount = spec["approved_amount"]
    tax_amount = round(amount * 0.06, 2)
    return [
        CreditMemoDocument(
            credit_memo_id=f"CM-{spec['refund_request_id']}",
            order_id=spec["order_id"],
            refund_request_id=spec["refund_request_id"],
            customer_id=spec["customer_id"],
            source_invoice_id=f"INV-{spec['order_id']}",
            amount=amount,
            tax_amount=tax_amount,
            currency="CNY",
            reason_code=spec["reason_code"],
            status=ErpDocumentStatus.POSTED,
            issued_at=spec["created_at"] + timedelta(days=3, minutes=55),
        ),
        ClearingDocument(
            clearing_document_id=f"CLR-{spec['refund_request_id']}",
            company_code_id="CCODE-CN01",
            business_partner_id=f"BP-{spec['customer_id']}",
            clearing_type="customer_refund",
            source_document=f"CM-{spec['refund_request_id']}",
            target_document=f"LED-REFUND-{spec['refund_request_id']}",
            amount=amount,
            currency="CNY",
            status=ErpDocumentStatus.POSTED,
            cleared_at=spec["created_at"] + timedelta(days=3, hours=1, minutes=5),
        ),
    ]


def _refund_compensation_records(spec: dict[str, Any]) -> list[Any]:
    records: list[Any] = []
    if spec["refund_status"] == ErpRefundStatus.REJECTED:
        records.append(
            CompensationTransaction(
                compensation_id=f"COMP-{spec['refund_request_id']}-RMA",
                saga_id=f"SAGA-{spec['refund_request_id']}",
                object_type="refund_request",
                object_id=spec["refund_request_id"],
                action="release_reserved_return_label",
                status=ErpCompensationStatus.EXECUTED,
                reason="Refund rejected; release return label and stop downstream finance posting.",
                payload={"rma_id": f"RMA-{spec['order_id']}", "policy": "non_returnable"},
                executed_at=spec["created_at"] + timedelta(days=3, hours=1, minutes=3),
            )
        )
    if spec["refund_status"] == ErpRefundStatus.DUPLICATE_SKIPPED:
        records.extend(
            [
                ReversalDocument(
                    reversal_document_id=f"REV-{spec['refund_request_id']}",
                    source_document=spec["refund_request_id"],
                    reason_code="duplicate_refund_attempt",
                    amount=spec["requested_amount"],
                    currency="CNY",
                    status=ErpDocumentStatus.POSTED,
                    posted_journal_entry_id=None,
                    created_at=spec["created_at"] + timedelta(days=3, minutes=7),
                ),
                CompensationTransaction(
                    compensation_id=f"COMP-{spec['refund_request_id']}-DUP",
                    saga_id=f"SAGA-{spec['refund_request_id']}",
                    object_type="refund_request",
                    object_id=spec["refund_request_id"],
                    action="mark_duplicate_and_cancel_side_effects",
                    status=ErpCompensationStatus.EXECUTED,
                    reason="Duplicate request detected by idempotency key before external payment side effect.",
                    payload={"duplicate_key": f"refund:{spec['order_id']}:{spec['requested_amount']:.2f}"},
                    executed_at=spec["created_at"] + timedelta(days=3, minutes=8),
                ),
            ]
        )
    return records


def _case_outbox_events(spec: dict[str, Any]) -> list[OutboxEvent]:
    events: list[OutboxEvent] = []
    if spec["refund_status"] == ErpRefundStatus.EXECUTED and spec["approved_amount"]:
        events.append(
            OutboxEvent(
                outbox_event_id=f"OUTBOX-CM-{spec['refund_request_id']}",
                aggregate_type="credit_memo",
                aggregate_id=f"CM-{spec['refund_request_id']}",
                event_type="refund.credit_memo.created",
                payload={
                    "order_id": spec["order_id"],
                    "refund_request_id": spec["refund_request_id"],
                    "amount": spec["approved_amount"],
                    "currency": "CNY",
                },
                status=ErpOutboxStatus.PENDING,
                attempts=0,
                idempotency_key=f"outbox:credit-memo:{spec['refund_request_id']}",
                next_attempt_at=spec["created_at"] + timedelta(days=3, hours=1, minutes=10),
                created_at=spec["created_at"] + timedelta(days=3, minutes=56),
            )
        )
    if spec["refund_status"] in {ErpRefundStatus.REJECTED, ErpRefundStatus.DUPLICATE_SKIPPED}:
        events.append(
            OutboxEvent(
                outbox_event_id=f"OUTBOX-COMP-{spec['refund_request_id']}",
                aggregate_type="compensation_transaction",
                aggregate_id=(
                    f"COMP-{spec['refund_request_id']}-RMA"
                    if spec["refund_status"] == ErpRefundStatus.REJECTED
                    else f"COMP-{spec['refund_request_id']}-DUP"
                ),
                event_type="refund.compensation.executed",
                payload={
                    "order_id": spec["order_id"],
                    "refund_request_id": spec["refund_request_id"],
                    "status": spec["refund_status"].value,
                },
                status=ErpOutboxStatus.DISPATCHED,
                attempts=1,
                idempotency_key=f"outbox:compensation:{spec['refund_request_id']}",
                created_at=spec["created_at"] + timedelta(days=3, minutes=9),
                dispatched_at=spec["created_at"] + timedelta(days=3, minutes=10),
            )
        )
    return events


def _document_flows(spec: dict[str, Any]) -> list[DocumentFlow]:
    case_id = spec["case_id"]
    order_id = spec["order_id"]
    refund_id = spec["refund_request_id"]
    base = spec["created_at"]
    edges: list[tuple[str, str, str, str, str, str]] = [
        ("sales_order", order_id, "payment_transaction", f"PAY-{order_id}", "captures", "1"),
        ("sales_order", order_id, "invoice", f"INV-{order_id}", "bills", "2"),
        ("sales_order", order_id, "shipment", f"SHP-{order_id}", "fulfills", "3"),
        ("sales_order", order_id, "support_ticket", f"CS-{order_id}", "triggers_service", "4"),
        ("support_ticket", f"CS-{order_id}", "customer_complaint", f"CMP-{order_id}", "records", "5"),
        ("customer_complaint", f"CMP-{order_id}", "refund_request", refund_id, "initiates", "6"),
        ("invoice", f"INV-{order_id}", "journal_entry", f"JE-SALE-{order_id}", "posts", "7"),
        ("payment_transaction", f"PAY-{order_id}", "journal_entry", f"JE-PAY-{order_id}", "posts", "8"),
    ]
    if spec["risk_level"] == "high" and spec["refund_status"] != ErpRefundStatus.DUPLICATE_SKIPPED:
        edges.append(("refund_request", refund_id, "business_approval", f"APR-{refund_id}", "requires_approval", "9"))
    if spec["refund_status"] != ErpRefundStatus.DUPLICATE_SKIPPED:
        edges.extend(
            [
                ("refund_request", refund_id, "return_authorization", f"RMA-{order_id}", "authorizes_return", "10"),
                ("return_authorization", f"RMA-{order_id}", "return_inspection", f"INS-{order_id}", "inspects", "11"),
            ]
        )
    if spec["refund_status"] == ErpRefundStatus.EXECUTED:
        edges.extend(
            [
                ("refund_request", refund_id, "credit_memo", f"CM-{refund_id}", "creates_credit_memo", "12"),
                ("credit_memo", f"CM-{refund_id}", "clearing_document", f"CLR-{refund_id}", "clears_open_item", "13"),
                ("refund_request", refund_id, "ledger_entry", f"LED-REFUND-{refund_id}", "posts_refund", "14"),
                ("refund_request", refund_id, "journal_entry", f"JE-REFUND-{refund_id}", "posts_refund_journal", "15"),
                ("credit_memo", f"CM-{refund_id}", "outbox_event", f"OUTBOX-CM-{refund_id}", "emits_webhook_event", "16"),
                ("return_inspection", f"INS-{order_id}", "inventory_movement", f"IM-RESTOCK-{order_id}", "restocks", "17"),
            ]
        )
    if spec["refund_status"] in {ErpRefundStatus.REJECTED, ErpRefundStatus.DUPLICATE_SKIPPED}:
        issue_prefix = "DQ-NONRETURNABLE" if spec["refund_status"] == ErpRefundStatus.REJECTED else "DQ-DUPLICATE"
        edges.append(("refund_request", refund_id, "data_quality_issue", f"{issue_prefix}-{order_id}", "flags_exception", "12"))
        compensation_id = f"COMP-{refund_id}-RMA" if spec["refund_status"] == ErpRefundStatus.REJECTED else f"COMP-{refund_id}-DUP"
        edges.append(("refund_request", refund_id, "compensation_transaction", compensation_id, "compensates_side_effects", "13"))
    if spec["refund_status"] == ErpRefundStatus.DUPLICATE_SKIPPED:
        edges.append(("refund_request", refund_id, "reversal_document", f"REV-{refund_id}", "records_reversal", "14"))
    return [
        DocumentFlow(
            document_flow_id=f"DF-{case_id}-{sequence}",
            case_id=case_id,
            order_id=order_id,
            source_doctype=source_doctype,
            source_id=source_id,
            target_doctype=target_doctype,
            target_id=target_id,
            relation_type=relation_type,
            sequence=int(sequence),
            created_at=base + timedelta(minutes=int(sequence)),
        )
        for source_doctype, source_id, target_doctype, target_id, relation_type, sequence in edges
    ]


def _change_documents(spec: dict[str, Any]) -> list[ChangeDocument]:
    final_status = spec["refund_status"].value
    docs = [
        ChangeDocument(
            change_id=f"CHG-ORDER-{spec['order_id']}-DELIVERED",
            object_type="sales_order",
            object_id=spec["order_id"],
            change_type="STATUS_CHANGE",
            changed_by="system",
            before_state={"status": "created"},
            after_state={"status": spec["order_status"]},
            reason="Shipment delivered and order lifecycle updated.",
            changed_at=spec["created_at"] + timedelta(days=2),
        ),
        ChangeDocument(
            change_id=f"CHG-REFUND-{spec['refund_request_id']}-{final_status}",
            object_type="refund_request",
            object_id=spec["refund_request_id"],
            change_type="STATUS_CHANGE",
            changed_by="agent_runtime",
            before_state={"status": "REQUESTED", "risk_level": "unknown"},
            after_state={"status": final_status, "risk_level": spec["risk_level"]},
            reason="Agent workflow completed policy, risk, and execution decisions.",
            changed_at=spec["created_at"] + timedelta(days=3, hours=1),
        ),
    ]
    if spec["risk_level"] == "high" and spec["refund_status"] != ErpRefundStatus.DUPLICATE_SKIPPED:
        docs.append(
            ChangeDocument(
                change_id=f"CHG-APPROVAL-{spec['refund_request_id']}",
                object_type="business_approval",
                object_id=f"APR-{spec['refund_request_id']}",
                change_type="DECISION",
                changed_by="EMP-OPS-001",
                before_state={"status": "SUBMITTED"},
                after_state={
                    "status": "APPROVED" if spec["refund_status"] == ErpRefundStatus.EXECUTED else "REJECTED"
                },
                reason="Manager decision captured for high-risk refund.",
                changed_at=spec["created_at"] + timedelta(days=3, minutes=45),
            )
        )
    return docs


def _inventory_movements(spec: dict[str, Any]) -> list[InventoryMovement]:
    product_balance = {
        "PROD-PB-20000": 180,
        "PROD-PROJECTOR-4K": 20,
        "PROD-CUSTOM-GIFT": 7,
    }.get(spec["product_id"], 10)
    movements = [
        InventoryMovement(
            movement_id=f"IM-SHIP-{spec['order_id']}",
            product_id=spec["product_id"],
            warehouse_id="WH-SH-01",
            order_id=spec["order_id"],
            rma_id=None,
            movement_type=ErpInventoryMovementType.SHIP,
            quantity_delta=-1,
            balance_after=product_balance - 1,
            reason="Customer order shipment",
            occurred_at=spec["created_at"] + timedelta(hours=4),
        )
    ]
    if spec["refund_status"] == ErpRefundStatus.EXECUTED and spec["returnable"]:
        movements.append(
            InventoryMovement(
                movement_id=f"IM-RESTOCK-{spec['order_id']}",
                product_id=spec["product_id"],
                warehouse_id="WH-SH-01",
                order_id=spec["order_id"],
                rma_id=f"RMA-{spec['order_id']}",
                movement_type=ErpInventoryMovementType.RESTOCK,
                quantity_delta=1,
                balance_after=product_balance,
                reason="Returned item restocked after inspection",
                occurred_at=spec["created_at"] + timedelta(days=4, hours=1),
            )
        )
    return movements


def _approval_records(spec: dict[str, Any]) -> list[BusinessApprovalRecord]:
    if spec["refund_status"] not in {ErpRefundStatus.EXECUTED, ErpRefundStatus.REJECTED}:
        return []
    if spec["risk_level"] != "high":
        return []
    status = ErpDocumentStatus.APPROVED if spec["refund_status"] == ErpRefundStatus.EXECUTED else ErpDocumentStatus.REJECTED
    reason = "High-risk refund reviewed by manager." if status == ErpDocumentStatus.APPROVED else "Rejected by policy."
    return [
        BusinessApprovalRecord(
            approval_id=f"APR-{spec['refund_request_id']}",
            scenario="return_to_refund",
            object_type="refund_request",
            object_id=spec["refund_request_id"],
            refund_request_id=spec["refund_request_id"],
            stage_id="manager_review",
            status=status,
            required_role="MANAGER",
            approver_employee_id="EMP-OPS-001",
            approval_limit=5000.0,
            decision_reason=reason,
            decided_at=spec["created_at"] + timedelta(days=3, minutes=45),
        )
    ]


def _ledger_entries(spec: dict[str, Any]) -> list[FinancialLedgerEntry]:
    entries = [
        FinancialLedgerEntry(
            ledger_entry_id=f"LED-SALE-{spec['order_id']}",
            order_id=spec["order_id"],
            refund_request_id=None,
            entry_type=ErpLedgerEntryType.SALE,
            amount=spec["amount"],
            debit_account="AccountsReceivable",
            credit_account="Revenue",
            source_document=f"INV-{spec['order_id']}",
            posted_at=spec["created_at"] + timedelta(minutes=5),
        ),
        FinancialLedgerEntry(
            ledger_entry_id=f"LED-PAY-{spec['order_id']}",
            order_id=spec["order_id"],
            refund_request_id=None,
            entry_type=ErpLedgerEntryType.PAYMENT_CAPTURE,
            amount=spec["amount"],
            debit_account="Cash",
            credit_account="AccountsReceivable",
            source_document=f"PAY-{spec['order_id']}",
            posted_at=spec["created_at"] + timedelta(minutes=6),
        ),
    ]
    if spec["refund_status"] == ErpRefundStatus.EXECUTED and spec["approved_amount"]:
        entries.append(
            FinancialLedgerEntry(
                ledger_entry_id=f"LED-REFUND-{spec['refund_request_id']}",
                order_id=spec["order_id"],
                refund_request_id=spec["refund_request_id"],
                entry_type=ErpLedgerEntryType.REFUND,
                amount=spec["approved_amount"],
                debit_account="RefundExpense",
                credit_account="Cash",
                source_document=spec["refund_request_id"],
                posted_at=spec["created_at"] + timedelta(days=3, hours=1),
            )
        )
    return entries


def _journal_records(spec: dict[str, Any]) -> list[Any]:
    records: list[Any] = [
        JournalEntry(
            journal_entry_id=f"JE-SALE-{spec['order_id']}",
            tenant_id="TENANT-DEMO-COMMERCE",
            source_document=f"INV-{spec['order_id']}",
            description=f"Recognize sale for order {spec['order_id']}",
            status=ErpJournalStatus.POSTED,
            total_debit=spec["amount"],
            total_credit=spec["amount"],
            posted_at=spec["created_at"] + timedelta(minutes=5),
        ),
        JournalLine(
            journal_line_id=f"JL-SALE-AR-{spec['order_id']}",
            journal_entry_id=f"JE-SALE-{spec['order_id']}",
            account_code="AccountsReceivable",
            debit=spec["amount"],
            credit=0.0,
            cost_center_id="CC-FINANCE",
            memo="Accounts receivable for sales order",
        ),
        JournalLine(
            journal_line_id=f"JL-SALE-REV-{spec['order_id']}",
            journal_entry_id=f"JE-SALE-{spec['order_id']}",
            account_code="Revenue",
            debit=0.0,
            credit=spec["amount"],
            cost_center_id="CC-FINANCE",
            memo="Revenue for sales order",
        ),
        JournalEntry(
            journal_entry_id=f"JE-PAY-{spec['order_id']}",
            tenant_id="TENANT-DEMO-COMMERCE",
            source_document=f"PAY-{spec['order_id']}",
            description=f"Capture payment for order {spec['order_id']}",
            status=ErpJournalStatus.POSTED,
            total_debit=spec["amount"],
            total_credit=spec["amount"],
            posted_at=spec["created_at"] + timedelta(minutes=6),
        ),
        JournalLine(
            journal_line_id=f"JL-PAY-CASH-{spec['order_id']}",
            journal_entry_id=f"JE-PAY-{spec['order_id']}",
            account_code="Cash",
            debit=spec["amount"],
            credit=0.0,
            cost_center_id="CC-FINANCE",
            memo="Payment capture",
        ),
        JournalLine(
            journal_line_id=f"JL-PAY-AR-{spec['order_id']}",
            journal_entry_id=f"JE-PAY-{spec['order_id']}",
            account_code="AccountsReceivable",
            debit=0.0,
            credit=spec["amount"],
            cost_center_id="CC-FINANCE",
            memo="Clear accounts receivable",
        ),
    ]
    if spec["refund_status"] == ErpRefundStatus.EXECUTED and spec["approved_amount"]:
        amount = spec["approved_amount"]
        records.extend(
            [
                JournalEntry(
                    journal_entry_id=f"JE-REFUND-{spec['refund_request_id']}",
                    tenant_id="TENANT-DEMO-COMMERCE",
                    source_document=spec["refund_request_id"],
                    description=f"Refund execution for {spec['refund_request_id']}",
                    status=ErpJournalStatus.POSTED,
                    total_debit=amount,
                    total_credit=amount,
                    posted_at=spec["created_at"] + timedelta(days=3, hours=1),
                ),
                JournalLine(
                    journal_line_id=f"JL-REFUND-EXP-{spec['refund_request_id']}",
                    journal_entry_id=f"JE-REFUND-{spec['refund_request_id']}",
                    account_code="RefundExpense",
                    debit=amount,
                    credit=0.0,
                    cost_center_id="CC-SUPPORT",
                    memo="Refund expense recognized",
                ),
                JournalLine(
                    journal_line_id=f"JL-REFUND-CASH-{spec['refund_request_id']}",
                    journal_entry_id=f"JE-REFUND-{spec['refund_request_id']}",
                    account_code="Cash",
                    debit=0.0,
                    credit=amount,
                    cost_center_id="CC-FINANCE",
                    memo="Cash refund paid",
                ),
            ]
        )
    return records


def _data_quality_issues(spec: dict[str, Any]) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    if not spec["returnable"]:
        issues.append(
            DataQualityIssue(
                issue_id=f"DQ-NONRETURNABLE-{spec['order_id']}",
                tenant_id="TENANT-DEMO-COMMERCE",
                object_type="refund_request",
                object_id=spec["refund_request_id"],
                severity="high",
                issue_type="policy_violation",
                description="Refund requested for a non-returnable customized product.",
                detected_at=spec["created_at"] + timedelta(days=3, minutes=3),
            )
        )
    if spec["refund_status"] == ErpRefundStatus.DUPLICATE_SKIPPED:
        issues.append(
            DataQualityIssue(
                issue_id=f"DQ-DUPLICATE-{spec['order_id']}",
                tenant_id="TENANT-DEMO-COMMERCE",
                object_type="refund_request",
                object_id=spec["refund_request_id"],
                severity="high",
                issue_type="duplicate_business_request",
                description="Duplicate refund request detected by deterministic idempotency key.",
                detected_at=spec["created_at"] + timedelta(days=3, minutes=5),
            )
        )
    return issues


def _event_logs(spec: dict[str, Any]) -> list[ProcessEventLog]:
    case_id = spec["case_id"]
    order_id = spec["order_id"]
    refund_id = spec["refund_request_id"]
    base = spec["created_at"]
    events = [
        ("order_created", "order", order_id, {}, {"status": "created"}, base),
        ("payment_captured", "payment", f"PAY-{order_id}", {"status": "pending"}, {"status": "captured"}, base + timedelta(minutes=2)),
        ("invoice_posted", "invoice", f"INV-{order_id}", {"status": "draft"}, {"status": "posted"}, base + timedelta(minutes=3)),
        ("journal_posted", "journal_entry", f"JE-SALE-{order_id}", {}, {"balanced": True}, base + timedelta(minutes=5)),
        ("shipment_delivered", "shipment", f"SHP-{order_id}", {"status": "shipped"}, {"status": "delivered"}, base + timedelta(days=2)),
        ("inventory_shipped", "inventory_movement", f"IM-SHIP-{order_id}", {}, {"quantity_delta": -1}, base + timedelta(hours=4)),
        ("support_ticket_created", "support_ticket", f"CS-{order_id}", {}, {"status": "open"}, base + timedelta(days=3, minutes=-10)),
        ("complaint_recorded", "customer_complaint", f"CMP-{order_id}", {}, {"severity": "high" if spec["risk_level"] == "high" else "medium"}, base + timedelta(days=3, minutes=-8)),
        ("refund_requested", "refund_request", refund_id, {}, {"status": "requested"}, base + timedelta(days=3)),
        (
            "risk_checked",
            "refund_request",
            refund_id,
            {"risk_level": "unknown"},
            {"risk_level": spec["risk_level"]},
            base + timedelta(days=3, minutes=3),
        ),
    ]
    if spec["risk_level"] == "high" and spec["refund_status"] != ErpRefundStatus.DUPLICATE_SKIPPED:
        events.append(
            (
                "manager_review_completed",
                "approval",
                f"APR-{refund_id}",
                {"status": "submitted"},
                {"status": "approved" if spec["refund_status"] == ErpRefundStatus.EXECUTED else "rejected"},
                base + timedelta(days=3, minutes=45),
            )
        )
    if spec["refund_status"] != ErpRefundStatus.DUPLICATE_SKIPPED:
        events.extend(
            [
                ("return_authorized", "return_authorization", f"RMA-{order_id}", {"status": "requested"}, {"status": "authorized" if spec["returnable"] else "rejected"}, base + timedelta(days=3, minutes=5)),
                ("return_inspected", "return_inspection", f"INS-{order_id}", {}, {"restockable": bool(spec["returnable"] and spec["refund_status"] == ErpRefundStatus.EXECUTED)}, base + timedelta(days=4, minutes=30)),
            ]
        )
    if spec["refund_status"] == ErpRefundStatus.EXECUTED:
        events.extend(
            [
                ("inventory_restocked", "inventory_movement", f"IM-RESTOCK-{order_id}", {}, {"quantity_delta": 1}, base + timedelta(days=4, hours=1)),
                ("refund_executed", "refund_request", refund_id, {"status": "approved"}, {"status": "executed"}, base + timedelta(days=3, hours=1)),
                ("credit_memo_posted", "credit_memo", f"CM-{refund_id}", {"status": "draft"}, {"status": "posted"}, base + timedelta(days=3, hours=1, minutes=1)),
                ("open_item_cleared", "clearing_document", f"CLR-{refund_id}", {"cleared": False}, {"cleared": True}, base + timedelta(days=3, hours=1, minutes=2)),
                ("ledger_posted", "ledger_entry", f"LED-REFUND-{refund_id}", {}, {"status": "posted"}, base + timedelta(days=3, hours=1, minutes=1)),
                ("journal_posted", "journal_entry", f"JE-REFUND-{refund_id}", {}, {"balanced": True}, base + timedelta(days=3, hours=1, minutes=2)),
                ("outbox_event_created", "outbox_event", f"OUTBOX-CM-{refund_id}", {}, {"status": "pending"}, base + timedelta(days=3, hours=1, minutes=3)),
            ]
        )
    elif spec["refund_status"] == ErpRefundStatus.REJECTED:
        events.extend(
            [
                ("refund_rejected", "refund_request", refund_id, {"status": "policy_checked"}, {"status": "rejected"}, base + timedelta(days=3, hours=1)),
                ("compensation_executed", "compensation_transaction", f"COMP-{refund_id}-RMA", {}, {"status": "executed"}, base + timedelta(days=3, hours=1, minutes=3)),
            ]
        )
    elif spec["refund_status"] == ErpRefundStatus.DUPLICATE_SKIPPED:
        events.extend(
            [
                ("duplicate_refund_skipped", "refund_request", refund_id, {"status": "requested"}, {"status": "duplicate_skipped"}, base + timedelta(days=3, minutes=5)),
                ("reversal_recorded", "reversal_document", f"REV-{refund_id}", {}, {"reason": "duplicate_refund_attempt"}, base + timedelta(days=3, minutes=7)),
                ("compensation_executed", "compensation_transaction", f"COMP-{refund_id}-DUP", {}, {"status": "executed"}, base + timedelta(days=3, minutes=8)),
            ]
        )

    return [
        ProcessEventLog(
            event_id=f"EVT-{case_id}-{idx + 1:02d}",
            case_id=case_id,
            object_type=object_type,
            object_id=object_id,
            event_type=event_type,
            actor_id="system" if event_type != "manager_review_completed" else "EMP-OPS-001",
            actor_role="SYSTEM" if event_type != "manager_review_completed" else "MANAGER",
            before_state=before_state,
            after_state=after_state,
            event_metadata={"variant": spec["reason_code"], "order_id": order_id},
            occurred_at=occurred_at,
        )
        for idx, (event_type, object_type, object_id, before_state, after_state, occurred_at) in enumerate(events)
    ]
