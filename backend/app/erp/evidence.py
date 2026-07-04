"""Append-only, hash-chained evidence for governed Agent executions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.masking import mask_dict
from app.db.models import ExecutionEvidence, SagaExecution, SagaStep


async def append_evidence(
    session: AsyncSession,
    *,
    tenant_id: str,
    saga_id: str,
    sequence: int,
    evidence_type: str,
    object_type: str,
    object_id: str,
    source_system: str,
    payload: dict[str, Any],
    actor_id: str,
    source_reference: str | None = None,
    policy_version: str | None = None,
    approval_id: str | None = None,
) -> ExecutionEvidence:
    existing = await session.scalar(
        select(ExecutionEvidence).where(
            ExecutionEvidence.tenant_id == tenant_id,
            ExecutionEvidence.saga_id == saga_id,
            ExecutionEvidence.sequence == sequence,
        )
    )
    safe_payload = mask_dict(payload)
    payload_hash = _hash_json(safe_payload)
    if existing:
        if existing.payload_hash != payload_hash or existing.evidence_type != evidence_type:
            raise ValueError("Evidence sequence was reused with different content")
        return existing

    previous = await session.scalar(
        select(ExecutionEvidence)
        .where(
            ExecutionEvidence.tenant_id == tenant_id,
            ExecutionEvidence.saga_id == saga_id,
            ExecutionEvidence.sequence < sequence,
        )
        .order_by(ExecutionEvidence.sequence.desc())
        .limit(1)
    )
    previous_hash = previous.record_hash if previous else None
    record_hash = _record_hash(
        saga_id=saga_id,
        sequence=sequence,
        evidence_type=evidence_type,
        payload_hash=payload_hash,
        previous_hash=previous_hash,
    )
    evidence = ExecutionEvidence(
        evidence_id=f"EVD-{record_hash[:28].upper()}",
        tenant_id=tenant_id,
        saga_id=saga_id,
        sequence=sequence,
        evidence_type=evidence_type,
        object_type=object_type,
        object_id=object_id,
        source_system=source_system,
        source_reference=source_reference,
        payload=safe_payload,
        payload_hash=payload_hash,
        previous_hash=previous_hash,
        record_hash=record_hash,
        policy_version=policy_version,
        approval_id=approval_id,
        actor_id=actor_id,
        occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def export_evidence_bundle(
    session: AsyncSession,
    *,
    tenant_id: str,
    saga_id: str,
) -> dict[str, Any] | None:
    saga = await session.scalar(
        select(SagaExecution).where(
            SagaExecution.tenant_id == tenant_id,
            SagaExecution.saga_id == saga_id,
        )
    )
    if saga is None:
        return None
    steps = (
        await session.execute(
            select(SagaStep)
            .where(SagaStep.tenant_id == tenant_id, SagaStep.saga_id == saga_id)
            .order_by(SagaStep.sequence)
        )
    ).scalars().all()
    evidence = (
        await session.execute(
            select(ExecutionEvidence)
            .where(
                ExecutionEvidence.tenant_id == tenant_id,
                ExecutionEvidence.saga_id == saga_id,
            )
            .order_by(ExecutionEvidence.sequence)
        )
    ).scalars().all()
    chain_valid = verify_evidence_chain(evidence)
    items = [
        {
            "evidence_id": item.evidence_id,
            "sequence": item.sequence,
            "type": item.evidence_type,
            "object_type": item.object_type,
            "object_id": item.object_id,
            "source_system": item.source_system,
            "source_reference": item.source_reference,
            "payload": item.payload,
            "payload_hash": item.payload_hash,
            "previous_hash": item.previous_hash,
            "record_hash": item.record_hash,
            "policy_version": item.policy_version,
            "approval_id": item.approval_id,
            "actor_id": item.actor_id,
            "occurred_at": item.occurred_at.isoformat(),
        }
        for item in evidence
    ]
    bundle = {
        "schema_version": "1.0",
        "tenant_id": tenant_id,
        "saga": {
            "saga_id": saga.saga_id,
            "type": saga.saga_type,
            "business_key": saga.business_key,
            "status": saga.status.value,
            "current_step": saga.current_step,
            "command": saga.command_payload,
            "context": saga.context_snapshot,
            "result": saga.result_snapshot,
            "created_at": saga.created_at.isoformat(),
            "completed_at": saga.completed_at.isoformat() if saga.completed_at else None,
        },
        "steps": [
            {
                "name": step.step_name,
                "sequence": step.sequence,
                "status": step.status.value,
                "attempts": step.attempts,
                "idempotency_key": step.idempotency_key,
                "remote_object_id": step.remote_object_id,
                "last_error": step.last_error,
            }
            for step in steps
        ],
        "evidence": items,
        "integrity": {
            "chain_valid": chain_valid,
            "item_count": len(items),
            "head_hash": items[-1]["record_hash"] if items else None,
        },
    }
    return {**bundle, "bundle_hash": _hash_json(bundle)}


def verify_evidence_chain(items: list[ExecutionEvidence]) -> bool:
    previous_hash: str | None = None
    for item in sorted(items, key=lambda row: row.sequence):
        if item.payload_hash != _hash_json(item.payload):
            return False
        expected = _record_hash(
            saga_id=item.saga_id,
            sequence=item.sequence,
            evidence_type=item.evidence_type,
            payload_hash=item.payload_hash,
            previous_hash=previous_hash,
        )
        if item.previous_hash != previous_hash or item.record_hash != expected:
            return False
        previous_hash = item.record_hash
    return True


def _record_hash(
    *,
    saga_id: str,
    sequence: int,
    evidence_type: str,
    payload_hash: str,
    previous_hash: str | None,
) -> str:
    raw = "|".join(
        [saga_id, str(sequence), evidence_type, payload_hash, previous_hash or "GENESIS"]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
