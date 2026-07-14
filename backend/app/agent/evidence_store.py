"""Persistent, append-only storage for task Evidence Graphs and cited decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from sqlalchemy import select

from app.agent.dependencies import resolve_session_factory
from app.db.database import AsyncSessionLocal
from app.db.models import (
    AgentDecisionRecord,
    AgentEvidenceRecord,
    AgentEvidenceRelationRecord,
)
from app.db.tenant_context import tenant_scope


def _stable_id(prefix: str, *parts: object) -> str:
    raw = ":".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _parse_datetime(value: object, *, required: bool = False) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    else:
        parsed = None
    if parsed is None and required:
        parsed = datetime.now(timezone.utc)
    if parsed is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _record_hash(
    *,
    task_id: str,
    sequence: int,
    evidence_id: str,
    predicate: str,
    content_hash: str,
    previous_hash: str | None,
) -> str:
    payload = json.dumps(
        {
            "task_id": task_id,
            "sequence": sequence,
            "evidence_id": evidence_id,
            "predicate": predicate,
            "content_hash": content_hash,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def persist_evidence_bundle(
    *,
    evidence_graph: Mapping[str, Any],
    task_spec: Mapping[str, Any],
    verification_result: Mapping[str, Any] | None = None,
    tenant_id: str,
    thread_id: str = "",
    trace_id: str = "",
    decision_type: str = "execution_verification",
    verifier: str = "deterministic_verifier",
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    """Append new claims/relations and optionally a decision in one transaction."""
    task_id = str(task_spec.get("task_id") or evidence_graph.get("task_id") or "")
    scenario_id = str(task_spec.get("scenario_id") or "unknown")
    if not task_id:
        raise ValueError("TaskSpec task_id is required for evidence persistence")

    factory = resolve_session_factory(session_factory)
    with tenant_scope(tenant_id):
        async with factory() as session:
            existing = (
                await session.execute(
                    select(AgentEvidenceRecord)
                    .where(
                        AgentEvidenceRecord.tenant_id == tenant_id,
                        AgentEvidenceRecord.task_id == task_id,
                    )
                    .order_by(AgentEvidenceRecord.sequence)
                    .with_for_update()
                )
            ).scalars().all()
            by_evidence_id = {item.evidence_id: item for item in existing}
            sequence = existing[-1].sequence if existing else 0
            previous_hash = existing[-1].record_hash if existing else None
            inserted = 0

            for claim in evidence_graph.get("claims") or []:
                evidence_id = str(claim.get("evidence_id") or "")
                if not evidence_id or evidence_id in by_evidence_id:
                    continue
                sequence += 1
                predicate = str(claim.get("predicate") or "unknown")
                content_hash = str(claim.get("content_hash") or hashlib.sha256(
                    json.dumps(
                        claim.get("observed_value", claim.get("value")),
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest())
                record_hash = _record_hash(
                    task_id=task_id,
                    sequence=sequence,
                    evidence_id=evidence_id,
                    predicate=predicate,
                    content_hash=content_hash,
                    previous_hash=previous_hash,
                )
                record = AgentEvidenceRecord(
                    record_id=_stable_id("AER", tenant_id, task_id, evidence_id),
                    tenant_id=tenant_id,
                    task_id=task_id,
                    evidence_id=evidence_id,
                    sequence=sequence,
                    scenario_id=scenario_id,
                    thread_id=thread_id or None,
                    trace_id=trace_id or claim.get("trace_id") or None,
                    predicate=predicate,
                    claim=str(claim.get("claim") or predicate),
                    subject=str(claim.get("subject") or claim.get("entity_id") or "unknown"),
                    observed_value=claim.get("observed_value", claim.get("value")),
                    source_system=str(claim.get("source_system") or claim.get("source") or "unknown"),
                    source_object=str(claim.get("source_object") or "unknown"),
                    entity_id=str(claim.get("entity_id") or claim.get("subject") or "unknown"),
                    confidence=float(claim.get("confidence") or 0.0),
                    observed_at=_parse_datetime(claim.get("observed_at"), required=True),
                    valid_from=_parse_datetime(claim.get("valid_from"), required=True),
                    expires_at=_parse_datetime(claim.get("expires_at")),
                    data_version=(str(claim.get("data_version")) if claim.get("data_version") else None),
                    content_hash=content_hash,
                    previous_hash=previous_hash,
                    record_hash=record_hash,
                    evidence_metadata=dict(claim.get("metadata") or {}),
                    created_at=datetime.utcnow(),
                )
                session.add(record)
                by_evidence_id[evidence_id] = record
                previous_hash = record_hash
                inserted += 1

            await session.flush()
            relation_inserted = 0
            for relation in evidence_graph.get("relations") or []:
                source = by_evidence_id.get(str(relation.get("source_evidence_id") or ""))
                target = by_evidence_id.get(str(relation.get("target_evidence_id") or ""))
                relation_type = str(relation.get("relation_type") or "")
                if source is None or target is None or not relation_type:
                    continue
                relation_id = _stable_id(
                    "AERR",
                    tenant_id,
                    task_id,
                    source.record_id,
                    target.record_id,
                    relation_type,
                )
                if await session.get(AgentEvidenceRelationRecord, relation_id) is not None:
                    continue
                session.add(
                    AgentEvidenceRelationRecord(
                        relation_id=relation_id,
                        tenant_id=tenant_id,
                        task_id=task_id,
                        source_record_id=source.record_id,
                        target_record_id=target.record_id,
                        relation_type=relation_type,
                        created_at=datetime.utcnow(),
                    )
                )
                relation_inserted += 1

            decision_record_id = None
            if verification_result:
                cited_ids = sorted(
                    {str(item) for item in verification_result.get("cited_evidence_ids") or []}
                )
                unknown_citations = sorted(set(cited_ids) - set(by_evidence_id))
                if unknown_citations:
                    raise ValueError(
                        f"Decision cites evidence outside the persisted task graph: {unknown_citations}"
                    )
                status = str(verification_result.get("status") or "unknown")
                if status == "pass" and not cited_ids:
                    raise ValueError("A passing Agent decision must cite persisted evidence")
                reason_codes = sorted(
                    {
                        str(issue.get("code") or "UNKNOWN")
                        for issue in verification_result.get("issues") or []
                    }
                )
                decision_id = _stable_id(
                    "DEC",
                    task_id,
                    decision_type,
                    status,
                    json.dumps(reason_codes),
                    json.dumps(cited_ids),
                )
                decision_record_id = _stable_id("ADR", tenant_id, task_id, decision_id)
                if await session.get(AgentDecisionRecord, decision_record_id) is None:
                    session.add(
                        AgentDecisionRecord(
                            record_id=decision_record_id,
                            tenant_id=tenant_id,
                            task_id=task_id,
                            decision_id=decision_id,
                            decision_type=decision_type,
                            status=status,
                            reason_codes=reason_codes,
                            cited_evidence_ids=cited_ids,
                            decision_payload=dict(verification_result),
                            verifier=verifier,
                            trace_id=trace_id or None,
                            created_at=datetime.utcnow(),
                        )
                    )
            await session.commit()

    return {
        "task_id": task_id,
        "inserted_claims": inserted,
        "inserted_relations": relation_inserted,
        "decision_record_id": decision_record_id,
        "last_record_hash": previous_hash,
    }


async def load_persisted_evidence_graph(
    task_id: str,
    *,
    tenant_id: str,
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    factory = resolve_session_factory(session_factory)
    with tenant_scope(tenant_id):
        async with factory() as session:
            records = (
                await session.execute(
                    select(AgentEvidenceRecord)
                    .where(
                        AgentEvidenceRecord.tenant_id == tenant_id,
                        AgentEvidenceRecord.task_id == task_id,
                    )
                    .order_by(AgentEvidenceRecord.sequence)
                )
            ).scalars().all()
            relations = (
                await session.execute(
                    select(AgentEvidenceRelationRecord).where(
                        AgentEvidenceRelationRecord.tenant_id == tenant_id,
                        AgentEvidenceRelationRecord.task_id == task_id,
                    )
                )
            ).scalars().all()
            decisions = (
                await session.execute(
                    select(AgentDecisionRecord)
                    .where(
                        AgentDecisionRecord.tenant_id == tenant_id,
                        AgentDecisionRecord.task_id == task_id,
                    )
                    .order_by(AgentDecisionRecord.created_at)
                )
            ).scalars().all()

    record_by_id = {item.record_id: item for item in records}
    return {
        "task_id": task_id,
        "claims": [
            {
                "evidence_id": item.evidence_id,
                "predicate": item.predicate,
                "claim": item.claim,
                "subject": item.subject,
                "observed_value": item.observed_value,
                "source_system": item.source_system,
                "source_object": item.source_object,
                "entity_id": item.entity_id,
                "confidence": item.confidence,
                "observed_at": item.observed_at.isoformat(),
                "valid_from": item.valid_from.isoformat(),
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "content_hash": item.content_hash,
                "record_hash": item.record_hash,
                "previous_hash": item.previous_hash,
                "sequence": item.sequence,
                "metadata": item.evidence_metadata or {},
            }
            for item in records
        ],
        "relations": [
            {
                "source_evidence_id": record_by_id[item.source_record_id].evidence_id,
                "target_evidence_id": record_by_id[item.target_record_id].evidence_id,
                "relation_type": item.relation_type,
            }
            for item in relations
            if item.source_record_id in record_by_id and item.target_record_id in record_by_id
        ],
        "decisions": [
            {
                "decision_id": item.decision_id,
                "decision_type": item.decision_type,
                "status": item.status,
                "reason_codes": item.reason_codes,
                "cited_evidence_ids": item.cited_evidence_ids,
                "verifier": item.verifier,
                "created_at": item.created_at.isoformat(),
            }
            for item in decisions
        ],
        "integrity": verify_persisted_evidence_chain(records),
    }


def verify_persisted_evidence_chain(records: list[AgentEvidenceRecord]) -> dict[str, Any]:
    previous_hash: str | None = None
    failures: list[int] = []
    for record in sorted(records, key=lambda item: item.sequence):
        expected = _record_hash(
            task_id=record.task_id,
            sequence=record.sequence,
            evidence_id=record.evidence_id,
            predicate=record.predicate,
            content_hash=record.content_hash,
            previous_hash=previous_hash,
        )
        if record.previous_hash != previous_hash or record.record_hash != expected:
            failures.append(record.sequence)
        previous_hash = record.record_hash
    return {
        "valid": not failures,
        "record_count": len(records),
        "invalid_sequences": failures,
        "last_record_hash": previous_hash,
    }
