import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.tools.order_tools import _model_to_dict as order_model_to_dict
from app.api.routes.erp_mock import _model_to_dict as api_model_to_dict
from app.core.auth import create_access_token
from app.core.tenant_middleware import TenantContextMiddleware
from app.db.database import Base
from app.db.models import (
    CDCCheckpoint,
    ChangeDataCaptureEvent,
    DataContract,
    ErpOutboxStatus,
    Order,
    OutboxEvent,
    PIIRecord,
    SystemReconciliationIssue,
)
from app.erp.cdc import record_cdc_change
from app.erp.outbox_worker import process_outbox_batch, replay_dead_letter
from app.erp.pii_vault import delete_subject_pii, get_pii, purge_expired_pii, put_pii
from app.erp.reconciliation import reconcile_snapshots
from app.erp.reconciliation_worker import process_reconciliation_batch
from app.erp.financial_validation import (
    FinancialInvariantError,
    require_balanced_journal,
    require_currency_consistency,
    require_non_negative,
)
from app.erp.schema_registry import (
    ContractValidationError,
    register_contract_version,
    validate_payload,
)


def test_decimal_money_is_json_serializable_at_api_boundaries():
    order = Order(
        id="ERP-ORD-DECIMAL",
        user_id=1,
        amount=Decimal("1299.10"),
        status="delivered",
        items=[],
        shipping_address=None,
    )
    tool_payload = order_model_to_dict(order)
    api_payload = api_model_to_dict(order)
    assert tool_payload["amount"] == 1299.1
    assert api_payload["amount"] == 1299.1
    json.dumps(tool_payload)
    json.dumps(api_payload)


def test_every_erp_business_table_has_an_explicit_tenant_boundary():
    scoped_names = {
        table.name
        for table in Base.metadata.tables.values()
        if table.name.startswith("erp_")
        or table.name in {"orders", "tickets", "refund_logs", "audit_logs", "approval_decisions"}
    }
    missing = sorted(
        name for name in scoped_names if "tenant_id" not in Base.metadata.tables[name].c
    )
    assert missing == []


def test_financial_invariants_reject_currency_mismatch_negative_and_unbalanced_entries():
    assert require_currency_consistency(
        {"order": "cny", "payment": "CNY", "credit_memo": "CNY"}
    ) == "CNY"
    require_non_negative({"order": Decimal("0.00"), "refund": Decimal("299.00")})
    require_balanced_journal(Decimal("299.00"), Decimal("299.00"))

    for operation in (
        lambda: require_currency_consistency({"order": "CNY", "payment": "USD"}),
        lambda: require_non_negative({"refund": Decimal("-0.01")}),
        lambda: require_balanced_journal(Decimal("299.00"), Decimal("298.99")),
    ):
        try:
            operation()
        except FinancialInvariantError:
            pass
        else:
            raise AssertionError("financial invariant violation must be rejected")


def test_pii_vault_enforces_purpose_retention_and_crypto_erasure(monkeypatch):
    async def run():
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "unit-test-field-key-never-use-in-production")
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with Session() as session:
            record = await put_pii(
                session,
                tenant_id="TENANT-A",
                subject_type="customer",
                subject_id="C-1",
                field_name="phone",
                value="13800138000",
                purpose="refund_contact",
                retention_days=30,
            )
            await session.commit()
            assert b"13800138000" not in record.ciphertext
            assert await get_pii(
                session,
                tenant_id="TENANT-A",
                subject_type="customer",
                subject_id="C-1",
                field_name="phone",
                purpose="refund_contact",
            ) == "13800138000"
            assert await get_pii(
                session,
                tenant_id="TENANT-A",
                subject_type="customer",
                subject_id="C-1",
                field_name="phone",
                purpose="marketing",
            ) is None
            assert await delete_subject_pii(
                session, tenant_id="TENANT-A", subject_type="customer", subject_id="C-1"
            ) == 1
            await session.commit()
            stored = await session.get(PIIRecord, record.pii_record_id)
            assert stored.ciphertext == b""
            assert stored.deleted_at is not None

            expired = await put_pii(
                session,
                tenant_id="TENANT-A",
                subject_type="customer",
                subject_id="C-2",
                field_name="email",
                value="test@example.com",
                purpose="support",
                retention_days=1,
            )
            expired.expires_at = datetime.utcnow() - timedelta(seconds=1)
            other_tenant = await put_pii(
                session,
                tenant_id="TENANT-B",
                subject_type="customer",
                subject_id="C-3",
                field_name="email",
                value="other@example.com",
                purpose="support",
                retention_days=1,
            )
            other_tenant.expires_at = datetime.utcnow() - timedelta(seconds=1)
            assert await purge_expired_pii(session, tenant_id="TENANT-A") == 1
            assert other_tenant.deleted_at is None
        await engine.dispose()

    asyncio.run(run())


