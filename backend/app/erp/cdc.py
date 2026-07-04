"""CDC normalization and checkpoint persistence."""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CDCCheckpoint, ChangeDataCaptureEvent, ErpOutboxStatus, OutboxEvent


async def record_cdc_change(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_system: str,
    stream_name: str,
    source_position: str,
    object_type: str,
    object_id: str,
    operation: str,
    before_state: dict | None,
    after_state: dict | None,
) -> ChangeDataCaptureEvent:
    digest = hashlib.sha256(f"{source_system}:{source_position}".encode()).hexdigest()[:24]
    event_id = f"CDC-{digest.upper()}"
    event = ChangeDataCaptureEvent(
        cdc_event_id=event_id,
        tenant_id=tenant_id,
        source_system=source_system,
        source_position=source_position,
        object_type=object_type,
        object_id=object_id,
        operation=operation.upper(),
        before_state=before_state,
        after_state=after_state,
    )
    await session.merge(event)
    await session.merge(
        CDCCheckpoint(
            checkpoint_id=f"{tenant_id}:{source_system}:{stream_name}",
            tenant_id=tenant_id,
            source_system=source_system,
            stream_name=stream_name,
            source_position=source_position,
            updated_at=datetime.utcnow(),
        )
    )
    await session.merge(
        OutboxEvent(
            outbox_event_id=f"OUTBOX-{event_id}",
            tenant_id=tenant_id,
            aggregate_type=object_type,
            aggregate_id=object_id,
            event_type=f"cdc.{object_type.lower()}.{operation.lower()}",
            payload={
                "cdc_event_id": event_id,
                "source_system": source_system,
                "source_position": source_position,
                "before": before_state,
                "after": after_state,
            },
            status=ErpOutboxStatus.PENDING,
            idempotency_key=f"cdc:{source_system}:{source_position}",
        )
    )
    return event
