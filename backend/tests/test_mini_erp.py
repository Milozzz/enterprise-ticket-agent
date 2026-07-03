import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import erp_mock
from app.agent.tools import order_tools
from app.agent.tool_gateway import ToolExecutionContext, execute_erp_connector_tool
from app.db.database import Base
from app.db.models import (
    AccessRequestRecord,
    APInvoice,
    ApprovalMatrixRule,
    AssetDepreciationRun,
    BillOfMaterial,
    BusinessPartner,
    BusinessApprovalRecord,
    ChangeDocument,
    ClearingDocument,
    CompanyCode,
    CompensationTransaction,
    ConsolidationRun,
    CreditMemoDocument,
    CustomerComplaint,
    CurrencyRate,
    DataQualityIssue,
    DataQualityRule,
    DocumentFlow,
    ErpRefundStatus,
    ExternalSystemConnector,
    FiscalPeriod,
    FinancialLedgerEntry,
    FixedAsset,
    GoodsReceipt,
    IdempotencyRecord,
    InventoryBatch,
    InventoryItem,
    InventoryMovement,
    InventorySerial,
    JournalEntry,
    JournalLine,
    MasterDataChangeRequest,
    MasterDataDuplicateCandidate,
    MasterDataValidationResult,
    MasterDataVersion,
    OpenItem,
    OutboxEvent,
    PeriodCloseRun,
    Plant,
    PolicyVersion,
    ProcessEventLog,
    ProductCatalog,
    PurchaseOrder,
    ProcurementApprovalRequest,
    RefundRequest,
    ReimbursementClaim,
    ReversalDocument,
    ReturnAuthorization,
    ReturnInspection,
    SalesOrganization,
    StockReservation,
    SupportTicket,
    Supplier,
    SystemReconciliationIssue,
    TaxCode,
    TenantOrganization,
    WebhookSubscription,
    WorkOrder,
)
from app.erp.process_generator import seed_return_to_refund_demo, validate_return_to_refund_case


