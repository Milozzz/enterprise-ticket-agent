"""Traceable evidence graph used by verifiers and human reviewers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

from app.agent.utils import get_state_val


def initialize_evidence_graph(state: Mapping[str, Any], task_spec: Mapping[str, Any]) -> dict[str, Any]:
    graph = {
        "task_id": task_spec.get("task_id"),
        "claims": [],
        "relations": [],
        "schema_version": "1",
    }
    goal = str(task_spec.get("goal") or "")
    if goal:
        graph = add_evidence(
            graph,
            predicate="task.user_goal",
            subject=str(task_spec.get("requester_id") or "anonymous"),
            value=goal,
            source="user_input",
            confidence=1.0,
            trace_id=str(get_state_val(state, "trace_id", "") or ""),
        )
    return graph


def add_evidence(
    graph: Mapping[str, Any] | None,
    *,
    predicate: str,
    subject: str,
    value: Any,
    source: str,
    confidence: float,
    trace_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    source_system: str | None = None,
    source_object: str | None = None,
    entity_id: str | None = None,
    freshness_seconds: int = 300,
    data_version: str | None = None,
) -> dict[str, Any]:
    updated = dict(graph or {"claims": [], "relations": [], "schema_version": "1"})
    claims = [dict(item) for item in updated.get("claims") or []]
    raw_identity = json.dumps(
        {"predicate": predicate, "subject": subject, "value": value, "source": source},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    evidence_id = "ev_" + hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()[:16]
    observed_at = datetime.now(timezone.utc)
    claim = {
        "evidence_id": evidence_id,
        "claim": f"{subject} {predicate}",
        "predicate": predicate,
        "subject": subject,
        "value": value,
        "observed_value": value,
        "source": source,
        "source_system": source_system or source,
        "source_object": source_object or predicate.split(".", 1)[0],
        "entity_id": entity_id or subject,
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 4),
        "trace_id": trace_id,
        "observed_at": observed_at.isoformat(),
        "valid_from": observed_at.isoformat(),
        "expires_at": (observed_at + timedelta(seconds=max(1, freshness_seconds))).isoformat(),
        "data_version": data_version,
        "content_hash": hashlib.sha256(
            json.dumps(value, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        "metadata": dict(metadata or {}),
    }
    by_id = {item.get("evidence_id"): item for item in claims}
    by_id[evidence_id] = claim
    updated["claims"] = list(by_id.values())
    updated.setdefault("relations", [])
    updated.setdefault("schema_version", "1")
    return updated


def add_evidence_relation(
    graph: Mapping[str, Any] | None,
    *,
    source_predicate: str,
    target_predicate: str,
    relation_type: str,
) -> dict[str, Any]:
    updated = dict(graph or {})
    claims = list(updated.get("claims") or [])
    source = next(
        (item for item in reversed(claims) if item.get("predicate") == source_predicate),
        None,
    )
    target = next(
        (item for item in reversed(claims) if item.get("predicate") == target_predicate),
        None,
    )
    if source is None or target is None:
        return updated
    relation = {
        "source_evidence_id": source["evidence_id"],
        "target_evidence_id": target["evidence_id"],
        "relation_type": relation_type,
    }
    relations = list(updated.get("relations") or [])
    key = (
        relation["source_evidence_id"],
        relation["target_evidence_id"],
        relation["relation_type"],
    )
    existing = {
        (
            item.get("source_evidence_id"),
            item.get("target_evidence_id"),
            item.get("relation_type"),
        )
        for item in relations
    }
    if key not in existing:
        relations.append(relation)
    updated["relations"] = relations
    return updated


def collect_refund_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    graph = dict(get_state_val(state, "evidence_graph", {}) or {})
    trace_id = str(get_state_val(state, "trace_id", "") or "")
    order_id = str(get_state_val(state, "order_id", "") or "")
    order = dict(get_state_val(state, "order_detail", {}) or {})
    source = str(order.get("sourceSystem") or "canonical_store")
    if order_id:
        graph = add_evidence(graph, predicate="order.identity", subject=order_id, value=order_id, source=source, confidence=1.0, trace_id=trace_id)
    amount = get_state_val(state, "order_amount")
    if amount not in (None, ""):
        graph = add_evidence(graph, predicate="order.amount", subject=order_id, value=amount, source=source, confidence=0.99, trace_id=trace_id)
    currency = str(get_state_val(state, "currency", "") or "")
    if currency:
        graph = add_evidence(graph, predicate="order.currency", subject=order_id, value=currency, source=source, confidence=0.99, trace_id=trace_id)
    return_validation = dict(get_state_val(state, "return_validation", {}) or {})
    if return_validation.get("rma_id"):
        return_source = "mini_erp.return_authorization"
        graph = add_evidence(
            graph,
            predicate="return.authorization",
            subject=str(return_validation["rma_id"]),
            value={
                "order_id": order_id,
                "status": return_validation.get("status"),
                "warehouse_id": return_validation.get("warehouse_id"),
            },
            source=return_source,
            source_system="MINI_ERP",
            source_object="ReturnAuthorization",
            entity_id=str(return_validation["rma_id"]),
            confidence=1.0,
            trace_id=trace_id,
        )
        if return_validation.get("received"):
            graph = add_evidence(
                graph,
                predicate="return.received",
                subject=str(return_validation["rma_id"]),
                value={"received": True, "received_at": return_validation.get("received_at")},
                source=return_source,
                source_system="MINI_ERP",
                source_object="ReturnAuthorization",
                entity_id=str(return_validation["rma_id"]),
                confidence=1.0,
                trace_id=trace_id,
            )
        if return_validation.get("inspection_id"):
            graph = add_evidence(
                graph,
                predicate="return.inspection",
                subject=str(return_validation["inspection_id"]),
                value={
                    "result": return_validation.get("inspection_result"),
                    "restockable": return_validation.get("restockable"),
                },
                source="mini_erp.return_inspection",
                source_system="MINI_ERP",
                source_object="ReturnInspection",
                entity_id=str(return_validation["inspection_id"]),
                confidence=1.0,
                trace_id=trace_id,
            )
    inventory_inspection = dict(get_state_val(state, "inventory_inspection", {}) or {})
    if inventory_inspection.get("consistent"):
        graph = add_evidence(
            graph,
            predicate="inventory.state",
            subject=str(inventory_inspection.get("warehouse_id") or order_id),
            value=inventory_inspection,
            source="mini_erp.inventory",
            source_system="MINI_ERP",
            source_object="InventoryItem",
            entity_id=str(inventory_inspection.get("warehouse_id") or order_id),
            confidence=1.0,
            trace_id=trace_id,
        )
    risk_score = get_state_val(state, "risk_score")
    if risk_score is not None:
        graph = add_evidence(graph, predicate="risk.score", subject=order_id, value=risk_score, source="risk_specialist", confidence=0.9, trace_id=trace_id, metadata={"risk_level": get_state_val(state, "risk_level", "")})
    policy_events = list(get_state_val(state, "policy_events", []) or [])
    if policy_events:
        graph = add_evidence(graph, predicate="policy.decision", subject=order_id, value=policy_events[-1], source="policy_as_code", confidence=1.0, trace_id=trace_id)
    human_decision = str(get_state_val(state, "human_decision", "") or "")
    if human_decision:
        graph = add_evidence(graph, predicate="approval.decision", subject=order_id, value=human_decision, source="human_review", confidence=1.0, trace_id=trace_id, metadata={"reviewer_id": get_state_val(state, "reviewer_id", "")})
    saga_status = str(get_state_val(state, "saga_status", "") or "")
    if saga_status:
        graph = add_evidence(graph, predicate="finance.saga_status", subject=order_id, value=saga_status, source="refund_finance_saga", confidence=1.0, trace_id=trace_id)
    reconciliation = dict(get_state_val(state, "reconciliation_result", {}) or {})
    if reconciliation:
        graph = add_evidence(
            graph,
            predicate="finance.reconciliation",
            subject=order_id,
            value=reconciliation,
            source="post_execution_verifier",
            confidence=1.0,
            trace_id=trace_id,
        )
    inventory_movement = dict(get_state_val(state, "inventory_restoration", {}) or {})
    if inventory_movement.get("success") and inventory_movement.get("movement_id"):
        graph = add_evidence(
            graph,
            predicate="inventory.movement",
            subject=str(inventory_movement["movement_id"]),
            value=inventory_movement,
            source="mini_erp.inventory_movement",
            source_system="MINI_ERP",
            source_object="InventoryMovement",
            entity_id=str(inventory_movement["movement_id"]),
            confidence=1.0,
            trace_id=trace_id,
        )
    if get_state_val(state, "notification_sent", False):
        graph = add_evidence(
            graph,
            predicate="notification.delivery",
            subject=str(get_state_val(state, "notification_email_id", "") or order_id),
            value={
                "sent": True,
                "message_id": get_state_val(state, "notification_email_id", ""),
                "recipient": get_state_val(state, "notification_to", ""),
            },
            source="notification_gateway",
            source_system="NotificationGateway",
            source_object="DeliveryReceipt",
            confidence=1.0,
            trace_id=trace_id,
        )
    final_reconciliation = dict(get_state_val(state, "final_reconciliation", {}) or {})
    if final_reconciliation.get("verified"):
        graph = add_evidence(
            graph,
            predicate="task.final_reconciliation",
            subject=str((get_state_val(state, "task_spec", {}) or {}).get("task_id") or order_id),
            value=final_reconciliation,
            source="independent_final_verifier",
            source_system="AgentRuntime",
            source_object="FinalVerification",
            confidence=1.0,
            trace_id=trace_id,
        )
    graph = add_evidence_relation(
        graph,
        source_predicate="order.identity",
        target_predicate="risk.score",
        relation_type="evaluated_by",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="risk.score",
        target_predicate="policy.decision",
        relation_type="governed_by",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="policy.decision",
        target_predicate="approval.decision",
        relation_type="authorized_by",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="finance.saga_status",
        target_predicate="finance.reconciliation",
        relation_type="verified_by",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="return.inspection",
        target_predicate="inventory.state",
        relation_type="constrains",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="inventory.state",
        target_predicate="inventory.movement",
        relation_type="resulted_in",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="finance.reconciliation",
        target_predicate="task.final_reconciliation",
        relation_type="supports",
    )
    graph = add_evidence_relation(
        graph,
        source_predicate="notification.delivery",
        target_predicate="task.final_reconciliation",
        relation_type="supports",
    )
    return graph


def add_runtime_evidence(
    graph: Mapping[str, Any] | None,
    *,
    scenario_id: str,
    slots: Mapping[str, Any],
    policy_events: list[Mapping[str, Any]],
    tool_events: list[Mapping[str, Any]],
    request: Mapping[str, Any] | None,
    trace_id: str,
) -> dict[str, Any]:
    updated = dict(graph or {})
    subject = str((request or {}).get("requestId") or scenario_id)
    predicate_map = {
        "system": "request.system",
        "permission_level": "request.permission_level",
        "amount": "request.amount",
        "category": "request.category",
    }
    for key, predicate in predicate_map.items():
        value = slots.get(key)
        if value not in (None, "", 0):
            updated = add_evidence(updated, predicate=predicate, subject=subject, value=value, source="slot_extraction", confidence=0.95, trace_id=trace_id)
    if policy_events:
        updated = add_evidence(updated, predicate="policy.decision", subject=subject, value=dict(policy_events[-1]), source="policy_as_code", confidence=1.0, trace_id=trace_id)
    if request and request.get("requestId"):
        updated = add_evidence(updated, predicate="request.id", subject=subject, value=request.get("requestId"), source="tool_gateway", confidence=1.0, trace_id=trace_id, metadata={"tool_events": len(tool_events)})
    updated = add_evidence_relation(
        updated,
        source_predicate="request.system" if scenario_id == "permission_request" else "request.amount",
        target_predicate="policy.decision",
        relation_type="evaluated_by",
    )
    updated = add_evidence_relation(
        updated,
        source_predicate="policy.decision",
        target_predicate="request.id",
        relation_type="authorized_creation_of",
    )
    return updated


def add_approval_evidence(
    graph: Mapping[str, Any] | None,
    *,
    subject: str,
    decision: str,
    reviewer_id: str,
    trace_id: str,
) -> dict[str, Any]:
    updated = add_evidence(
        graph,
        predicate="approval.decision",
        subject=subject,
        value=decision,
        source="human_review" if reviewer_id else "policy_as_code",
        confidence=1.0,
        trace_id=trace_id,
        metadata={"reviewer_id": reviewer_id or "automatic_policy"},
    )
    return add_evidence_relation(
        updated,
        source_predicate="policy.decision",
        target_predicate="approval.decision",
        relation_type="authorized_by",
    )


def evidence_predicates(graph: Mapping[str, Any] | None) -> set[str]:
    return {str(item.get("predicate")) for item in (graph or {}).get("claims") or []}


def fresh_evidence_predicates(
    graph: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> set[str]:
    current = now or datetime.now(timezone.utc)
    fresh: set[str] = set()
    for item in (graph or {}).get("claims") or []:
        expires_at = item.get("expires_at")
        if not expires_at:
            fresh.add(str(item.get("predicate")))
            continue
        try:
            expires = datetime.fromisoformat(str(expires_at))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires >= current:
                fresh.add(str(item.get("predicate")))
        except (TypeError, ValueError):
            continue
    return fresh


def evidence_ids_for_predicates(
    graph: Mapping[str, Any] | None,
    predicates: set[str] | list[str],
) -> list[str]:
    wanted = {str(item) for item in predicates}
    return [
        str(item.get("evidence_id"))
        for item in (graph or {}).get("claims") or []
        if str(item.get("predicate")) in wanted and item.get("evidence_id")
    ]


def detect_evidence_conflicts(
    graph: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Detect fresh claims that disagree for the same predicate and entity."""
    current = now or datetime.now(timezone.utc)
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for claim in (graph or {}).get("claims") or []:
        expires_at = _parse_evidence_time(claim.get("expires_at"))
        if expires_at is not None and expires_at < current:
            continue
        key = (
            str(claim.get("predicate") or ""),
            str(claim.get("entity_id") or claim.get("subject") or ""),
        )
        grouped.setdefault(key, []).append(claim)
    conflicts: list[dict[str, Any]] = []
    for (predicate, entity_id), claims in grouped.items():
        hashes = {str(item.get("content_hash") or "") for item in claims}
        if len(hashes) <= 1:
            continue
        conflicts.append(
            {
                "predicate": predicate,
                "entity_id": entity_id,
                "evidence_ids": [str(item.get("evidence_id")) for item in claims],
                "observed_values": [item.get("observed_value", item.get("value")) for item in claims],
            }
        )
    return conflicts


def _parse_evidence_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
