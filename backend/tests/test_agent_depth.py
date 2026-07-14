from app.agent.agent_depth_eval import run_agent_depth_eval
from app.agent.plan_graph import SPECIALIST_CATALOG, build_plan_graph, validate_plan_graph
from app.agent.scenario_registry import get_default_registry
from app.agent.task_spec import build_task_spec
from app.agent.dynamic_planner import validate_plan_candidate


def test_task_spec_and_plan_graph_form_one_contract():
    task = build_task_spec(
        {
            "messages": [{"role": "user", "content": "订单 123456 申请退款"}],
            "thread_id": "task-contract",
            "tenant_id": "default",
            "user_id": "3",
            "order_id": "123456",
        },
        get_default_registry().get("refund"),
    )
    plan = build_plan_graph(task)

    assert task["task_id"] == plan["task_id"]
    assert task["success_criteria"]
    assert not validate_plan_graph(plan)
    assert {step["specialist"] for step in plan["steps"]} <= set(SPECIALIST_CATALOG)


def test_agent_depth_release_gate_passes_all_cases():
    report = run_agent_depth_eval()

    assert report["case_count"] == 48
    assert report["pass_rate"] == 1.0, report["failures"]


def test_dynamic_planner_cannot_remove_required_evidence_or_approval():
    task = build_task_spec(
        {
            "messages": [{"role": "user", "content": "订单 123456 申请退款"}],
            "thread_id": "unsafe-plan",
            "tenant_id": "default",
            "user_id": "3",
            "order_id": "123456",
        },
        get_default_registry().get("refund"),
    )
    unsafe_plan = {
        "task_id": task["task_id"],
        "scenario_id": "refund",
        "steps": [
            {
                "step_id": "write",
                "specialist": "executor",
                "capability": "refund_finance_saga",
                "tool": "execute_refund",
                "side_effect": "write",
                "approval_required": False,
            }
        ],
    }

    errors = validate_plan_candidate(task, unsafe_plan)
    assert any("approval" in error for error in errors)
    assert any("success evidence" in error for error in errors)


def test_plan_graph_rejects_dependency_cycles():
    cyclic = {
        "steps": [
            {
                "step_id": "a",
                "specialist": "supervisor",
                "capability": "a",
                "dependencies": ["b"],
                "side_effect": "none",
            },
            {
                "step_id": "b",
                "specialist": "verifier",
                "capability": "b",
                "dependencies": ["a"],
                "side_effect": "decision",
            },
        ]
    }

    assert "plan dependencies must form an acyclic graph" in validate_plan_graph(cyclic)
