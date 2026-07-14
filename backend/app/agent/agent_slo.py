"""Task-level Agent quality, cost, latency, safety, and HITL metrics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import func, select

from app.agent.plan_graph import validate_plan_graph
from app.db.models import ApprovalDecision, ApprovalTask, AuditLog, LLMUsageRecord


_MAX_AUDIT_ROWS = 5000


async def compute_agent_slo(session, *, hours: int = 24) -> dict[str, Any]:
    """Aggregate operational metrics from durable runtime records."""

    since = datetime.utcnow() - timedelta(hours=max(1, hours))
    now = datetime.utcnow()
    audit_rows = (
        await session.execute(
            select(
                AuditLog.thread_id,
                AuditLog.node_name,
                AuditLog.event_type,
                AuditLog.output_data,
                AuditLog.duration_ms,
                AuditLog.success,
                AuditLog.created_at,
            )
            .where(AuditLog.created_at >= since)
            .order_by(AuditLog.id.desc())
            .limit(_MAX_AUDIT_ROWS)
        )
    ).all()

    routing_total = 0
    routing_by_scenario: dict[str, int] = {}
    routing_semantic = 0
    routing_clarifications = 0
    node_total = 0
    node_errors = 0
    degraded_events = 0
    first_result_values: list[float] = []
    threads: dict[str, dict[str, Any]] = defaultdict(_empty_thread_metrics)

    for (
        thread_id,
        node_name,
        event_type,
        raw_output,
        duration_ms,
        success,
        created_at,
    ) in reversed(audit_rows):
        output = raw_output if isinstance(raw_output, dict) else {}
        if node_name == "stream_first_result":
            value = output.get("value", duration_ms)
            if isinstance(value, (int, float)):
                first_result_values.append(float(value))
            continue
        if node_name in {"erp_connector", "approval_escalation"}:
            continue

        node_total += 1
        if success is False or output.get("error_message"):
            node_errors += 1
        if output.get("llm_degraded"):
            degraded_events += 1
        if node_name == "supervisor_router":
            decision = output.get("supervisor_decision") or {}
            if isinstance(decision, dict) and decision.get("scenario_id"):
                routing_total += 1
                scenario_id = str(decision["scenario_id"])
                routing_by_scenario[scenario_id] = (
                    routing_by_scenario.get(scenario_id, 0) + 1
                )
                routing_semantic += int(decision.get("routing_method") == "llm_semantic")
                routing_clarifications += int(bool(decision.get("needs_clarification")))

        if not thread_id:
            continue
        task = threads[str(thread_id)]
        task["first_at"] = min(task["first_at"] or created_at, created_at)
        task["last_at"] = max(task["last_at"] or created_at, created_at)
        task["duration_sum_ms"] += int(duration_ms or 0)
        task["has_error"] = task["has_error"] or success is False or bool(
            output.get("error_message")
        )
        if isinstance(output.get("task_spec"), dict):
            task["task_spec"] = output["task_spec"]
        if isinstance(output.get("plan_graph"), dict):
            task["plan_graph"] = output["plan_graph"]
        if isinstance(output.get("evidence_graph"), dict):
            task["evidence_graph"] = output["evidence_graph"]
        verification = output.get("verification_result")
        if isinstance(verification, dict):
            task["verification_status"] = str(verification.get("status") or "")
        task["tool_events"].extend(_tool_events(output))
        task["compensations"].extend(_compensation_events(output))
        plan_status = str((output.get("plan_graph") or {}).get("status") or "")
        if plan_status == "completed" or output.get("is_completed") is True:
            task["terminal"] = True
            task["successful"] = True
        elif plan_status == "blocked" or task["verification_status"] == "block":
            task["terminal"] = True
            task["blocked"] = True
        if output.get("final_reconciliation", {}).get("verified") is True:
            task["terminal"] = True
            task["successful"] = True
        if output.get("refund_success") is True and output.get("notification_sent") is True:
            task["terminal"] = True
            task["successful"] = True

    terminal_tasks = [task for task in threads.values() if task["terminal"]]
    successful_tasks = [task for task in terminal_tasks if task["successful"]]
    blocked_tasks = [task for task in terminal_tasks if task["blocked"]]
    in_progress_tasks = [task for task in threads.values() if not task["terminal"]]

    plan_results: list[bool] = []
    evidence_coverages: list[float] = []
    tool_events: list[dict[str, Any]] = []
    compensation_events: list[dict[str, Any]] = []
    replan_tasks: list[dict[str, Any]] = []
    completion_values: list[float] = []
    for task in threads.values():
        plan = task["plan_graph"]
        if plan:
            plan_results.append(not validate_plan_graph(plan, task["task_spec"] or None))
            if int(plan.get("replan_count") or 0) > 0 and task["terminal"]:
                replan_tasks.append(task)
        coverage = _evidence_coverage(task["task_spec"], task["evidence_graph"])
        if coverage is not None:
            evidence_coverages.append(coverage)
        tool_events.extend(task["tool_events"])
        compensation_events.extend(task["compensations"])
        if task["terminal"]:
            wall_ms = max(
                float(task["duration_sum_ms"]),
                max(0.0, (task["last_at"] - task["first_at"]).total_seconds() * 1000),
            )
            completion_values.append(wall_ms)

    tool_selection_correct = sum(
        1
        for event in tool_events
        if event.get("authorized") is True
        and str(event.get("policy", {}).get("effect") or "allow") != "deny"
    )
    policy_violations = sum(
        1
        for event in tool_events
        if event.get("success") is True
        and str(event.get("policy", {}).get("effect") or "") == "deny"
    )
    compensation_attempts = [
        event for event in compensation_events if event.get("status") not in {"AVAILABLE", ""}
    ]
    compensation_successes = sum(
        1
        for event in compensation_attempts
        if str(event.get("status") or "").upper()
        in {"COMPLETED", "COMPENSATED", "SUCCESS", "SUCCEEDED"}
    )

    approvals = (
        await session.execute(
            select(
                ApprovalTask.status,
                ApprovalTask.created_at,
                ApprovalTask.completed_at,
                ApprovalTask.due_at,
            ).where(ApprovalTask.created_at >= since)
        )
    ).all()
    decision_minutes: list[float] = []
    hitl_completed = 0
    hitl_overdue = 0
    for status_value, created_at, completed_at, due_at in approvals:
        if status_value in {"approved", "rejected"}:
            hitl_completed += 1
            if completed_at and created_at:
                decision_minutes.append(
                    max(0.0, (completed_at - created_at).total_seconds() / 60)
                )
        elif status_value == "pending" and due_at and due_at < now:
            hitl_overdue += 1

    decisions = (
        await session.execute(
            select(ApprovalDecision.action, ApprovalDecision.policy_event).where(
                ApprovalDecision.created_at >= since
            )
        )
    ).all()
    human_overrides = sum(
        1 for action, policy_event in decisions if _is_human_override(action, policy_event)
    )

    llm_summary = (
        await session.execute(
            select(
                func.count(LLMUsageRecord.id),
                func.sum(LLMUsageRecord.total_tokens),
                func.avg(LLMUsageRecord.latency_ms),
                func.sum(LLMUsageRecord.total_cost_usd),
            ).where(LLMUsageRecord.created_at >= since)
        )
    ).one()
    llm_total = int(llm_summary[0] or 0)
    llm_tokens = int(llm_summary[1] or 0)
    llm_avg_latency = float(llm_summary[2] or 0.0)
    llm_cost = Decimal(llm_summary[3] or 0)
    llm_success = int(
        (
            await session.execute(
                select(func.count(LLMUsageRecord.id)).where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.success.is_(True),
                )
            )
        ).scalar()
        or 0
    )
    llm_failover = int(
        (
            await session.execute(
                select(func.count(LLMUsageRecord.id)).where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.fallback_index > 0,
                )
            )
        ).scalar()
        or 0
    )

    return {
        "window_hours": hours,
        "computed_at": now.isoformat(),
        "task_quality": {
            "terminal_tasks": len(terminal_tasks),
            "successful_tasks": len(successful_tasks),
            "blocked_tasks": len(blocked_tasks),
            "in_progress_tasks": len(in_progress_tasks),
            "task_success_rate": _rate(len(successful_tasks), len(terminal_tasks)),
            "plan_validity_rate": _rate(sum(plan_results), len(plan_results)),
            "tool_selection_accuracy": _rate(tool_selection_correct, len(tool_events)),
            "evidence_coverage": _average(evidence_coverages),
            "replan_success_rate": _rate(
                sum(task["successful"] for task in replan_tasks), len(replan_tasks)
            ),
            "human_override_rate": _rate(human_overrides, len(decisions)),
            "policy_violation_rate": _rate(policy_violations, len(tool_events)),
            "compensation_success_rate": _rate(
                compensation_successes, len(compensation_attempts)
            ),
            "cost_per_successful_task_usd": (
                round(float(llm_cost) / len(successful_tasks), 8)
                if successful_tasks
                else None
            ),
        },
        "latency": {
            "time_to_first_result_ms": _distribution(first_result_values),
            "end_to_end_completion_ms": _distribution(completion_values),
        },
        "routing": {
            "total": routing_total,
            "by_scenario": routing_by_scenario,
            "semantic_route_rate": _rate(routing_semantic, routing_total),
            "clarification_rate": _rate(routing_clarifications, routing_total),
        },
        "hitl": {
            "created": len(approvals),
            "completed": hitl_completed,
            "overdue_pending": hitl_overdue,
            "human_overrides": human_overrides,
            "avg_decision_minutes": _average(decision_minutes, digits=1),
        },
        "nodes": {
            "events": node_total,
            "error_rate": _rate(node_errors, node_total),
            "degraded_decisions": degraded_events,
        },
        "llm": {
            "calls": llm_total,
            "success_rate": _rate(llm_success, llm_total),
            "failover_rate": _rate(llm_failover, llm_total),
            "avg_latency_ms": round(llm_avg_latency, 1),
            "total_tokens": llm_tokens,
            "total_cost_usd": round(float(llm_cost), 8),
        },
        "audit_rows_scanned": len(audit_rows),
        "audit_rows_capped": len(audit_rows) >= _MAX_AUDIT_ROWS,
    }


def _empty_thread_metrics() -> dict[str, Any]:
    return {
        "first_at": None,
        "last_at": None,
        "duration_sum_ms": 0,
        "has_error": False,
        "terminal": False,
        "successful": False,
        "blocked": False,
        "verification_status": "",
        "task_spec": {},
        "plan_graph": {},
        "evidence_graph": {},
        "tool_events": [],
        "compensations": [],
    }


def _tool_events(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in ("tool_gateway_events", "tool_events"):
        value = output.get(key)
        if isinstance(value, list):
            events.extend(dict(item) for item in value if isinstance(item, Mapping))
    return events


def _compensation_events(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    compensation = output.get("compensation")
    if isinstance(compensation, Mapping):
        events.append(dict(compensation))
    for event in output.get("tool_gateway_events") or []:
        if isinstance(event, Mapping) and event.get("compensation"):
            value = event["compensation"]
            if isinstance(value, Mapping):
                events.append(dict(value))
    return events


def _evidence_coverage(
    task_spec: Mapping[str, Any], evidence_graph: Mapping[str, Any]
) -> float | None:
    if not task_spec or not evidence_graph:
        return None
    required = {
        str(predicate)
        for criterion in task_spec.get("success_criteria") or []
        if criterion.get("mandatory", True)
        for predicate in criterion.get("required_evidence") or []
    }
    if not required:
        return None
    present = {
        str(claim.get("predicate")) for claim in evidence_graph.get("claims") or []
    }
    return round(len(required & present) / len(required), 4)


def _is_human_override(action: Any, policy_event: Any) -> bool:
    if not isinstance(policy_event, Mapping):
        return False
    effect = str(policy_event.get("effect") or "").lower()
    decision = str(action or "").lower()
    return (effect == "allow" and decision == "reject") or (
        effect == "deny" and decision == "approve"
    )


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"samples": 0, "avg": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "avg": round(sum(ordered) / len(ordered), 1),
        "p50": round(_percentile(ordered, 0.50), 1),
        "p95": round(_percentile(ordered, 0.95), 1),
        "max": round(ordered[-1], 1),
    }


def _percentile(ordered: list[float], percentile: float) -> float:
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _average(values: list[float], *, digits: int = 4) -> float | None:
    return round(sum(values) / len(values), digits) if values else None
