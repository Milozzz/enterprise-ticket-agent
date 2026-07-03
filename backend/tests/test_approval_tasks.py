from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.approval_tasks import (
    complete_approval_task,
    ensure_approval_task,
    escalate_overdue_tasks,
)
from app.db.models import ApprovalTask, AuditLog


@pytest.fixture
async def approval_session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'approval.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(ApprovalTask.__table__.create)
        await connection.run_sync(AuditLog.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_approval_task_is_idempotent_and_completes(approval_session_factory):
    values = dict(
        task_key="REQ-1:manager",
        request_id="REQ-1",
        scenario_id="reimbursement",
        approval_type="expense",
        stage_id="manager",
        stage_name="Manager review",
        requester_id="employee-1",
        requester_role="USER",
        assigned_roles=["MANAGER"],
        thread_id="thread-approval",
        business_payload={"amount": 1200},
        sla_minutes=30,
        tenant_id="default",
        session_factory=approval_session_factory,
    )
    first = await ensure_approval_task(**values)
    second = await ensure_approval_task(**values)
    assert first.id == second.id
    assert first.status == "pending"

    completed = await complete_approval_task(
        values["task_key"],
        action="approve",
        reviewer_id="manager-1",
        comment="within policy",
        tenant_id="default",
        session_factory=approval_session_factory,
    )
    assert completed is not None
    assert completed.status == "approved"
    assert completed.reviewer_comment == "within policy"
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_overdue_task_is_escalated_and_audited(approval_session_factory):
    task = await ensure_approval_task(
        task_key="REQ-2:finance",
        request_id="REQ-2",
        scenario_id="reimbursement",
        approval_type="expense",
        stage_id="finance",
        stage_name="Finance review",
        requester_id="employee-2",
        requester_role="USER",
        assigned_roles=["FINANCE"],
        thread_id="thread-overdue",
        business_payload={"amount": 3000},
        sla_minutes=30,
        tenant_id="default",
        session_factory=approval_session_factory,
    )
    async with approval_session_factory() as session:
        stored = await session.get(ApprovalTask, task.id)
        stored.due_at = datetime.utcnow() - timedelta(minutes=1)
        await session.commit()

    count = await escalate_overdue_tasks(
        tenant_id="default",
        session_factory=approval_session_factory,
    )
    assert count == 1
    async with approval_session_factory() as session:
        stored = await session.get(ApprovalTask, task.id)
        assert stored.status == "escalated"
        assert stored.escalation_level == 1
