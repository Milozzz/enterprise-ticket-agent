"""PlanGraph-driven dispatcher for bounded, resumable dynamic execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import asyncio
from typing import Any

from langgraph.errors import GraphInterrupt

from app.agent.evidence_graph import add_approval_evidence, collect_refund_evidence
from app.agent.execution_governance import (
    PlanExecutionBlocked,
    authorize_plan_step,
    finish_plan_step,
)
from app.agent.nodes.final_reconciliation import final_reconciliation_node
from app.agent.nodes.human_review import human_review_node
from app.agent.nodes.notification import send_notification_node
from app.agent.nodes.order_lookup import lookup_order_node
from app.agent.nodes.reconciliation import reconcile_refund_node
from app.agent.nodes.refund import execute_refund_node
from app.agent.nodes.return_inventory import (
    inspect_return_inventory_node,
    restore_return_inventory_node,
    validate_return_node,
)
from app.agent.state import AgentState
from app.agent.subgraphs import build_risk_agent
from app.agent.utils import get_state_val
from app.agent.verifier import execution_verifier_node

PlanHandler = Callable[[AgentState], Awaitable[Mapping[str, Any]]]

EXECUTABLE_PLAN_CAPABILITIES = {
    "task_understanding",
    "order_lookup",
    "return_validation",
    "inventory_consistency",
    "risk_assessment",
    "evidence_verification",
    "approval_validation",
    "refund_finance_saga",
    "restore_return_inventory",
    "reconcile_cross_system_result",
    "notify_stakeholders",
    "verify_success_criteria",
}

EXECUTABLE_PLAN_TOOLS = {
    "lookup_order",
    "validate_return",
    "inspect_return_inventory",
    "check_risk_level",
    "execute_refund",
    "restore_return_inventory",
    "erp_query_doctype",
    "send_notification",
}


async def _risk_handler(state: AgentState) -> Mapping[str, Any]:
    return await build_risk_agent().ainvoke(state)


async def _task_understanding_handler(state: AgentState) -> Mapping[str, Any]:
    # Imported lazily because task_understanding -> dynamic_planner validates
    # candidates against this runtime's executable capability catalog.
    from app.agent.nodes.task_understanding import task_understanding_node

    return await task_understanding_node(state)


async def _approval_handler(state: AgentState) -> Mapping[str, Any]:
    if get_state_val(state, "requires_human_approval", False):
        result = await human_review_node(state)
        return {
            **dict(result),
            "evidence_graph": collect_refund_evidence({**dict(state), **dict(result)}),
        }

    approval_id = str(get_state_val(state, "approval_id", "") or "")
    if not approval_id:
        raise PlanExecutionBlocked("Automatic policy approval evidence is missing")
    evidence_graph = add_approval_evidence(
        get_state_val(state, "evidence_graph", {}) or {},
        subject=str(get_state_val(state, "order_id", "") or approval_id),
        decision="approve",
        reviewer_id="",
        trace_id=str(get_state_val(state, "trace_id", "") or ""),
    )
    return {
        "human_decision": "approve",
        "reviewer_id": "policy-engine",
        "evidence_graph": evidence_graph,
        "current_step": "automatic_approval_verified",
    }


def _handler_for_step(step: Mapping[str, Any]) -> PlanHandler | None:
    capability_handlers: dict[str, PlanHandler] = {
        "task_understanding": _task_understanding_handler,
        "order_lookup": lookup_order_node,
        "return_validation": validate_return_node,
        "inventory_consistency": inspect_return_inventory_node,
        "risk_assessment": _risk_handler,
        "evidence_verification": execution_verifier_node,
        "approval_validation": _approval_handler,
        "refund_finance_saga": execute_refund_node,
        "restore_return_inventory": restore_return_inventory_node,
        "reconcile_cross_system_result": reconcile_refund_node,
        "notify_stakeholders": send_notification_node,
        "verify_success_criteria": final_reconciliation_node,
    }
    tool_handlers: dict[str, PlanHandler] = {
        "lookup_order": lookup_order_node,
        "validate_return": validate_return_node,
        "inspect_return_inventory": inspect_return_inventory_node,
        "check_risk_level": _risk_handler,
        "execute_refund": execute_refund_node,
        "restore_return_inventory": restore_return_inventory_node,
        "erp_query_doctype": reconcile_refund_node,
        "send_notification": send_notification_node,
    }
    return capability_handlers.get(str(step.get("capability") or "")) or tool_handlers.get(
        str(step.get("tool") or "")
    )


def next_ready_plan_step(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the next dependency-ready step in stable plan order."""
    steps = [dict(item) for item in plan.get("steps") or []]
    status_by_id = {
        str(item.get("step_id") or ""): str(item.get("status") or "planned")
        for item in steps
    }
    for step in steps:
        if str(step.get("status") or "planned") not in {"planned", "waiting_approval"}:
            continue
        if all(
            status_by_id.get(str(dependency)) == "completed"
            for dependency in step.get("dependencies") or []
        ):
            return step
    return None


