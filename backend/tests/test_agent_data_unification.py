from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.graph import build_graph
import app.agent.nodes.generic_approval as generic_approval
from app.db.database import Base
from app.db.models import (
    AccessRequestRecord,
    CostCenter,
    ErpBusinessRequestStatus,
    ReimbursementClaim,
)


@pytest.mark.asyncio
async def test_configured_permission_workflow_resumes_each_stage_and_persists_canonical_record(
    tmp_path,
    monkeypatch,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-unification.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(generic_approval, "AsyncSessionLocal", session_factory)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "permission-unification"}}
    first = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="申请 GitHub admin 权限用于生产发布")],
            "user_id": "employee-3",
            "user_role": "USER",
            "tenant_id": "TENANT-DEMO-COMMERCE",
            "thread_id": "permission-unification",
            "trace_id": "trace-permission-unification",
            "ui_events": [],
            "approval_history": [],
        },
        config=config,
    )
    assert first["scenario_id"] == "permission_request"
    assert "__interrupt__" in first
    assert first["__interrupt__"][0].value["stage_id"] == "manager_review"

    second = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "reviewer_id": "manager-1",
                "reviewer_role": "MANAGER",
            }
        ),
        config=config,
    )
    assert "__interrupt__" in second
    assert second["__interrupt__"][0].value["stage_id"] == "security_review"

    final = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "reviewer_id": "security-1",
                "reviewer_role": "SECURITY",
            }
        ),
        config=config,
    )
    assert final["business_request"]["status"] == "approved"
    assert len(final["approval_history"]) == 2

    request_id = final["business_request"]["requestId"]
    async with session_factory() as session:
        record = await session.get(AccessRequestRecord, request_id)
        assert record is not None
        assert record.status == ErpBusinessRequestStatus.APPROVED
        assert record.granted_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_reimbursement_workflow_persists_decimal_claim_after_finance_approval(
    tmp_path,
    monkeypatch,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reimbursement.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(generic_approval, "AsyncSessionLocal", session_factory)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(
            CostCenter(
                tenant_id="TENANT-DEMO-COMMERCE",
                cost_center_id="CC-SUPPORT",
                department_id="DEPT-SUPPORT",
                name="Support",
            )
        )
        await session.commit()

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "reimbursement-unification"}}
    first = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="报销 1200.50 元差旅费，客户现场支持")],
            "user_id": "employee-3",
            "user_role": "USER",
            "tenant_id": "TENANT-DEMO-COMMERCE",
            "thread_id": "reimbursement-unification",
            "trace_id": "trace-reimbursement-unification",
            "ui_events": [],
            "approval_history": [],
        },
        config=config,
    )
    assert first["scenario_id"] == "reimbursement"
    assert first["__interrupt__"][0].value["stage_id"] == "manager_review"

    second = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "reviewer_id": "manager-1",
                "reviewer_role": "MANAGER",
            }
        ),
        config=config,
    )
    assert second["__interrupt__"][0].value["stage_id"] == "finance_review"

    final = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "reviewer_id": "finance-1",
                "reviewer_role": "FINANCE",
            }
        ),
        config=config,
    )
    request_id = final["business_request"]["requestId"]
    async with session_factory() as session:
        claim = await session.get(ReimbursementClaim, request_id)
        assert claim is not None
        assert str(claim.amount) == "1200.50"
        assert claim.status == ErpBusinessRequestStatus.APPROVED

    await engine.dispose()
