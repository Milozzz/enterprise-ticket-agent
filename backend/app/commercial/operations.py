"""Tenant onboarding, usage metering, and commercial operations metrics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    A2ATask,
    AuditLog,
    CompensationTransaction,
    ErpSagaStatus,
    ExecutionEvidence,
    ExternalSystemConnector,
    OutboxEvent,
    SagaExecution,
    TenantOnboarding,
    TenantOrganization,
    TenantSLO,
    TenantSubscription,
    UsageEvent,
)


ONBOARDING_ITEMS = {
    "identity_configured": False,
    "connector_configured": False,
    "connector_health_passed": False,
    "policy_published": False,
    "approval_roles_mapped": False,
    "eval_gate_passed": False,
    "shadow_write_verified": False,
    "evidence_export_verified": False,
    "production_change_approved": False,
}


async def provision_tenant(
    session: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    industry: str,
    region: str,
    plan_code: str = "PILOT",
    monthly_action_quota: int = 1000,
) -> dict[str, Any]:
    now = _now()
    organization = await session.get(TenantOrganization, tenant_id)
    if organization is None:
        organization = TenantOrganization(
            tenant_id=tenant_id,
            name=name,
            industry=industry,
            region=region,
            plan=plan_code.lower(),
            active=True,
            created_at=now,
        )
        session.add(organization)
    else:
        organization.name = name
        organization.industry = industry
        organization.region = region

    onboarding = await session.get(TenantOnboarding, tenant_id)
    if onboarding is None:
        onboarding = TenantOnboarding(
            tenant_id=tenant_id,
            status="DRAFT",
            environment="sandbox",
            checklist=dict(ONBOARDING_ITEMS),
            created_at=now,
            updated_at=now,
        )
        session.add(onboarding)

    subscription = await session.scalar(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    )
    if subscription is None:
        subscription = TenantSubscription(
            subscription_id=_stable_id("SUB", tenant_id),
            tenant_id=tenant_id,
            plan_code=plan_code.upper(),
            status="TRIAL",
            monthly_action_quota=monthly_action_quota,
            billing_currency="CNY",
            period_start=now,
            period_end=now + timedelta(days=30),
            trial_end=now + timedelta(days=30),
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)

    for metric_name, target, unit in [
        ("workflow_success_rate", Decimal("99.0000"), "percent"),
        ("p95_connector_latency_ms", Decimal("3000.0000"), "milliseconds"),
        ("callback_delivery_rate", Decimal("99.0000"), "percent"),
    ]:
        slo = await session.scalar(
            select(TenantSLO).where(
                TenantSLO.tenant_id == tenant_id,
                TenantSLO.metric_name == metric_name,
            )
        )
        if slo is None:
            session.add(
                TenantSLO(
                    slo_id=_stable_id("SLO", tenant_id, metric_name),
                    tenant_id=tenant_id,
                    metric_name=metric_name,
                    target_operator="<=" if "latency" in metric_name else ">=",
                    target_value=target,
                    unit=unit,
                    window_minutes=43200,
                    active=True,
                    created_at=now,
                )
            )
    await session.flush()
    return {
        "tenant_id": tenant_id,
        "onboarding_status": onboarding.status,
        "subscription_id": subscription.subscription_id,
        "plan_code": subscription.plan_code,
    }


async def update_onboarding(
    session: AsyncSession,
    *,
    tenant_id: str,
    updates: dict[str, bool],
    environment: str | None = None,
    primary_connector_id: str | None = None,
) -> TenantOnboarding:
    onboarding = await session.get(TenantOnboarding, tenant_id)
    if onboarding is None:
        raise LookupError("Tenant onboarding record not found")
    unknown = set(updates) - set(ONBOARDING_ITEMS)
    if unknown:
        raise ValueError(f"Unknown onboarding checklist items: {sorted(unknown)}")
    checklist = {**ONBOARDING_ITEMS, **dict(onboarding.checklist or {}), **updates}
    onboarding.checklist = checklist
    if environment:
        onboarding.environment = environment
    if primary_connector_id:
        connector = await session.scalar(
            select(ExternalSystemConnector).where(
                ExternalSystemConnector.tenant_id == tenant_id,
                ExternalSystemConnector.connector_id == primary_connector_id,
            )
        )
        if connector is None:
            raise ValueError("Primary connector does not belong to the current tenant")
        onboarding.primary_connector_id = primary_connector_id
        checklist["connector_configured"] = True
        onboarding.checklist = checklist
    required = [key for key in ONBOARDING_ITEMS if key != "production_change_approved"]
    ready = all(bool(checklist.get(key)) for key in required)
    if onboarding.environment == "production":
        ready = ready and bool(checklist.get("production_change_approved"))
    onboarding.status = "READY" if ready else "IN_PROGRESS"
    onboarding.completed_at = _now() if ready else None
    onboarding.updated_at = _now()
    await session.flush()
    return onboarding


async def record_usage(
    session: AsyncSession,
    *,
    tenant_id: str,
    metric_name: str,
    source_type: str,
    source_id: str,
    quantity: Decimal = Decimal("1.0000"),
    unit: str = "action",
    metadata: dict[str, Any] | None = None,
) -> UsageEvent:
    event_id = _stable_id("USE", tenant_id, metric_name, source_type, source_id)
    event = await session.get(UsageEvent, event_id)
    if event is not None:
        return event
    event = UsageEvent(
        usage_event_id=event_id,
        tenant_id=tenant_id,
        metric_name=metric_name,
        quantity=quantity.quantize(Decimal("0.0001")),
        unit=unit,
        source_type=source_type,
        source_id=source_id,
        usage_metadata=metadata,
        occurred_at=_now(),
    )
    session.add(event)
    await session.flush()
    return event


async def commercial_snapshot(session: AsyncSession, *, tenant_id: str) -> dict[str, Any]:
    now = _now()
    subscription = await session.scalar(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    )
    onboarding = await session.get(TenantOnboarding, tenant_id)
    period_start = subscription.period_start if subscription else now - timedelta(days=30)
    period_end = subscription.period_end if subscription else now

    usage_rows = (
        await session.execute(
            select(UsageEvent.metric_name, func.sum(UsageEvent.quantity))
            .where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.occurred_at >= period_start,
                UsageEvent.occurred_at < period_end,
            )
            .group_by(UsageEvent.metric_name)
        )
    ).all()
    usage_by_metric = {name: float(quantity or 0) for name, quantity in usage_rows}
    consumed_actions = sum(usage_by_metric.values())
    quota = subscription.monthly_action_quota if subscription else 0

    saga_rows = (
        await session.execute(
            select(SagaExecution.status, func.count())
            .where(
                SagaExecution.tenant_id == tenant_id,
                SagaExecution.created_at >= period_start,
            )
            .group_by(SagaExecution.status)
        )
    ).all()
    saga_counts = {status.value: count for status, count in saga_rows}
    completed = saga_counts.get(ErpSagaStatus.COMPLETED.value, 0)
    failed = saga_counts.get(ErpSagaStatus.FAILED.value, 0)
    manual = saga_counts.get(ErpSagaStatus.MANUAL_REVIEW.value, 0)
    terminal = completed + failed + manual
    workflow_success_rate = round(completed / terminal * 100, 2) if terminal else 100.0

    a2a_rows = (
        await session.execute(
            select(A2ATask.status, func.count())
            .where(A2ATask.tenant_id == tenant_id, A2ATask.created_at >= period_start)
            .group_by(A2ATask.status)
        )
    ).all()
    a2a_counts = {status: count for status, count in a2a_rows}
    callbacks_total = await session.scalar(
        select(func.count()).select_from(A2ATask).where(
            A2ATask.tenant_id == tenant_id,
            A2ATask.push_notifications.is_(True),
            A2ATask.created_at >= period_start,
        )
    ) or 0
    callbacks_sent = await session.scalar(
        select(func.count()).select_from(A2ATask).where(
            A2ATask.tenant_id == tenant_id,
            A2ATask.callback_status == "SENT",
            A2ATask.created_at >= period_start,
        )
    ) or 0
    callback_rate = round(callbacks_sent / callbacks_total * 100, 2) if callbacks_total else 100.0

    durations = list(
        (
            await session.scalars(
                select(AuditLog.duration_ms).where(
                    AuditLog.tenant_id == tenant_id,
                    AuditLog.node_name == "erp_connector",
                    AuditLog.duration_ms.is_not(None),
                    AuditLog.created_at >= period_start,
                )
            )
        ).all()
    )
    p95_latency = _percentile([int(value) for value in durations if value is not None], 0.95)
    outbox_rows = (
        await session.execute(
            select(OutboxEvent.status, func.count())
            .where(OutboxEvent.tenant_id == tenant_id)
            .group_by(OutboxEvent.status)
        )
    ).all()
    outbox_counts = {status.value: count for status, count in outbox_rows}
    evidence_count = await session.scalar(
        select(func.count()).select_from(ExecutionEvidence).where(
            ExecutionEvidence.tenant_id == tenant_id,
            ExecutionEvidence.occurred_at >= period_start,
        )
    ) or 0
    compensation_count = await session.scalar(
        select(func.count()).select_from(CompensationTransaction).where(
            CompensationTransaction.tenant_id == tenant_id
        )
    ) or 0

    measured = {
        "workflow_success_rate": workflow_success_rate,
        "p95_connector_latency_ms": p95_latency,
        "callback_delivery_rate": callback_rate,
    }
    slos = (
        await session.execute(
            select(TenantSLO).where(TenantSLO.tenant_id == tenant_id, TenantSLO.active.is_(True))
        )
    ).scalars().all()
    slo_report = [
        {
            "metric": slo.metric_name,
            "target": f"{slo.target_operator} {float(slo.target_value)} {slo.unit}",
            "actual": measured.get(slo.metric_name),
            "met": _compare(measured.get(slo.metric_name), slo.target_operator, float(slo.target_value)),
        }
        for slo in slos
    ]
    return {
        "tenant_id": tenant_id,
        "generated_at": now.replace(tzinfo=timezone.utc).isoformat(),
        "onboarding": {
            "status": onboarding.status if onboarding else "NOT_STARTED",
            "environment": onboarding.environment if onboarding else None,
            "checklist": onboarding.checklist if onboarding else ONBOARDING_ITEMS,
        },
        "subscription": {
            "plan": subscription.plan_code if subscription else None,
            "status": subscription.status if subscription else None,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "quota": quota,
            "consumed": consumed_actions,
            "remaining": max(0, quota - consumed_actions),
            "usage_percent": round(consumed_actions / quota * 100, 2) if quota else 0,
            "by_metric": usage_by_metric,
        },
        "operations": {
            "sagas": saga_counts,
            "a2a_tasks": a2a_counts,
            "outbox": outbox_counts,
            "evidence_items": evidence_count,
            "compensations": compensation_count,
        },
        "business_kpis": {
            "automated_finance_transactions": completed,
            "manual_review_cases": manual,
            "failed_transactions": failed,
            "workflow_success_rate": workflow_success_rate,
        },
        "slos": slo_report,
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _compare(actual: float | int | None, operator: str, target: float) -> bool:
    if actual is None:
        return False
    if operator == "<=":
        return actual <= target
    if operator == ">":
        return actual > target
    if operator == "<":
        return actual < target
    return actual >= target


def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(parts)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:28].upper()}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
