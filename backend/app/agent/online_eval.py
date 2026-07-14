"""Production feedback capture, governed eval promotion and drift reporting."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.masking import mask_dict
from app.db.models import AgentFeedbackRecord, OnlineEvalCase


async def record_agent_feedback(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    trace_id: str | None,
    task_id: str | None,
    scenario_id: str,
    submitted_by: str,
    disposition: str,
    rating: int | None,
    reason_codes: Sequence[str],
    correction: Mapping[str, Any] | None,
    task_snapshot: Mapping[str, Any],
    version_context: Mapping[str, Any],
) -> tuple[AgentFeedbackRecord, OnlineEvalCase | None]:
    disposition = str(disposition).lower()
    if disposition not in {"accept", "correct", "reject"}:
        raise ValueError("disposition must be accept, correct or reject")
    if rating is not None and not 1 <= rating <= 5:
        raise ValueError("rating must be between 1 and 5")
    feedback_id = f"fdb_{uuid.uuid4().hex}"
    is_candidate = disposition in {"correct", "reject"}
    feedback = AgentFeedbackRecord(
        feedback_id=feedback_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        trace_id=trace_id,
        task_id=task_id,
        scenario_id=scenario_id,
        submitted_by=submitted_by,
        disposition=disposition,
        rating=rating,
        reason_codes=[str(item) for item in reason_codes],
        correction=mask_dict(dict(correction or {})) if correction else None,
        task_snapshot=mask_dict(dict(task_snapshot or {})),
        version_context=mask_dict(dict(version_context or {})),
        eval_candidate=is_candidate,
    )
    session.add(feedback)
    eval_case = None
    if is_candidate:
        eval_case = OnlineEvalCase(
            case_id=f"oev_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            source_feedback_id=feedback_id,
            dataset_name="production_feedback",
            scenario_id=scenario_id,
            input_snapshot=mask_dict(dict(task_snapshot or {})),
            expected_outcome=mask_dict(
                dict(correction or {"disposition": "reject", "reason_codes": list(reason_codes)})
            ),
            status="candidate",
        )
        session.add(eval_case)
    await session.flush()
    return feedback, eval_case


async def promote_online_eval_case(
    session: AsyncSession,
    *,
    tenant_id: str,
    case_id: str,
    reviewed_by: str,
    dataset_version: str,
    status: str = "approved",
) -> OnlineEvalCase:
    case = await session.scalar(
        select(OnlineEvalCase).where(
            OnlineEvalCase.case_id == case_id,
            OnlineEvalCase.tenant_id == tenant_id,
        )
    )
    if case is None:
        raise ValueError("online eval case was not found")
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved or rejected")
    case.status = status
    case.reviewed_by = reviewed_by
    case.dataset_version = dataset_version if status == "approved" else None
    case.published_at = datetime.utcnow() if status == "approved" else None
    await session.flush()
    return case


async def online_eval_report(
    session: AsyncSession,
    *,
    tenant_id: str,
    window_hours: int = 24,
) -> dict[str, Any]:
    now = datetime.utcnow()
    current_start = now - timedelta(hours=window_hours)
    previous_start = now - timedelta(hours=window_hours * 2)
    feedback = list(
        (
            await session.execute(
                select(AgentFeedbackRecord)
                .where(
                    AgentFeedbackRecord.tenant_id == tenant_id,
                    AgentFeedbackRecord.created_at >= previous_start,
                )
                .order_by(AgentFeedbackRecord.created_at.asc())
            )
        ).scalars()
    )
    cases = list(
        (
            await session.execute(
                select(OnlineEvalCase).where(OnlineEvalCase.tenant_id == tenant_id)
            )
        ).scalars()
    )
    current = [item for item in feedback if item.created_at >= current_start]
    previous = [item for item in feedback if item.created_at < current_start]
    current_metrics = _feedback_metrics(current)
    previous_metrics = _feedback_metrics(previous)
    acceptance_delta = current_metrics["acceptance_rate"] - previous_metrics["acceptance_rate"]
    settings = get_settings()
    sample_ready = min(len(current), len(previous)) >= settings.online_eval_minimum_sample_size
    drift_detected = bool(sample_ready and acceptance_delta <= -abs(settings.online_eval_drift_threshold))
    return {
        "tenant_id": tenant_id,
        "window_hours": window_hours,
        "current": current_metrics,
        "previous": previous_metrics,
        "drift": {
            "detected": drift_detected,
            "acceptance_rate_delta": round(acceptance_delta, 4),
            "threshold": settings.online_eval_drift_threshold,
            "minimum_sample_size": settings.online_eval_minimum_sample_size,
            "sample_ready": sample_ready,
        },
        "version_comparison": _version_metrics(current),
        "dataset": {
            "candidate": sum(item.status == "candidate" for item in cases),
            "approved": sum(item.status == "approved" for item in cases),
            "rejected": sum(item.status == "rejected" for item in cases),
            "published_versions": sorted({item.dataset_version for item in cases if item.dataset_version}),
        },
    }


def serialize_feedback(feedback: AgentFeedbackRecord) -> dict[str, Any]:
    return {
        "feedback_id": feedback.feedback_id,
        "thread_id": feedback.thread_id,
        "scenario_id": feedback.scenario_id,
        "disposition": feedback.disposition,
        "rating": feedback.rating,
        "reason_codes": list(feedback.reason_codes or []),
        "eval_candidate": feedback.eval_candidate,
        "created_at": feedback.created_at.isoformat(),
    }


def serialize_eval_case(case: OnlineEvalCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "source_feedback_id": case.source_feedback_id,
        "dataset_name": case.dataset_name,
        "scenario_id": case.scenario_id,
        "status": case.status,
        "dataset_version": case.dataset_version,
        "reviewed_by": case.reviewed_by,
        "published_at": case.published_at.isoformat() if case.published_at else None,
    }


def _feedback_metrics(records: Sequence[AgentFeedbackRecord]) -> dict[str, Any]:
    total = len(records)
    counts = {key: sum(item.disposition == key for item in records) for key in ("accept", "correct", "reject")}
    ratings = [item.rating for item in records if item.rating is not None]
    return {
        "sample_size": total,
        "accepted": counts["accept"],
        "corrected": counts["correct"],
        "rejected": counts["reject"],
        "acceptance_rate": round(counts["accept"] / total, 4) if total else 0.0,
        "human_override_rate": round((counts["correct"] + counts["reject"]) / total, 4) if total else 0.0,
        "average_rating": round(sum(ratings) / len(ratings), 3) if ratings else None,
    }


def _version_metrics(records: Sequence[AgentFeedbackRecord]) -> list[dict[str, Any]]:
    groups: dict[str, list[AgentFeedbackRecord]] = {}
    for item in records:
        context = dict(item.version_context or {})
        key = "|".join(
            str(context.get(name) or "unknown")
            for name in ("model", "prompt_version", "policy_version", "plan_version")
        )
        groups.setdefault(key, []).append(item)
    return [
        {"version_key": key, **_feedback_metrics(values)}
        for key, values in sorted(groups.items())
    ]
