"""Business and reliability metrics for ERP-backed agent execution."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import (
    ApprovalDecision,
    AuditLog,
    CompensationTransaction,
    CreditMemoDocument,
    ErpCompensationStatus,
    ErpOutboxStatus,
    OutboxEvent,
)


async def get_erp_business_metrics(days: int = 7) -> dict[str, Any]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max(1, min(days, 90)))
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    AuditLog.event_type,
                    AuditLog.duration_ms,
                    AuditLog.success,
                    AuditLog.output_data,
                ).where(
                    AuditLog.node_name == "erp_connector",
                    AuditLog.created_at >= since,
                )
            )
        ).all()
        pending_outbox = int(
            await session.scalar(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == ErpOutboxStatus.PENDING)
            )
            or 0
        )
        compensation_total = int(
            await session.scalar(select(func.count()).select_from(CompensationTransaction)) or 0
        )
        compensation_failed = int(
            await session.scalar(
                select(func.count())
                .select_from(CompensationTransaction)
                .where(CompensationTransaction.status == ErpCompensationStatus.FAILED)
            )
            or 0
        )
        approval_count = int(await session.scalar(select(func.count()).select_from(ApprovalDecision)) or 0)
        credit_memo_count = int(await session.scalar(select(func.count()).select_from(CreditMemoDocument)) or 0)

    total = len(rows)
    successes = sum(1 for row in rows if row.success is True)
    failures = sum(1 for row in rows if row.success is False)
    shadow_count = sum(1 for row in rows if isinstance(row.output_data, dict) and row.output_data.get("shadow"))
    replay_count = sum(1 for row in rows if isinstance(row.output_data, dict) and row.output_data.get("replayed"))
    durations = sorted(float(row.duration_ms or 0) for row in rows)
    p95 = _percentile(durations, 0.95)
    success_rate = successes / total if total else 1.0
    compensation_rate = compensation_total / successes if successes else 0.0

    operations: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "success_count": 0, "failure_count": 0, "duration_ms": []}
    )
    for row in rows:
        bucket = operations[str(row.event_type or "unknown")]
        bucket["count"] += 1
        bucket["success_count"] += int(row.success is True)
        bucket["failure_count"] += int(row.success is False)
        bucket["duration_ms"].append(float(row.duration_ms or 0))

    operation_metrics = []
    for operation, values in sorted(operations.items()):
        count = int(values["count"])
        operation_metrics.append(
            {
                "operation": operation,
                "count": count,
                "success_rate": round(int(values["success_count"]) / count, 4) if count else 0,
                "avg_latency_ms": round(sum(values["duration_ms"]) / count, 2) if count else 0,
                "p95_latency_ms": round(_percentile(sorted(values["duration_ms"]), 0.95), 2),
            }
        )

    objectives = {
        "success_rate": {
            "target": settings.agent_slo_success_rate,
            "actual": round(success_rate, 4),
            "met": success_rate >= settings.agent_slo_success_rate,
        },
        "p95_latency_ms": {
            "target": settings.agent_slo_p95_latency_ms,
            "actual": round(p95, 2),
            "met": p95 <= settings.agent_slo_p95_latency_ms,
        },
        "compensation_rate": {
            "target_max": settings.agent_slo_max_compensation_rate,
            "actual": round(compensation_rate, 4),
            "met": compensation_rate <= settings.agent_slo_max_compensation_rate,
        },
    }
    return {
        "window_days": days,
        "connector_executions": {
            "total": total,
            "success_count": successes,
            "failure_count": failures,
            "success_rate": round(success_rate, 4),
            "shadow_count": shadow_count,
            "idempotency_replay_count": replay_count,
            "avg_latency_ms": round(sum(durations) / total, 2) if total else 0,
            "p95_latency_ms": round(p95, 2),
        },
        "business_outcomes": {
            "credit_memo_count": credit_memo_count,
            "approval_decision_count": approval_count,
            "pending_outbox_count": pending_outbox,
            "compensation_count": compensation_total,
            "failed_compensation_count": compensation_failed,
        },
        "operations": operation_metrics,
        "slo": {
            "met": all(value["met"] for value in objectives.values()),
            "objectives": objectives,
        },
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(len(values) * percentile) - 1))
    return values[index]
