"""Transactional outbox delivery with leasing, DLQ, and replay."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ErpOutboxStatus, OutboxEvent, WebhookSubscription


Dispatcher = Callable[[OutboxEvent], Awaitable[None]]


async def claim_outbox_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    limit: int = 50,
    lease_seconds: int = 60,
    tenant_id: str | None = None,
) -> list[OutboxEvent]:
    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=lease_seconds)
    conditions = [
        OutboxEvent.status.in_([ErpOutboxStatus.PENDING, ErpOutboxStatus.FAILED]),
        or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
        or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at < stale_before),
    ]
    if tenant_id is not None:
        conditions.append(OutboxEvent.tenant_id == tenant_id)
    stmt = (
        select(OutboxEvent)
        .where(*conditions)
        .order_by(OutboxEvent.created_at.asc())
        .limit(max(1, min(limit, 500)))
    )
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    events = (await session.execute(stmt)).scalars().all()
    for event in events:
        event.locked_at = now
        event.locked_by = worker_id
    await session.flush()
    return events


async def webhook_dispatcher(session: AsyncSession, event: OutboxEvent) -> None:
    subscriptions = (
        await session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.tenant_id == event.tenant_id,
                WebhookSubscription.event_type == event.event_type,
                WebhookSubscription.active.is_(True),
            )
        )
    ).scalars().all()
    async with httpx.AsyncClient(timeout=10.0) as client:
        for subscription in subscriptions:
            body = {
                "event_id": event.outbox_event_id,
                "event_type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "occurred_at": event.created_at.isoformat(),
                "data": event.payload,
            }
            raw = __import__("json").dumps(body, sort_keys=True, separators=(",", ":")).encode()
            headers = {
                "Content-Type": "application/json",
                "Idempotency-Key": event.idempotency_key or event.outbox_event_id,
                "X-Event-ID": event.outbox_event_id,
            }
            if subscription.secret_ref:
                secret = os.getenv(subscription.secret_ref, "")
                if not secret:
                    raise RuntimeError(f"missing webhook secret: {subscription.secret_ref}")
                headers["X-Webhook-Signature"] = "sha256=" + hmac.new(
                    secret.encode(), raw, hashlib.sha256
                ).hexdigest()
            response = await client.post(subscription.target_url, content=raw, headers=headers)
            response.raise_for_status()


async def process_outbox_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    dispatcher: Dispatcher,
    limit: int = 50,
    max_attempts: int = 8,
    tenant_id: str | None = None,
) -> dict[str, int]:
    events = await claim_outbox_batch(
        session,
        worker_id=worker_id,
        limit=limit,
        tenant_id=tenant_id,
    )
    result = {"claimed": len(events), "dispatched": 0, "failed": 0, "dead_lettered": 0}
    for event in events:
        try:
            await dispatcher(event)
            event.status = ErpOutboxStatus.DISPATCHED
            event.dispatched_at = datetime.utcnow()
            event.last_error = None
            result["dispatched"] += 1
        except Exception as exc:
            event.attempts = (event.attempts or 0) + 1
            event.last_error = str(exc)[:1000]
            if event.attempts >= max_attempts:
                event.status = ErpOutboxStatus.DEAD_LETTER
                event.dead_lettered_at = datetime.utcnow()
                result["dead_lettered"] += 1
            else:
                event.status = ErpOutboxStatus.FAILED
                delay_seconds = min(3600, 2 ** min(event.attempts, 10))
                event.next_attempt_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
                result["failed"] += 1
        finally:
            event.locked_at = None
            event.locked_by = None
    await session.commit()
    return result


async def replay_dead_letter(
    session: AsyncSession,
    *,
    event_id: str,
    replayed_by: str,
    tenant_id: str | None = None,
) -> OutboxEvent:
    event = await session.get(OutboxEvent, event_id)
    if event is None or (tenant_id is not None and event.tenant_id != tenant_id):
        raise LookupError(event_id)
    if event.status != ErpOutboxStatus.DEAD_LETTER:
        raise ValueError("only dead-letter events can be replayed")
    event.status = ErpOutboxStatus.PENDING
    event.attempts = 0
    event.next_attempt_at = datetime.utcnow()
    event.dead_lettered_at = None
    event.last_error = f"replayed by {replayed_by}"
    event.replay_count = (event.replay_count or 0) + 1
    await session.commit()
    return event


async def run_outbox_worker(
    session_factory,
    *,
    worker_id: str,
    poll_seconds: float = 1.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    while stop_event is None or not stop_event.is_set():
        async with session_factory() as session:
            async def dispatch(event: OutboxEvent) -> None:
                await webhook_dispatcher(session, event)

            result = await process_outbox_batch(
                session, worker_id=worker_id, dispatcher=dispatch
            )
        if not result["claimed"]:
            await asyncio.sleep(poll_seconds)
