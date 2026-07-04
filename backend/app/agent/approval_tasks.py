from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.masking import mask_dict
from app.db.database import AsyncSessionLocal
from app.db.models import ApprovalTask, AuditLog
from app.db.tenant_context import current_tenant_id, tenant_scope

logger = get_logger(__name__)
settings = get_settings()


async def ensure_approval_task(
    *,
    task_key: str,
    request_id: str,
    scenario_id: str,
    approval_type: str,
    stage_id: str,
    stage_name: str,
    requester_id: str,
    requester_role: str,
    assigned_roles: list[str],
    thread_id: str,
    business_payload: dict[str, Any],
    sla_minutes: int,
    priority: str = "normal",
    tenant_id: str | None = None,
    session_factory=AsyncSessionLocal,
) -> ApprovalTask:
    tenant = tenant_id or current_tenant_id()
    now = datetime.utcnow()
    with tenant_scope(tenant):
        async with session_factory() as session:
            existing = await session.scalar(
                select(ApprovalTask).where(
                    ApprovalTask.tenant_id == tenant,
                    ApprovalTask.task_key == task_key,
                )
            )
            if existing is not None:
                return existing
            task = ApprovalTask(
                tenant_id=tenant,
                task_key=task_key,
                request_id=request_id,
                scenario_id=scenario_id,
                approval_type=approval_type,
                stage_id=stage_id,
                stage_name=stage_name,
                requester_id=requester_id,
                requester_role=requester_role.upper(),
                assigned_roles=sorted({role.upper() for role in assigned_roles}),
                status="pending",
                priority=priority,
                business_payload=mask_dict(business_payload),
                thread_id=thread_id,
                created_at=now,
                due_at=now + timedelta(minutes=max(1, sla_minutes)),
            )
            session.add(task)
            try:
                await session.commit()
                await session.refresh(task)
                return task
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(ApprovalTask).where(
                        ApprovalTask.tenant_id == tenant,
                        ApprovalTask.task_key == task_key,
                    )
                )
                if existing is None:
                    raise
                return existing


async def complete_approval_task(
    task_key: str,
    *,
    action: str,
    reviewer_id: str,
    comment: str = "",
    tenant_id: str | None = None,
    session_factory=AsyncSessionLocal,
) -> ApprovalTask | None:
    tenant = tenant_id or current_tenant_id()
    with tenant_scope(tenant):
        async with session_factory() as session:
            task = await session.scalar(
                select(ApprovalTask).where(
                    ApprovalTask.tenant_id == tenant,
                    ApprovalTask.task_key == task_key,
                )
            )
            if task is None:
                return None
            expected_status = "approved" if action == "approve" else "rejected"
            if task.status in {"approved", "rejected"}:
                if task.status != expected_status:
                    raise ValueError("Approval task already has a different terminal decision")
                return task
            task.status = expected_status
            task.current_assignee_id = reviewer_id
            task.reviewer_comment = comment[:500]
            task.completed_at = datetime.utcnow()
            await session.commit()
            await session.refresh(task)
            return task


async def escalate_overdue_tasks(
    *,
    tenant_id: str | None = None,
    session_factory=AsyncSessionLocal,
) -> int:
    tenant = tenant_id or settings.default_tenant_id
    now = datetime.utcnow()
    with tenant_scope(tenant):
        async with session_factory() as session:
            tasks = (
                await session.execute(
                    select(ApprovalTask).where(
                        ApprovalTask.tenant_id == tenant,
                        ApprovalTask.status == "pending",
                        ApprovalTask.due_at <= now,
                    )
                )
            ).scalars().all()
            for task in tasks:
                task.status = "escalated"
                task.escalation_level += 1
                task.escalated_at = now
                task.escalation_reason = "Approval SLA exceeded"
                session.add(
                    AuditLog(
                        thread_id=task.thread_id or task.task_key,
                        trace_id=None,
                        node_name="approval_escalation",
                        event_type="approval_sla_exceeded",
                        input_data={"task_id": task.id, "due_at": task.due_at.isoformat()},
                        output_data={"status": "escalated", "level": task.escalation_level},
                        success=True,
                    )
                )
            if tasks:
                await session.commit()
            return len(tasks)


async def run_approval_escalation_worker(
    session_factory=AsyncSessionLocal,
    *,
    poll_seconds: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    stop = stop_event or asyncio.Event()
    interval = poll_seconds or settings.approval_escalation_poll_seconds
    while not stop.is_set():
        try:
            count = await escalate_overdue_tasks(session_factory=session_factory)
            if count:
                logger.info("approval_tasks_escalated", count=count)
        except Exception as exc:
            logger.warning("approval_escalation_worker_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(1.0, interval))
        except asyncio.TimeoutError:
            pass


def serialize_approval_task(task: ApprovalTask) -> dict[str, Any]:
    now = datetime.utcnow()
    return {
        "taskId": task.id,
        "taskKey": task.task_key,
        "requestId": task.request_id,
        "scenarioId": task.scenario_id,
        "approvalType": task.approval_type,
        "stageId": task.stage_id,
        "stageName": task.stage_name,
        "requesterId": task.requester_id,
        "requesterRole": task.requester_role,
        "assignedRoles": task.assigned_roles or [],
        "status": task.status,
        "priority": task.priority,
        "businessPayload": task.business_payload or {},
        "threadId": task.thread_id,
        "currentAssigneeId": task.current_assignee_id,
        "escalationLevel": task.escalation_level,
        "escalationReason": task.escalation_reason,
        "reviewerComment": task.reviewer_comment,
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "dueAt": task.due_at.isoformat() if task.due_at else None,
        "remainingSeconds": int((task.due_at - now).total_seconds()) if task.due_at else None,
        "completedAt": task.completed_at.isoformat() if task.completed_at else None,
    }