def test_outbox_failure_moves_to_dlq_and_can_be_replayed():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with Session() as session:
            session.add_all(
                [OutboxEvent(
                    outbox_event_id="OUTBOX-DLQ-1",
                    tenant_id="TENANT-DEMO-COMMERCE",
                    aggregate_type="refund",
                    aggregate_id="REF-1",
                    event_type="refund.posted",
                    payload={"refund_id": "REF-1"},
                    status=ErpOutboxStatus.PENDING,
                ),
                OutboxEvent(
                    outbox_event_id="OUTBOX-TENANT-B",
                    tenant_id="TENANT-B",
                    aggregate_type="refund",
                    aggregate_id="REF-B",
                    event_type="refund.posted",
                    payload={"refund_id": "REF-B"},
                    status=ErpOutboxStatus.PENDING,
                )]
            )
            await session.commit()

            async def fail(_event):
                raise RuntimeError("downstream unavailable")

            result = await process_outbox_batch(
                session,
                worker_id="test-worker",
                dispatcher=fail,
                max_attempts=1,
                tenant_id="TENANT-DEMO-COMMERCE",
            )
            assert result["dead_lettered"] == 1
            event = await session.get(OutboxEvent, "OUTBOX-DLQ-1")
            assert event.status == ErpOutboxStatus.DEAD_LETTER
            assert event.locked_by is None
            other = await session.get(OutboxEvent, "OUTBOX-TENANT-B")
            assert other.status == ErpOutboxStatus.PENDING

            replayed = await replay_dead_letter(
                session, event_id="OUTBOX-DLQ-1", replayed_by="security-test"
            )
            assert replayed.status == ErpOutboxStatus.PENDING
            assert replayed.replay_count == 1
        await engine.dispose()

    asyncio.run(run())


def test_cdc_checkpoint_reconciliation_and_contract_compatibility():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with Session() as session:
            await record_cdc_change(
                session,
                tenant_id="TENANT-DEMO-COMMERCE",
                source_system="SAP",
                stream_name="sales-order",
                source_position="000001",
                object_type="ORDER",
                object_id="ERP-ORD-1",
                operation="UPDATE",
                before_state={"status": "open"},
                after_state={"status": "closed"},
            )
            await session.commit()
            assert await session.scalar(select(func.count()).select_from(ChangeDataCaptureEvent)) == 1
            assert await session.scalar(select(func.count()).select_from(CDCCheckpoint)) == 1
            assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1

            async def matching_target(_session, _event):
                return {"status": "closed"}

            worker_result = await process_reconciliation_batch(
                session, target_loader=matching_target
            )
            assert worker_result == {
                "processed": 1,
                "matched": 1,
                "mismatched": 0,
                "target_missing": 0,
            }

            issue = await reconcile_snapshots(
                session,
                tenant_id="TENANT-DEMO-COMMERCE",
                object_type="ORDER",
                object_id="ERP-ORD-1",
                source_system="SAP",
                target_system="MINI_ERP",
                source_snapshot={"amount": Decimal("10.00")},
                target_snapshot={"amount": Decimal("11.00")},
            )
            await session.commit()
            assert issue is not None and issue.status == "open"
            assert await reconcile_snapshots(
                session,
                tenant_id="TENANT-DEMO-COMMERCE",
                object_type="ORDER",
                object_id="ERP-ORD-1",
                source_system="SAP",
                target_system="MINI_ERP",
                source_snapshot={"amount": Decimal("10.00")},
                target_snapshot={"amount": Decimal("10.00")},
            ) is None
            await session.commit()
            resolved = await session.get(SystemReconciliationIssue, issue.reconciliation_issue_id)
            assert resolved.status == "resolved"

            schema_v1 = {
                "type": "object",
                "required": ["order_id"],
                "properties": {"order_id": {"type": "string"}},
                "additionalProperties": False,
            }
            version = await register_contract_version(
                session,
                tenant_id="TENANT-DEMO-COMMERCE",
                contract_name="canonical-order",
                object_type="ORDER",
                owner="data-platform",
                schema=schema_v1,
                created_by="test",
            )
            await session.commit()
            assert version.version == 1
            assert validate_payload(schema_v1, {"order_id": "ERP-1"}) == []
            assert validate_payload(schema_v1, {}) == ["missing required field: order_id"]

            incompatible = {
                "type": "object",
                "required": ["order_id", "currency"],
                "properties": {
                    "order_id": {"type": "string"},
                    "currency": {"type": "string"},
                },
            }
            try:
                await register_contract_version(
                    session,
                    tenant_id="TENANT-DEMO-COMMERCE",
                    contract_name="canonical-order",
                    object_type="ORDER",
                    owner="data-platform",
                    schema=incompatible,
                    created_by="test",
                )
            except ContractValidationError:
                pass
            else:
                raise AssertionError("incompatible contract must be rejected")
            assert await session.scalar(select(func.count()).select_from(DataContract)) == 1
        await engine.dispose()

    asyncio.run(run())


def test_production_tenant_header_must_match_jwt(monkeypatch):
    async def run():
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "tenant-middleware-test-secret")
        app = FastAPI()
        app.add_middleware(TenantContextMiddleware)

        @app.get("/tenant")
        async def tenant(request: Request):
            return {"tenant_id": request.state.tenant_id}

        token = create_access_token("user-1", "AGENT", tenant_id="TENANT-A")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get(
                "/tenant",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-ID": "TENANT-B",
                },
            )
            allowed = await client.get(
                "/tenant", headers={"Authorization": f"Bearer {token}"}
            )
        assert denied.status_code == 403
        assert allowed.json()["tenant_id"] == "TENANT-A"

    asyncio.run(run())