def test_return_to_refund_seed_creates_erp_consistent_dataset():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with Session() as session:
            result = await seed_return_to_refund_demo(session)
            assert result["case_count"] == 4

            product_count = await session.scalar(select(func.count()).select_from(ProductCatalog))
            inventory_count = await session.scalar(select(func.count()).select_from(InventoryItem))
            refund_count = await session.scalar(select(func.count()).select_from(RefundRequest))
            approval_count = await session.scalar(select(func.count()).select_from(BusinessApprovalRecord))
            ledger_count = await session.scalar(select(func.count()).select_from(FinancialLedgerEntry))
            event_count = await session.scalar(select(func.count()).select_from(ProcessEventLog))
            tenant_count = await session.scalar(select(func.count()).select_from(TenantOrganization))
            support_count = await session.scalar(select(func.count()).select_from(SupportTicket))
            complaint_count = await session.scalar(select(func.count()).select_from(CustomerComplaint))
            rma_count = await session.scalar(select(func.count()).select_from(ReturnAuthorization))
            inspection_count = await session.scalar(select(func.count()).select_from(ReturnInspection))
            movement_count = await session.scalar(select(func.count()).select_from(InventoryMovement))
            journal_count = await session.scalar(select(func.count()).select_from(JournalEntry))
            journal_line_count = await session.scalar(select(func.count()).select_from(JournalLine))
            policy_count = await session.scalar(select(func.count()).select_from(PolicyVersion))
            approval_rule_count = await session.scalar(select(func.count()).select_from(ApprovalMatrixRule))
            dq_count = await session.scalar(select(func.count()).select_from(DataQualityIssue))
            business_partner_count = await session.scalar(select(func.count()).select_from(BusinessPartner))
            company_code_count = await session.scalar(select(func.count()).select_from(CompanyCode))
            sales_org_count = await session.scalar(select(func.count()).select_from(SalesOrganization))
            plant_count = await session.scalar(select(func.count()).select_from(Plant))
            document_flow_count = await session.scalar(select(func.count()).select_from(DocumentFlow))
            fiscal_period_count = await session.scalar(select(func.count()).select_from(FiscalPeriod))
            tax_code_count = await session.scalar(select(func.count()).select_from(TaxCode))
            open_item_count = await session.scalar(select(func.count()).select_from(OpenItem))
            credit_memo_count = await session.scalar(select(func.count()).select_from(CreditMemoDocument))
            clearing_count = await session.scalar(select(func.count()).select_from(ClearingDocument))
            reversal_count = await session.scalar(select(func.count()).select_from(ReversalDocument))
            compensation_count = await session.scalar(select(func.count()).select_from(CompensationTransaction))
            change_document_count = await session.scalar(select(func.count()).select_from(ChangeDocument))
            supplier_count = await session.scalar(select(func.count()).select_from(Supplier))
            purchase_order_count = await session.scalar(select(func.count()).select_from(PurchaseOrder))
            goods_receipt_count = await session.scalar(select(func.count()).select_from(GoodsReceipt))
            ap_invoice_count = await session.scalar(select(func.count()).select_from(APInvoice))
            procurement_request_count = await session.scalar(select(func.count()).select_from(ProcurementApprovalRequest))
            reimbursement_count = await session.scalar(select(func.count()).select_from(ReimbursementClaim))
            access_request_count = await session.scalar(select(func.count()).select_from(AccessRequestRecord))
            batch_count = await session.scalar(select(func.count()).select_from(InventoryBatch))
            serial_count = await session.scalar(select(func.count()).select_from(InventorySerial))
            reservation_count = await session.scalar(select(func.count()).select_from(StockReservation))
            data_quality_rule_count = await session.scalar(select(func.count()).select_from(DataQualityRule))
            mdg_version_count = await session.scalar(select(func.count()).select_from(MasterDataVersion))
            mdg_request_count = await session.scalar(select(func.count()).select_from(MasterDataChangeRequest))
            mdg_validation_count = await session.scalar(select(func.count()).select_from(MasterDataValidationResult))
            mdg_duplicate_count = await session.scalar(select(func.count()).select_from(MasterDataDuplicateCandidate))
            connector_count = await session.scalar(select(func.count()).select_from(ExternalSystemConnector))
            webhook_count = await session.scalar(select(func.count()).select_from(WebhookSubscription))
            outbox_count = await session.scalar(select(func.count()).select_from(OutboxEvent))
            idempotency_count = await session.scalar(select(func.count()).select_from(IdempotencyRecord))
            reconciliation_count = await session.scalar(select(func.count()).select_from(SystemReconciliationIssue))
            bom_count = await session.scalar(select(func.count()).select_from(BillOfMaterial))
            work_order_count = await session.scalar(select(func.count()).select_from(WorkOrder))
            asset_count = await session.scalar(select(func.count()).select_from(FixedAsset))
            depreciation_count = await session.scalar(select(func.count()).select_from(AssetDepreciationRun))
            fx_count = await session.scalar(select(func.count()).select_from(CurrencyRate))
            period_close_count = await session.scalar(select(func.count()).select_from(PeriodCloseRun))
            consolidation_count = await session.scalar(select(func.count()).select_from(ConsolidationRun))

            assert tenant_count == 1
            assert business_partner_count >= 10
            assert company_code_count == 2
            assert sales_org_count == 1
            assert plant_count == 1
            assert product_count == 5
            assert inventory_count == 5
            assert refund_count == 4
            assert support_count == 4
            assert complaint_count == 4
            assert rma_count == 3
            assert inspection_count == 3
            assert movement_count >= 6
            assert approval_count >= 2
            assert ledger_count >= 9
            assert journal_count >= 10
            assert journal_line_count >= 20
            assert policy_count == 1
            assert approval_rule_count == 3
            assert dq_count >= 9
            assert document_flow_count >= 55
            assert fiscal_period_count == 2
            assert tax_code_count == 2
            assert open_item_count >= 7
            assert credit_memo_count == 2
            assert clearing_count == 2
            assert reversal_count == 1
            assert compensation_count == 2
            assert change_document_count >= 13
            assert supplier_count == 3
            assert purchase_order_count == 1
            assert goods_receipt_count == 1
            assert ap_invoice_count == 1
            assert procurement_request_count == 2
            assert reimbursement_count == 2
            assert access_request_count == 2
            assert batch_count == 2
            assert serial_count == 1
            assert reservation_count == 4
            assert data_quality_rule_count >= 4
            assert mdg_version_count == 2
            assert mdg_request_count == 2
            assert mdg_validation_count == 1
            assert mdg_duplicate_count == 1
            assert connector_count == 2
            assert webhook_count == 2
            assert outbox_count >= 6
            assert idempotency_count >= 1
            assert reconciliation_count == 1
            assert bom_count == 1
            assert work_order_count == 1
            assert asset_count == 1
            assert depreciation_count == 1
            assert fx_count == 1
            assert period_close_count == 1
            assert consolidation_count == 1
            assert event_count >= 60

            validation = await validate_return_to_refund_case(session, "ERP-ORD-1002")
            assert validation["valid"] is True
            assert validation["checks"]["approved_refund_amount"] == 1299.0
            assert validation["checks"]["inventory_movement_count"] >= 2
            assert validation["checks"]["document_flow_count"] >= 10
            assert validation["checks"]["open_item_count"] >= 2
            assert validation["checks"]["credit_memo_count"] == 1
            assert validation["checks"]["clearing_document_count"] == 1
            assert validation["checks"]["outbox_event_count"] >= 1
            assert validation["checks"]["change_document_count"] >= 2
            assert validation["checks"]["stock_reservation_count"] == 1
            assert validation["checks"]["has_return_authorization"] is True
            assert validation["checks"]["has_open_fiscal_period"] is True
            assert validation["checks"]["has_active_tax_code"] is True
            assert validation["checks"]["has_active_policy"] is True

            rejected = await session.scalar(
                select(RefundRequest).where(RefundRequest.refund_request_id == "ERP-REF-1003")
            )
            duplicate = await session.scalar(
                select(RefundRequest).where(RefundRequest.refund_request_id == "ERP-REF-1004")
            )
            assert rejected.status == ErpRefundStatus.REJECTED
            assert duplicate.status == ErpRefundStatus.DUPLICATE_SKIPPED
        await engine.dispose()

    asyncio.run(run())


