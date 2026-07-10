"""
订单相关工具 (Function Calling Tools)
使用 Pydantic + Field(description) 确保 LLM 准确理解参数含义
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.db.database import AsyncSessionLocal
from app.db.tenant_context import apply_tenant_context, current_tenant_id, tenant_scope
from app.erp.identity import resolve_order_id
from app.db.models import (
    ApprovalMatrixRule,
    BusinessPartner,
    ChangeDocument,
    ClearingDocument,
    CompanyCode,
    CompensationTransaction,
    CreditMemoDocument,
    CustomerProfile,
    DataQualityIssue,
    DataQualityRule,
    DocumentFlow,
    ExternalSystemConnector,
    ErpOrderLine,
    FiscalPeriod,
    FinancialLedgerEntry,
    InventoryMovement,
    InventoryBatch,
    InventorySerial,
    InvoiceDocument,
    JournalEntry,
    JournalLine,
    OpenItem,
    OutboxEvent,
    Order,
    PaymentTransaction,
    PolicyVersion,
    RefundRequest,
    ReversalDocument,
    ReturnAuthorization,
    ReturnInspection,
    ShipmentDocument,
    StockReservation,
    SystemReconciliationIssue,
    SupportTicket,
    TaxCode,
    WebhookSubscription,
)
from sqlalchemy import select
from datetime import datetime
from decimal import Decimal
from enum import Enum


# ── Input Schema ──────────────────────────────────────────────────
class GetOrderDetailInput(BaseModel):
    order_id: str = Field(
        description="订单号，纯数字字符串，例如 '789012'。从用户消息中提取。"
    )


# ── Tool ──────────────────────────────────────────────────────────
@tool(args_schema=GetOrderDetailInput)
async def get_order_detail(order_id: str) -> dict:
    """
    查询指定订单的完整详情。
    返回商品列表、订单状态、实付金额、收货地址及物流单号。
    在处理退款申请前必须先调用此工具获取订单信息。
    """
    return await _get_order_detail_async(order_id)


async def _get_order_detail_async(order_id: str, *, tenant_id: str | None = None) -> dict:
    resolved_tenant = tenant_id or current_tenant_id()
    with tenant_scope(resolved_tenant):
        async with AsyncSessionLocal() as session:
            await apply_tenant_context(session, resolved_tenant)
            identity = await resolve_order_id(session, order_id, tenant_id=resolved_tenant)
            stmt = select(Order).where(
                Order.id == identity.canonical_id,
                Order.tenant_id == resolved_tenant,
            )
            result = await session.execute(stmt)
            order = result.scalar_one_or_none()

            if not order:
                return {"error": f"未找到订单 #{order_id}，请确认订单号是否正确"}

            erp_context = await _load_erp_order_context(session, order)
            return {
                "id": identity.requested_id,
                "canonicalId": order.id,
                "sourceSystem": identity.source_system,
                "aliasUsed": identity.alias_used,
                "tenantId": resolved_tenant,
                "userId": str(order.user_id),
                "status": order.status,
                "items": order.items,
                "totalAmount": float(order.amount),
                "currency": order.currency,
                "shippingAddress": order.shipping_address,
                "createdAt": order.created_at.isoformat() if order.created_at else None,
                "trackingNumber": getattr(order, "tracking_number", None),
                "carrier": getattr(order, "carrier", None),
                "erpContext": erp_context,
            }


async def _load_erp_order_context(session, order: Order) -> dict:
    order_id = order.id
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
    support_tickets = (await session.execute(select(SupportTicket).where(SupportTicket.order_id == order_id))).scalars().all()
    returns = (
        await session.execute(select(ReturnAuthorization).where(ReturnAuthorization.order_id == order_id))
    ).scalars().all()
    rma_ids = [row.rma_id for row in returns]
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
            await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id.in_([*refund_ids, *(credit_memo_ids or [])])))
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
    if product_ids:
        inventory_batches = (
            await session.execute(select(InventoryBatch).where(InventoryBatch.product_id.in_(product_ids)))
        ).scalars().all()
    inventory_serials = (
        await session.execute(select(InventorySerial).where(InventorySerial.assigned_order_id == order_id))
    ).scalars().all()
    journal_entries = (
        await session.execute(select(JournalEntry).where(JournalEntry.source_document.in_(source_documents)))
    ).scalars().all()
    journal_lines = []
    journal_ids = [entry.journal_entry_id for entry in journal_entries]
    if journal_ids:
        journal_lines = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id.in_(journal_ids)))
        ).scalars().all()
    policy_versions = (
        await session.execute(select(PolicyVersion).where(PolicyVersion.scenario == "return_to_refund"))
    ).scalars().all()
    approval_rules = (
        await session.execute(select(ApprovalMatrixRule).where(ApprovalMatrixRule.scenario == "return_to_refund"))
    ).scalars().all()
    company_codes = (await session.execute(select(CompanyCode))).scalars().all()
    fiscal_periods = (await session.execute(select(FiscalPeriod))).scalars().all()
    tax_codes = (await session.execute(select(TaxCode))).scalars().all()
    data_quality_rules = (await session.execute(select(DataQualityRule))).scalars().all()
    connectors = (await session.execute(select(ExternalSystemConnector))).scalars().all()
    webhooks = (await session.execute(select(WebhookSubscription))).scalars().all()
    reconciliation_issues = (
        await session.execute(select(SystemReconciliationIssue).where(SystemReconciliationIssue.object_id.in_(source_documents)))
    ).scalars().all()
    dq_issues = []
    if refunds:
        dq_issues = (
            await session.execute(
                select(DataQualityIssue).where(DataQualityIssue.object_id.in_([refund.refund_request_id for refund in refunds]))
            )
        ).scalars().all()
    return {
        "customer": _model_to_dict(customer) if customer else None,
        "customerPartner": _model_to_dict(customer_partner) if customer_partner else None,
        "lines": [_model_to_dict(row) for row in lines],
        "payments": [_model_to_dict(row) for row in payments],
        "invoices": [_model_to_dict(row) for row in invoices],
        "shipments": [_model_to_dict(row) for row in shipments],
        "refundRequests": [_model_to_dict(row) for row in refunds],
        "supportTickets": [_model_to_dict(row) for row in support_tickets],
        "documentFlows": [_model_to_dict(row) for row in document_flows],
        "returnAuthorizations": [_model_to_dict(row) for row in returns],
        "returnInspections": [_model_to_dict(row) for row in inspections],
        "inventoryMovements": [_model_to_dict(row) for row in inventory_movements],
        "inventoryBatches": [_model_to_dict(row) for row in inventory_batches],
        "inventorySerials": [_model_to_dict(row) for row in inventory_serials],
        "stockReservations": [_model_to_dict(row) for row in stock_reservations],
        "ledgerEntries": [_model_to_dict(row) for row in ledger_entries],
        "journalEntries": [_model_to_dict(row) for row in journal_entries],
        "journalLines": [_model_to_dict(row) for row in journal_lines],
        "openItems": [_model_to_dict(row) for row in open_items],
        "creditMemos": [_model_to_dict(row) for row in credit_memos],
        "clearingDocuments": [_model_to_dict(row) for row in clearing_documents],
        "reversalDocuments": [_model_to_dict(row) for row in reversal_documents],
        "compensationTransactions": [_model_to_dict(row) for row in compensation_transactions],
        "outboxEvents": [_model_to_dict(row) for row in outbox_events],
        "changeDocuments": [_model_to_dict(row) for row in change_documents],
        "companyCodes": [_model_to_dict(row) for row in company_codes],
        "fiscalPeriods": [_model_to_dict(row) for row in fiscal_periods],
        "taxCodes": [_model_to_dict(row) for row in tax_codes],
        "connectors": [_model_to_dict(row) for row in connectors],
        "webhooks": [_model_to_dict(row) for row in webhooks],
        "reconciliationIssues": [_model_to_dict(row) for row in reconciliation_issues],
        "policyVersions": [_model_to_dict(row) for row in policy_versions],
        "approvalMatrixRules": [_model_to_dict(row) for row in approval_rules],
        "dataQualityRules": [_model_to_dict(row) for row in data_quality_rules],
        "dataQualityIssues": [_model_to_dict(row) for row in dq_issues],
    }


def _model_to_dict(row) -> dict:
    if row is None:
        return {}
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            result[column.name] = value.isoformat()
        elif isinstance(value, Enum):
            result[column.name] = value.value
        elif isinstance(value, Decimal):
            result[column.name] = float(value)
        else:
            result[column.name] = value
    return result
