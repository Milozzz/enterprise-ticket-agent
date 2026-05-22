"""Unified gateway for agent tool execution.

The gateway keeps business tools behind one auditable boundary. It does not
replace domain-level idempotency checks in nodes/repositories; it standardizes
metadata, permission checks, dry-run behavior, and execution envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import logging
from time import perf_counter
from typing import Any, Mapping

from app.agent.utils import get_state_val
from app.core.policy import PolicyDecision, evaluate_action_policy

logger = logging.getLogger(__name__)


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
    idempotency_fields: tuple[str, ...] = ()
    idempotency_namespace: str | None = None
    dry_run_supported: bool = True


@dataclass(frozen=True)
class ToolExecutionContext:
    actor_role: str = "AGENT"
    requested_by_role: str = "USER"
    user_id: str = "unknown"
    thread_id: str = ""
    trace_id: str = ""
    scenario: str = "refund"
    dry_run: bool = False


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


TOOL_SPECS: dict[str, ToolSpec] = {
    "lookup_order": ToolSpec(
        name="lookup_order",
        action="lookup_order",
        description="Read order details before processing a refund workflow.",
        risk_level=ToolRiskLevel.LOW,
        side_effect=ToolSideEffect.READ,
    ),
    "check_risk_level": ToolSpec(
        name="check_risk_level",
        action="check_risk_level",
        description="Evaluate refund risk and decide whether HITL is required.",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=ToolSideEffect.DECISION,
    ),
    "execute_refund": ToolSpec(
        name="execute_refund",
        action="execute_refund",
        description="Create a refund side effect after risk/HITL gates pass.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        idempotency_fields=("order_id", "ticket_id", "amount"),
        idempotency_namespace="refund",
    ),
    "send_notification": ToolSpec(
        name="send_notification",
        action="send_notification",
        description="Notify finance after a refund is completed.",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=ToolSideEffect.EXTERNAL,
        idempotency_fields=("order_id", "refund_id"),
        idempotency_namespace="notification",
    ),
    "create_permission_request": ToolSpec(
        name="create_permission_request",
        action="create_permission_request",
        description="Create an enterprise access request with approval metadata.",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=ToolSideEffect.WRITE,
        idempotency_fields=("requester_id", "system", "permission_level"),
        idempotency_namespace="permission_request",
    ),
    "create_reimbursement_request": ToolSpec(
        name="create_reimbursement_request",
        action="create_reimbursement_request",
        description="Create an employee reimbursement request for finance approval.",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=ToolSideEffect.WRITE,
        idempotency_fields=("requester_id", "amount", "category", "description"),
        idempotency_namespace="reimbursement",
    ),
}


def list_tool_specs() -> list[dict[str, str]]:
    """Return compact registry metadata for docs, debug APIs, or tests."""
    return [
        {
            "name": spec.name,
            "action": spec.action,
            "risk_level": spec.risk_level.value,
            "side_effect": spec.side_effect.value,
            "description": spec.description,
        }
        for spec in TOOL_SPECS.values()
    ]


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

    try:
        data = _invoke_handler(handler, dict(args))
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": True,
            "success": True,
            "duration_ms": duration_ms,
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
        duration_ms = _elapsed_ms(started)
        audit_event = {
            **audit_base,
            "authorized": True,
            "success": False,
            "duration_ms": duration_ms,
            "error": str(exc),
        }
        _log("error", "tool_gateway_failed", audit_event)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error=str(exc),
            duration_ms=duration_ms,
            idempotency_key=idempotency_key,
            audit_event=audit_event,
        )


def _invoke_handler(handler: Any, args: dict[str, Any]) -> Any:
    if hasattr(handler, "invoke"):
        return handler.invoke(args)
    if callable(handler):
        return handler(**args)
    raise TypeError(f"Handler for tool is not invokable: {type(handler)!r}")


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
        "dry_run": context.dry_run,
        "idempotency_key": idempotency_key,
        "policy": action_policy.to_audit_event(),
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _log(level: str, event: str, payload: dict[str, Any]) -> None:
    getattr(logger, level)("%s %s", event, payload)
