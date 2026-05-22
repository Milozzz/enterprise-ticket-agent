"""Shared builders for Generative UI events emitted by agent nodes."""

from __future__ import annotations

from typing import Any

from app.core.policy import PolicyDecision


def policy_summary(decision: PolicyDecision) -> dict[str, Any]:
    event = decision.to_audit_event()
    return {
        "effect": event["effect"],
        "requiresHumanReview": event["requires_human_review"],
        "matchedRules": event["matched_rules"],
        "reason": event["reason"],
        "version": event["policy_version"],
    }


def build_business_request_card(
    *,
    scenario_id: str,
    scenario_name: str,
    request_id: str,
    status: str,
    title: str,
    summary: str,
    fields: list[dict[str, Any]],
    policy_decision: PolicyDecision,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "business_request_card",
        "data": {
            "scenarioId": scenario_id,
            "scenarioName": scenario_name,
            "requestId": request_id,
            "status": status,
            "title": title,
            "summary": summary,
            "fields": fields,
            "policy": policy_summary(policy_decision),
            "timeline": timeline,
        },
    }


def build_generic_approval_panel(
    *,
    scenario_id: str,
    approval_type: str,
    request_id: str,
    thread_id: str | None = None,
    title: str,
    requester_id: str,
    review_roles: list[str],
    approval_chain: list[dict[str, Any]] | None = None,
    current_status: str,
    policy_decision: PolicyDecision,
    approve_label: str,
    reject_label: str,
) -> dict[str, Any]:
    return {
        "type": "generic_approval_panel",
        "data": {
            "scenarioId": scenario_id,
            "approvalType": approval_type,
            "requestId": request_id,
            "threadId": thread_id,
            "title": title,
            "requesterId": requester_id,
            "reviewRoles": review_roles,
            "approvalChain": approval_chain or [],
            "currentStatus": current_status,
            "policyReason": policy_decision.reason,
            "matchedRules": list(policy_decision.matched_rules),
            "actions": [
                {"label": approve_label, "action": "approve", "variant": "approve"},
                {"label": reject_label, "action": "reject", "variant": "reject"},
            ],
        },
    }
