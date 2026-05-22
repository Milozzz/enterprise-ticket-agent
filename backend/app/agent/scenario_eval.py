"""Scenario-level evals for configurable runtime workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.generic_runtime import run_configured_scenario
from app.agent.scenario_registry import get_default_registry


DEFAULT_SCENARIO_EVALS: dict[str, list[dict[str, Any]]] = {
    "permission_request": [
        {
            "id": "permission_admin_github",
            "message": "permission request GitHub admin access for production release",
            "expect": {
                "intent": "permission_request",
                "approval_required": True,
                "permission_system": "GitHub",
                "permission_level": "admin",
            },
        }
    ],
    "reimbursement": [
        {
            "id": "reimbursement_travel_1200",
            "message": "reimbursement 1200 travel expense invoice",
            "expect": {
                "intent": "reimbursement",
                "approval_required": True,
                "reimbursement_amount": 1200.0,
                "reimbursement_category": "travel",
            },
        }
    ],
}


@dataclass(frozen=True)
class ScenarioEvalCaseResult:
    case_id: str
    passed: bool
    message: str
    errors: tuple[str, ...]
    output: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "message": self.message,
            "errors": list(self.errors),
            "output": self.output,
        }


async def run_scenario_eval(scenario_id: str) -> dict[str, Any]:
    scenario = get_default_registry().get(scenario_id)
    cases = DEFAULT_SCENARIO_EVALS.get(scenario.id, [])
    if not cases:
        return {
            "scenario_id": scenario.id,
            "case_count": 0,
            "passed_count": 0,
            "pass_rate": 0,
            "results": [],
            "message": "No scenario-level eval cases are configured for this scenario.",
        }

    results = []
    for case in cases:
        output = await run_configured_scenario(
            {
                "messages": [{"role": "user", "content": case["message"]}],
                "user_id": "eval-user",
                "user_role": "USER",
                "thread_id": f"eval-{scenario.id}-{case['id']}",
                "trace_id": f"eval-{scenario.id}-{case['id']}",
            },
            scenario.id,
            dry_run=True,
        )
        errors = _compare_expectations(case["expect"], output)
        results.append(
            ScenarioEvalCaseResult(
                case_id=case["id"],
                passed=not errors,
                message=case["message"],
                errors=tuple(errors),
                output=_compact_output(output),
            )
        )

    passed_count = sum(1 for result in results if result.passed)
    return {
        "scenario_id": scenario.id,
        "case_count": len(results),
        "passed_count": passed_count,
        "pass_rate": round(passed_count / len(results), 4),
        "results": [result.to_dict() for result in results],
    }


def list_scenario_eval_catalog() -> dict[str, Any]:
    return {
        scenario_id: [{"id": case["id"], "message": case["message"], "expect": case["expect"]} for case in cases]
        for scenario_id, cases in DEFAULT_SCENARIO_EVALS.items()
    }


def _compare_expectations(expected: dict[str, Any], output: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, expected_value in expected.items():
        actual_value = output.get(key)
        if actual_value != expected_value:
            errors.append(f"{key}: expected {expected_value!r}, got {actual_value!r}")
    return errors


def _compact_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": output.get("intent"),
        "scenario_id": output.get("scenario_id"),
        "approval_required": output.get("approval_required"),
        "business_request": output.get("business_request"),
        "policy_events": output.get("policy_events"),
        "tool_gateway_events": output.get("tool_gateway_events"),
        "reply_text": output.get("reply_text"),
    }
