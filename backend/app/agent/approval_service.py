"""Generic approval authorization for supervisor-based scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.scenario_registry import get_default_registry
from app.core.policy import allowed_roles_for_action, evaluate_action_policy


@dataclass(frozen=True)
class ApprovalAuthorizationResult:
    allowed: bool
    status_code: int
    reason: str
    review_roles: tuple[str, ...] = ()
    stage_id: str | None = None
    stage_name: str | None = None
    policy_action: str = ""
    policy_event: dict[str, Any] | None = None


def approval_action_name(action: str, approval_type: str) -> str:
    return f"{str(action).lower()}_{str(approval_type).lower()}"


def authorize_generic_approval(
    *,
    scenario_id: str,
    approval_type: str,
    action: str,
    reviewer_role: str,
    stage_id: str | None = None,
) -> ApprovalAuthorizationResult:
    registry = get_default_registry()
    scenarios = {scenario.id: scenario for scenario in registry.list()}
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        return ApprovalAuthorizationResult(
            allowed=False,
            status_code=404,
            reason=f"Unknown scenario '{scenario_id}'.",
        )

    if not scenario.hitl.enabled:
        return ApprovalAuthorizationResult(
            allowed=False,
            status_code=400,
            reason=f"Scenario '{scenario_id}' does not enable human review.",
        )

    normalized_role = str(reviewer_role or "").upper()
    review_roles = tuple(str(role).upper() for role in scenario.hitl.review_roles)
    approval_chain = scenario.hitl.approval_chain
    selected_stage = None
    if approval_chain and stage_id is not None:
        selected_stage = next((stage for stage in approval_chain if stage.id == stage_id), None)
        if selected_stage is None:
            return ApprovalAuthorizationResult(
                allowed=False,
                status_code=404,
                reason=f"Unknown approval stage '{stage_id}' for scenario '{scenario_id}'.",
                review_roles=review_roles,
            )
        stage_roles = tuple(str(role).upper() for role in selected_stage.roles)
        allowed_review_roles = stage_roles
    else:
        allowed_review_roles = review_roles

    if normalized_role not in allowed_review_roles:
        return ApprovalAuthorizationResult(
            allowed=False,
            status_code=403,
            reason=f"Role '{normalized_role}' is not in scenario review roles: {', '.join(allowed_review_roles)}.",
            review_roles=allowed_review_roles,
            stage_id=selected_stage.id if selected_stage else None,
            stage_name=selected_stage.name if selected_stage else None,
        )

    policy_action = approval_action_name(action, approval_type)
    policy_decision = evaluate_action_policy(normalized_role, policy_action)
    policy_roles = allowed_roles_for_action(policy_action)
    if policy_roles is not None and not policy_decision.allowed:
        return ApprovalAuthorizationResult(
            allowed=False,
            status_code=403,
            reason=policy_decision.reason,
            review_roles=allowed_review_roles,
            stage_id=selected_stage.id if selected_stage else None,
            stage_name=selected_stage.name if selected_stage else None,
            policy_action=policy_action,
            policy_event=policy_decision.to_audit_event(),
        )

    return ApprovalAuthorizationResult(
        allowed=True,
        status_code=200,
        reason="Reviewer is authorized for this scenario approval.",
        review_roles=allowed_review_roles,
        stage_id=selected_stage.id if selected_stage else None,
        stage_name=selected_stage.name if selected_stage else None,
        policy_action=policy_action,
        policy_event=policy_decision.to_audit_event(),
    )
