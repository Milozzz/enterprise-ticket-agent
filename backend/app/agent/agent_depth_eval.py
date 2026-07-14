"""Quality gates for bounded autonomy: task, plan, evidence, and verification."""

from __future__ import annotations

from typing import Any

from app.agent.evidence_graph import collect_refund_evidence
from app.agent.plan_graph import build_plan_graph, validate_plan_graph
from app.agent.plan_graph import mark_plan_steps
from app.agent.execution_governance import (
    authorize_plan_step,
    initialize_execution_budget,
)
from app.agent.scenario_registry import get_default_registry
from app.agent.task_spec import build_task_spec
from app.agent.verifier import VerificationStatus, verify_refund_execution


def run_agent_depth_eval() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    messages = {
        "refund": [
            "订单号 123456 申请退款，商品破损",
            "订单号 789012 退款，收到错误商品",
            "订单号 610003 申请退款，质量问题",
        ],
        "permission_request": [
            "申请 GitHub 管理员权限",
            "申请 Jira 只读权限",
            "申请生产数据库写权限",
        ],
        "reimbursement": [
            "报销 1200 元差旅费",
            "报销 88 元餐费",
            "报销 500 元客户招待费",
        ],
    }
    for scenario_id, prompts in messages.items():
        scenario = get_default_registry().get(scenario_id)
        for index, message in enumerate(prompts, start=1):
            state = {
                "messages": [{"role": "user", "content": message}],
                "thread_id": f"depth-{scenario_id}-{index}",
                "tenant_id": "default",
                "user_id": "eval-user",
                "order_id": "123456" if scenario_id == "refund" else "",
            }
            task = build_task_spec(state, scenario)
            plan = build_plan_graph(task)
            _add_result(
                results,
                f"TASK-{scenario_id}-{index}",
                "task_spec",
                bool(task["goal"] and task["success_criteria"] and task["constraints"]),
            )
            _add_result(
                results,
                f"PLAN-{scenario_id}-{index}",
                "plan_graph",
                not validate_plan_graph(plan),
            )

    for index in range(1, 7):
        pass_state = _refund_state(order_id=f"PASS-{index}")
        pass_evidence = collect_refund_evidence(pass_state)
        pass_result = verify_refund_execution(pass_state, pass_evidence)
        _add_result(
            results,
            f"VERIFY-PASS-{index}",
            "verification",
            pass_result.status == VerificationStatus.PASS,
        )
        _add_result(
            results,
            f"EVIDENCE-RELATION-{index}",
            "evidence_relations",
            len(pass_evidence.get("relations") or []) >= 2,
        )

        governed_plan = mark_plan_steps(
            pass_state["plan_graph"],
            {"understand": "completed"},
            graph_status="running",
        )
        governed_state = {
            **pass_state,
            "plan_graph": governed_plan,
            "evidence_graph": pass_evidence,
            "execution_budget": initialize_execution_budget(pass_state["task_spec"]),
        }
        try:
            authorized_plan, _, _ = authorize_plan_step(governed_state, "load_order")
            governance_passed = next(
                step for step in authorized_plan["steps"] if step["step_id"] == "load_order"
            )["status"] == "running"
        except Exception:
            governance_passed = False
        _add_result(
            results,
            f"GOVERNANCE-{index}",
            "execution_governance",
            governance_passed,
        )

        replan_state = _refund_state(order_id="")
        replan_evidence = collect_refund_evidence(replan_state)
        replan_result = verify_refund_execution(replan_state, replan_evidence)
        _add_result(
            results,
            f"VERIFY-REPLAN-{index}",
            "replanning",
            replan_result.status == VerificationStatus.REPLAN,
        )

        blocked_state = _refund_state(order_id=f"BLOCK-{index}")
        blocked_state.update({"requires_human_approval": True, "human_decision": "reject"})
        blocked_evidence = collect_refund_evidence(blocked_state)
        blocked_result = verify_refund_execution(blocked_state, blocked_evidence)
        _add_result(
            results,
            f"VERIFY-BLOCK-{index}",
            "safety_block",
            blocked_result.status == VerificationStatus.BLOCK
            and any(issue.code == "POLICY_CONFLICT" for issue in blocked_result.issues),
        )

    passed = sum(1 for result in results if result["passed"])
    dimensions = sorted({result["dimension"] for result in results})
    return {
        "suite": "bounded-autonomy-depth-v1",
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "pass_rate": round(passed / len(results), 4),
        "dimensions": {
            dimension: {
                "case_count": sum(1 for result in results if result["dimension"] == dimension),
                "pass_rate": round(
                    sum(
                        1
                        for result in results
                        if result["dimension"] == dimension and result["passed"]
                    )
                    / sum(1 for result in results if result["dimension"] == dimension),
                    4,
                ),
            }
            for dimension in dimensions
        },
        "failures": [result for result in results if not result["passed"]],
    }


def _refund_state(*, order_id: str) -> dict[str, Any]:
    task = build_task_spec(
        {
            "messages": [{"role": "user", "content": f"订单 {order_id} 申请退款"}],
            "thread_id": f"verify-{order_id or 'missing'}",
            "tenant_id": "default",
            "user_id": "eval-user",
            "order_id": order_id,
        },
        get_default_registry().get("refund"),
    )
    plan = build_plan_graph(task)
    return {
        "task_spec": task,
        "plan_graph": plan,
        "order_id": order_id,
        "order_detail": {"sourceSystem": "MINI_ERP"},
        "order_amount": 299.0,
        "currency": "CNY",
        "return_validation": {
            "valid": True,
            "rma_id": f"RMA-{order_id}",
            "warehouse_id": "WH-001",
            "status": "INSPECTED",
            "received": True,
            "received_at": "2026-07-13T00:00:00+00:00",
            "inspection_id": f"INSP-{order_id}",
            "inspection_result": "ACCEPTED",
            "restockable": True,
        },
        "inventory_inspection": {
            "consistent": True,
            "warehouse_id": "WH-001",
            "items": [{"product_id": "P-001", "quantity_on_hand": 10}],
        },
        "risk_score": 10,
        "risk_level": "low",
        "requires_human_approval": False,
        "policy_events": [{"policy_version": "eval-v1", "effect": "allow"}],
        "trace_id": f"trace-{order_id or 'missing'}",
        "replan_count": 0,
    }


def _add_result(
    results: list[dict[str, Any]],
    case_id: str,
    dimension: str,
    passed: bool,
) -> None:
    results.append(
        {
            "case_id": case_id,
            "dimension": dimension,
            "passed": bool(passed),
        }
    )
