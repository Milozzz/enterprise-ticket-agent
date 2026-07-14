from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.agent_slo import compute_agent_slo
from app.agent.evidence_graph import collect_refund_evidence
from app.agent.plan_graph import build_plan_graph
from app.agent.scenario_registry import get_default_registry
from app.agent.task_spec import build_task_spec
from app.db.models import ApprovalDecision, ApprovalTask, AuditLog, Base, LLMUsageRecord


@pytest.mark.asyncio
async def test_agent_slo_aggregates_task_quality_cost_and_latency():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        AuditLog.__table__,
        ApprovalTask.__table__,
        ApprovalDecision.__table__,
        LLMUsageRecord.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    task = build_task_spec(
        {
            "messages": [{"role": "user", "content": "Refund order 123456"}],
            "thread_id": "slo-task",
            "tenant_id": "default",
            "user_id": "3",
            "order_id": "123456",
        },
        get_default_registry().get("refund"),
    )
    plan = build_plan_graph(task)
    completed_plan = {
        **plan,
        "status": "completed",
        "steps": [{**step, "status": "completed"} for step in plan["steps"]],
    }
    state = {
        "task_spec": task,
        "order_id": "123456",
        "order_detail": {"sourceSystem": "MINI_ERP"},
        "order_amount": 299.0,
        "currency": "CNY",
        "return_validation": {
            "rma_id": "RMA-1",
            "status": "INSPECTED",
            "received": True,
            "inspection_id": "INSP-1",
            "inspection_result": "ACCEPTED",
            "restockable": True,
            "warehouse_id": "WH-1",
        },
        "inventory_inspection": {"consistent": True, "warehouse_id": "WH-1"},
        "risk_score": 10,
        "policy_events": [{"effect": "allow"}],
        "saga_status": "COMPLETED",
        "reconciliation_result": {"consistent": True},
        "inventory_restoration": {"success": True, "movement_id": "MV-1"},
        "notification_sent": True,
        "notification_email_id": "MSG-1",
        "final_reconciliation": {"verified": True},
        "trace_id": "trace-slo",
    }
    evidence = collect_refund_evidence(state)
    now = datetime.utcnow()
    async with session_factory() as session:
        session.add_all(
            [
                AuditLog(
                    tenant_id="default",
                    thread_id="slo-task",
                    trace_id="trace-slo",
                    node_name="dynamic_dispatch",
                    event_type="on_chain_end",
                    output_data={
                        "task_spec": task,
                        "plan_graph": completed_plan,
                        "evidence_graph": evidence,
                        "final_reconciliation": {"verified": True},
                        "tool_gateway_events": [
                            {
                                "tool": "lookup_order",
                                "authorized": True,
                                "success": True,
                                "policy": {"effect": "allow"},
                            }
                        ],
                    },
                    duration_ms=900,
                    success=True,
                    created_at=now,
                ),
                AuditLog(
                    tenant_id="default",
                    thread_id="slo-task",
                    trace_id="trace-slo",
                    node_name="stream_first_result",
                    event_type="metric",
                    output_data={"metric": "time_to_first_result_ms", "value": 120},
                    duration_ms=120,
                    success=True,
                    created_at=now + timedelta(milliseconds=120),
                ),
                ApprovalDecision(
                    tenant_id="default",
                    request_id="REQ-SLO-1",
                    scenario_id="refund",
                    approval_type="refund_review",
                    action="reject",
                    status="rejected",
                    reviewer_id="manager-1",
                    reviewer_role="MANAGER",
                    policy_event={"effect": "allow"},
                    thread_id="slo-task",
                    created_at=now,
                ),
                LLMUsageRecord(
                    tenant_id="default",
                    thread_id="slo-task",
                    trace_id="trace-slo",
                    node_name="classify_intent",
                    provider="test",
                    model="test-model",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    total_cost_usd=Decimal("0.01000000"),
                    latency_ms=50,
                    success=True,
                    fallback_index=0,
                    created_at=now,
                ),
            ]
        )
        await session.commit()
        report = await compute_agent_slo(session, hours=24)

    assert report["task_quality"]["task_success_rate"] == 1.0
    assert report["task_quality"]["plan_validity_rate"] == 1.0
    assert report["task_quality"]["tool_selection_accuracy"] == 1.0
    assert report["task_quality"]["human_override_rate"] == 1.0
    assert report["task_quality"]["policy_violation_rate"] == 0.0
    assert report["task_quality"]["cost_per_successful_task_usd"] == 0.01
    assert report["latency"]["time_to_first_result_ms"]["p50"] == 120.0
    assert report["llm"]["total_tokens"] == 15
    await engine.dispose()
