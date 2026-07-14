"""Independent final success-criteria verifier for a completed refund task."""

from __future__ import annotations

from typing import Any

from app.agent.evidence_graph import collect_refund_evidence, fresh_evidence_predicates
from app.agent.evidence_store import persist_evidence_bundle
from app.agent.dependencies import get_agent_dependencies
from app.agent.plan_graph import mark_plan_steps
from app.agent.state import AgentState
from app.agent.utils import get_state_val


async def final_reconciliation_node(state: AgentState) -> dict[str, Any]:
    evidence_graph = collect_refund_evidence(state)
    task_spec = dict(get_state_val(state, "task_spec", {}) or {})
    required = {
        str(predicate)
        for criterion in task_spec.get("success_criteria") or []
        if criterion.get("mandatory", True)
        for predicate in criterion.get("required_evidence") or []
        if predicate != "task.final_reconciliation"
    }
    predicates = fresh_evidence_predicates(evidence_graph)
    missing = sorted(required - predicates)
    business_checks = {
        "refund_success": bool(get_state_val(state, "refund_success", False)),
        "finance_reconciled": bool(
            (get_state_val(state, "reconciliation_result", {}) or {}).get("verified")
        ),
        "inventory_restored": bool(
            (get_state_val(state, "inventory_restoration", {}) or {}).get("success")
        ),
        "notification_delivered": bool(
            get_state_val(state, "notification_sent", False)
        ),
    }
    verified = not missing and all(business_checks.values())
    final = {
        "verified": verified,
        "required_predicates": sorted(required),
        "cited_evidence_ids": [
            str(item.get("evidence_id"))
            for item in evidence_graph.get("claims") or []
            if str(item.get("predicate")) in required
        ],
        "missing_evidence": missing,
        "business_checks": business_checks,
    }
    output = {"final_reconciliation": final}
    evidence_graph = collect_refund_evidence({**dict(state), **output})
    if not verified:
        plan = mark_plan_steps(
            get_state_val(state, "plan_graph", {}) or {},
            {"final_reconcile": "blocked"},
            graph_status="blocked",
        )
        return {
            **output,
            "evidence_graph": evidence_graph,
            "plan_graph": plan,
            "verification_result": {
                "status": "block",
                "issues": [
                    {
                        "code": "SUCCESS_CRITERIA_NOT_MET",
                        "message": "Final refund success criteria are not satisfied",
                        "missing_evidence": missing,
                    }
                ],
                "cited_evidence_ids": final["cited_evidence_ids"],
            },
            "error_message": "Final refund reconciliation failed",
            "current_step": "final_reconciliation_blocked",
            "is_completed": False,
        }
    plan = mark_plan_steps(
        get_state_val(state, "plan_graph", {}) or {},
        {"final_reconcile": "completed"},
        graph_status="completed",
    )
    verification_result = {
        "status": "pass",
        "issues": [],
        "checked_predicates": sorted(fresh_evidence_predicates(evidence_graph)),
        "cited_evidence_ids": final["cited_evidence_ids"],
    }
    try:
        persistence = await persist_evidence_bundle(
            evidence_graph=evidence_graph,
            task_spec=task_spec,
            verification_result=verification_result,
            tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
            thread_id=str(get_state_val(state, "thread_id", "") or ""),
            trace_id=str(get_state_val(state, "trace_id", "") or ""),
            decision_type="refund_final_reconciliation",
            session_factory=get_agent_dependencies().session_factory,
        )
    except Exception as exc:
        return {
            **output,
            "evidence_graph": evidence_graph,
            "plan_graph": mark_plan_steps(
                plan,
                {"final_reconcile": "blocked"},
                graph_status="blocked",
            ),
            "verification_result": {
                "status": "block",
                "issues": [
                    {
                        "code": "EVIDENCE_PERSISTENCE_FAILED",
                        "message": "Final evidence could not be persisted",
                    }
                ],
                "cited_evidence_ids": final["cited_evidence_ids"],
            },
            "evidence_persistence": {"error": str(exc)},
            "error_message": "Final evidence persistence failed",
            "current_step": "final_evidence_persistence_blocked",
            "is_completed": False,
        }
    return {
        **output,
        "evidence_graph": evidence_graph,
        "plan_graph": plan,
        "verification_result": verification_result,
        "evidence_persistence": persistence,
        "current_step": "task_completed",
        "is_completed": True,
    }


def route_after_final_reconciliation(state: AgentState) -> str:
    return "summarize_session"
