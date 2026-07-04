"""Durable refund finance Saga with resumable steps and transactional outbox."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any

from sqlalchemy import select

from app.agent.tool_gateway import (
    ToolExecutionContext,
    ToolExecutionResult,
    execute_erp_connector_tool_async,
)
from app.db.database import AsyncSessionLocal
from app.db.models import (
    CompensationTransaction,
    ErpCompensationStatus,
    ErpOutboxStatus,
    ErpSagaStatus,
    ErpSagaStepStatus,
    OutboxEvent,
    SagaExecution,
    SagaStep,
)
from app.db.tenant_context import current_tenant_id, tenant_scope
from app.commercial.operations import record_usage
from app.erp.evidence import append_evidence


@dataclass(frozen=True)
class RefundFinanceCommand:
    order_id: str
    refund_request_id: str
    open_item_id: str
    amount: Decimal
    currency: str = "CNY"
    connector_id: str = "CONN-MOCK-ERP"
    reason_code: str = "CUSTOMER_RETURN"

    def __post_init__(self) -> None:
        try:
            amount = Decimal(str(self.amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("amount must be a valid decimal value") from exc
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        currency = self.currency.strip().upper()
        if len(currency) != 3:
            raise ValueError("currency must be a three-letter ISO code")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)

    def to_payload(self) -> dict[str, Any]:
        return {**asdict(self), "amount": format(self.amount, ".2f")}


async def execute_refund_finance_saga(
    command: RefundFinanceCommand,
    *,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    """Post credit memo and clearing, resuming safely after process restarts."""

    tenant_id = _tenant_id(context)
    saga_id = _stable_id("SAGA", tenant_id, command.refund_request_id, command.connector_id)
    if context.dry_run:
        return {
            "saga_id": saga_id,
            "status": "DRY_RUN",
            "success": True,
            "steps": [
                {"name": "create_credit_memo", "status": "PLANNED"},
                {"name": "clear_open_item", "status": "PLANNED"},
                {"name": "publish_refund_posted", "status": "PLANNED"},
            ],
            "compensation": {"name": "reverse_credit_memo", "status": "AVAILABLE"},
        }

    replay = await _start_or_resume_saga(saga_id, tenant_id, command, context)
    if replay is not None:
        return {**replay, "replayed": True}

    steps: list[dict[str, Any]] = []
    preflight_specs = [
        (
            1,
            "read_sales_order",
            "erp_get_order",
            {"order_id": command.order_id, "connector_id": command.connector_id},
        ),
        (
            2,
            "read_delivery_documents",
            "erp_query_doctype",
            {
                "doctype": "delivery_document",
                "filter": f"ReferenceSDDocument eq '{command.order_id}'",
                "top": 20,
                "connector_id": command.connector_id,
            },
        ),
        (
            3,
            "read_billing_documents",
            "erp_query_doctype",
            {
                "doctype": "billing_document",
                "filter": f"SalesDocument eq '{command.order_id}'",
                "top": 20,
                "connector_id": command.connector_id,
            },
        ),
        (
            4,
            "read_open_item",
            "erp_query_doctype",
            {
                "doctype": "open_item",
                "filter": f"AccountingDocument eq '{command.open_item_id}'",
                "top": 20,
                "connector_id": command.connector_id,
            },
        ),
    ]
    preflight_results: dict[str, ToolExecutionResult] = {}
    for sequence, step_name, tool_name, args in preflight_specs:
        preflight_result = await _execute_step(
            saga_id=saga_id,
            tenant_id=tenant_id,
            sequence=sequence,
            step_name=step_name,
            tool_name=tool_name,
            args=args,
            context=context,
        )
        steps.append(_step_result(step_name, preflight_result))
        preflight_results[step_name] = preflight_result
        if not preflight_result.success:
            result = _failed_saga(saga_id, steps, step_name, preflight_result.error)
            await _finish_saga(
                saga_id, tenant_id, ErpSagaStatus.FAILED, result, preflight_result.error
            )
            return result
        if step_name == "read_sales_order":
            try:
                _validate_order_identity(command.order_id, preflight_result)
            except ValueError as exc:
                result = _failed_saga(saga_id, steps, step_name, str(exc))
                await _finish_saga(
                    saga_id, tenant_id, ErpSagaStatus.MANUAL_REVIEW, result, str(exc)
                )
                return result

    preflight_decision = _validate_financial_preflight(command, preflight_results)
    await _record_preflight_decision(
        saga_id,
        tenant_id,
        command,
        context,
        preflight_decision,
    )
    steps.append(
        {
            "name": "validate_financial_preflight",
            "status": "COMPLETED" if preflight_decision["allowed"] else "FAILED",
            "output": preflight_decision,
        }
    )
    if not preflight_decision["allowed"]:
        error = "; ".join(preflight_decision["violations"])
        result = _failed_saga(
            saga_id,
            steps,
            "validate_financial_preflight",
            error,
        )
        result["status"] = "MANUAL_REVIEW"
        await _finish_saga(
            saga_id, tenant_id, ErpSagaStatus.MANUAL_REVIEW, result, error
        )
        return result

    create_result = await _execute_step(
        saga_id=saga_id,
        tenant_id=tenant_id,
        sequence=5,
        step_name="create_credit_memo",
        tool_name="erp_create_credit_memo",
        args={
            "order_id": command.order_id,
            "refund_request_id": command.refund_request_id,
            "amount": command.amount,
            "currency": command.currency,
            "connector_id": command.connector_id,
        },
        context=context,
    )
    steps.append(_step_result("create_credit_memo", create_result))
    if not create_result.success:
        result = _failed_saga(saga_id, steps, "create_credit_memo", create_result.error)
        await _finish_saga(saga_id, tenant_id, ErpSagaStatus.FAILED, result, create_result.error)
        return result

    connector_result = dict(create_result.data or {})
    connector_data = dict(connector_result.get("data") or {})
    shadow = bool(connector_result.get("shadow"))
    credit_memo_id = str(
        connector_data.get("creditMemoId")
        or connector_data.get("CreditMemoRequest")
        or f"SHADOW-CM-{command.refund_request_id}"
    )

    clear_result = await _execute_step(
        saga_id=saga_id,
        tenant_id=tenant_id,
        sequence=6,
        step_name="clear_open_item",
        tool_name="erp_clear_open_item",
        args={
            "credit_memo_id": credit_memo_id,
            "open_item_id": command.open_item_id,
            "amount": command.amount,
            "currency": command.currency,
            "connector_id": command.connector_id,
        },
        context=context,
    )
    steps.append(_step_result("clear_open_item", clear_result))
    if not clear_result.success:
        await _set_saga_status(saga_id, tenant_id, ErpSagaStatus.COMPENSATING, "reverse_credit_memo")
        compensation = await _compensate_credit_memo(
            saga_id=saga_id,
            tenant_id=tenant_id,
            credit_memo_id=credit_memo_id,
            command=command,
            context=context,
            skip=shadow,
        )
        result = {
            **_failed_saga(saga_id, steps, "clear_open_item", clear_result.error),
            "compensation": compensation,
        }
        final_status = (
            ErpSagaStatus.MANUAL_REVIEW
            if compensation["status"] == "FAILED"
            else ErpSagaStatus.FAILED
        )
        await _finish_compensated_saga(
            saga_id,
            tenant_id,
            command,
            credit_memo_id,
            compensation,
            final_status,
            result,
        )
        return result

    clearing_connector_result = dict(clear_result.data or {})
    clearing_data = dict(clearing_connector_result.get("data") or {})
    clearing_document_id = str(
        clearing_data.get("clearingDocumentId")
        or clearing_data.get("AccountingDocument")
        or f"SHADOW-CLR-{command.refund_request_id}"
    )
    event = {
        "saga_id": saga_id,
        "refund_request_id": command.refund_request_id,
        "order_id": command.order_id,
        "credit_memo_id": credit_memo_id,
        "clearing_document_id": clearing_document_id,
        "amount": format(command.amount, ".2f"),
        "currency": command.currency,
        "connector_id": command.connector_id,
        "shadow": shadow or bool(clearing_connector_result.get("shadow")),
    }
    steps.append({"name": "publish_refund_posted", "status": "COMPLETED", "output": event})
    result = {
        "saga_id": saga_id,
        "status": "SHADOW_COMPLETED" if event["shadow"] else "COMPLETED",
        "success": True,
        "steps": steps,
        "credit_memo_id": credit_memo_id,
        "clearing_document_id": clearing_document_id,
        "outbox_event": event,
    }
    await _complete_saga_with_outbox(saga_id, tenant_id, command, result, event)
    return result


async def _execute_step(
    *,
    saga_id: str,
    tenant_id: str,
    sequence: int,
    step_name: str,
    tool_name: str,
    args: dict[str, Any],
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    step_id = _stable_id("STEP", saga_id, step_name)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            step = await session.get(SagaStep, step_id)
            if step and step.tenant_id != tenant_id:
                raise PermissionError("Saga step belongs to another tenant")
            if step and step.status == ErpSagaStepStatus.COMPLETED and step.response_snapshot:
                return _restore_tool_result(step.response_snapshot)
            now = _utcnow()
            if step is None:
                step = SagaStep(
                    step_id=step_id,
                    tenant_id=tenant_id,
                    saga_id=saga_id,
                    step_name=step_name,
                    sequence=sequence,
                    request_snapshot=_json_safe(args),
                )
                session.add(step)
            step.status = ErpSagaStepStatus.RUNNING
            step.attempts = (step.attempts or 0) + 1
            step.started_at = step.started_at or now
            step.last_error = None
            saga = await session.get(SagaExecution, saga_id)
            if saga is None or saga.tenant_id != tenant_id:
                raise RuntimeError("Durable Saga state is missing")
            saga.status = ErpSagaStatus.RUNNING
            saga.current_step = step_name
            saga.updated_at = now
            saga.version += 1
            await session.commit()

    result = await execute_erp_connector_tool_async(tool_name, args, context=context)
    snapshot = _tool_result_snapshot(result)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            step = await session.get(SagaStep, step_id)
            if step is None or step.tenant_id != tenant_id:
                raise RuntimeError("Saga step disappeared while executing")
            step.idempotency_key = result.idempotency_key
            step.response_snapshot = snapshot
            step.last_error = result.error
            step.status = (
                ErpSagaStepStatus.COMPLETED if result.success else ErpSagaStepStatus.FAILED
            )
            step.completed_at = _utcnow()
            connector_snapshot = dict(result.data or {}) if isinstance(result.data, dict) else {}
            await append_evidence(
                session,
                tenant_id=tenant_id,
                saga_id=saga_id,
                sequence=sequence * 10,
                evidence_type=f"tool.{step_name}",
                object_type="refund_request",
                object_id=str(args.get("refund_request_id") or saga_id),
                source_system=str(args.get("connector_id") or "ERP"),
                source_reference=str(
                    connector_snapshot.get("remoteRequestId")
                    or connector_snapshot.get("requestId")
                    or ""
                )
                or None,
                payload={
                    "request": _json_safe(args),
                    "result": snapshot,
                },
                actor_id=context.user_id,
                approval_id=context.approval_id,
            )
            saga = await session.get(SagaExecution, saga_id)
            if saga:
                saga.last_error = result.error
                saga.updated_at = _utcnow()
                saga.version += 1
            await session.commit()
    return result


async def _compensate_credit_memo(
    *,
    saga_id: str,
    tenant_id: str,
    credit_memo_id: str,
    command: RefundFinanceCommand,
    context: ToolExecutionContext,
    skip: bool,
) -> dict[str, Any]:
    if skip:
        return {
            "name": "reverse_credit_memo",
            "status": "SKIPPED",
            "reason": "Shadow execution did not create a remote document.",
        }
    result = await _execute_step(
        saga_id=saga_id,
        tenant_id=tenant_id,
        sequence=7,
        step_name="reverse_credit_memo",
        tool_name="erp_reverse_document",
        args={
            "source_document_id": credit_memo_id,
            "reason_code": "CLEARING_FAILED",
            "amount": command.amount,
            "currency": command.currency,
            "connector_id": command.connector_id,
        },
        context=context,
    )
    return {
        "name": "reverse_credit_memo",
        "status": "COMPLETED" if result.success else "FAILED",
        "result": result.data,
        "error": result.error,
        "saga_id": saga_id,
    }


async def _start_or_resume_saga(
    saga_id: str,
    tenant_id: str,
    command: RefundFinanceCommand,
    context: ToolExecutionContext,
) -> dict[str, Any] | None:
    command_payload = command.to_payload()
    context_snapshot = {
        "actor_role": context.actor_role,
        "requested_by_role": context.requested_by_role,
        "user_id": context.user_id,
        "thread_id": context.thread_id,
        "trace_id": context.trace_id,
        "scenario": context.scenario,
        "tenant_id": tenant_id,
        "approval_id": context.approval_id,
        "allow_live_write": context.allow_live_write,
    }
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            saga = await session.scalar(
                select(SagaExecution)
                .where(SagaExecution.saga_id == saga_id, SagaExecution.tenant_id == tenant_id)
                .with_for_update()
            )
            if saga is None:
                saga = SagaExecution(
                    saga_id=saga_id,
                    tenant_id=tenant_id,
                    saga_type="refund_finance_posting",
                    business_key=command.refund_request_id,
                    status=ErpSagaStatus.RUNNING,
                    command_payload=command_payload,
                    context_snapshot=context_snapshot,
                    started_at=_utcnow(),
                )
                session.add(saga)
                await session.flush()
                await append_evidence(
                    session,
                    tenant_id=tenant_id,
                    saga_id=saga_id,
                    sequence=1,
                    evidence_type="saga.requested",
                    object_type="refund_request",
                    object_id=command.refund_request_id,
                    source_system="AGENT",
                    source_reference=context.trace_id or None,
                    payload={
                        "command": command_payload,
                        "execution_context": context_snapshot,
                    },
                    actor_id=context.user_id,
                    approval_id=context.approval_id,
                )
                await session.commit()
                return None
            if saga.command_payload != command_payload:
                raise ValueError("The refund Saga business key was reused with a different command")
            if saga.status == ErpSagaStatus.COMPLETED and saga.result_snapshot:
                return dict(saga.result_snapshot)
            if saga.status in {ErpSagaStatus.FAILED, ErpSagaStatus.MANUAL_REVIEW}:
                return dict(saga.result_snapshot or {
                    "saga_id": saga_id,
                    "status": saga.status.value,
                    "success": False,
                    "error": saga.last_error,
                })
            saga.status = ErpSagaStatus.RUNNING
            saga.context_snapshot = context_snapshot
            saga.updated_at = _utcnow()
            saga.version += 1
            await session.commit()
    return None


async def _complete_saga_with_outbox(
    saga_id: str,
    tenant_id: str,
    command: RefundFinanceCommand,
    result: dict[str, Any],
    event_payload: dict[str, Any],
) -> None:
    """Commit completion state and its integration event atomically."""

    event_id = _stable_id("OUTBOX", tenant_id, command.refund_request_id, "refund-finance-posted")
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            saga = await session.scalar(
                select(SagaExecution)
                .where(SagaExecution.saga_id == saga_id, SagaExecution.tenant_id == tenant_id)
                .with_for_update()
            )
            if saga is None:
                raise RuntimeError("Cannot complete a missing Saga")
            existing_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.outbox_event_id == event_id,
                    OutboxEvent.tenant_id == tenant_id,
                )
            )
            if existing_event is None:
                session.add(
                    OutboxEvent(
                        outbox_event_id=event_id,
                        tenant_id=tenant_id,
                        aggregate_type="refund_request",
                        aggregate_id=command.refund_request_id,
                        event_type="refund.finance_posted",
                        payload=event_payload,
                        status=ErpOutboxStatus.PENDING,
                        idempotency_key=(
                            f"outbox:{tenant_id}:refund:{command.refund_request_id}:finance-posted"
                        ),
                    )
                )
            saga.status = ErpSagaStatus.COMPLETED
            saga.current_step = "publish_refund_posted"
            saga.result_snapshot = _json_safe(result)
            saga.last_error = None
            saga.completed_at = _utcnow()
            saga.updated_at = _utcnow()
            saga.version += 1
            await record_usage(
                session,
                tenant_id=tenant_id,
                metric_name="refund_finance_action",
                source_type="saga",
                source_id=saga_id,
                metadata={
                    "refund_request_id": command.refund_request_id,
                    "connector_id": command.connector_id,
                    "shadow": bool(event_payload.get("shadow")),
                },
            )
            await session.commit()


async def _finish_compensated_saga(
    saga_id: str,
    tenant_id: str,
    command: RefundFinanceCommand,
    credit_memo_id: str,
    compensation: dict[str, Any],
    final_status: ErpSagaStatus,
    result: dict[str, Any],
) -> None:
    compensation_id = _stable_id("COMP", saga_id, credit_memo_id)
    status_map = {
        "COMPLETED": ErpCompensationStatus.EXECUTED,
        "FAILED": ErpCompensationStatus.FAILED,
        "SKIPPED": ErpCompensationStatus.SKIPPED,
    }
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            saga = await session.get(SagaExecution, saga_id)
            if saga is None or saga.tenant_id != tenant_id:
                raise RuntimeError("Cannot finish a missing Saga")
            record = await session.get(CompensationTransaction, compensation_id)
            if record is None:
                status = status_map.get(
                    str(compensation.get("status")), ErpCompensationStatus.PENDING
                )
                session.add(
                    CompensationTransaction(
                        compensation_id=compensation_id,
                        tenant_id=tenant_id,
                        saga_id=saga_id,
                        object_type="credit_memo",
                        object_id=credit_memo_id,
                        action="reverse_credit_memo",
                        status=status,
                        reason="Clearing failed after credit memo creation.",
                        payload={"command": command.to_payload(), "result": _json_safe(compensation)},
                        executed_at=_utcnow() if status == ErpCompensationStatus.EXECUTED else None,
                    )
                )
            saga.status = final_status
            saga.current_step = "reverse_credit_memo"
            saga.result_snapshot = _json_safe(result)
            saga.last_error = str(result.get("error") or "")[:1000] or None
            saga.completed_at = _utcnow()
            saga.updated_at = _utcnow()
            saga.version += 1
            await session.commit()


async def _finish_saga(
    saga_id: str,
    tenant_id: str,
    status: ErpSagaStatus,
    result: dict[str, Any],
    error: str | None,
) -> None:
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            saga = await session.get(SagaExecution, saga_id)
            if saga is None or saga.tenant_id != tenant_id:
                raise RuntimeError("Cannot finish a missing Saga")
            saga.status = status
            saga.result_snapshot = _json_safe(result)
            saga.last_error = (error or "")[:1000] or None
            saga.completed_at = _utcnow()
            saga.updated_at = _utcnow()
            saga.version += 1
            await session.commit()


async def _set_saga_status(
    saga_id: str,
    tenant_id: str,
    status: ErpSagaStatus,
    current_step: str,
) -> None:
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            saga = await session.get(SagaExecution, saga_id)
            if saga is None or saga.tenant_id != tenant_id:
                raise RuntimeError("Cannot update a missing Saga")
            saga.status = status
            saga.current_step = current_step
            saga.updated_at = _utcnow()
            saga.version += 1
            await session.commit()


def _tool_result_snapshot(result: ToolExecutionResult) -> dict[str, Any]:
    return _json_safe(
        {
            "tool_name": result.tool_name,
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "authorized": result.authorized,
            "dry_run": result.dry_run,
            "duration_ms": result.duration_ms,
            "idempotency_key": result.idempotency_key,
            "audit_event": result.audit_event,
        }
    )


def _restore_tool_result(snapshot: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=str(snapshot.get("tool_name") or "unknown"),
        success=bool(snapshot.get("success")),
        data=snapshot.get("data"),
        error=snapshot.get("error"),
        authorized=bool(snapshot.get("authorized", True)),
        dry_run=bool(snapshot.get("dry_run", False)),
        duration_ms=int(snapshot.get("duration_ms") or 0),
        idempotency_key=snapshot.get("idempotency_key"),
        audit_event=snapshot.get("audit_event"),
    )


def _step_result(name: str, result: ToolExecutionResult) -> dict[str, Any]:
    return {
        "name": name,
        "status": "COMPLETED" if result.success else "FAILED",
        "duration_ms": result.duration_ms,
        "idempotency_key": result.idempotency_key,
        "output": result.data,
        "error": result.error,
    }


def _validate_order_identity(expected_order_id: str, result: ToolExecutionResult) -> None:
    connector = dict(result.data or {}) if isinstance(result.data, dict) else {}
    payload = connector.get("data")
    if not isinstance(payload, dict):
        return
    actual = payload.get("orderId") or payload.get("SalesOrder")
    if actual and str(actual) != expected_order_id:
        raise ValueError(
            f"SAP order identity mismatch: requested {expected_order_id}, received {actual}"
        )


def _validate_financial_preflight(
    command: RefundFinanceCommand,
    results: dict[str, ToolExecutionResult],
) -> dict[str, Any]:
    """Apply deterministic financial checks to facts returned by SAP."""

    violations: list[str] = []
    checks: list[dict[str, Any]] = []
    order = _first_record(results.get("read_sales_order"))
    order_amount = _decimal_field(order, "amount", "TotalNetAmount")
    if order_amount is not None:
        passed = command.amount <= order_amount
        checks.append(
            {
                "rule": "refund_not_above_order_amount",
                "passed": passed,
                "order_amount": format(order_amount, ".2f"),
                "refund_amount": format(command.amount, ".2f"),
            }
        )
        if not passed:
            violations.append("refund amount exceeds the SAP sales order amount")

    currencies = {
        str(value).upper()
        for value in [
            order.get("currency") or order.get("TransactionCurrency"),
            *[
                row.get("currency") or row.get("TransactionCurrency")
                for row in _records(results.get("read_billing_documents"))
            ],
            *[
                row.get("currency") or row.get("TransactionCurrency")
                for row in _records(results.get("read_open_item"))
            ],
        ]
        if value
    }
    currency_passed = not currencies or currencies == {command.currency}
    checks.append(
        {
            "rule": "currency_consistency",
            "passed": currency_passed,
            "expected": command.currency,
            "observed": sorted(currencies),
        }
    )
    if not currency_passed:
        violations.append("SAP documents use inconsistent currencies")

    open_items = _records(results.get("read_open_item"))
    already_cleared = any(
        row.get("clearingDocument") or row.get("ClearingAccountingDocument")
        for row in open_items
    )
    checks.append(
        {
            "rule": "open_item_not_already_cleared",
            "passed": not already_cleared,
            "open_item_count": len(open_items),
        }
    )
    if already_cleared:
        violations.append("SAP open item is already cleared")

    return {
        "allowed": not violations,
        "decision": "ALLOW" if not violations else "MANUAL_REVIEW",
        "checks": checks,
        "violations": violations,
    }


async def _record_preflight_decision(
    saga_id: str,
    tenant_id: str,
    command: RefundFinanceCommand,
    context: ToolExecutionContext,
    decision: dict[str, Any],
) -> None:
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            await append_evidence(
                session,
                tenant_id=tenant_id,
                saga_id=saga_id,
                sequence=45,
                evidence_type="policy.financial_preflight",
                object_type="refund_request",
                object_id=command.refund_request_id,
                source_system="POLICY_ENGINE",
                source_reference=context.trace_id or None,
                payload=decision,
                actor_id=context.user_id,
                approval_id=context.approval_id,
            )
            await session.commit()


def _records(result: ToolExecutionResult | None) -> list[dict[str, Any]]:
    if result is None or not isinstance(result.data, dict):
        return []
    payload = result.data.get("data")
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        return [dict(payload)]
    return []


def _first_record(result: ToolExecutionResult | None) -> dict[str, Any]:
    rows = _records(result)
    return rows[0] if rows else {}


def _decimal_field(record: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = record.get(name)
        if value is None or value == "":
            continue
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None
    return None


def _failed_saga(
    saga_id: str,
    steps: list[dict[str, Any]],
    failed_step: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "saga_id": saga_id,
        "status": "FAILED",
        "success": False,
        "steps": steps,
        "failed_step": failed_step,
        "error": error,
    }


def _tenant_id(context: ToolExecutionContext) -> str:
    candidate = str(context.tenant_id or "").strip()
    return current_tenant_id() if candidate in {"", "default"} else candidate


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()
    return f"{prefix}-{digest}"
