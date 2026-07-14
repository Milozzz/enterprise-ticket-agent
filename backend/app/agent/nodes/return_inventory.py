"""Trusted return and inventory specialist nodes for the refund workflow."""

from __future__ import annotations

from typing import Any

from app.agent.dependencies import resolve_session_factory
from app.agent.evidence_graph import collect_refund_evidence
from app.agent.state import AgentState
from app.agent.tool_gateway import execute_tool_async, gateway_context_from_state
from app.agent.tools.inventory_tools import (
    inspect_return_inventory,
    restore_return_inventory,
    validate_return,
)
from app.agent.utils import get_state_val
from app.db.database import AsyncSessionLocal


async def validate_return_node(state: AgentState) -> dict[str, Any]:
    order_id = str(get_state_val(state, "order_id", "") or "")
    tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")

    async def handler(*, order_id: str):
        return await validate_return(
            order_id=order_id,
            tenant_id=tenant_id,
            session_factory=resolve_session_factory(AsyncSessionLocal),
        )

    result = await execute_tool_async(
        "validate_return",
        {"order_id": order_id},
        context=gateway_context_from_state(
            state,
            actor_role="AGENT",
            scenario="refund",
            specialist_id="inventory_specialist",
        ),
        handler=handler,
    )
    validation = dict(result.data or {}) if result.success else {
        "valid": False,
        "order_id": order_id,
        "reason": result.error or "RETURN_VALIDATION_FAILED",
    }
    output = {
        "return_validation": validation,
        "return_authorization_id": str(validation.get("rma_id") or ""),
        "tool_gateway_events": [result.audit_event],
        "current_step": (
            "return_validated" if validation.get("valid") else "return_validation_blocked"
        ),
        **(
            {}
            if validation.get("valid")
            else {"error_message": str(validation.get("reason") or "Return is not ready")}
        ),
    }
    output["evidence_graph"] = collect_refund_evidence({**dict(state), **output})
    return output


async def inspect_return_inventory_node(state: AgentState) -> dict[str, Any]:
    order_id = str(get_state_val(state, "order_id", "") or "")
    tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")

    async def handler(*, order_id: str):
        return await inspect_return_inventory(
            order_id=order_id,
            tenant_id=tenant_id,
            session_factory=resolve_session_factory(AsyncSessionLocal),
        )

    result = await execute_tool_async(
        "inspect_return_inventory",
        {"order_id": order_id},
        context=gateway_context_from_state(
            state,
            actor_role="AGENT",
            scenario="refund",
            specialist_id="inventory_specialist",
        ),
        handler=handler,
    )
    inspection = dict(result.data or {}) if result.success else {
        "consistent": False,
        "order_id": order_id,
        "reason": result.error or "INVENTORY_INSPECTION_FAILED",
    }
    output = {
        "inventory_inspection": inspection,
        "tool_gateway_events": [result.audit_event],
        "current_step": (
            "inventory_inspected"
            if inspection.get("consistent")
            else "inventory_inspection_blocked"
        ),
        **(
            {}
            if inspection.get("consistent")
            else {"error_message": "Return inventory is incomplete or inconsistent"}
        ),
    }
    output["evidence_graph"] = collect_refund_evidence({**dict(state), **output})
    return output


async def restore_return_inventory_node(state: AgentState) -> dict[str, Any]:
    order_id = str(get_state_val(state, "order_id", "") or "")
    rma_id = str(get_state_val(state, "return_authorization_id", "") or "")
    tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")

    async def handler(*, order_id: str, rma_id: str):
        return await restore_return_inventory(
            order_id=order_id,
            rma_id=rma_id,
            tenant_id=tenant_id,
            session_factory=resolve_session_factory(AsyncSessionLocal),
        )

    result = await execute_tool_async(
        "restore_return_inventory",
        {"order_id": order_id, "rma_id": rma_id},
        context=gateway_context_from_state(
            state,
            actor_role="AGENT",
            scenario="refund",
            specialist_id="executor",
        ),
        handler=handler,
    )
    restoration = dict(result.data or {}) if result.success else {
        "success": False,
        "order_id": order_id,
        "rma_id": rma_id,
        "reason": result.error or "INVENTORY_RESTORATION_FAILED",
    }
    output = {
        "inventory_restoration": restoration,
        "inventory_movement_id": str(restoration.get("movement_id") or ""),
        "tool_gateway_events": [result.audit_event],
        "current_step": (
            "inventory_restored"
            if restoration.get("success")
            else "inventory_restoration_blocked"
        ),
        **(
            {}
            if restoration.get("success")
            else {"error_message": str(restoration.get("reason"))}
        ),
    }
    output["evidence_graph"] = collect_refund_evidence({**dict(state), **output})
    return output


def route_after_return_validation(state: AgentState) -> str:
    validation = dict(get_state_val(state, "return_validation", {}) or {})
    return "inspect_inventory" if validation.get("valid") else "summarize_session"


def route_after_inventory_inspection(state: AgentState) -> str:
    inspection = dict(get_state_val(state, "inventory_inspection", {}) or {})
    return "risk_agent" if inspection.get("consistent") else "summarize_session"


def route_after_inventory_restoration(state: AgentState) -> str:
    restoration = dict(get_state_val(state, "inventory_restoration", {}) or {})
    return "reconcile_refund" if restoration.get("success") else "summarize_session"
