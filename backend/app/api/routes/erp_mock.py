"""ERP-like mock API for commercial Agent demos.

This exposes resource-style endpoints inspired by Odoo/ERPNext doctypes while
remaining lightweight enough for local demos and customer trials.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.orm import DeclarativeBase

from app.db.database import AsyncSessionLocal
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
    ErpOrderLine,
    ErpIdempotencyStatus,
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
    PeriodCloseRun,
    PaymentTransaction,
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
    Warehouse,
    WebhookSubscription,
    WorkOrder,
    WorkOrderComponentIssue,
    ExternalSystemConnector,
)
from app.erp.process_generator import seed_return_to_refund_demo, validate_return_to_refund_case

router = APIRouter()


DOCTYPE_REGISTRY: dict[str, tuple[type[DeclarativeBase], str]] = {
    "tenant": (TenantOrganization, "tenant_id"),
    "business_partner": (BusinessPartner, "partner_id"),
    "company_code": (CompanyCode, "company_code_id"),
    "business_unit": (BusinessUnit, "business_unit_id"),
    "department": (Department, "department_id"),
    "cost_center": (CostCenter, "cost_center_id"),
    "sales_org": (SalesOrganization, "sales_org_id"),
    "plant": (Plant, "plant_id"),
    "customer": (CustomerProfile, "customer_id"),
    "employee": (EmployeeProfile, "employee_id"),
    "supplier": (Supplier, "supplier_id"),
    "product": (ProductCatalog, "product_id"),
    "warehouse": (Warehouse, "warehouse_id"),
    "inventory_item": (InventoryItem, "inventory_id"),
    "inventory_batch": (InventoryBatch, "batch_id"),
    "inventory_serial": (InventorySerial, "serial_id"),
    "inventory_movement": (InventoryMovement, "movement_id"),
    "stock_reservation": (StockReservation, "reservation_id"),
    "sales_order": (Order, "id"),
    "sales_order_line": (ErpOrderLine, "line_id"),
    "payment_transaction": (PaymentTransaction, "payment_id"),
    "invoice": (InvoiceDocument, "invoice_id"),
    "credit_memo": (CreditMemoDocument, "credit_memo_id"),
    "clearing_document": (ClearingDocument, "clearing_document_id"),
    "reversal_document": (ReversalDocument, "reversal_document_id"),
    "compensation_transaction": (CompensationTransaction, "compensation_id"),
    "shipment": (ShipmentDocument, "shipment_id"),
    "document_flow": (DocumentFlow, "document_flow_id"),
    "support_ticket": (SupportTicket, "support_ticket_id"),
    "customer_complaint": (CustomerComplaint, "complaint_id"),
    "refund_request": (RefundRequest, "refund_request_id"),
    "return_authorization": (ReturnAuthorization, "rma_id"),
    "return_inspection": (ReturnInspection, "inspection_id"),
    "business_approval": (BusinessApprovalRecord, "approval_id"),
    "ledger_entry": (FinancialLedgerEntry, "ledger_entry_id"),
    "journal_entry": (JournalEntry, "journal_entry_id"),
    "journal_line": (JournalLine, "journal_line_id"),
    "fiscal_period": (FiscalPeriod, "fiscal_period_id"),
    "tax_code": (TaxCode, "tax_code_id"),
    "open_item": (OpenItem, "open_item_id"),
    "change_document": (ChangeDocument, "change_id"),
    "purchase_order": (PurchaseOrder, "purchase_order_id"),
    "purchase_order_line": (PurchaseOrderLine, "po_line_id"),
    "procurement_approval_request": (ProcurementApprovalRequest, "procurement_request_id"),
    "reimbursement_claim": (ReimbursementClaim, "reimbursement_id"),
    "access_request": (AccessRequestRecord, "access_request_id"),
    "goods_receipt": (GoodsReceipt, "goods_receipt_id"),
    "goods_receipt_line": (GoodsReceiptLine, "gr_line_id"),
    "ap_invoice": (APInvoice, "ap_invoice_id"),
    "policy_version": (PolicyVersion, "policy_version_id"),
    "approval_matrix_rule": (ApprovalMatrixRule, "approval_rule_id"),
    "data_quality_rule": (DataQualityRule, "rule_id"),
    "data_quality_issue": (DataQualityIssue, "issue_id"),
    "master_data_version": (MasterDataVersion, "version_id"),
    "master_data_change_request": (MasterDataChangeRequest, "mdg_request_id"),
    "master_data_validation_result": (MasterDataValidationResult, "validation_result_id"),
    "master_data_duplicate_candidate": (MasterDataDuplicateCandidate, "duplicate_id"),
    "external_system_connector": (ExternalSystemConnector, "connector_id"),
    "webhook_subscription": (WebhookSubscription, "subscription_id"),
    "outbox_event": (OutboxEvent, "outbox_event_id"),
    "idempotency_record": (IdempotencyRecord, "idempotency_key"),
    "system_reconciliation_issue": (SystemReconciliationIssue, "reconciliation_issue_id"),
    "bom": (BillOfMaterial, "bom_id"),
    "bom_line": (BillOfMaterialLine, "bom_line_id"),
    "work_order": (WorkOrder, "work_order_id"),
    "work_order_component_issue": (WorkOrderComponentIssue, "issue_id"),
    "fixed_asset": (FixedAsset, "asset_id"),
    "asset_depreciation_run": (AssetDepreciationRun, "depreciation_run_id"),
    "currency_rate": (CurrencyRate, "currency_rate_id"),
    "period_close_run": (PeriodCloseRun, "close_run_id"),
    "consolidation_group": (ConsolidationGroup, "group_id"),
    "consolidation_run": (ConsolidationRun, "consolidation_run_id"),
    "process_event": (ProcessEventLog, "event_id"),
}


@router.get("/catalog")
async def get_erp_catalog() -> dict[str, Any]:
    return {
        "product": "Mini ERP Mock API",
        "style": "Odoo/ERPNext-inspired resource doctypes",
        "doctypes": sorted(DOCTYPE_REGISTRY),
        "workflows": [
            {
                "id": "return_to_refund",
                "description": "Order-to-payment-to-delivery-to-refund flow with approvals, ledger entries, and exceptions.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            },
            {
                "id": "procure_to_pay",
                "description": "Supplier, purchase order, goods receipt, AP invoice, and open payable sample data.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            },
            {
                "id": "procurement_approval",
                "description": "Purchase approval requests with manager/finance gates, SLA timeout, and policy snapshots.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            },
            {
                "id": "reimbursement",
                "description": "Employee reimbursement claims with receipt validation, finance approval, and exception cases.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            },
            {
                "id": "permission_request",
                "description": "Enterprise access requests with SoD-sensitive permission levels and security review.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            },
            {
                "id": "manufacture_to_stock",
                "description": "BOM, work order, component issue, inventory batch, and finished-goods stock sample data.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            },
            {
                "id": "master_data_governance",
                "description": "Field validation, duplicate detection, change approval, and version history for master data.",
                "seed_endpoint": "/api/erp/seed/return-to-refund",
            }
        ],
    }


@router.get("/summary")
async def get_erp_summary() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        counts = {}
        for doctype, (model, _) in DOCTYPE_REGISTRY.items():
            rows = (await session.execute(select(model))).scalars().all()
            counts[doctype] = len(rows)
    return {
        "product": "Mini ERP Mock API",
        "doctype_count": len(DOCTYPE_REGISTRY),
        "counts": counts,
        "commercial_readiness": {
            "tenant_isolation": counts.get("tenant", 0) > 0,
            "business_partner_master_data": counts.get("business_partner", 0) > 0,
            "sap_like_org_model": counts.get("company_code", 0) > 0 and counts.get("sales_org", 0) > 0 and counts.get("plant", 0) > 0,
            "support_workflow": counts.get("support_ticket", 0) > 0,
            "return_workflow": counts.get("return_authorization", 0) > 0,
            "inventory_movements": counts.get("inventory_movement", 0) > 0,
            "document_flow": counts.get("document_flow", 0) > 0,
            "balanced_journals": counts.get("journal_entry", 0) > 0,
            "finance_controls": counts.get("fiscal_period", 0) > 0 and counts.get("tax_code", 0) > 0 and counts.get("open_item", 0) > 0,
            "refund_credit_memo_clearing": counts.get("credit_memo", 0) > 0 and counts.get("clearing_document", 0) > 0,
            "refund_compensation": counts.get("reversal_document", 0) > 0 and counts.get("compensation_transaction", 0) > 0,
            "procure_to_pay": counts.get("purchase_order", 0) > 0 and counts.get("goods_receipt", 0) > 0 and counts.get("ap_invoice", 0) > 0,
            "approval_depth": counts.get("procurement_approval_request", 0) > 0 and counts.get("reimbursement_claim", 0) > 0 and counts.get("access_request", 0) > 0,
            "inventory_traceability": counts.get("inventory_batch", 0) > 0 and counts.get("inventory_serial", 0) > 0 and counts.get("stock_reservation", 0) > 0,
            "manufacturing": counts.get("bom", 0) > 0 and counts.get("work_order", 0) > 0,
            "asset_and_close": counts.get("fixed_asset", 0) > 0 and counts.get("period_close_run", 0) > 0,
            "multi_company": counts.get("consolidation_group", 0) > 0 and counts.get("consolidation_run", 0) > 0,
            "policy_governance": counts.get("policy_version", 0) > 0 and counts.get("approval_matrix_rule", 0) > 0,
            "master_data_governance": counts.get("master_data_change_request", 0) > 0 and counts.get("master_data_duplicate_candidate", 0) > 0,
            "integration_governance": counts.get("external_system_connector", 0) > 0 and counts.get("outbox_event", 0) > 0 and counts.get("webhook_subscription", 0) > 0,
            "exception_dataset": counts.get("data_quality_issue", 0) >= 8 and counts.get("system_reconciliation_issue", 0) > 0,
        },
    }


@router.post("/seed/return-to-refund")
async def seed_return_to_refund(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        if idempotency_key:
            existing = await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == idempotency_key))
            if existing and existing.status == ErpIdempotencyStatus.COMPLETED and existing.response_snapshot:
                return {**existing.response_snapshot, "idempotent_replay": True, "idempotency_key": idempotency_key}
            await session.merge(
                IdempotencyRecord(
                    idempotency_key=idempotency_key,
                    scope="erp_seed",
                    request_hash=_stable_request_hash({"dataset": "return_to_refund_demo"}),
                    response_snapshot=None,
                    status=ErpIdempotencyStatus.IN_PROGRESS,
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
            await session.flush()
        result = await seed_return_to_refund_demo(session)
        if idempotency_key:
            await session.merge(
                IdempotencyRecord(
                    idempotency_key=idempotency_key,
                    scope="erp_seed",
                    request_hash=_stable_request_hash({"dataset": "return_to_refund_demo"}),
                    response_snapshot=result,
                    status=ErpIdempotencyStatus.COMPLETED,
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
            await session.commit()
            return {**result, "idempotent_replay": False, "idempotency_key": idempotency_key}
        return result


@router.get("/metadata")
async def get_erp_metadata() -> dict[str, Any]:
    return {
        "service": "Mini ERP Metadata",
        "query_style": {
            "list": "/api/erp/doctype/{doctype}?$select=field1,field2&$filter=status eq 'POSTED'&$orderby=created_at desc&$top=20&$skip=0&$expand=document_flow",
            "record": "/api/erp/doctype/{doctype}/{record_id}",
            "metadata": "/api/erp/metadata/{doctype}",
            "supported_filter_ops": ["eq", "ne", "gt", "ge", "lt", "le", "contains(field,'text')", "and"],
            "supported_query_options": ["$select", "$filter", "$orderby", "$top", "$skip", "$expand", "page", "page_size"],
        },
        "doctypes": {
            doctype: _doctype_metadata(doctype, model, pk_attr)
            for doctype, (model, pk_attr) in sorted(DOCTYPE_REGISTRY.items())
        },
    }


@router.get("/metadata/{doctype}")
async def get_doctype_metadata(doctype: str) -> dict[str, Any]:
    model, pk_attr = _resolve_doctype(doctype)
    return _doctype_metadata(doctype, model, pk_attr)


@router.get("/doctype/{doctype}")
async def list_doctype_records(
    doctype: str,
    limit: int = 50,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    skip: int = Query(default=0, ge=0, alias="$skip"),
    top: int | None = Query(default=None, ge=1, le=200, alias="$top"),
    select_fields: str | None = Query(default=None, alias="$select"),
    raw_filter: str | None = Query(default=None, alias="$filter"),
    order_by: str | None = Query(default=None, alias="$orderby"),
    expand: str | None = Query(default=None, alias="$expand"),
) -> dict[str, Any]:
    limit = int(_query_param_value(limit, 50) or 50)
    page = int(_query_param_value(page, 1) or 1)
    page_size_value = _query_param_value(page_size, None)
    page_size = int(page_size_value) if page_size_value is not None else None
    skip = int(_query_param_value(skip, 0) or 0)
    top_value = _query_param_value(top, None)
    top = int(top_value) if top_value is not None else None
    select_fields = _query_param_value(select_fields, None)
    raw_filter = _query_param_value(raw_filter, None)
    order_by = _query_param_value(order_by, None)
    expand = _query_param_value(expand, None)
    model, pk_attr = _resolve_doctype(doctype)
    stmt = select(model)
    stmt = _apply_odata_filter(stmt, model, raw_filter)
    stmt = _apply_order_by(stmt, model, order_by)
    effective_top = min(top or page_size or limit, 200)
    effective_skip = skip
    if page_size and skip == 0:
        effective_skip = (page - 1) * page_size
    async with AsyncSessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await session.execute(stmt.offset(effective_skip).limit(effective_top))).scalars().all()
        expanded = await _expand_rows(session, doctype, pk_attr, rows, expand)
    row_ids = [str(getattr(row, pk_attr)) for row in rows]
    projected = [_project_fields(_model_to_dict(row), select_fields) for row in rows]
    if expanded:
        for row, record_id in zip(projected, row_ids):
            if record_id in expanded:
                row["_expanded"] = expanded[record_id]
    return {
        "doctype": doctype,
        "count": len(projected),
        "total": int(total or 0),
        "page": page,
        "page_size": effective_top,
        "skip": effective_skip,
        "select": select_fields,
        "filter": raw_filter,
        "orderby": order_by,
        "expand": expand,
        "records": projected,
    }


@router.get("/doctype/{doctype}/{record_id}")
async def get_doctype_record(doctype: str, record_id: str) -> dict[str, Any]:
    model, pk_attr = _resolve_doctype(doctype)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(model).where(getattr(model, pk_attr) == record_id))
    if not row:
        raise HTTPException(status_code=404, detail=f"{doctype} '{record_id}' not found")
    return {"doctype": doctype, "record": _model_to_dict(row)}


@router.get("/orders/{order_id}")
async def get_order_aggregate(order_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id))
        if not order:
            raise HTTPException(status_code=404, detail=f"order '{order_id}' not found")
        customer = await session.scalar(select(CustomerProfile).where(CustomerProfile.user_id == order.user_id))
        customer_partner = None
        if customer:
            customer_partner = await session.scalar(
                select(BusinessPartner).where(BusinessPartner.partner_id == f"BP-{customer.customer_id}")
            )
        lines = (await session.execute(select(ErpOrderLine).where(ErpOrderLine.order_id == order_id))).scalars().all()
        product_ids = [line.product_id for line in lines]
        payments = (
            await session.execute(select(PaymentTransaction).where(PaymentTransaction.order_id == order_id))
        ).scalars().all()
        invoices = (await session.execute(select(InvoiceDocument).where(InvoiceDocument.order_id == order_id))).scalars().all()
        shipments = (await session.execute(select(ShipmentDocument).where(ShipmentDocument.order_id == order_id))).scalars().all()
        refunds = (await session.execute(select(RefundRequest).where(RefundRequest.order_id == order_id))).scalars().all()
        support_tickets = (
            await session.execute(select(SupportTicket).where(SupportTicket.order_id == order_id))
        ).scalars().all()
        support_ticket_ids = [ticket.support_ticket_id for ticket in support_tickets]
        complaints = []
        if support_ticket_ids:
            complaints = (
                await session.execute(
                    select(CustomerComplaint).where(CustomerComplaint.support_ticket_id.in_(support_ticket_ids))
                )
            ).scalars().all()
        return_authorizations = (
            await session.execute(select(ReturnAuthorization).where(ReturnAuthorization.order_id == order_id))
        ).scalars().all()
        rma_ids = [rma.rma_id for rma in return_authorizations]
        inspections = []
        if rma_ids:
            inspections = (
                await session.execute(select(ReturnInspection).where(ReturnInspection.rma_id.in_(rma_ids)))
            ).scalars().all()
        inventory_movements = (
            await session.execute(select(InventoryMovement).where(InventoryMovement.order_id == order_id))
        ).scalars().all()
        ledger_entries = (
            await session.execute(select(FinancialLedgerEntry).where(FinancialLedgerEntry.order_id == order_id))
        ).scalars().all()
        source_documents = [f"INV-{order_id}", f"PAY-{order_id}", *[refund.refund_request_id for refund in refunds]]
        document_flows = (
            await session.execute(select(DocumentFlow).where(DocumentFlow.order_id == order_id).order_by(DocumentFlow.sequence.asc()))
        ).scalars().all()
        open_items = (
            await session.execute(select(OpenItem).where(OpenItem.source_document.in_(source_documents)))
        ).scalars().all()
        credit_memos = []
        clearing_documents = []
        reversal_documents = []
        compensation_transactions = []
        outbox_events = []
        if refunds:
            refund_ids = [refund.refund_request_id for refund in refunds]
            credit_memos = (
                await session.execute(select(CreditMemoDocument).where(CreditMemoDocument.refund_request_id.in_(refund_ids)))
            ).scalars().all()
            credit_memo_ids = [memo.credit_memo_id for memo in credit_memos]
            if credit_memo_ids:
                clearing_documents = (
                    await session.execute(select(ClearingDocument).where(ClearingDocument.source_document.in_(credit_memo_ids)))
                ).scalars().all()
            reversal_documents = (
                await session.execute(select(ReversalDocument).where(ReversalDocument.source_document.in_(refund_ids)))
            ).scalars().all()
            compensation_transactions = (
                await session.execute(select(CompensationTransaction).where(CompensationTransaction.object_id.in_(refund_ids)))
            ).scalars().all()
            outbox_events = (
                await session.execute(
                    select(OutboxEvent).where(
                        or_(
                            OutboxEvent.aggregate_id.in_(refund_ids),
                            OutboxEvent.aggregate_id.in_(credit_memo_ids or ["__none__"]),
                        )
                    )
                )
            ).scalars().all()
        change_documents = (
            await session.execute(
                select(ChangeDocument)
                .where(ChangeDocument.object_id.in_([order_id, *[refund.refund_request_id for refund in refunds]]))
                .order_by(ChangeDocument.changed_at.asc())
            )
        ).scalars().all()
        stock_reservations = (
            await session.execute(select(StockReservation).where(StockReservation.order_id == order_id))
        ).scalars().all()
        inventory_batches = []
        inventory_serials = (
            await session.execute(select(InventorySerial).where(InventorySerial.assigned_order_id == order_id))
        ).scalars().all()
        if product_ids:
            inventory_batches = (
                await session.execute(select(InventoryBatch).where(InventoryBatch.product_id.in_(product_ids)))
            ).scalars().all()
        journal_entries = (
            await session.execute(select(JournalEntry).where(JournalEntry.source_document.in_(source_documents)))
        ).scalars().all()
        journal_entry_ids = [journal.journal_entry_id for journal in journal_entries]
        journal_lines = []
        if journal_entry_ids:
            journal_lines = (
                await session.execute(select(JournalLine).where(JournalLine.journal_entry_id.in_(journal_entry_ids)))
            ).scalars().all()
        policy_versions = (
            await session.execute(select(PolicyVersion).where(PolicyVersion.scenario == "return_to_refund"))
        ).scalars().all()
        approval_rules = (
            await session.execute(select(ApprovalMatrixRule).where(ApprovalMatrixRule.scenario == "return_to_refund"))
        ).scalars().all()
        company_codes = (await session.execute(select(CompanyCode))).scalars().all()
        sales_orgs = (await session.execute(select(SalesOrganization))).scalars().all()
        plants = (await session.execute(select(Plant))).scalars().all()
        fiscal_periods = (await session.execute(select(FiscalPeriod))).scalars().all()
        tax_codes = (await session.execute(select(TaxCode))).scalars().all()
        suppliers = (await session.execute(select(Supplier))).scalars().all()
        purchase_orders = (await session.execute(select(PurchaseOrder))).scalars().all()
        goods_receipts = (await session.execute(select(GoodsReceipt))).scalars().all()
        ap_invoices = (await session.execute(select(APInvoice))).scalars().all()
        data_quality_rules = (await session.execute(select(DataQualityRule))).scalars().all()
        boms = (await session.execute(select(BillOfMaterial))).scalars().all()
        work_orders = (await session.execute(select(WorkOrder))).scalars().all()
        fixed_assets = (await session.execute(select(FixedAsset))).scalars().all()
        currency_rates = (await session.execute(select(CurrencyRate))).scalars().all()
        period_close_runs = (await session.execute(select(PeriodCloseRun))).scalars().all()
        consolidation_runs = (await session.execute(select(ConsolidationRun))).scalars().all()
        connectors = (await session.execute(select(ExternalSystemConnector))).scalars().all()
        webhooks = (await session.execute(select(WebhookSubscription))).scalars().all()
        reconciliation_issues = (
            await session.execute(select(SystemReconciliationIssue).where(SystemReconciliationIssue.object_id.in_(source_documents)))
        ).scalars().all()
        data_quality_issues = []
        if refunds:
            data_quality_issues = (
                await session.execute(
                    select(DataQualityIssue).where(
                        DataQualityIssue.object_id.in_([refund.refund_request_id for refund in refunds])
                    )
                )
            ).scalars().all()
        all_events = (
            await session.execute(select(ProcessEventLog).order_by(ProcessEventLog.occurred_at.asc()))
        ).scalars().all()
        events = [event for event in all_events if (event.event_metadata or {}).get("order_id") == order_id]
        validation = await validate_return_to_refund_case(session, order_id)
    return {
        "order": _model_to_dict(order),
        "customer": _model_to_dict(customer) if customer else None,
        "customer_partner": _model_to_dict(customer_partner) if customer_partner else None,
        "lines": [_model_to_dict(row) for row in lines],
        "payments": [_model_to_dict(row) for row in payments],
        "invoices": [_model_to_dict(row) for row in invoices],
        "shipments": [_model_to_dict(row) for row in shipments],
        "document_flows": [_model_to_dict(row) for row in document_flows],
        "support_tickets": [_model_to_dict(row) for row in support_tickets],
        "complaints": [_model_to_dict(row) for row in complaints],
        "refund_requests": [_model_to_dict(row) for row in refunds],
        "return_authorizations": [_model_to_dict(row) for row in return_authorizations],
        "return_inspections": [_model_to_dict(row) for row in inspections],
        "inventory_movements": [_model_to_dict(row) for row in inventory_movements],
        "inventory_batches": [_model_to_dict(row) for row in inventory_batches],
        "inventory_serials": [_model_to_dict(row) for row in inventory_serials],
        "stock_reservations": [_model_to_dict(row) for row in stock_reservations],
        "ledger_entries": [_model_to_dict(row) for row in ledger_entries],
        "journal_entries": [_model_to_dict(row) for row in journal_entries],
        "journal_lines": [_model_to_dict(row) for row in journal_lines],
        "open_items": [_model_to_dict(row) for row in open_items],
        "credit_memos": [_model_to_dict(row) for row in credit_memos],
        "clearing_documents": [_model_to_dict(row) for row in clearing_documents],
        "reversal_documents": [_model_to_dict(row) for row in reversal_documents],
        "compensation_transactions": [_model_to_dict(row) for row in compensation_transactions],
        "outbox_events": [_model_to_dict(row) for row in outbox_events],
        "change_documents": [_model_to_dict(row) for row in change_documents],
        "policy_versions": [_model_to_dict(row) for row in policy_versions],
        "approval_matrix_rules": [_model_to_dict(row) for row in approval_rules],
        "enterprise_reference": {
            "company_codes": [_model_to_dict(row) for row in company_codes],
            "sales_orgs": [_model_to_dict(row) for row in sales_orgs],
            "plants": [_model_to_dict(row) for row in plants],
            "fiscal_periods": [_model_to_dict(row) for row in fiscal_periods],
            "tax_codes": [_model_to_dict(row) for row in tax_codes],
            "suppliers": [_model_to_dict(row) for row in suppliers],
            "purchase_orders": [_model_to_dict(row) for row in purchase_orders],
            "goods_receipts": [_model_to_dict(row) for row in goods_receipts],
            "ap_invoices": [_model_to_dict(row) for row in ap_invoices],
            "data_quality_rules": [_model_to_dict(row) for row in data_quality_rules],
            "boms": [_model_to_dict(row) for row in boms],
            "work_orders": [_model_to_dict(row) for row in work_orders],
            "fixed_assets": [_model_to_dict(row) for row in fixed_assets],
            "currency_rates": [_model_to_dict(row) for row in currency_rates],
            "period_close_runs": [_model_to_dict(row) for row in period_close_runs],
            "consolidation_runs": [_model_to_dict(row) for row in consolidation_runs],
            "connectors": [_model_to_dict(row) for row in connectors],
            "webhooks": [_model_to_dict(row) for row in webhooks],
            "reconciliation_issues": [_model_to_dict(row) for row in reconciliation_issues],
        },
        "data_quality_issues": [_model_to_dict(row) for row in data_quality_issues],
        "events": [_model_to_dict(row) for row in events],
        "validation": validation,
    }


@router.get("/master-data-governance")
async def get_master_data_governance_report() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        rules = (await session.execute(select(DataQualityRule))).scalars().all()
        validation_results = (await session.execute(select(MasterDataValidationResult))).scalars().all()
        duplicate_candidates = (await session.execute(select(MasterDataDuplicateCandidate))).scalars().all()
        change_requests = (
            await session.execute(select(MasterDataChangeRequest).order_by(MasterDataChangeRequest.created_at.desc()))
        ).scalars().all()
        versions = (await session.execute(select(MasterDataVersion))).scalars().all()
        issues = (
            await session.execute(
                select(DataQualityIssue)
                .where(DataQualityIssue.object_type.in_(["business_partner", "tax_code", "inventory_item", "invoice"]))
                .order_by(DataQualityIssue.detected_at.desc())
            )
        ).scalars().all()
    return {
        "summary": {
            "rule_count": len(rules),
            "validation_result_count": len(validation_results),
            "duplicate_candidate_count": len(duplicate_candidates),
            "pending_change_request_count": sum(1 for item in change_requests if item.status.value == "PENDING_APPROVAL"),
            "version_count": len(versions),
            "open_issue_count": sum(1 for item in issues if not item.resolved),
        },
        "rules": [_model_to_dict(row) for row in rules],
        "validation_results": [_model_to_dict(row) for row in validation_results],
        "duplicate_candidates": [_model_to_dict(row) for row in duplicate_candidates],
        "change_requests": [_model_to_dict(row) for row in change_requests],
        "versions": [_model_to_dict(row) for row in versions],
        "issues": [_model_to_dict(row) for row in issues],
    }


@router.get("/connectors")
async def list_external_connectors() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        connectors = (await session.execute(select(ExternalSystemConnector))).scalars().all()
        webhooks = (await session.execute(select(WebhookSubscription))).scalars().all()
        outbox = (
            await session.execute(select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(50))
        ).scalars().all()
    return {
        "summary": {
            "connector_count": len(connectors),
            "active_connector_count": sum(1 for connector in connectors if connector.status.value == "ACTIVE"),
            "webhook_count": len(webhooks),
            "pending_outbox_count": sum(1 for event in outbox if event.status.value == "PENDING"),
        },
        "connectors": [_model_to_dict(row) for row in connectors],
        "webhooks": [_model_to_dict(row) for row in webhooks],
        "outbox_events": [_model_to_dict(row) for row in outbox],
    }


@router.get("/connectors/{connector_id}/health")
async def get_connector_health(connector_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        connector = await session.scalar(
            select(ExternalSystemConnector).where(ExternalSystemConnector.connector_id == connector_id)
        )
    if not connector:
        raise HTTPException(status_code=404, detail=f"connector '{connector_id}' not found")
    return {
        "connector": _model_to_dict(connector),
        "health": {
            "reachable": connector.status.value == "ACTIVE",
            "latency_ms": 37 if connector.status.value == "ACTIVE" else None,
            "mode": "dry_run" if (connector.config or {}).get("dry_run_only") else "live_mock",
            "checked_at": datetime.utcnow().isoformat(),
        },
    }


@router.get("/outbox")
async def list_outbox_events(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    stmt = select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(min(max(limit, 1), 200))
    if status:
        stmt = select(OutboxEvent).where(OutboxEvent.status == status.upper()).order_by(OutboxEvent.created_at.desc()).limit(min(max(limit, 1), 200))
    async with AsyncSessionLocal() as session:
        events = (await session.execute(stmt)).scalars().all()
    return {"count": len(events), "events": [_model_to_dict(row) for row in events]}


@router.get("/exceptions")
async def list_enterprise_exceptions() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        quality_issues = (
            await session.execute(select(DataQualityIssue).order_by(DataQualityIssue.detected_at.desc()))
        ).scalars().all()
        reconciliation_issues = (
            await session.execute(select(SystemReconciliationIssue).order_by(SystemReconciliationIssue.detected_at.desc()))
        ).scalars().all()
    return {
        "summary": {
            "data_quality_issue_count": len(quality_issues),
            "reconciliation_issue_count": len(reconciliation_issues),
            "critical_count": sum(1 for item in quality_issues if item.severity == "critical"),
            "open_count": sum(1 for item in quality_issues if not item.resolved)
            + sum(1 for item in reconciliation_issues if item.status == "open"),
        },
        "data_quality_issues": [_model_to_dict(row) for row in quality_issues],
        "reconciliation_issues": [_model_to_dict(row) for row in reconciliation_issues],
    }


@router.get("/process-cases/{case_id}/events")
async def get_process_case_events(case_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        events = (
            await session.execute(
                select(ProcessEventLog)
                .where(ProcessEventLog.case_id == case_id)
                .order_by(ProcessEventLog.occurred_at.asc())
            )
        ).scalars().all()
    if not events:
        raise HTTPException(status_code=404, detail=f"process case '{case_id}' not found")
    return {
        "case_id": case_id,
        "event_count": len(events),
        "events": [_model_to_dict(row) for row in events],
    }


def _resolve_doctype(doctype: str) -> tuple[type[DeclarativeBase], str]:
    entry = DOCTYPE_REGISTRY.get(doctype)
    if not entry:
        raise HTTPException(status_code=404, detail=f"unknown doctype '{doctype}'")
    return entry


def _doctype_metadata(doctype: str, model: type[DeclarativeBase], pk_attr: str) -> dict[str, Any]:
    fields = []
    for column in model.__table__.columns:
        fields.append(
            {
                "name": column.name,
                "type": column.type.__class__.__name__,
                "primary_key": column.primary_key,
                "nullable": column.nullable,
                "indexed": bool(column.index),
            }
        )
    return {
        "doctype": doctype,
        "table": model.__tablename__,
        "primary_key": pk_attr,
        "fields": fields,
    }


def _apply_odata_filter(stmt: Any, model: type[DeclarativeBase], raw_filter: str | None) -> Any:
    if not raw_filter:
        return stmt
    predicates = []
    for clause in _split_filter_clauses(raw_filter):
        predicate = _parse_filter_clause(model, clause)
        predicates.append(predicate)
    if not predicates:
        return stmt
    return stmt.where(and_(*predicates))


def _split_filter_clauses(raw_filter: str) -> list[str]:
    return [clause.strip() for clause in raw_filter.split(" and ") if clause.strip()]


def _parse_filter_clause(model: type[DeclarativeBase], clause: str) -> Any:
    if clause.startswith("contains(") and clause.endswith(")"):
        inner = clause[len("contains("):-1]
        field_name, expected = inner.split(",", 1)
        field_name = field_name.strip()
        expected = expected.strip().strip("'\"")
        if not hasattr(model, field_name):
            raise HTTPException(status_code=400, detail=f"Unknown filter field '{field_name}'")
        return getattr(model, field_name).contains(expected)

    for op_name, operator in [
        (" ge ", lambda column, value: column >= value),
        (" le ", lambda column, value: column <= value),
        (" gt ", lambda column, value: column > value),
        (" lt ", lambda column, value: column < value),
        (" ne ", lambda column, value: column != value),
        (" eq ", lambda column, value: column == value),
    ]:
        if op_name in clause:
            left, right = clause.split(op_name, 1)
            field_name = left.strip()
            if not hasattr(model, field_name):
                raise HTTPException(status_code=400, detail=f"Unknown filter field '{field_name}'")
            column = getattr(model, field_name)
            return operator(column, _coerce_filter_value(column, right.strip().strip("'\"")))

    if "=" in clause:
        left, right = clause.split("=", 1)
        field_name = left.strip()
        if not hasattr(model, field_name):
            raise HTTPException(status_code=400, detail=f"Unknown filter field '{field_name}'")
        column = getattr(model, field_name)
        return column == _coerce_filter_value(column, right.strip().strip("'\""))

    raise HTTPException(status_code=400, detail=f"Unsupported filter clause '{clause}'")


def _coerce_filter_value(column: Any, value: str) -> Any:
    column_type = column.property.columns[0].type
    type_name = column_type.__class__.__name__.lower()
    if "integer" in type_name:
        return int(value)
    if "float" in type_name or "numeric" in type_name:
        return float(value)
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _apply_order_by(stmt: Any, model: type[DeclarativeBase], raw_order: str | None) -> Any:
    if not raw_order:
        return stmt
    order_clauses = []
    for clause in raw_order.split(","):
        parts = clause.strip().split()
        if not parts:
            continue
        field_name = parts[0]
        if not hasattr(model, field_name):
            raise HTTPException(status_code=400, detail=f"Unknown orderby field '{field_name}'")
        column = getattr(model, field_name)
        order_clauses.append(desc(column) if len(parts) > 1 and parts[1].lower() == "desc" else asc(column))
    return stmt.order_by(*order_clauses) if order_clauses else stmt


async def _expand_rows(
    session: Any,
    doctype: str,
    pk_attr: str,
    rows: list[Any],
    expand: str | None,
) -> dict[str, Any]:
    if not expand:
        return {}
    expansions = {item.strip() for item in expand.split(",") if item.strip()}
    row_ids = [str(getattr(row, pk_attr)) for row in rows]
    result: dict[str, Any] = {row_id: {} for row_id in row_ids}
    if "document_flow" in expansions:
        flows = (
            await session.execute(
                select(DocumentFlow).where(
                    or_(
                        DocumentFlow.source_id.in_(row_ids),
                        DocumentFlow.target_id.in_(row_ids),
                        DocumentFlow.order_id.in_(row_ids),
                    )
                )
            )
        ).scalars().all()
        for row_id in row_ids:
            result[row_id]["document_flow"] = [
                _model_to_dict(flow)
                for flow in flows
                if row_id in {str(flow.source_id), str(flow.target_id), str(flow.order_id)}
            ]
    if "change_document" in expansions:
        changes = (
            await session.execute(
                select(ChangeDocument).where(
                    ChangeDocument.object_type == doctype,
                    ChangeDocument.object_id.in_(row_ids),
                )
            )
        ).scalars().all()
        for row_id in row_ids:
            result[row_id]["change_document"] = [
                _model_to_dict(change)
                for change in changes
                if str(change.object_id) == row_id
            ]
    return {key: value for key, value in result.items() if value}


def _stable_request_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _query_param_value(value: Any, default: Any = None) -> Any:
    if hasattr(value, "default"):
        value = value.default
    return default if value is None else value


def _project_fields(row: dict[str, Any], select_fields: str | None) -> dict[str, Any]:
    if not select_fields:
        return row
    requested = [field.strip() for field in select_fields.split(",") if field.strip()]
    if not requested:
        return row
    return {field: row.get(field) for field in requested if field in row}


def _model_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    data: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            data[column.name] = value.isoformat()
        elif isinstance(value, Enum):
            data[column.name] = value.value
        elif isinstance(value, Decimal):
            data[column.name] = float(value)
        else:
            data[column.name] = value
    return data
