"""Unified gateway for agent tool execution.

The gateway keeps business tools behind one auditable boundary. It does not
replace domain-level idempotency checks in nodes/repositories; it standardizes
metadata, permission checks, dry-run behavior, and execution envelopes.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import inspect
import logging
from threading import Lock
from time import perf_counter
from typing import Any, Mapping

from app.agent.utils import get_state_val
from app.core.config import get_settings
from app.core.policy import PolicyDecision, evaluate_action_policy
from app.erp.connectors import (
    build_erp_clear_open_item_request,
    build_erp_create_credit_memo_request,
    build_erp_get_order_request,
    build_erp_query_doctype_request,
    build_erp_reverse_document_request,
)
from app.erp.runtime import execute_connector_envelope

logger = logging.getLogger(__name__)
settings = get_settings()


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolSideEffect(str, Enum):
    READ = "read"
    DECISION = "decision"
    WRITE = "write"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    action: str
    description: str
    risk_level: ToolRiskLevel
    side_effect: ToolSideEffect
    category: str = "business"
    owner: str = "Platform"
    enabled: bool = True
    timeout_seconds: float = 10.0
    retry_limit: int = 0
    circuit_failure_threshold: int | None = None
    circuit_reset_seconds: int | None = None
    fallback_strategy: str = "structured_error"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    idempotency_fields: tuple[str, ...] = ()
    idempotency_namespace: str | None = None
    dry_run_supported: bool = True
    approval_required: bool = False


@dataclass(frozen=True)
class ToolExecutionContext:
    actor_role: str = "AGENT"
    requested_by_role: str = "USER"
    user_id: str = "unknown"
    thread_id: str = ""
    trace_id: str = ""
    scenario: str = "refund"
    dry_run: bool = False
    tenant_id: str = "default"
    approval_id: str | None = None
    principal_token: str | None = field(default=None, repr=False)
    allow_live_write: bool = False


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
    authorized: bool = True
    dry_run: bool = False
    duration_ms: int = 0
    idempotency_key: str | None = None
    audit_event: dict[str, Any] | None = None


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


_CIRCUITS: dict[str, _CircuitState] = {}
_CIRCUIT_LOCK = Lock()
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="tool-gateway")


TOOL_SPECS: dict[str, ToolSpec] = {
    "lookup_order": ToolSpec(
        name="lookup_order",
        action="lookup_order",
        description="Read order details before processing a refund workflow.",
        risk_level=ToolRiskLevel.LOW,
        side_effect=ToolSideEffect.READ,
        category="order",
        owner="Customer Operations",
        timeout_seconds=3.0,
        input_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {"order_id": {"type": "string"}},
        },
    ),
    "check_risk_level": ToolSpec(
        name="check_risk_level",
        action="check_risk_level",
        description="Evaluate refund risk and decide whether HITL is required.",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=ToolSideEffect.DECISION,
        category="risk",
        owner="Risk Platform",
        timeout_seconds=5.0,
        input_schema={
            "type": "object",
            "required": ["order_id", "amount", "user_id"],
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number"},
                "user_id": {"type": "string"},
            },
        },
    ),
    "execute_refund": ToolSpec(
        name="execute_refund",
        action="execute_refund",
        description="Create a refund side effect after risk/HITL gates pass.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        category="payment",
        owner="Finance Operations",
        timeout_seconds=8.0,
        retry_limit=1,
        idempotency_fields=("order_id", "ticket_id", "amount"),
        idempotency_namespace="refund",
        input_schema={
            "type": "object",
            "required": ["order_id", "ticket_id", "amount"],
            "properties": {
                "order_id": {"type": "string"},
                "ticket_id": {"type": "string"},
                "amount": {"type": "number"},
            },
        },
    ),
    "send_notification": ToolSpec(
        name="send_notification",
        action="send_notification",
        description="Notify finance after a refund is completed.",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=ToolSideEffect.EXTERNAL,
        category="notification",
        owner="Customer Operations",
        timeout_seconds=5.0,
        retry_limit=2,
        idempotency_fields=("order_id", "refund_id"),
        idempotency_namespace="notification",
        input_schema={
            "type": "object",
            "required": ["order_id", "refund_id"],
            "properties": {
                "order_id": {"type": "string"},
                "refund_id": {"type": "string"},
            },
        },
    ),
    "create_permission_request": ToolSpec(
        name="create_permission_request",
        action="create_permission_request",
        description="Create an enterprise access request with approval metadata.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        category="access",
        owner="Security",
        timeout_seconds=6.0,
        retry_limit=1,
        idempotency_fields=("requester_id", "system", "permission_level"),
        idempotency_namespace="permission_request",
        input_schema={
            "type": "object",
            "required": ["requester_id", "system", "permission_level"],
            "properties": {
                "requester_id": {"type": "string"},
                "system": {"type": "string"},
                "permission_level": {"type": "string"},
                "business_reason": {"type": "string"},
            },
        },
    ),
    "create_reimbursement_request": ToolSpec(
        name="create_reimbursement_request",
        action="create_reimbursement_request",
        description="Create an employee reimbursement request for finance approval.",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=ToolSideEffect.WRITE,
        category="finance",
        owner="Finance Operations",
        timeout_seconds=6.0,
        retry_limit=1,
        idempotency_fields=("requester_id", "amount", "category", "description"),
        idempotency_namespace="reimbursement",
        input_schema={
            "type": "object",
            "required": ["requester_id", "amount", "category"],
            "properties": {
                "requester_id": {"type": "string"},
                "amount": {"type": "number"},
                "category": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    ),
    "erp_get_order": ToolSpec(
        name="erp_get_order",
        action="erp_get_order",
        description="Read an ERP order aggregate through the configured ERP Connector.",
        risk_level=ToolRiskLevel.LOW,
        side_effect=ToolSideEffect.READ,
        category="erp_connector",
        owner="Enterprise Integration",
        timeout_seconds=5.0,
        input_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {"type": "string"},
                "connector_id": {"type": "string"},
            },
        },
    ),
    "erp_query_doctype": ToolSpec(
        name="erp_query_doctype",
        action="erp_query_doctype",
        description="Query an ERP doctype with OData-like select/filter/order/paging options.",
        risk_level=ToolRiskLevel.LOW,
        side_effect=ToolSideEffect.READ,
        category="erp_connector",
        owner="Enterprise Integration",
        timeout_seconds=5.0,
        input_schema={
            "type": "object",
            "required": ["doctype"],
            "properties": {
                "doctype": {"type": "string"},
                "filter": {"type": "string"},
                "select": {"type": "string"},
                "orderby": {"type": "string"},
                "top": {"type": "integer"},
                "skip": {"type": "integer"},
                "expand": {"type": "string"},
                "connector_id": {"type": "string"},
            },
        },
    ),
    "erp_create_credit_memo": ToolSpec(
        name="erp_create_credit_memo",
        action="erp_create_credit_memo",
        description="Create an ERP credit memo request through the configured connector.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        category="erp_connector",
        owner="Finance Operations",
        timeout_seconds=8.0,
        retry_limit=1,
        idempotency_fields=("refund_request_id", "amount", "currency"),
        idempotency_namespace="erp_credit_memo",
        approval_required=True,
        input_schema={
            "type": "object",
            "required": ["order_id", "refund_request_id", "amount"],
            "properties": {
                "order_id": {"type": "string"},
                "refund_request_id": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
                "connector_id": {"type": "string"},
            },
        },
    ),
    "erp_clear_open_item": ToolSpec(
        name="erp_clear_open_item",
        action="erp_clear_open_item",
        description="Clear an ERP customer open item against a posted credit memo.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        category="erp_connector",
        owner="Finance Operations",
        timeout_seconds=10.0,
        retry_limit=1,
        idempotency_fields=("credit_memo_id", "open_item_id", "amount", "currency"),
        idempotency_namespace="erp_clearing",
        approval_required=True,
        input_schema={
            "type": "object",
            "required": ["credit_memo_id", "open_item_id", "amount"],
            "properties": {
                "credit_memo_id": {"type": "string"},
                "open_item_id": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
                "connector_id": {"type": "string"},
            },
        },
    ),
    "erp_reverse_document": ToolSpec(
        name="erp_reverse_document",
        action="erp_reverse_document",
        description="Reverse an ERP financial document as a compensating action.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        category="erp_connector",
        owner="Finance Operations",
        timeout_seconds=10.0,
        retry_limit=1,
        idempotency_fields=("source_document_id", "reason_code"),
        idempotency_namespace="erp_reversal",
        approval_required=True,
        input_schema={
            "type": "object",
            "required": ["source_document_id", "reason_code", "amount"],
            "properties": {
                "source_document_id": {"type": "string"},
                "reason_code": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
                "connector_id": {"type": "string"},
            },
        },
    ),
}


def list_tool_specs() -> list[dict[str, Any]]:
    """Return compact registry metadata for docs, debug APIs, or tests."""
    return [
        {
            "name": spec.name,
            "action": spec.action,
            "risk_level": spec.risk_level.value,
            "side_effect": spec.side_effect.value,
            "description": spec.description,
            "category": spec.category,
            "owner": spec.owner,
            "enabled": spec.enabled,
            "timeout_seconds": spec.timeout_seconds,
            "retry_limit": spec.retry_limit,
            "circuit_failure_threshold": _failure_threshold(spec),
            "circuit_reset_seconds": _reset_seconds(spec),
            "fallback_strategy": spec.fallback_strategy,
            "dry_run_supported": spec.dry_run_supported,
            "approval_required": spec.approval_required,
            "idempotency_fields": list(spec.idempotency_fields),
            "idempotency_namespace": spec.idempotency_namespace,
            "input_schema": spec.input_schema,
            "output_schema": spec.output_schema,
        }
        for spec in TOOL_SPECS.values()
    ]


def tool_registry_report() -> dict[str, Any]:
    """Return governance-oriented Tool Gateway registry details."""
    tools = list_tool_specs()
    return {
        "tools": tools,
        "summary": {
            "tool_count": len(tools),
            "enabled_count": sum(1 for tool in tools if tool["enabled"]),
            "high_risk_count": sum(1 for tool in tools if tool["risk_level"] == ToolRiskLevel.HIGH.value),
            "write_tool_count": sum(1 for tool in tools if tool["side_effect"] == ToolSideEffect.WRITE.value),
            "external_tool_count": sum(1 for tool in tools if tool["side_effect"] == ToolSideEffect.EXTERNAL.value),
            "idempotent_tool_count": sum(1 for tool in tools if tool["idempotency_fields"]),
            "retry_enabled_count": sum(1 for tool in tools if int(tool["retry_limit"]) > 0),
            "open_circuit_count": sum(1 for state in circuit_breaker_snapshot().values() if state["state"] == "open"),
        },
        "governance": {
            "boundary": "LLM decisions must pass Tool Gateway before business side effects.",
            "controls": [
                "action policy authorization",
                "dry-run execution",
                "idempotency key generation",
                "structured audit event",
                "retry metadata",
                "timeout metadata",
                "circuit breaker isolation",
            ],
            "circuits": circuit_breaker_snapshot(),
        },
    }


def gateway_context_from_state(
    state: Mapping[str, Any],
    *,
    actor_role: str | None = None,
    dry_run: bool = False,
    scenario: str = "refund",
) -> ToolExecutionContext:
    requested_by_role = str(get_state_val(state, "user_role", "USER") or "USER")
    return ToolExecutionContext(
        actor_role=str(actor_role or requested_by_role),
        requested_by_role=requested_by_role,
        user_id=str(get_state_val(state, "user_id", "unknown") or "unknown"),
        thread_id=str(get_state_val(state, "thread_id", "") or ""),
        trace_id=str(get_state_val(state, "trace_id", "") or ""),
        scenario=scenario,
        dry_run=dry_run,
        tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
        approval_id=(
            str(get_state_val(state, "approval_id"))
            if get_state_val(state, "approval_id")
            else None
        ),
        allow_live_write=bool(
            get_state_val(state, "human_decision") == "approve"
            or not get_state_val(state, "requires_human_approval", False)
        ),
    )


def execute_tool(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    context: ToolExecutionContext,
    handler: Any,
) -> ToolExecutionResult:
    spec = TOOL_SPECS[tool_name]
    started = perf_counter()
    idempotency_key = _build_idempotency_key(spec, args)
    action_policy = evaluate_action_policy(
        context.actor_role,
        spec.action,
        {
            "tool": tool_name,
            "requested_by_role": context.requested_by_role,
            "scenario": context.scenario,
            **dict(args),
        },
        routing_key=context.thread_id or context.trace_id or context.user_id,
    )
    audit_base = _audit_base(spec, context, idempotency_key, action_policy)

    if not action_policy.allowed:
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": False,
            "success": False,
            "duration_ms": duration_ms,
            "error": action_policy.reason,
        }
        _log("warning", "tool_gateway_permission_denied", audit_event)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error=action_policy.reason,
            authorized=False,
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )

    if spec.approval_required and not context.approval_id and not context.dry_run:
        duration_ms = _elapsed_ms(started)
        error = f"Tool '{tool_name}' requires approval evidence before execution."
        audit_event = {
            **audit_base,
            "authorized": False,
            "success": False,
            "duration_ms": duration_ms,
            "error": error,
        }
        _log("warning", "tool_gateway_approval_missing", audit_event)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error=error,
            authorized=False,
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )

    schema_error = _validate_tool_input(spec, args)
    if schema_error:
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": True,
            "success": False,
            "duration_ms": duration_ms,
            "error": schema_error,
        }
        _log("warning", "tool_gateway_input_invalid", audit_event)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error=schema_error,
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )

    if context.dry_run:
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": True,
            "success": True,
            "duration_ms": duration_ms,
            "dry_run": True,
        }
        _log("info", "tool_gateway_dry_run", audit_event)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            data={
                "dryRun": True,
                "tool": spec.name,
                "sideEffect": spec.side_effect.value,
                "riskLevel": spec.risk_level.value,
                "idempotencyKey": idempotency_key,
            },
            dry_run=True,
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )

    circuit_error = _before_tool_call(spec)
    if circuit_error:
        return _circuit_open_result(tool_name, started, idempotency_key, audit_base, circuit_error)

    attempts = 0
    last_error: Exception | None = None
    while attempts <= max(0, spec.retry_limit):
        attempts += 1
        try:
            future = _TOOL_EXECUTOR.submit(_invoke_handler, handler, dict(args))
            try:
                data = future.result(timeout=spec.timeout_seconds)
            except FutureTimeoutError as exc:
                future.cancel()
                raise TimeoutError(
                    f"Tool '{tool_name}' timed out after {spec.timeout_seconds:.1f}s"
                ) from exc
            duration_ms = _elapsed_ms(started)
            _record_tool_success(spec)
            audit_event = {
                **audit_base,
                "authorized": True,
                "success": True,
                "duration_ms": duration_ms,
                "attempts": attempts,
                "timeout_seconds": spec.timeout_seconds,
                "retry_limit": spec.retry_limit,
            }
            _log("info", "tool_gateway_executed", audit_event)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                data=data,
                duration_ms=duration_ms,
                idempotency_key=idempotency_key,
                audit_event=audit_event,
            )
        except Exception as exc:
            last_error = exc
            if attempts <= spec.retry_limit:
                _log(
                    "warning",
                    "tool_gateway_retry",
                    {
                        **audit_base,
                        "attempt": attempts,
                        "retry_limit": spec.retry_limit,
                        "error": str(exc),
                    },
                )

    if last_error is not None:
        _record_tool_failure(spec)
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": True,
            "success": False,
            "duration_ms": duration_ms,
            "attempts": attempts,
            "timeout_seconds": spec.timeout_seconds,
            "retry_limit": spec.retry_limit,
            "fallback_strategy": spec.fallback_strategy,
            "error": str(last_error),
        }
        _log("error", "tool_gateway_failed", audit_event)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error=str(last_error),
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )

    raise RuntimeError("Tool Gateway reached an impossible execution state.")


def execute_erp_connector_tool(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """Execute built-in ERP connector tools through the same gateway boundary."""

    handlers = {
        "erp_get_order": lambda **kwargs: build_erp_get_order_request(
            kwargs["order_id"],
            connector_id=kwargs.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_query_doctype": lambda **kwargs: build_erp_query_doctype_request(
            kwargs["doctype"],
            filter=kwargs.get("filter"),
            select=kwargs.get("select"),
            orderby=kwargs.get("orderby"),
            top=int(kwargs.get("top") or 20),
            skip=int(kwargs.get("skip") or 0),
            expand=kwargs.get("expand"),
            connector_id=kwargs.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_create_credit_memo": lambda **kwargs: build_erp_create_credit_memo_request(
            kwargs["order_id"],
            kwargs["refund_request_id"],
            float(kwargs["amount"]),
            currency=kwargs.get("currency") or "CNY",
            connector_id=kwargs.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_clear_open_item": lambda **kwargs: build_erp_clear_open_item_request(
            kwargs["credit_memo_id"],
            kwargs["open_item_id"],
            float(kwargs["amount"]),
            currency=kwargs.get("currency") or "CNY",
            connector_id=kwargs.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_reverse_document": lambda **kwargs: build_erp_reverse_document_request(
            kwargs["source_document_id"],
            kwargs["reason_code"],
            float(kwargs["amount"]),
            currency=kwargs.get("currency") or "CNY",
            connector_id=kwargs.get("connector_id") or "CONN-MOCK-ERP",
        ),
    }
    if tool_name not in handlers:
        raise KeyError(f"Unknown ERP connector tool: {tool_name}")
    return execute_tool(tool_name, args, context=context, handler=handlers[tool_name])


async def execute_tool_async(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    context: ToolExecutionContext,
    handler: Any,
) -> ToolExecutionResult:
    """Async Tool Gateway execution with enforced timeout and retry policy."""

    spec = TOOL_SPECS[tool_name]
    started = perf_counter()
    idempotency_key = _build_idempotency_key(spec, args)
    action_policy = evaluate_action_policy(
        context.actor_role,
        spec.action,
        {
            "tool": tool_name,
            "requested_by_role": context.requested_by_role,
            "scenario": context.scenario,
            "tenant_id": context.tenant_id,
            **dict(args),
        },
        routing_key=context.thread_id or context.trace_id or context.user_id,
    )
    audit_base = _audit_base(spec, context, idempotency_key, action_policy)
    if not action_policy.allowed:
        return _blocked_result(tool_name, started, idempotency_key, audit_base, action_policy.reason)
    if spec.approval_required and not context.approval_id and not context.dry_run:
        return _blocked_result(
            tool_name,
            started,
            idempotency_key,
            audit_base,
            f"Tool '{tool_name}' requires approval evidence before execution.",
        )
    schema_error = _validate_tool_input(spec, args)
    if schema_error:
        return _blocked_result(tool_name, started, idempotency_key, audit_base, schema_error)
    if context.dry_run:
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": True,
            "success": True,
            "duration_ms": duration_ms,
            "dry_run": True,
        }
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            data={
                "dryRun": True,
                "tool": spec.name,
                "sideEffect": spec.side_effect.value,
                "riskLevel": spec.risk_level.value,
                "idempotencyKey": idempotency_key,
            },
            dry_run=True,
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )

    circuit_error = _before_tool_call(spec)
    if circuit_error:
        return _circuit_open_result(tool_name, started, idempotency_key, audit_base, circuit_error)

    attempts = 0
    last_error: Exception | None = None
    while attempts <= max(0, spec.retry_limit):
        attempts += 1
        try:
            data = await asyncio.wait_for(_invoke_handler_async(handler, dict(args)), timeout=spec.timeout_seconds)
            duration_ms = _elapsed_ms(started)
            _record_tool_success(spec)
            audit_event = {
                **audit_base,
                "authorized": True,
                "success": True,
                "duration_ms": duration_ms,
                "attempts": attempts,
            }
            _log("info", "tool_gateway_async_executed", audit_event)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                data=data,
                duration_ms=duration_ms,
                idempotency_key=idempotency_key,
                audit_event=audit_event,
            )
        except Exception as exc:
            last_error = exc
            if attempts <= spec.retry_limit:
                await asyncio.sleep(min(0.1 * attempts, 0.5))

    duration_ms = _elapsed_ms(started)
    _record_tool_failure(spec)
    error = str(last_error or "Unknown async tool execution failure.")
    audit_event = {
        **audit_base,
        "authorized": True,
        "success": False,
        "duration_ms": duration_ms,
        "attempts": attempts,
        "error": error,
    }
    _log("error", "tool_gateway_async_failed", audit_event)
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error=error,
        duration_ms=duration_ms,
        idempotency_key=idempotency_key,
        audit_event=audit_event,
    )


async def execute_erp_connector_tool_async(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    """Build and execute an ERP request through the live/mock connector runtime."""

    builders = {
        "erp_get_order": lambda values: build_erp_get_order_request(
            values["order_id"], connector_id=values.get("connector_id") or "CONN-MOCK-ERP"
        ),
        "erp_query_doctype": lambda values: build_erp_query_doctype_request(
            values["doctype"],
            filter=values.get("filter"),
            select=values.get("select"),
            orderby=values.get("orderby"),
            top=int(values.get("top") or 20),
            skip=int(values.get("skip") or 0),
            expand=values.get("expand"),
            connector_id=values.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_create_credit_memo": lambda values: build_erp_create_credit_memo_request(
            values["order_id"],
            values["refund_request_id"],
            values["amount"],
            currency=values.get("currency") or "CNY",
            connector_id=values.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_clear_open_item": lambda values: build_erp_clear_open_item_request(
            values["credit_memo_id"],
            values["open_item_id"],
            values["amount"],
            currency=values.get("currency") or "CNY",
            connector_id=values.get("connector_id") or "CONN-MOCK-ERP",
        ),
        "erp_reverse_document": lambda values: build_erp_reverse_document_request(
            values["source_document_id"],
            values["reason_code"],
            values["amount"],
            currency=values.get("currency") or "CNY",
            connector_id=values.get("connector_id") or "CONN-MOCK-ERP",
        ),
    }
    if tool_name not in builders:
        raise KeyError(f"Unknown ERP connector tool: {tool_name}")

    async def handler(**values: Any) -> dict[str, Any]:
        envelope = builders[tool_name](values)
        # F2：把 agent 侧链路标识透传进连接器审计，端到端 trace 可 join
        from app.erp.runtime import AgentTraceRef

        result = await execute_connector_envelope(
            envelope,
            principal_token=context.principal_token,
            force_write=context.allow_live_write,
            tenant_id=context.tenant_id,
            agent_trace=AgentTraceRef(
                trace_id=context.trace_id or None,
                thread_id=context.thread_id or None,
                scenario=context.scenario,
                actor_role=context.actor_role,
            ),
        )
        return result.to_dict()

    return await execute_tool_async(tool_name, args, context=context, handler=handler)


def _invoke_handler(handler: Any, args: dict[str, Any]) -> Any:
    if hasattr(handler, "invoke"):
        return handler.invoke(args)
    if callable(handler):
        return handler(**args)
    raise TypeError(f"Handler for tool is not invokable: {type(handler)!r}")


async def _invoke_handler_async(handler: Any, args: dict[str, Any]) -> Any:
    if hasattr(handler, "ainvoke"):
        return await handler.ainvoke(args)
    if hasattr(handler, "invoke"):
        value = handler.invoke(args)
    elif callable(handler):
        value = handler(**args)
    else:
        raise TypeError(f"Handler for tool is not invokable: {type(handler)!r}")
    return await value if inspect.isawaitable(value) else value


def _blocked_result(
    tool_name: str,
    started: float,
    idempotency_key: str | None,
    audit_base: dict[str, Any],
    error: str,
) -> ToolExecutionResult:
    duration_ms = _elapsed_ms(started)
    audit_event = {
        **audit_base,
        "authorized": False,
        "success": False,
        "duration_ms": duration_ms,
        "error": error,
    }
    _log("warning", "tool_gateway_async_blocked", audit_event)
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error=error,
        authorized=False,
        duration_ms=duration_ms,
        idempotency_key=idempotency_key,
        audit_event=audit_event,
    )


def _circuit_open_result(
    tool_name: str,
    started: float,
    idempotency_key: str | None,
    audit_base: dict[str, Any],
    error: str,
) -> ToolExecutionResult:
    duration_ms = _elapsed_ms(started)
    audit_event = {
        **audit_base,
        "authorized": True,
        "success": False,
        "duration_ms": duration_ms,
        "error": error,
        "circuit_state": "open",
    }
    _log("warning", "tool_gateway_circuit_open", audit_event)
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error=error,
        authorized=True,
        duration_ms=duration_ms,
        idempotency_key=idempotency_key,
        audit_event=audit_event,
    )


def _failure_threshold(spec: ToolSpec) -> int:
    return max(1, spec.circuit_failure_threshold or settings.tool_circuit_failure_threshold)


def _reset_seconds(spec: ToolSpec) -> int:
    return max(1, spec.circuit_reset_seconds or settings.tool_circuit_reset_seconds)


def _before_tool_call(spec: ToolSpec) -> str | None:
    now = perf_counter()
    with _CIRCUIT_LOCK:
        state = _CIRCUITS.setdefault(spec.name, _CircuitState())
        if state.opened_at is None:
            return None
        elapsed = now - state.opened_at
        if elapsed >= _reset_seconds(spec):
            # Half-open probe. A success closes the circuit; another failure reopens it.
            state.opened_at = None
            state.failures = max(0, _failure_threshold(spec) - 1)
            return None
        remaining = _reset_seconds(spec) - elapsed
        return f"Tool '{spec.name}' circuit is open; retry after {remaining:.1f}s"


def _record_tool_success(spec: ToolSpec) -> None:
    with _CIRCUIT_LOCK:
        _CIRCUITS[spec.name] = _CircuitState()


def _record_tool_failure(spec: ToolSpec) -> None:
    with _CIRCUIT_LOCK:
        state = _CIRCUITS.setdefault(spec.name, _CircuitState())
        state.failures += 1
        if state.failures >= _failure_threshold(spec):
            state.opened_at = perf_counter()


def circuit_breaker_snapshot() -> dict[str, dict[str, Any]]:
    now = perf_counter()
    with _CIRCUIT_LOCK:
        result: dict[str, dict[str, Any]] = {}
        for name, state in _CIRCUITS.items():
            spec = TOOL_SPECS.get(name)
            reset_seconds = _reset_seconds(spec) if spec else settings.tool_circuit_reset_seconds
            remaining = max(0.0, reset_seconds - (now - state.opened_at)) if state.opened_at else 0.0
            result[name] = {
                "state": "open" if state.opened_at and remaining > 0 else "closed",
                "failures": state.failures,
                "retry_after_seconds": round(remaining, 1),
            }
        return result


def reset_circuit_breakers() -> None:
    """Operational/test hook for clearing transient in-process circuit state."""

    with _CIRCUIT_LOCK:
        _CIRCUITS.clear()


_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _validate_tool_input(spec: ToolSpec, args: Mapping[str, Any]) -> str | None:
    """按 spec.input_schema 强校验 LLM 提取的参数，防止脏参数进业务工具。

    返回 None 表示通过，否则返回错误说明。仅做 required + 基础类型校验（不依赖外部
    jsonschema 库），足以拦截 LLM 漏填/类型错误的常见问题。"""
    schema = spec.input_schema or {}
    if not schema:
        return None
    properties = schema.get("properties", {})
    errors: list[str] = []
    for required_field in schema.get("required", []):
        if args.get(required_field) in (None, ""):
            errors.append(f"缺少必填参数 '{required_field}'")
    for field_name, value in args.items():
        definition = properties.get(field_name)
        if not definition or value is None:
            continue
        expected = _JSON_TYPE_MAP.get(definition.get("type"))
        # bool 是 int 的子类，number/integer 校验时要排除 bool 误判
        if expected and not isinstance(value, expected):
            errors.append(f"参数 '{field_name}' 类型应为 {definition.get('type')}")
        elif definition.get("type") in ("integer", "number") and isinstance(value, bool):
            errors.append(f"参数 '{field_name}' 类型应为 {definition.get('type')}")
    if errors:
        return f"Tool '{spec.name}' 参数校验失败: " + "; ".join(errors)
    return None


def _build_idempotency_key(spec: ToolSpec, args: Mapping[str, Any]) -> str | None:
    if not spec.idempotency_fields:
        return None
    parts = [args.get(field, "") for field in spec.idempotency_fields]
    raw = ":".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    namespace = spec.idempotency_namespace or spec.name
    return f"idempotency:{namespace}:{digest}"


def _audit_base(
    spec: ToolSpec,
    context: ToolExecutionContext,
    idempotency_key: str | None,
    action_policy: PolicyDecision,
) -> dict[str, Any]:
    return {
        "tool": spec.name,
        "action": spec.action,
        "risk_level": spec.risk_level.value,
        "side_effect": spec.side_effect.value,
        "actor_role": context.actor_role,
        "requested_by_role": context.requested_by_role,
        "user_id": context.user_id,
        "thread_id": context.thread_id,
        "trace_id": context.trace_id,
        "scenario": context.scenario,
        "tenant_id": context.tenant_id,
        "approval_id": context.approval_id,
        "dry_run": context.dry_run,
        "idempotency_key": idempotency_key,
        "category": spec.category,
        "owner": spec.owner,
        "timeout_seconds": spec.timeout_seconds,
        "retry_limit": spec.retry_limit,
        "approval_required": spec.approval_required,
        "policy": action_policy.to_audit_event(),
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _log(level: str, event: str, payload: dict[str, Any]) -> None:
    getattr(logger, level)("%s %s", event, payload)
