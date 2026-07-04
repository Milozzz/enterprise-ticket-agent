"""Consume normalized CDC events and reconcile them with the canonical store."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChangeDataCaptureEvent, Order
from app.erp.reconciliation import reconcile_snapshots


TargetLoader = Callable[[AsyncSession, ChangeDataCaptureEvent], Awaitable[dict[str, Any] | None]]


async def canonical_target_loader(
    session: AsyncSession, event: ChangeDataCaptureEvent
) -> dict[str, Any] | None:
    if event.object_type.upper() != "ORDER":
        return None
    order = await session.get(Order, event.object_id)
    if order is None:
        return None
    return {
        "order_id": order.id,
        "amount": order.amount,
        "status": order.status,
        "source_system": order.source_system,
    }


async def process_reconciliation_batch(
    session: AsyncSession,
    *,
    target_loader: TargetLoader = canonical_target_loader,
    limit: int = 100,
) -> dict[str, int]:
    stmt = (
        select(ChangeDataCaptureEvent)
        .where(ChangeDataCaptureEvent.published_at.is_(None))
        .order_by(ChangeDataCaptureEvent.occurred_at.asc())
        .limit(max(1, min(limit, 1000)))
    )
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    events = (await session.execute(stmt)).scalars().all()
    result = {"processed": 0, "matched": 0, "mismatched": 0, "target_missing": 0}
    for event in events:
        target = await target_loader(session, event)
        source = dict(event.after_state or {})
        if target is None:
            target = {"_target_missing": True}
            result["target_missing"] += 1
        else:
            # CDC payloads are frequently partial updates. Compare only fields
            # supplied by the source event instead of manufacturing differences.
            target = {key: target.get(key) for key in source}
        issue = await reconcile_snapshots(
            session,
            tenant_id=event.tenant_id,
            object_type=event.object_type,
            object_id=event.object_id,
            source_system=event.source_system,
            target_system="CANONICAL_DB",
            source_snapshot=source,
            target_snapshot=target,
        )
        if issue is None:
            result["matched"] += 1
        else:
            result["mismatched"] += 1
        event.published_at = datetime.utcnow()
        result["processed"] += 1
    await session.commit()
    return result


async def run_reconciliation_worker(
    session_factory,
    *,
    poll_seconds: float = 5.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    while stop_event is None or not stop_event.is_set():
        async with session_factory() as session:
            result = await process_reconciliation_batch(session)
        if not result["processed"]:
            await asyncio.sleep(poll_seconds)
