"""Post-execution refund reconciliation against durable Saga and outbox state."""

from __future__ import annotations

from sqlalchemy import select

from app.agent.dependencies import resolve_session_factory
from app.agent.evidence_graph import collect_refund_evidence
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.db.database import AsyncSessionLocal
from app.db.models import ErpSagaStatus, OutboxEvent, SagaExecution
from app.db.tenant_context import tenant_scope


async def reconcile_refund_node(state: AgentState) -> dict:
    saga_id = str(get_state_val(state, "saga_id", "") or "")
    refund_request_id = str(get_state_val(state, "refund_request_id", "") or "")
    expected_credit = str(get_state_val(state, "credit_memo_id", "") or "")
    expected_clearing = str(get_state_val(state, "clearing_document_id", "") or "")
    tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")

    if str(get_state_val(state, "saga_status", "")) == "DRY_RUN":
        reconciliation = {
            "status": "verified_dry_run",
            "verified": True,
            "checks": ["dry_run_plan_complete"],
        }
    else:
        with tenant_scope(tenant_id):
            async with resolve_session_factory(AsyncSessionLocal)() as session:
                saga = await session.get(SagaExecution, saga_id)
                outbox = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.tenant_id == tenant_id,
                        OutboxEvent.aggregate_type == "refund_request",
                        OutboxEvent.aggregate_id == refund_request_id,
                        OutboxEvent.event_type == "refund.finance_posted",
                    )
                )

        snapshot = dict(saga.result_snapshot or {}) if saga else {}
        checks = {
            "saga_exists": saga is not None,
            "saga_completed": bool(saga and saga.status == ErpSagaStatus.COMPLETED),
            "credit_memo_matches": bool(
                expected_credit and snapshot.get("credit_memo_id") == expected_credit
            ),
            "clearing_document_matches": bool(
                expected_clearing
                and snapshot.get("clearing_document_id") == expected_clearing
            ),
            "transactional_outbox_exists": outbox is not None,
            "outbox_payload_matches": bool(
                outbox
                and (outbox.payload or {}).get("credit_memo_id") == expected_credit
                and (outbox.payload or {}).get("clearing_document_id") == expected_clearing
            ),
        }
        verified = all(checks.values())
        reconciliation = {
            "status": "verified" if verified else "mismatch",
            "verified": verified,
            "checks": checks,
            "saga_id": saga_id,
            "outbox_event_id": outbox.outbox_event_id if outbox else None,
        }

    merged = {**dict(state), "reconciliation_result": reconciliation}
    evidence_graph = collect_refund_evidence(merged)
    if not reconciliation["verified"]:
        return {
            "reconciliation_result": reconciliation,
            "evidence_graph": evidence_graph,
            "error_message": "Refund post-execution reconciliation failed",
            "current_step": "refund_reconciliation_blocked",
            "verification_result": {
                "status": "block",
                "issues": [
                    {
                        "code": "finance.reconciliation_mismatch",
                        "message": "Durable Saga and outbox evidence do not match the refund result",
                    }
                ],
            },
        }
    return {
        "reconciliation_result": reconciliation,
        "evidence_graph": evidence_graph,
        "verification_result": {
            "status": "pass",
            "issues": [],
            "checked_predicates": sorted(
                {item.get("predicate") for item in evidence_graph.get("claims") or []}
            ),
        },
        "current_step": "refund_reconciliation_verified",
    }


def route_after_reconciliation(state: AgentState) -> str:
    result = dict(get_state_val(state, "reconciliation_result", {}) or {})
    return "send_notification" if result.get("verified") else "summarize_session"


def route_after_refund_execution(state: AgentState) -> str:
    return "reconcile_refund" if get_state_val(state, "refund_success", False) else "summarize_session"
