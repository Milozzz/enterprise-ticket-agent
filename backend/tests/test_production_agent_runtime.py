from __future__ import annotations

import pytest

from app.agent.evidence_graph import collect_refund_evidence
from app.agent.dynamic_plan_runtime import (
    dynamic_plan_dispatch_node,
    next_ready_plan_step,
    plan_is_executable,
)
from app.agent.execution_governance import (
    PlanExecutionBlocked,
    authorize_plan_step,
    initialize_execution_budget,
)
from app.agent.nodes.reconciliation import reconcile_refund_node
from app.agent.plan_graph import build_plan_graph, mark_plan_steps
from app.agent.scenario_graph_runtime import build_declarative_scenario_graph
from app.agent.scenario_registry import get_default_registry
from app.agent.scenario_validation import validate_scenario_config
from app.agent.task_spec import build_task_spec


def _refund_contract():
    scenario = get_default_registry().get("refund")
    state = {
        "messages": [{"role": "user", "content": "订单 123456 申请退款"}],
        "thread_id": "production-contract",
        "trace_id": "trace-production-contract",
        "tenant_id": "default",
        "user_id": "3",
        "order_id": "123456",
        "order_detail": {"sourceSystem": "MINI_ERP"},
        "order_amount": 299,
        "currency": "CNY",
        "risk_score": 5,
        "policy_events": [{"effect": "allow"}],
    }
    task = build_task_spec(state, scenario)
    plan = build_plan_graph(task)
    return state, task, plan


def test_every_active_scenario_is_valid_declarative_v3():
    for scenario in get_default_registry().list():
        if scenario.status != "active":
            continue
        assert scenario.runtime["schema_version"] == "3"
        assert validate_scenario_config(scenario).valid
        assert build_declarative_scenario_graph(scenario) is not None


def test_verified_precedent_is_consumed_before_planning():
    _, task, base_plan = _refund_contract()
    precedent = {
        "id": 71,
        "attributes": {
            "scenario_id": "refund",
            "steps": [
                {**step, "status": "planned"} for step in base_plan["steps"]
            ],
        },
    }
    reused = build_plan_graph(task, precedents=[precedent])

    assert reused["generation_source"] == "verified_precedent"
    assert reused["precedent_ids"] == [71]


def test_side_effecting_step_requires_policy_evidence_and_budget():
    state, task, plan = _refund_contract()
    plan = mark_plan_steps(
        plan,
        {
            "understand": "completed",
            "load_order": "completed",
            "assess_risk": "completed",
            "verify": "completed",
            "approve": "completed",
        },
        graph_status="running",
    )
    governed = {
        **state,
        "task_spec": task,
        "plan_graph": plan,
        "evidence_graph": {"claims": [], "relations": []},
        "execution_budget": initialize_execution_budget(task),
    }

    with pytest.raises(PlanExecutionBlocked, match="policy authorization"):
        authorize_plan_step(governed, "execute_finance")

    evidence = collect_refund_evidence(state)
    governed["evidence_graph"] = evidence
    governed["verification_result"] = {"status": "pass"}
    authorized, _, _ = authorize_plan_step(governed, "execute_finance")
    step = next(item for item in authorized["steps"] if item["step_id"] == "execute_finance")
    assert step["status"] == "running"


def test_evidence_graph_contains_traceable_relations():
    state, _, _ = _refund_contract()
    evidence = collect_refund_evidence(state)

    assert {item["relation_type"] for item in evidence["relations"]} >= {
        "evaluated_by",
        "governed_by",
    }


def test_dynamic_plan_selects_dependency_ready_step_and_has_trusted_handlers():
    _, _, plan = _refund_contract()
    plan = mark_plan_steps(plan, {"understand": "completed"}, graph_status="running")

    assert plan_is_executable(plan) == []
    assert next_ready_plan_step(plan)["step_id"] == "load_order"


@pytest.mark.asyncio
async def test_dynamic_dispatch_executes_task_understanding_as_first_step():
    state, task, plan = _refund_contract()
    result = await dynamic_plan_dispatch_node(
        {
            **state,
            "task_spec": task,
            "plan_graph": plan,
            "execution_budget": initialize_execution_budget(task),
        }
    )

    understand = next(
        item for item in result["plan_graph"]["steps"] if item["step_id"] == "understand"
    )
    assert understand["status"] == "completed"
    assert result["task_spec"]["task_id"] == task["task_id"]


@pytest.mark.asyncio
async def test_dynamic_dispatch_executes_one_step_and_checkpoints_result(monkeypatch):
    state, task, plan = _refund_contract()
    plan = mark_plan_steps(plan, {"understand": "completed"}, graph_status="running")

    async def fake_lookup(_state):
        return {
            "order_id": "123456",
            "order_amount": 299,
            "currency": "CNY",
            "order_detail": {"sourceSystem": "MINI_ERP"},
            "current_step": "order_lookup_done",
        }

    monkeypatch.setattr(
        "app.agent.dynamic_plan_runtime._handler_for_step",
        lambda _step: fake_lookup,
    )
    result = await dynamic_plan_dispatch_node(
        {
            **state,
            "task_spec": task,
            "plan_graph": plan,
            "evidence_graph": {"claims": [], "relations": []},
            "execution_budget": initialize_execution_budget(task),
        }
    )

    load_order = next(
        item for item in result["plan_graph"]["steps"] if item["step_id"] == "load_order"
    )
    assert load_order["status"] == "completed"
    assert "order.identity" in {
        item["predicate"] for item in result["evidence_graph"]["claims"]
    }


@pytest.mark.asyncio
async def test_refund_dry_run_requires_post_execution_reconciliation():
    result = await reconcile_refund_node(
        {
            "saga_status": "DRY_RUN",
            "order_id": "123456",
            "refund_request_id": "REF-DRY",
            "trace_id": "trace-dry",
            "tenant_id": "default",
            "evidence_graph": {"claims": [], "relations": []},
        }
    )

    assert result["reconciliation_result"]["verified"] is True
    assert result["verification_result"]["status"] == "pass"
    assert "finance.reconciliation" in {
        item["predicate"] for item in result["evidence_graph"]["claims"]
    }
