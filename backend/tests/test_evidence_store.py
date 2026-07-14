from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.evidence_graph import (
    add_evidence,
    add_evidence_relation,
    detect_evidence_conflicts,
)
from app.agent.evidence_store import (
    load_persisted_evidence_graph,
    persist_evidence_bundle,
)
from app.db.database import Base
from app.db.models import AgentEvidenceRecord


@pytest.fixture
async def evidence_session_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'evidence-store.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _graph():
    graph = {"task_id": "task-evidence-1", "claims": [], "relations": []}
    graph = add_evidence(
        graph,
        predicate="order.identity",
        subject="123456",
        value="123456",
        source="MINI_ERP",
        source_system="MINI_ERP",
        source_object="Order",
        entity_id="123456",
        confidence=1.0,
    )
    graph = add_evidence(
        graph,
        predicate="policy.decision",
        subject="123456",
        value={"effect": "allow"},
        source="policy_as_code",
        entity_id="123456",
        confidence=1.0,
    )
    return add_evidence_relation(
        graph,
        source_predicate="order.identity",
        target_predicate="policy.decision",
        relation_type="governed_by",
    )


@pytest.mark.asyncio
async def test_evidence_graph_is_append_only_queryable_and_hash_chained(
    evidence_session_factory,
):
    graph = _graph()
    cited_ids = [item["evidence_id"] for item in graph["claims"]]
    task_spec = {
        "task_id": "task-evidence-1",
        "scenario_id": "refund",
    }
    first = await persist_evidence_bundle(
        evidence_graph=graph,
        task_spec=task_spec,
        verification_result={
            "status": "pass",
            "issues": [],
            "cited_evidence_ids": cited_ids,
        },
        tenant_id="default",
        thread_id="thread-evidence-1",
        trace_id="trace-evidence-1",
        session_factory=evidence_session_factory,
    )
    replay = await persist_evidence_bundle(
        evidence_graph=graph,
        task_spec=task_spec,
        verification_result={
            "status": "pass",
            "issues": [],
            "cited_evidence_ids": cited_ids,
        },
        tenant_id="default",
        session_factory=evidence_session_factory,
    )
    loaded = await load_persisted_evidence_graph(
        "task-evidence-1",
        tenant_id="default",
        session_factory=evidence_session_factory,
    )

    assert first["inserted_claims"] == 2
    assert replay["inserted_claims"] == 0
    assert loaded["integrity"]["valid"] is True
    assert loaded["integrity"]["record_count"] == 2
    assert loaded["relations"][0]["relation_type"] == "governed_by"
    assert loaded["decisions"][0]["cited_evidence_ids"] == sorted(cited_ids)


@pytest.mark.asyncio
async def test_passing_decision_without_evidence_citations_is_rejected(
    evidence_session_factory,
):
    with pytest.raises(ValueError, match="must cite persisted evidence"):
        await persist_evidence_bundle(
            evidence_graph=_graph(),
            task_spec={"task_id": "task-evidence-1", "scenario_id": "refund"},
            verification_result={"status": "pass", "issues": []},
            tenant_id="default",
            session_factory=evidence_session_factory,
        )


@pytest.mark.asyncio
async def test_hash_chain_detects_persisted_record_tampering(evidence_session_factory):
    graph = _graph()
    await persist_evidence_bundle(
        evidence_graph=graph,
        task_spec={"task_id": "task-evidence-1", "scenario_id": "refund"},
        tenant_id="default",
        session_factory=evidence_session_factory,
    )
    async with evidence_session_factory() as session:
        record = await session.scalar(
            select(AgentEvidenceRecord).where(AgentEvidenceRecord.sequence == 1)
        )
        record.record_hash = "0" * 64
        await session.commit()
    loaded = await load_persisted_evidence_graph(
        "task-evidence-1",
        tenant_id="default",
        session_factory=evidence_session_factory,
    )
    assert loaded["integrity"]["valid"] is False


def test_fresh_conflicting_evidence_is_detected():
    now = datetime.now(timezone.utc)
    graph = _graph()
    graph["claims"].append(
        {
            **graph["claims"][0],
            "evidence_id": "ev-conflict",
            "observed_value": "654321",
            "value": "654321",
            "content_hash": "different",
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
        }
    )
    conflicts = detect_evidence_conflicts(graph, now=now)
    assert conflicts[0]["predicate"] == "order.identity"