def test_mock_erp_api_returns_order_aggregate_and_events(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(erp_mock, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with Session() as session:
            await seed_return_to_refund_demo(session)

        catalog = await erp_mock.get_erp_catalog()
        summary = await erp_mock.get_erp_summary()
        metadata = await erp_mock.get_doctype_metadata("purchase_order")
        credit_memo_metadata = await erp_mock.get_doctype_metadata("credit_memo")
        filtered_flows = await erp_mock.list_doctype_records(
            "document_flow",
            select_fields="source_doctype,target_doctype,relation_type",
            raw_filter="order_id eq 'ERP-ORD-1001'",
        )
        filtered_refunds = await erp_mock.list_doctype_records(
            "refund_request",
            raw_filter="risk_level eq 'high' and status ne 'EXECUTED'",
            order_by="created_at desc",
            top=2,
            expand="document_flow",
        )
        aggregate = await erp_mock.get_order_aggregate("ERP-ORD-1001")
        events = await erp_mock.get_process_case_events("RTR-CASE-1001")
        mdg = await erp_mock.get_master_data_governance_report()
        connectors = await erp_mock.list_external_connectors()
        pending_outbox = await erp_mock.list_outbox_events(status="PENDING")
        exceptions = await erp_mock.list_enterprise_exceptions()
        seeded = await erp_mock.seed_return_to_refund(idempotency_key="test-mini-erp-seed")
        replay = await erp_mock.seed_return_to_refund(idempotency_key="test-mini-erp-seed")

        assert "refund_request" in catalog["doctypes"]
        assert "journal_entry" in catalog["doctypes"]
        assert "business_partner" in catalog["doctypes"]
        assert "work_order" in catalog["doctypes"]
        assert "credit_memo" in catalog["doctypes"]
        assert "master_data_change_request" in catalog["doctypes"]
        assert "external_system_connector" in catalog["doctypes"]
        assert metadata["primary_key"] == "purchase_order_id"
        assert any(field["name"] == "supplier_id" for field in metadata["fields"])
        assert credit_memo_metadata["primary_key"] == "credit_memo_id"
        assert filtered_flows["count"] >= 10
        assert set(filtered_flows["records"][0]) <= {"source_doctype", "target_doctype", "relation_type"}
        assert filtered_refunds["count"] == 2
        assert "_expanded" in filtered_refunds["records"][0]
        assert summary["commercial_readiness"]["support_workflow"] is True
        assert summary["commercial_readiness"]["policy_governance"] is True
        assert summary["commercial_readiness"]["sap_like_org_model"] is True
        assert summary["commercial_readiness"]["procure_to_pay"] is True
        assert summary["commercial_readiness"]["manufacturing"] is True
        assert summary["commercial_readiness"]["multi_company"] is True
        assert summary["commercial_readiness"]["refund_credit_memo_clearing"] is True
        assert summary["commercial_readiness"]["refund_compensation"] is True
        assert summary["commercial_readiness"]["approval_depth"] is True
        assert summary["commercial_readiness"]["master_data_governance"] is True
        assert summary["commercial_readiness"]["integration_governance"] is True
        assert summary["commercial_readiness"]["exception_dataset"] is True
        assert aggregate["order"]["id"] == "ERP-ORD-1001"
        assert aggregate["validation"]["valid"] is True
        assert aggregate["refund_requests"][0]["status"] == "EXECUTED"
        assert aggregate["customer_partner"]["partner_type"] == "CUSTOMER"
        assert aggregate["document_flows"]
        assert aggregate["open_items"]
        assert aggregate["change_documents"]
        assert aggregate["stock_reservations"]
        assert aggregate["support_tickets"][0]["support_ticket_id"] == "CS-ERP-ORD-1001"
        assert aggregate["return_authorizations"][0]["rma_id"] == "RMA-ERP-ORD-1001"
        assert aggregate["inventory_movements"]
        assert aggregate["credit_memos"][0]["credit_memo_id"] == "CM-ERP-REF-1001"
        assert aggregate["clearing_documents"][0]["clearing_document_id"] == "CLR-ERP-REF-1001"
        assert any("credit_memo" in event["event_type"] for event in aggregate["outbox_events"])
        assert aggregate["enterprise_reference"]["purchase_orders"][0]["purchase_order_id"] == "PO-2026-0001"
        assert aggregate["enterprise_reference"]["work_orders"][0]["work_order_id"] == "WO-PB-2026-0501"
        assert aggregate["enterprise_reference"]["period_close_runs"]
        assert aggregate["enterprise_reference"]["connectors"]
        assert aggregate["enterprise_reference"]["webhooks"]
        assert aggregate["journal_entries"]
        assert aggregate["policy_versions"][0]["policy_id"] == "REFUND-POLICY"
        assert mdg["summary"]["duplicate_candidate_count"] == 1
        assert mdg["summary"]["pending_change_request_count"] == 1
        assert connectors["summary"]["connector_count"] == 2
        assert connectors["summary"]["pending_outbox_count"] >= 1
        assert pending_outbox["count"] >= 1
        assert exceptions["summary"]["data_quality_issue_count"] >= 9
        assert seeded["idempotent_replay"] is False
        assert replay["idempotent_replay"] is True
        event_types = [event["event_type"] for event in events["events"]]
        assert "journal_posted" in event_types
        assert "credit_memo_posted" in event_types
        assert "open_item_cleared" in event_types
        assert "outbox_event_created" in event_types
        assert event_types[-1] == "inventory_restocked"
        await engine.dispose()

    asyncio.run(run())


def test_lookup_order_tool_includes_mini_erp_context(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(order_tools, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with Session() as session:
            await seed_return_to_refund_demo(session)

        detail = await order_tools._get_order_detail_async("ERP-ORD-1002")

        assert detail["id"] == "ERP-ORD-1002"
        assert detail["erpContext"]["customer"]["customer_id"] == "CUST-8102"
        assert detail["erpContext"]["supportTickets"][0]["priority"] == "high"
        assert detail["erpContext"]["refundRequests"][0]["risk_level"] == "high"
        assert detail["erpContext"]["customerPartner"]["partner_type"] == "CUSTOMER"
        assert detail["erpContext"]["documentFlows"]
        assert detail["erpContext"]["openItems"]
        assert detail["erpContext"]["creditMemos"][0]["credit_memo_id"] == "CM-ERP-REF-1002"
        assert detail["erpContext"]["clearingDocuments"][0]["clearing_document_id"] == "CLR-ERP-REF-1002"
        assert any("credit_memo" in event["event_type"] for event in detail["erpContext"]["outboxEvents"])
        assert detail["erpContext"]["changeDocuments"]
        assert detail["erpContext"]["stockReservations"]
        assert detail["erpContext"]["inventorySerials"][0]["serial_number"] == "PJ4K2026050002"
        assert detail["erpContext"]["companyCodes"]
        assert detail["erpContext"]["taxCodes"]
        assert detail["erpContext"]["dataQualityRules"]
        assert detail["erpContext"]["approvalMatrixRules"]
        assert detail["erpContext"]["journalEntries"]
        assert detail["erpContext"]["connectors"]
        assert detail["erpContext"]["webhooks"]
        await engine.dispose()

    asyncio.run(run())


def test_tool_gateway_builds_erp_connector_envelopes():
    context = ToolExecutionContext(
        actor_role="AGENT",
        requested_by_role="MANAGER",
        scenario="refund",
        approval_id="APR-ERP-1002",
    )

    query_result = execute_erp_connector_tool(
        "erp_query_doctype",
        {
            "doctype": "refund_request",
            "filter": "risk_level eq 'high'",
            "select": "refund_request_id,status,risk_level",
            "orderby": "created_at desc",
            "top": 5,
            "expand": "document_flow",
        },
        context=context,
    )
    credit_memo_result = execute_erp_connector_tool(
        "erp_create_credit_memo",
        {
            "order_id": "ERP-ORD-1002",
            "refund_request_id": "ERP-REF-1002",
            "amount": 1299.0,
            "currency": "CNY",
        },
        context=context,
    )

    assert query_result.success is True
    assert query_result.data["connectorId"] == "CONN-MOCK-ERP"
    assert query_result.data["method"] == "GET"
    assert query_result.data["path"] == "/api/erp/doctype/refund_request"
    assert query_result.data["payload"]["query"]["$filter"] == "risk_level eq 'high'"
    assert credit_memo_result.success is True
    assert credit_memo_result.data["method"] == "POST"
    assert credit_memo_result.data["path"] == "/api/erp/doctype/credit_memo"
    assert credit_memo_result.data["idempotencyKey"].startswith("erp-credit-memo:ERP-REF-1002")
    assert credit_memo_result.idempotency_key is not None
    assert credit_memo_result.audit_event["side_effect"] == "write"
