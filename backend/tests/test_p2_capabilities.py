from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.agent_jobs import claim_next_job, enqueue_agent_job, finish_job
from app.agent.long_term_memory import list_active_memories, upsert_long_term_memory
from app.agent.subgraphs import build_policy_qa_agent, build_risk_agent
from app.db.models import AgentExecutionJob, UserLongTermMemory
from app.llm.prompt_registry import PromptRegistry
from app.core import policy as policy_module


@pytest.fixture
async def p2_session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'p2.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(UserLongTermMemory.__table__.create)
        await connection.run_sync(AgentExecutionJob.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_prompt_canary_assignment_is_deterministic():
    registry = PromptRegistry(
        versions_json='{"answer_policy":{"v2":"canary template"}}',
        rollouts_json='{"answer_policy":{"stable":"builtin-v1","canary":"v2","percent":100}}',
    )
    first = registry.select("answer_policy", "stable template", routing_key="thread-1")
    second = registry.select("answer_policy", "stable template", routing_key="thread-1")
    assert first == second
    assert first.version == "v2"
    assert first.variant == "canary"
    assert first.template == "canary template"


def test_policy_canary_assignment_is_deterministic(monkeypatch):
    candidate = {**policy_module.load_policy(), "version": "canary-v2"}
    monkeypatch.setattr(
        policy_module,
        "get_settings",
        lambda: SimpleNamespace(
            policy_canary_json=json.dumps(candidate),
            policy_canary_percent=100,
        ),
    )
    first = policy_module.evaluate_action_policy(
        "USER",
        "lookup_order",
        routing_key="thread-1",
    )
    second = policy_module.evaluate_action_policy(
        "USER",
        "lookup_order",
        routing_key="thread-1",
    )
    assert first == second
    assert first.policy_version == "canary-v2"
    assert first.policy_variant == "canary"


def test_specialists_are_real_subgraphs_with_narrow_responsibilities():
    risk_nodes = build_risk_agent().get_graph().nodes
    policy_nodes = build_policy_qa_agent().get_graph().nodes
    assert {"dispatch", "check_risk", "fetch_user_history", "join"} <= set(risk_nodes)
    assert {"retrieve_and_answer", "summarize"} <= set(policy_nodes)
    assert "execute_refund" not in policy_nodes
    assert "execute_refund" not in risk_nodes


@pytest.mark.asyncio
async def test_long_term_memory_upsert_and_retrieval(p2_session_factory):
    first = await upsert_long_term_memory(
        user_id="user-1",
        memory_type="preference",
        memory_key="notification_channel",
        content="User prefers email notifications.",
        attributes={"value": "email"},
        source_thread_id="thread-1",
        tenant_id="default",
        session_factory=p2_session_factory,
    )
    second = await upsert_long_term_memory(
        user_id="user-1",
        memory_type="preference",
        memory_key="notification_channel",
        content="User prefers SMS notifications.",
        attributes={"value": "sms"},
        source_thread_id="thread-2",
        tenant_id="default",
        session_factory=p2_session_factory,
    )
    assert first.id == second.id
    memories = await list_active_memories(
        "user-1",
        tenant_id="default",
        session_factory=p2_session_factory,
    )
    assert len(memories) == 1
    assert memories[0]["attributes"]["value"] == "sms"
    assert memories[0]["source_thread_id"] == "thread-2"


@pytest.mark.asyncio
async def test_agent_job_is_idempotent_claimable_and_completable(p2_session_factory):
    payload = {
        "messages": [{"role": "user", "content": "Query order 123456"}],
        "thread_id": "job-thread-1",
    }
    first, duplicate_first = await enqueue_agent_job(
        payload,
        tenant_id="default",
        requester_id="user-1",
        requester_role="USER",
        idempotency_key="job-key-1",
        session_factory=p2_session_factory,
    )
    second, duplicate_second = await enqueue_agent_job(
        payload,
        tenant_id="default",
        requester_id="user-1",
        requester_role="USER",
        idempotency_key="job-key-1",
        session_factory=p2_session_factory,
    )
    assert duplicate_first is False
    assert duplicate_second is True
    assert first.job_id == second.job_id

    claimed = await claim_next_job(
        tenant_id="default",
        worker_id="worker-a",
        session_factory=p2_session_factory,
    )
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.locked_by == "worker-a"
    assert claimed.attempts == 1

    await finish_job(
        claimed,
        output={"job_status": "succeeded", "state": {"intent": "query_order"}},
        session_factory=p2_session_factory,
    )
    async with p2_session_factory() as session:
        stored = await session.get(AgentExecutionJob, claimed.job_id)
    assert stored.status == "succeeded"
    assert stored.completed_at is not None
    assert stored.output_payload["state"]["intent"] == "query_order"
