"""Workflow resolution layer for the supervisor-based platform."""

from __future__ import annotations

from app.agent.scenario_registry import DEFAULT_SCENARIO_ID, get_default_registry


WORKFLOW_ENTRYPOINTS: dict[str, str] = {
    "refund_workflow": "classify_intent",
    "permission_request_workflow": "permission_request",
    "reimbursement_workflow": "reimbursement",
    "configured_workflow": "configured_runtime",
}


def resolve_workflow_name(scenario_id: str) -> str:
    return get_default_registry().get(scenario_id or DEFAULT_SCENARIO_ID).workflow


def resolve_workflow_entry(scenario_id: str) -> str:
    workflow = resolve_workflow_name(scenario_id)
    return WORKFLOW_ENTRYPOINTS.get(workflow, "configured_runtime")


def route_to_workflow_entry(state: dict) -> str:
    return resolve_workflow_entry(str(state.get("scenario_id") or DEFAULT_SCENARIO_ID))
