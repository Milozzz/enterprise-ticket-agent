from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import AgentExecutionJob
from app.db.tenant_context import tenant_scope

logger = get_logger(__name__)
settings = get_settings()
JobExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def derive_job_idempotency_key(tenant_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{tenant_id}:{canonical}".encode()).hexdigest()
    return f"agent-job:{digest}"


async def enqueue_agent_job(
    payload: dict[str, Any],
    *,
    tenant_id: str,
    requester_id: str,
    requester_role: str,
    idempotency_key: str | None = None,
    priority: int = 50,
    session_factory=AsyncSessionLocal,
) -> tuple[AgentExecutionJob, bool]:
    key = idempotency_key or derive_job_idempotency_key(tenant_id, payload)
    thread_id = str(payload.get("thread_id") or f"job-thread-{uuid.uuid4().hex}")
    trace_id = str(payload.get("trace_id") or thread_id)
    now = datetime.utcnow()
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            existing = await session.scalar(
                select(AgentExecutionJob).where(
                    AgentExecutionJob.tenant_id == tenant_id,
                    AgentExecutionJob.idempotency_key == key,
                )
            )
            if existing is not None:
                return existing, True
            job = AgentExecutionJob(
                job_id=f"job_{uuid.uuid4().hex}",
                tenant_id=tenant_id,
                idempotency_key=key,
                thread_id=thread_id,
                trace_id=trace_id,
                requester_id=requester_id,
                requester_role=requester_role.upper(),
                status="queued",
                priority=min(max(priority, 0), 100),
                input_payload={**payload, "thread_id": thread_id, "trace_id": trace_id},
                attempts=0,
                max_attempts=settings.agent_job_max_attempts,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            try:
                await session.commit()
                await session.refresh(job)
                return job, False
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(AgentExecutionJob).where(
                        AgentExecutionJob.tenant_id == tenant_id,
                        AgentExecutionJob.idempotency_key == key,
                    )
                )
                if existing is None:
                    raise
                return existing, True


async def claim_next_job(
    *,
    tenant_id: str,
    worker_id: str,
    session_factory=AsyncSessionLocal,
) -> AgentExecutionJob | None:
    now = datetime.utcnow()
    stale = now - timedelta(minutes=5)
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            query = (
                select(AgentExecutionJob)
                .where(
                    AgentExecutionJob.tenant_id == tenant_id,
                    AgentExecutionJob.available_at <= now,
                    or_(
                        AgentExecutionJob.status.in_(["queued", "retry"]),
                        (AgentExecutionJob.status == "running") & (AgentExecutionJob.locked_at <= stale),
                    ),
                )
                .order_by(AgentExecutionJob.priority.desc(), AgentExecutionJob.created_at.asc())
                .limit(1)
            )
            bind = session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            job = await session.scalar(query)
            if job is None:
                return None
            job.status = "running"
            job.locked_by = worker_id
            job.locked_at = now
            job.started_at = job.started_at or now
            job.attempts += 1
            job.updated_at = now
            await session.commit()
            await session.refresh(job)
            return job


async def finish_job(
    job: AgentExecutionJob,
    *,
    output: dict[str, Any] | None = None,
    error: Exception | None = None,
    session_factory=AsyncSessionLocal,
) -> None:
    now = datetime.utcnow()
    with tenant_scope(job.tenant_id):
        async with session_factory() as session:
            stored = await session.get(AgentExecutionJob, job.job_id)
            if stored is None or stored.status == "cancelled":
                return
            if error is None:
                stored.output_payload = output or {}
                stored.status = str((output or {}).get("job_status") or "succeeded")
                stored.error_message = None
                stored.completed_at = now if stored.status == "succeeded" else None
            elif stored.attempts < stored.max_attempts:
                stored.status = "retry"
                stored.error_message = str(error)[:1000]
                stored.available_at = now + timedelta(seconds=min(2 ** stored.attempts, 60))
            else:
                stored.status = "failed"
                stored.error_message = str(error)[:1000]
                stored.completed_at = now
            stored.locked_by = None
            stored.locked_at = None
            stored.updated_at = now
            await session.commit()


async def execute_agent_payload(payload: dict[str, Any], graph: Any) -> dict[str, Any]:
    messages = []
    for item in payload.get("messages", []):
        if item.get("role") == "assistant":
            messages.append(AIMessage(content=str(item.get("content", ""))))
        elif item.get("role", "user") == "user":
            messages.append(HumanMessage(content=str(item.get("content", ""))))
    state = {
        "messages": messages,
        "thread_id": payload["thread_id"],
        "trace_id": payload.get("trace_id") or payload["thread_id"],
        "tenant_id": payload["tenant_id"],
        "user_id": payload.get("user_id", "anonymous"),
        "user_role": payload.get("user_role", "USER"),
        "ui_events": [],
    }
    config = {"configurable": {"thread_id": payload["thread_id"]}}
    await graph.ainvoke(state, config=config)
    snapshot = graph.get_state(config)
    values = snapshot.values if snapshot else {}
    allowed = {
        "scenario_id", "intent", "current_step", "reply_text", "error_message",
        "is_completed", "refund_id", "refund_success", "saga_id", "saga_status",
        "business_request", "policy_citations", "specialist_handoffs",
    }
    return {
        "job_status": "waiting_approval" if snapshot and snapshot.next else "succeeded",
        "thread_id": payload["thread_id"],
        "next": list(snapshot.next) if snapshot and snapshot.next else [],
        "state": {key: value for key, value in values.items() if key in allowed},
    }


async def run_agent_job_worker(
    graph: Any,
    session_factory=AsyncSessionLocal,
    *,
    worker_id: str | None = None,
    tenant_ids: list[str] | None = None,
    poll_seconds: float | None = None,
    stop_event: asyncio.Event | None = None,
    executor: JobExecutor | None = None,
) -> None:
    stop = stop_event or asyncio.Event()
    worker = worker_id or settings.agent_job_worker_id
    tenants = tenant_ids or [settings.default_tenant_id]
    interval = poll_seconds or settings.agent_job_poll_seconds
    execute = executor or (lambda payload: execute_agent_payload(payload, graph))
    while not stop.is_set():
        claimed = False
        for tenant_id in tenants:
            job = await claim_next_job(
                tenant_id=tenant_id,
                worker_id=worker,
                session_factory=session_factory,
            )
            if job is None:
                continue
            claimed = True
            try:
                output = await execute({**job.input_payload, "tenant_id": job.tenant_id})
                await finish_job(job, output=output, session_factory=session_factory)
            except Exception as exc:
                logger.warning("agent_job_failed", job_id=job.job_id, error=str(exc))
                await finish_job(job, error=exc, session_factory=session_factory)
        if not claimed:
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.1, interval))
            except asyncio.TimeoutError:
                pass


async def cancel_agent_job(
    job_id: str,
    *,
    tenant_id: str,
    session_factory=AsyncSessionLocal,
) -> bool:
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            job = await session.get(AgentExecutionJob, job_id)
            if job is None or job.tenant_id != tenant_id or job.status in {"succeeded", "failed", "cancelled"}:
                return False
            job.status = "cancelled"
            job.completed_at = datetime.utcnow()
            job.updated_at = datetime.utcnow()
            await session.commit()
            return True


def serialize_agent_job(job: AgentExecutionJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "trace_id": job.trace_id,
        "status": job.status,
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "output": job.output_payload,
        "error": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