def plan_is_executable(plan: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for step in plan.get("steps") or []:
        if str(step.get("status") or "planned") == "completed":
            continue
        capability = str(step.get("capability") or "")
        tool = str(step.get("tool") or "")
        if capability == "task_understanding":
            continue
        if capability not in EXECUTABLE_PLAN_CAPABILITIES and tool not in EXECUTABLE_PLAN_TOOLS:
            errors.append(
                f"{step.get('step_id')}: no trusted runtime handler for "
                f"capability={capability!r}, tool={tool!r}"
            )
    return errors


async def dynamic_plan_dispatch_node(state: AgentState) -> dict[str, Any]:
    """Execute exactly one ready PlanStep so every transition is checkpointed."""
    plan = dict(get_state_val(state, "plan_graph", {}) or {})
    executable_errors = plan_is_executable(plan)
    if executable_errors:
        plan["status"] = "blocked"
        return {
            "plan_graph": plan,
            "error_message": "; ".join(executable_errors),
            "current_step": "plan_handler_missing",
            "verification_result": {
                "status": "block",
                "issues": [
                    {"code": "PLAN_HANDLER_MISSING", "message": message}
                    for message in executable_errors
                ],
            },
        }

    step = next_ready_plan_step(plan)
    if step is None:
        if all(str(item.get("status")) == "completed" for item in plan.get("steps") or []):
            return {"plan_graph": {**plan, "status": "completed"}, "is_completed": True}
        plan["status"] = "blocked"
        return {
            "plan_graph": plan,
            "error_message": "PlanGraph has no dependency-ready step",
            "current_step": "plan_deadlock_blocked",
            "verification_result": {
                "status": "block",
                "issues": [
                    {
                        "code": "PLAN_DEADLOCK",
                        "message": "No dependency-ready step exists in the current plan revision",
                    }
                ],
            },
        }

    step_id = str(step.get("step_id") or "")
    handler = _handler_for_step(step)
    if handler is None:
        raise PlanExecutionBlocked(f"No trusted handler for PlanStep '{step_id}'")

    try:
        authorized_plan, budget, started = authorize_plan_step(state, step_id)
        working = {
            **dict(state),
            "plan_graph": authorized_plan,
            "execution_budget": budget,
        }
        attempts = 0
        last_error: Exception | None = None
        result: dict[str, Any] = {}
        while attempts <= max(0, int(step.get("retry_limit") or 0)):
            attempts += 1
            try:
                result = dict(
                    await asyncio.wait_for(
                        handler(working),
                        timeout=max(0.1, float(step.get("timeout_seconds") or 10.0)),
                    )
                    or {}
                )
                last_error = None
                break
            except GraphInterrupt:
                raise
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            result = {
                "error_message": f"PlanStep '{step_id}' failed after {attempts} attempts: {last_error}",
                "current_step": "plan_step_failed",
                "plan_step_attempts": attempts,
            }
        else:
            result["plan_step_attempts"] = attempts
        completion = finish_plan_step(
            working,
            result,
            step_id=step_id,
            started=started,
        )
        return {**result, **completion}
    except PlanExecutionBlocked as exc:
        plan["status"] = "blocked"
        return {
            "plan_graph": plan,
            "error_message": str(exc),
            "current_step": "plan_execution_blocked",
            "verification_result": {
                "status": "block",
                "issues": [{"code": "PLAN_EXECUTION_BLOCKED", "message": str(exc)}],
            },
        }


def route_after_dynamic_plan_step(state: AgentState) -> str:
    plan = dict(get_state_val(state, "plan_graph", {}) or {})
    if str(plan.get("status") or "") in {"blocked", "completed"}:
        return "summarize_session"
    if get_state_val(state, "error_message", ""):
        return "summarize_session"
    return "dynamic_dispatch"
