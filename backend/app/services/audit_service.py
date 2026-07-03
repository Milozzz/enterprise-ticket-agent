from __future__ import annotations

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.masking import mask_dict
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog

logger = get_logger(__name__)


async def add_audit_log(
    thread_id: str,
    node_name: str,
    event_type: str,
    input_data: dict | None,
    output_data: dict | None,
    trace_id: str | None = None,
    duration_ms: int | None = None,
    success: bool | None = None,
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            clean_input = {key: value for key, value in (input_data or {}).items() if key != "messages"}
            has_error = isinstance(output_data, dict) and bool(output_data.get("error_message"))
            session.add(
                AuditLog(
                    thread_id=thread_id,
                    trace_id=trace_id,
                    node_name=node_name,
                    event_type=event_type,
                    input_data=mask_dict(clean_input),
                    output_data=mask_dict(output_data) if isinstance(output_data, dict) else output_data,
                    duration_ms=duration_ms,
                    success=success if success is not None else not has_error,
                )
            )
            await session.commit()
    except Exception as exc:
        logger.error("audit_log_error", error=str(exc))


async def list_audit_logs(thread_id: str) -> list[dict]:
    try:
        async with AsyncSessionLocal() as session:
            logs = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.thread_id == thread_id)
                    .order_by(AuditLog.created_at.asc())
                )
            ).scalars().all()
        return [
            {
                "node": log.node_name,
                "event": log.event_type,
                "input": log.input_data,
                "output": log.output_data,
                "trace_id": log.trace_id,
                "duration_ms": log.duration_ms,
                "success": log.success,
                "time": log.created_at.isoformat(),
            }
            for log in logs
        ]
    except Exception as exc:
        logger.error("audit_logs_db_error", error=str(exc), thread_id=thread_id)
        return []


async def replay_audit_logs(thread_id: str) -> dict:
    logs = await list_audit_logs(thread_id)
    if not logs:
        return {"thread_id": thread_id, "trace_id": None, "nodes": [], "summary": {}}

    trace_id = next((log["trace_id"] for log in logs if log["trace_id"]), None)
    summary_keys = {
        "intent", "order_id", "order_amount", "risk_score", "risk_level",
        "requires_human_approval", "human_decision", "refund_id", "refund_success",
        "notification_to", "_refund_state",
        "policy_events", "prompt_events", "specialist_handoffs",
    }
    nodes = []
    for log in logs:
        output = log["output"] or {}
        nodes.append(
            {
                "node": log["node"],
                "refund_state": output.get("_refund_state"),
                "success": log["success"],
                "duration_ms": log["duration_ms"],
                "error": output.get("error_message"),
                "time": log["time"],
                "summary": {key: value for key, value in output.items() if key in summary_keys},
            }
        )
    failed_nodes = [node["node"] for node in nodes if node["success"] is False]
    return {
        "thread_id": thread_id,
        "trace_id": trace_id,
        "nodes": nodes,
        "summary": {
            "total_duration_ms": sum(node["duration_ms"] or 0 for node in nodes),
            "node_count": len(nodes),
            "failed_nodes": failed_nodes,
            "success": not failed_nodes,
        },
    }
