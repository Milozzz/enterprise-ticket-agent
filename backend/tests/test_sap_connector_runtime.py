import asyncio
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.tool_gateway import (
    ToolExecutionContext,
    ToolExecutionResult,
    execute_erp_connector_tool_async,
)
from app.db.database import Base
from app.agent.enterprise_readiness import run_enterprise_readiness_eval
from app.api.routes import erp_runtime
from app.db.models import (
    AuditLog,
    CompensationTransaction,
    ExecutionEvidence,
    ExternalSystemConnector,
    IdempotencyRecord,
    OutboxEvent,
    SagaExecution,
    SagaStep,
)
from app.erp import metrics, refund_saga, runtime
from app.erp.connectors import build_erp_batch_request, build_erp_create_credit_memo_request
from app.erp.evidence import export_evidence_bundle
from app.erp.refund_saga import RefundFinanceCommand, execute_refund_finance_saga
from app.erp.runtime import (
    ConnectorRuntimeConfig,
    ERPWriteBlockedError,
    SAPODataConnector,
    execute_connector_envelope,
)


def test_connector_control_plane_persists_only_non_secret_metadata(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(erp_runtime, "AsyncSessionLocal", Session)
        monkeypatch.setattr(runtime, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        payload = erp_runtime.ConnectorConfigurationPayload(
            name="SAP Sandbox",
            system_type="sap_s4hana",
            base_url="https://sandbox.api.sap.com",
            auth_type="api_key",
            status="DRAFT",
            mode="live",
            read_only=True,
            shadow_writes=True,
            operation_paths={"doctype.business_partner": "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner"},
        )
        response = await erp_runtime.update_runtime_config(
            "CONN-SAP-CONTROL-PLANE",
            payload,
            {"user_id": "manager-1", "role": "MANAGER"},
        )

        assert response["connector"]["mode"] == "live"
        assert response["connector"]["read_only"] is True
        assert response["secret_policy"]["persisted"] is False
        assert response["secret_policy"]["required_environment_variables"] == ["SAP_API_KEY"]
        async with Session() as session:
            record = await session.get(ExternalSystemConnector, "CONN-SAP-CONTROL-PLANE")
            assert record.config["operation_paths"]["doctype.business_partner"].startswith("/sap/")
            assert "api_key" not in record.config
            assert "password" not in record.config
        await engine.dispose()

    asyncio.run(run())


def test_connector_control_plane_requires_change_ticket_for_unshadowed_live_writes():
    payload = erp_runtime.ConnectorConfigurationPayload(
        name="SAP Production",
        base_url="https://sap.example.test",
        mode="live",
        read_only=False,
        shadow_writes=False,
    )
    try:
        erp_runtime._validate_connector_payload(payload)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "change_ticket" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("live writes must require a change ticket")


def test_enterprise_readiness_passes_internal_gates_and_reports_external_blockers():
    report = asyncio.run(run_enterprise_readiness_eval())

    assert report["interview_demo_ready"] is True
    assert report["production_ready"] is False
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["policy_fail_closed"]["passed"] is True
    assert checks["erp_write_approval"]["passed"] is True
    assert checks["mcp_runtime_coverage"]["passed"] is True
    assert checks["live_sap_connection"]["status"] == "BLOCKED_EXTERNAL"


def test_erp_business_metrics_expose_slo_and_operation_breakdown(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(metrics, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with Session() as session:
            session.add_all(
                [
                    AuditLog(
                        thread_id="erp:1",
                        trace_id="trace-1",
                        node_name="erp_connector",
                        event_type="get_order",
                        input_data={},
                        output_data={"shadow": False, "replayed": False},
                        duration_ms=100,
                        success=True,
                    ),
                    AuditLog(
                        thread_id="erp:2",
                        trace_id="trace-2",
                        node_name="erp_connector",
                        event_type="create_credit_memo",
                        input_data={},
                        output_data={"shadow": True, "replayed": False},
                        duration_ms=300,
                        success=True,
                    ),
                ]
            )
            await session.commit()

        report = await metrics.get_erp_business_metrics()
        assert report["connector_executions"]["total"] == 2
        assert report["connector_executions"]["success_rate"] == 1.0
        assert report["connector_executions"]["shadow_count"] == 1
        assert {item["operation"] for item in report["operations"]} == {
            "get_order",
            "create_credit_memo",
        }
        assert report["slo"]["objectives"]["success_rate"]["met"] is True
        await engine.dispose()

    asyncio.run(run())


def test_mock_connector_executes_write_with_persistent_idempotency(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(runtime, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        envelope = build_erp_create_credit_memo_request(
            "ERP-ORD-1002", "ERP-REF-1002", 1299.0, connector_id="CONN-MOCK-ERP"
        )
        first = await execute_connector_envelope(envelope)
        second = await execute_connector_envelope(envelope)

        assert first.success is True
        assert first.data["creditMemoId"].startswith("CM-")
        assert second.replayed is True
        assert second.data == first.data
        async with Session() as session:
            assert await session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        await engine.dispose()

    asyncio.run(run())


def test_async_tool_gateway_requires_approval_for_financial_write(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(runtime, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        arguments = {
            "order_id": "ERP-ORD-1002",
            "refund_request_id": "ERP-REF-1002",
            "amount": 1299.0,
            "connector_id": "CONN-MOCK-ERP",
        }
        blocked = await execute_erp_connector_tool_async(
            "erp_create_credit_memo",
            arguments,
            context=ToolExecutionContext(actor_role="AGENT"),
        )
        allowed = await execute_erp_connector_tool_async(
            "erp_create_credit_memo",
            arguments,
            context=ToolExecutionContext(actor_role="AGENT", approval_id="APR-1002"),
        )

        assert blocked.success is False
        assert blocked.authorized is False
        assert "approval evidence" in blocked.error
        assert allowed.success is True
        assert allowed.data["data"]["creditMemoId"].startswith("CM-")
        await engine.dispose()

    asyncio.run(run())


def test_sap_odata_write_fetches_csrf_and_normalizes_response(monkeypatch):
    async def run():
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    headers={"x-csrf-token": "csrf-123", "set-cookie": "sap-usercontext=demo"},
                    json={"value": []},
                )
            assert request.headers["x-csrf-token"] == "csrf-123"
            assert request.headers["apikey"] == "sandbox-key"
            assert request.headers["idempotency-key"].startswith("erp-credit-memo:")
            return httpx.Response(
                201,
                headers={"etag": "W/\"42\"", "x-request-id": "sap-request-42"},
                json={"d": {"CreditMemoRequest": "90000042"}},
            )

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)

        def client_factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(runtime.httpx, "AsyncClient", client_factory)
        connector = SAPODataConnector(
            ConnectorRuntimeConfig(
                connector_id="CONN-SAP-TEST",
                mode="live",
                base_url="https://sap.example.test",
                auth_type="api_key",
                api_key="sandbox-key",
                read_only=False,
                shadow_writes=False,
                max_retries=0,
            )
        )
        envelope = build_erp_create_credit_memo_request(
            "50000001", "REF-42", 99.0, connector_id="CONN-SAP-TEST"
        )
        result = await connector.execute(envelope)

        assert result.success is True
        assert result.data["creditMemoId"] == "90000042"
        assert result.etag == 'W/"42"'
        assert result.remote_request_id == "sap-request-42"
        assert [request.method for request in requests] == ["GET", "POST"]

    asyncio.run(run())


def test_sap_multipart_batch_fetches_csrf_and_preserves_identity(monkeypatch):
    async def run():
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                assert request.headers["authorization"] == "Bearer named-user-token"
                return httpx.Response(200, headers={"x-csrf-token": "csrf-batch"}, json={"value": []})
            assert request.headers["authorization"] == "Bearer named-user-token"
            assert request.headers["x-csrf-token"] == "csrf-batch"
            assert request.headers["content-type"].startswith("multipart/mixed; boundary=batch_")
            assert b"GET /sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder" in request.content
            return httpx.Response(200, json={"value": [{"id": "order-read", "status": 200}]})

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)

        def client_factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(runtime.httpx, "AsyncClient", client_factory)
        connector = SAPODataConnector(
            ConnectorRuntimeConfig(
                connector_id="CONN-SAP-BATCH",
                mode="live",
                base_url="https://sap.example.test",
                auth_type="principal_propagation",
                read_only=False,
                shadow_writes=False,
                max_retries=0,
            )
        )
        envelope = build_erp_batch_request(
            [
                {
                    "id": "order-read",
                    "method": "GET",
                    "url": "/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('1')",
                }
            ],
            batch_format="multipart",
            connector_id="CONN-SAP-BATCH",
        )
        result = await connector.execute(envelope, principal_token="named-user-token")
        assert result.success is True
        assert [request.method for request in requests] == ["GET", "POST"]

    asyncio.run(run())


def test_principal_propagation_forbids_technical_user_fallback():
    async def run():
        connector = SAPODataConnector(
            ConnectorRuntimeConfig(
                connector_id="CONN-SAP-PRINCIPAL",
                mode="live",
                base_url="https://sap.example.test",
                auth_type="principal_propagation",
            )
        )
        async with httpx.AsyncClient() as client:
            try:
                await connector._authentication(client, None)
            except Exception as exc:
                assert "named-user token" in str(exc)
            else:
                raise AssertionError("principal propagation must not fall back to a technical user")

    asyncio.run(run())


def test_sap_error_parser_handles_v2_innererror_and_request_id():
    response = httpx.Response(
        400,
        headers={"x-request-id": "REQ-42"},
        json={
            "error": {
                "code": "SD/042",
                "message": {"lang": "en", "value": "Credit memo reason is invalid"},
                "innererror": {
                    "transactionid": "TX-42",
                    "errordetails": [{"code": "SD/043", "message": "Invalid reason"}],
                },
            }
        },
    )
    message = SAPODataConnector._safe_http_error(response)
    assert "SD/042" in message
    assert "Credit memo reason is invalid" in message
    assert "TX-42" in message


def test_live_sap_connector_blocks_writes_in_read_only_mode():
    async def run():
        connector = SAPODataConnector(
            ConnectorRuntimeConfig(
                connector_id="CONN-SAP-READONLY",
                mode="live",
                base_url="https://sap.example.test",
                auth_type="none",
                read_only=True,
                shadow_writes=False,
            )
        )
        envelope = build_erp_create_credit_memo_request("1", "2", 10.0, connector_id="CONN-SAP-READONLY")
        try:
            await connector.execute(envelope)
        except ERPWriteBlockedError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("read-only connector must block financial writes")

    asyncio.run(run())


def test_refund_finance_saga_posts_credit_memo_clearing_and_outbox(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(runtime, "AsyncSessionLocal", Session)
        monkeypatch.setattr(refund_saga, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        result = await execute_refund_finance_saga(
            RefundFinanceCommand(
                order_id="ERP-ORD-1002",
                refund_request_id="ERP-REF-LIVE-1002",
                open_item_id="OI-1002",
                amount=1299.0,
            ),
            context=ToolExecutionContext(
                actor_role="FINANCE",
                requested_by_role="MANAGER",
                approval_id="APR-FIN-1002",
                scenario="refund_finance_posting",
            ),
        )

        assert result["success"] is True
        assert result["status"] == "COMPLETED"
        assert result["credit_memo_id"].startswith("CM-")
        assert result["clearing_document_id"].startswith("CLR-")
        replay = await execute_refund_finance_saga(
            RefundFinanceCommand(
                order_id="ERP-ORD-1002",
                refund_request_id="ERP-REF-LIVE-1002",
                open_item_id="OI-1002",
                amount=Decimal("1299.00"),
            ),
            context=ToolExecutionContext(
                actor_role="FINANCE",
                requested_by_role="MANAGER",
                approval_id="APR-FIN-1002",
                scenario="refund_finance_posting",
            ),
        )
        assert replay["replayed"] is True
        assert replay["credit_memo_id"] == result["credit_memo_id"]
        async with Session() as session:
            assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
            assert await session.scalar(select(func.count()).select_from(SagaExecution)) == 1
            assert await session.scalar(select(func.count()).select_from(SagaStep)) == 6
            assert await session.scalar(select(func.count()).select_from(ExecutionEvidence)) == 8
            bundle = await export_evidence_bundle(
                session,
                tenant_id="TENANT-DEMO-COMMERCE",
                saga_id=result["saga_id"],
            )
            assert bundle is not None
            assert bundle["integrity"]["chain_valid"] is True
            assert bundle["integrity"]["item_count"] == 8
        await engine.dispose()

    asyncio.run(run())


def test_refund_finance_saga_compensates_credit_memo_when_clearing_fails(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(refund_saga, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        calls = []

        async def fake_execute(tool_name, args, *, context):
            calls.append(tool_name)
            if tool_name == "erp_create_credit_memo":
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=True,
                    data={"data": {"creditMemoId": "CM-FAIL-CLEAR"}},
                )
            if tool_name == "erp_clear_open_item":
                return ToolExecutionResult(tool_name=tool_name, success=False, error="clearing rejected")
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                data={"data": {"reversalDocumentId": "REV-42"}},
            )

        monkeypatch.setattr(refund_saga, "execute_erp_connector_tool_async", fake_execute)
        result = await execute_refund_finance_saga(
            RefundFinanceCommand(
                order_id="ERP-ORD-FAIL",
                refund_request_id="ERP-REF-FAIL",
                open_item_id="OI-FAIL",
                amount=88.0,
            ),
            context=ToolExecutionContext(actor_role="FINANCE", approval_id="APR-FAIL"),
        )

        assert result["success"] is False
        assert result["failed_step"] == "clear_open_item"
        assert result["compensation"]["status"] == "COMPLETED"
        assert calls == [
            "erp_get_order",
            "erp_query_doctype",
            "erp_query_doctype",
            "erp_query_doctype",
            "erp_create_credit_memo",
            "erp_clear_open_item",
            "erp_reverse_document",
        ]
        replay = await execute_refund_finance_saga(
            RefundFinanceCommand(
                order_id="ERP-ORD-FAIL",
                refund_request_id="ERP-REF-FAIL",
                open_item_id="OI-FAIL",
                amount=Decimal("88.00"),
            ),
            context=ToolExecutionContext(actor_role="FINANCE", approval_id="APR-FAIL"),
        )
        assert replay["replayed"] is True
        assert replay["compensation"]["status"] == "COMPLETED"
        assert len(calls) == 7
        async with Session() as session:
            assert await session.scalar(select(func.count()).select_from(CompensationTransaction)) == 1
        await engine.dispose()

    asyncio.run(run())


def test_refund_finance_saga_blocks_write_when_sap_amount_is_inconsistent(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(refund_saga, "AsyncSessionLocal", Session)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        calls: list[str] = []

        async def fake_execute(tool_name, args, *, context):
            calls.append(tool_name)
            if tool_name == "erp_get_order":
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=True,
                    data={
                        "connectorId": "CONN-SAP",
                        "data": {
                            "orderId": "ERP-ORD-RISK",
                            "amount": "10.00",
                            "currency": "CNY",
                        },
                    },
                )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                data={"connectorId": "CONN-SAP", "data": []},
            )

        monkeypatch.setattr(refund_saga, "execute_erp_connector_tool_async", fake_execute)
        result = await execute_refund_finance_saga(
            RefundFinanceCommand(
                order_id="ERP-ORD-RISK",
                refund_request_id="ERP-REF-RISK",
                open_item_id="OI-RISK",
                amount=Decimal("88.00"),
            ),
            context=ToolExecutionContext(actor_role="FINANCE", approval_id="APR-RISK"),
        )

        assert result["success"] is False
        assert result["status"] == "MANUAL_REVIEW"
        assert result["failed_step"] == "validate_financial_preflight"
        assert "erp_create_credit_memo" not in calls
        await engine.dispose()

    asyncio.run(run())
