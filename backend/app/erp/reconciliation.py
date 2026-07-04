"""Cross-system snapshot reconciliation with deterministic issue IDs."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemReconciliationIssue


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


async def reconcile_snapshots(
    session: AsyncSession,
    *,
    tenant_id: str,
    object_type: str,
    object_id: str,
    source_system: str,
    target_system: str,
    source_snapshot: dict[str, Any],
    target_snapshot: dict[str, Any],
) -> SystemReconciliationIssue | None:
    if _normalize(source_snapshot) == _normalize(target_snapshot):
        existing = await session.scalar(
            select(SystemReconciliationIssue).where(
                SystemReconciliationIssue.tenant_id == tenant_id,
                SystemReconciliationIssue.object_type == object_type,
                SystemReconciliationIssue.object_id == object_id,
                SystemReconciliationIssue.source_system == source_system,
                SystemReconciliationIssue.target_system == target_system,
                SystemReconciliationIssue.status == "open",
            )
        )
        if existing:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
        return None
    raw = f"{tenant_id}:{object_type}:{object_id}:{source_system}:{target_system}"
    issue = SystemReconciliationIssue(
        reconciliation_issue_id=f"RECON-{hashlib.sha256(raw.encode()).hexdigest()[:24].upper()}",
        tenant_id=tenant_id,
        object_type=object_type,
        object_id=object_id,
        source_system=source_system,
        target_system=target_system,
        mismatch_type="snapshot_mismatch",
        source_snapshot=_normalize(source_snapshot),
        target_snapshot=_normalize(target_snapshot),
        severity="high",
        status="open",
        detected_at=datetime.utcnow(),
        resolved_at=None,
    )
    await session.merge(issue)
    return issue
