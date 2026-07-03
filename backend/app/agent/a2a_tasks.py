"""Durable A2A task lifecycle and push-notification worker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import secrets
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.generic_runtime import run_configured_scenario
from app.agent.scenario_registry import get_default_registry
from app.commercial.operations import record_usage
from app.core.config import get_settings
from app.db.models import A2ATask, A2ATaskEvent
from app.db.tenant_context import tenant_scope


TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}
RUNNABLE_STATES = {"submitted", "working"}
CallbackDispatcher = Callable[[A2ATask, dict[str, Any]], Awaitable[None]]


async def create_or_resume_task(
    session: AsyncSession,
    *,
    task_id: str,
    context_id: str,
    tenant_id: str,
    owner_user_id: str,
    owner_role: str,
    message: dict[str, Any],
    push_notifications: bool,
    callback_url: str | None,
    callback_auth_ref: str | None = None,
) -> A2ATask:
    task = await session.get(A2ATask, task_id)
    now = _now()
    if task is not None:
        if task.tenant_id != tenant_id or task.owner_user_id != owner_user_id:
            raise LookupError("Task not found")
        if task.status in TERMINAL_STATES:
            return task
        task.input_message = message
        task.context_id = context_id
        task.status = "submitted"
        task.push_notifications = push_notifications or task.push_notifications
        task.callback_url = callback_url or task.callback_url
        task.callback_auth_ref = callback_auth_ref or task.callback_auth_ref
        task.task_metadata = {**dict(task.task_metadata or {}), "owner_role": owner_role}
        task.last_error = None
        task.next_attempt_at = now
        task.updated_at = now
        await append_task_event(
            session,
            task=task,
            state="submitted",
            message={"kind": "message", "role": "user", "parts": message.get("parts", [])},
            metadata={"resumed": True},
        )
        return task

    task = A2ATask(
        task_id=task_id,
        tenant_id=tenant_id,
        context_id=context_id,
        owner_user_id=owner_user_id,
        status="submitted",
        input_message=message,
        output_artifacts=[],
        task_metadata={
            "execution_mode": "governed",
            "approval_required": False,
            "owner_role": owner_role,
        },
        push_notifications=push_notifications,
        callback_url=callback_url,
        callback_auth_ref=callback_auth_ref,
        callback_status="PENDING" if push_notifications else None,
        callback_attempts=0,
        attempts=0,
        max_attempts=3,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.flush()
    await append_task_event(
        session,
        task=task,
        state="submitted",
        message={"kind": "message", "role": "user", "parts": message.get("parts", [])},
        metadata={"resumed": False},
    )
    return task


async def append_task_event(
    session: AsyncSession,
    *,
    task: A2ATask,
    state: str,
    message: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> A2ATaskEvent:
    sequence = int(
        await session.scalar(
            select(func.coalesce(func.max(A2ATaskEvent.sequence), 0)).where(
                A2ATaskEvent.tenant_id == task.tenant_id,
                A2ATaskEvent.task_id == task.task_id,
            )
        )
        or 0
    ) + 1
    event = A2ATaskEvent(
        event_id=_stable_id("A2AEVT", task.tenant_id, task.task_id, str(sequence)),
        tenant_id=task.tenant_id,
        task_id=task.task_id,
        sequence=sequence,
        state=state,
        message=message,
        event_metadata=metadata,
        created_at=_now(),
    )
    session.add(event)
    return event


async def get_owned_task(
    session: AsyncSession,
    *,
    task_id: str,
    tenant_id: str,
    owner_user_id: str,
) -> A2ATask | None:
    return await session.scalar(
        select(A2ATask).where(
            A2ATask.task_id == task_id,
            A2ATask.tenant_id == tenant_id,
            A2ATask.owner_user_id == owner_user_id,
        )
    )


async def cancel_owned_task(
    session: AsyncSession,
    *,
    task_id: str,
    tenant_id: str,
    owner_user_id: str,
) -> A2ATask:
    task = await get_owned_task(
        session,
        task_id=task_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
    )
    if task is None:
        raise LookupError("Task not found")
    if task.status in TERMINAL_STATES:
        raise ValueError(f"Task in state '{task.status}' cannot be canceled")
    task.status = "canceled"
    task.completed_at = _now()
    task.updated_at = _now()
    task.locked_at = None
    task.locked_by = None
    if task.push_notifications:
        task.callback_status = "PENDING"
        task.callback_next_attempt_at = _now()
    await append_task_event(session, task=task, state="canceled")
    await session.commit()
    return task


async def task_to_a2a(session: AsyncSession, task: A2ATask) -> dict[str, Any]:
    events = (
        await session.execute(
            select(A2ATaskEvent)
            .where(
                A2ATaskEvent.tenant_id == task.tenant_id,
                A2ATaskEvent.task_id == task.task_id,
            )
            .order_by(A2ATaskEvent.sequence)
        )
    ).scalars().all()
    status_event = events[-1] if events else None
    status: dict[str, Any] = {
        "state": task.status,
        "timestamp": (status_event.created_at if status_event else task.updated_at).replace(
            tzinfo=timezone.utc
        ).isoformat(),
    }
    if status_event and status_event.message:
        status["message"] = status_event.message
    history = [event.message for event in events if event.message]
    metadata = dict(task.task_metadata or {})
    metadata.update(
        {
            "scenario_id": task.scenario_id,
            "push_notifications": task.push_notifications,
            "callback_status": task.callback_status,
            "attempts": task.attempts,
        }
    )
    return {
        "id": task.task_id,
        "contextId": task.context_id,
        "kind": "task",
        "status": status,
        "history": history,
        "artifacts": list(task.output_artifacts or []),
        "metadata": metadata,
    }


async def execute_task(
    session_factory: async_sessionmaker,
    *,
    task_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            task = await session.scalar(
                select(A2ATask)
                .where(A2ATask.task_id == task_id, A2ATask.tenant_id == tenant_id)
                .with_for_update()
            )
            if task is None:
                raise LookupError("Task not found")
            if task.status in TERMINAL_STATES:
                return await task_to_a2a(session, task)
            task.status = "working"
            task.attempts = (task.attempts or 0) + 1
            task.updated_at = _now()
            task.last_error = None
            await append_task_event(
                session,
                task=task,
                state="working",
                message=_agent_message("The delegated enterprise workflow is running."),
            )
            await session.commit()
            message = dict(task.input_message)
            owner_user_id = task.owner_user_id
            owner_role = str((task.task_metadata or {}).get("owner_role") or "USER")
            context_id = task.context_id

    try:
        text = message_text(message)
        route = get_default_registry().match(text)
        scenario = get_default_registry().get(route.scenario_id)
        if scenario.runtime:
            output = await run_configured_scenario(
                {
                    "messages": [{"role": "user", "content": text}],
                    "user_id": owner_user_id,
                    "user_role": owner_role,
                    "thread_id": context_id,
                    "trace_id": f"a2a-{task_id}",
                },
                scenario.id,
                dry_run=True,
            )
            # Runtime slots intentionally use Decimal for financial precision,
            # while A2A artifacts are persisted in JSON columns.
            output = jsonable_encoder(output)
            approval_required = bool(output.get("approval_required"))
        else:
            output = {
                "scenario_id": scenario.id,
                "scenario_name": scenario.name,
                "route_reason": route.reason,
                "matched_keywords": list(route.matched_keywords),
                "next_action": "Continue through the checkpointed LangGraph HITL workflow.",
            }
            approval_required = scenario.hitl.enabled
        state = "input-required" if approval_required else "completed"
        status_text = (
            "Human approval is required before any side effect."
            if approval_required
            else "Delegated workflow completed."
        )
        artifact = {
            "artifactId": secrets.token_hex(12),
            "name": "scenario-execution-result",
            "parts": [{"kind": "data", "data": output}],
        }
        error = None
    except Exception as exc:
        state = "failed"
        status_text = "Scenario delegation failed safely."
        artifact = None
        approval_required = False
        scenario = None
        error = f"{type(exc).__name__}: {exc}"[:1000]

    with tenant_scope(tenant_id):
        async with session_factory() as session:
            task = await session.scalar(
                select(A2ATask)
                .where(A2ATask.task_id == task_id, A2ATask.tenant_id == tenant_id)
                .with_for_update()
            )
            if task is None:
                raise LookupError("Task not found")
            if task.status == "canceled":
                return await task_to_a2a(session, task)
            task.status = state
            task.scenario_id = scenario.id if scenario else None
            task.output_artifacts = [artifact] if artifact else []
            task.task_metadata = {
                "owner_user_id": task.owner_user_id,
                "owner_role": owner_role,
                "scenario_id": task.scenario_id,
                "approval_required": approval_required,
                "execution_mode": "simulation" if approval_required else "governed",
            }
            task.last_error = error
            task.locked_at = None
            task.locked_by = None
            task.updated_at = _now()
            if state in TERMINAL_STATES:
                task.completed_at = _now()
            if task.push_notifications:
                task.callback_status = "PENDING"
                task.callback_next_attempt_at = _now()
            await append_task_event(
                session,
                task=task,
                state=state,
                message=_agent_message(status_text),
                metadata={"error": error} if error else None,
            )
            await record_usage(
                session,
                tenant_id=tenant_id,
                metric_name="a2a_task_run",
                source_type="a2a_task",
                source_id=task.task_id,
                metadata={
                    "state": state,
                    "scenario_id": task.scenario_id,
                    "approval_required": approval_required,
                },
            )
            await session.commit()
            return await task_to_a2a(session, task)


async def claim_task_ids(
    session: AsyncSession,
    *,
    tenant_id: str,
    worker_id: str,
    limit: int = 10,
    lease_seconds: int = 60,
) -> list[str]:
    now = _now()
    stale = now - timedelta(seconds=lease_seconds)
    tasks = (
        await session.execute(
            select(A2ATask)
            .where(
                A2ATask.tenant_id == tenant_id,
                A2ATask.status == "submitted",
                or_(A2ATask.next_attempt_at.is_(None), A2ATask.next_attempt_at <= now),
                or_(A2ATask.locked_at.is_(None), A2ATask.locked_at < stale),
            )
            .order_by(A2ATask.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for task in tasks:
        task.locked_at = now
        task.locked_by = worker_id
    await session.commit()
    return [task.task_id for task in tasks]


async def deliver_pending_callbacks(
    session_factory: async_sessionmaker,
    *,
    tenant_id: str,
    dispatcher: CallbackDispatcher,
    limit: int = 20,
) -> dict[str, int]:
    now = _now()
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            tasks = (
                await session.execute(
                    select(A2ATask)
                    .where(
                        A2ATask.tenant_id == tenant_id,
                        A2ATask.push_notifications.is_(True),
                        A2ATask.callback_status.in_(["PENDING", "FAILED"]),
                        A2ATask.callback_attempts < A2ATask.max_attempts,
                        or_(
                            A2ATask.callback_next_attempt_at.is_(None),
                            A2ATask.callback_next_attempt_at <= now,
                        ),
                    )
                    .order_by(A2ATask.updated_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            result = {"sent": 0, "failed": 0}
            for task in tasks:
                payload = await task_to_a2a(session, task)
                try:
                    await dispatcher(task, payload)
                    task.callback_status = "SENT"
                    task.callback_last_error = None
                    task.callback_next_attempt_at = None
                    result["sent"] += 1
                except Exception as exc:
                    task.callback_status = "FAILED"
                    task.callback_attempts = (task.callback_attempts or 0) + 1
                    task.callback_last_error = f"{type(exc).__name__}: {exc}"[:1000]
                    if task.callback_attempts >= task.max_attempts:
                        task.callback_status = "DEAD_LETTER"
                        task.callback_next_attempt_at = None
                    else:
                        task.callback_next_attempt_at = now + timedelta(
                            seconds=min(300, 2 ** min(task.callback_attempts, 8))
                        )
                    result["failed"] += 1
            await session.commit()
            return result


async def http_callback_dispatcher(task: A2ATask, task_payload: dict[str, Any]) -> None:
    if not task.callback_url:
        raise ValueError("A2A push notification callback URL is missing")
    headers = {"Content-Type": "application/json"}
    token = get_settings().a2a_callback_bearer_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "method": "tasks/status",
        "params": {"task": task_payload},
    }
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.post(task.callback_url, json=body, headers=headers)
        response.raise_for_status()


async def run_a2a_worker(
    session_factory: async_sessionmaker,
    *,
    worker_id: str,
    tenant_ids: list[str],
    poll_seconds: float = 1.0,
    stop_event: asyncio.Event | None = None,
    dispatcher: CallbackDispatcher = http_callback_dispatcher,
) -> None:
    while stop_event is None or not stop_event.is_set():
        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                async with session_factory() as session:
                    task_ids = await claim_task_ids(
                        session,
                        tenant_id=tenant_id,
                        worker_id=worker_id,
                    )
            for task_id in task_ids:
                try:
                    await execute_task(
                        session_factory,
                        task_id=task_id,
                        tenant_id=tenant_id,
                    )
                except Exception:
                    await _schedule_retry(
                        session_factory,
                        task_id=task_id,
                        tenant_id=tenant_id,
                    )
            await deliver_pending_callbacks(
                session_factory,
                tenant_id=tenant_id,
                dispatcher=dispatcher,
            )
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(poll_seconds)


async def _schedule_retry(
    session_factory: async_sessionmaker,
    *,
    task_id: str,
    tenant_id: str,
) -> None:
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            task = await session.get(A2ATask, task_id)
            if task is None or task.tenant_id != tenant_id:
                return
            task.locked_at = None
            task.locked_by = None
            if task.attempts >= task.max_attempts:
                task.status = "failed"
                task.completed_at = _now()
                await append_task_event(
                    session,
                    task=task,
                    state="failed",
                    message=_agent_message("The delegated task exhausted its retry budget."),
                )
            else:
                task.status = "submitted"
                task.next_attempt_at = _now() + timedelta(
                    seconds=min(60, 2 ** max(1, task.attempts))
                )
            await session.commit()


def validate_callback_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    settings = get_settings()
    if parsed.scheme not in ({"https"} if settings.environment != "development" else {"http", "https"}):
        raise ValueError("A2A callback URL must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A2A callback URL is invalid")
    allowed = {
        value.strip().lower()
        for value in settings.a2a_allowed_callback_hosts.split(",")
        if value.strip()
    }
    if settings.environment != "development" and not allowed:
        raise ValueError("A2A callback host allowlist is required in production")
    if allowed and parsed.hostname.lower() not in allowed:
        raise ValueError("A2A callback host is not allowlisted")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if settings.environment != "development" and address and (
        address.is_private or address.is_loopback or address.is_link_local
    ):
        raise ValueError("A2A callback URL cannot target a private network address")
    return url


def message_text(message: Mapping[str, Any]) -> str:
    parts = message.get("parts") or []
    if not isinstance(parts, list):
        return ""
    values = [
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, Mapping) and str(part.get("kind") or part.get("type")) == "text"
    ]
    return "\n".join(value for value in values if value).strip()


def _agent_message(text: str) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": "agent",
        "messageId": secrets.token_hex(12),
        "parts": [{"kind": "text", "text": text}],
    }


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(parts)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:28].upper()}"
