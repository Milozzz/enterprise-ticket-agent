"""Generic approval authorization for supervisor-based scenarios."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from sqlalchemy import select

from app.agent.scenario_registry import get_default_registry
from app.core.policy import allowed_roles_for_action, evaluate_action_policy
from app.db.models import ApprovalDecision
from app.db.tenant_context import tenant_scope


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


def approval_evidence_id(
    *,
    tenant_id: str,
    scenario_id: str,
    thread_id: str,
    stage_id: str,
) -> str:
    raw = f"{tenant_id}:{scenario_id}:{thread_id}:{stage_id}"
    return f"APR-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32].upper()}"


async def persist_approval_evidence(
    *,
    tenant_id: str,
    scenario_id: str,
    approval_type: str,
    thread_id: str,
    stage_id: str,
    action: str,
    reviewer_id: str,
    reviewer_role: str,
    review_roles: list[str],
    comment: str = "",
    policy_event: dict[str, Any] | None = None,
    session_factory,
) -> str:
    """Persist a stable approval artifact consumed by high-risk tool calls."""

    evidence_id = approval_evidence_id(
        tenant_id=tenant_id,
        scenario_id=scenario_id,
        thread_id=thread_id,
        stage_id=stage_id,
    )
    normalized_action = str(action).lower()
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            existing = await session.scalar(
                select(ApprovalDecision).where(
                    ApprovalDecision.tenant_id == tenant_id,
                    ApprovalDecision.request_id == evidence_id,
                )
            )
            if existing is None:
                session.add(
                    ApprovalDecision(
                        tenant_id=tenant_id,
                        request_id=evidence_id,
                        scenario_id=scenario_id,
                        approval_type=approval_type,
                        action=normalized_action,
                        status="approved" if normalized_action in {"approve", "auto_approve"} else "rejected",
                        reviewer_id=reviewer_id,
                        reviewer_role=reviewer_role,
                        review_roles={"roles": review_roles, "stage_id": stage_id},
                        comment=comment,
                        thread_id=thread_id,
                        policy_event=policy_event,
                    )
                )
                await session.commit()
            elif existing.action != normalized_action:
                raise ValueError("Approval evidence already contains a different decision")
    return evidence_id


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
