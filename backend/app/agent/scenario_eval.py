"""Scenario-level evals for configurable runtime workflows."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agent.generic_runtime import run_configured_scenario
from app.agent.scenario_registry import get_default_registry


_TRAJECTORY_DATASET = Path(__file__).resolve().parents[2] / "evals" / "trajectory_dataset.json"


def _load_trajectory_cases() -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(_TRAJECTORY_DATASET.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in payload.get("cases", []):
        grouped.setdefault(str(case["scenario_id"]), []).append(case)
    return grouped


DEFAULT_SCENARIO_EVALS = _load_trajectory_cases()


@dataclass(frozen=True)
class ScenarioEvalCaseResult:
    case_id: str
    passed: bool
    message: str
    errors: tuple[str, ...]
    output: dict[str, Any]
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "message": self.message,
            "errors": list(self.errors),
            "output": self.output,
            "duration_ms": self.duration_ms,
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
        started = perf_counter()
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
        output["trajectory"] = _build_trajectory(case["message"], scenario.id, output)
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        errors = _compare_expectations(case["expect"], output)
        results.append(
            ScenarioEvalCaseResult(
                case_id=case["id"],
                passed=not errors,
                message=case["message"],
                errors=tuple(errors),
                output=_compact_output(output),
                duration_ms=duration_ms,
            )
        )

    passed_count = sum(1 for result in results if result.passed)
    total_duration_ms = sum(result.duration_ms for result in results)
    return {
        "scenario_id": scenario.id,
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "pass_rate": round(passed_count / len(results), 4),
        "avg_latency_ms": round(total_duration_ms / len(results), 2) if results else 0,
        "total_duration_ms": total_duration_ms,
        "results": [result.to_dict() for result in results],
    }


def list_scenario_eval_catalog() -> dict[str, Any]:
    return {
        scenario_id: {
            "case_count": len(cases),
            "cases": [{"id": case["id"], "message": case["message"], "expect": case["expect"]} for case in cases],
        }
        for scenario_id, cases in DEFAULT_SCENARIO_EVALS.items()
    }


async def run_all_scenario_evals() -> dict[str, Any]:
    scenario_ids = sorted(DEFAULT_SCENARIO_EVALS)
    scenario_reports = [await run_scenario_eval(scenario_id) for scenario_id in scenario_ids]
    total_cases = sum(report["case_count"] for report in scenario_reports)
    passed_cases = sum(report["passed_count"] for report in scenario_reports)
    failed_cases = sum(report["failed_count"] for report in scenario_reports)
    total_duration_ms = sum(report["total_duration_ms"] for report in scenario_reports)
    return {
        "scenario_count": len(scenario_reports),
        "case_count": total_cases,
        "passed_count": passed_cases,
        "failed_count": failed_cases,
        "pass_rate": round(passed_cases / total_cases, 4) if total_cases else 0,
        "avg_latency_ms": round(total_duration_ms / total_cases, 2) if total_cases else 0,
        "total_duration_ms": total_duration_ms,
        "scenarios": scenario_reports,
    }


def _compare_expectations(expected: dict[str, Any], output: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, expected_value in expected.items():
        actual_value = output.get("trajectory", {}).get(key) if key in {
            "route", "tool_sequence", "final_status"
        } else output.get(key)
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
        "trajectory": output.get("trajectory"),
    }


def _build_trajectory(message: str, scenario_id: str, output: dict[str, Any]) -> dict[str, Any]:
    match = get_default_registry().match(message)
    approval_required = bool(output.get("approval_required"))
    route = [
        "supervisor_router",
        scenario_id,
        "generic_human_review" if approval_required else "finalize_business_request",
    ]
    return {
        "matched_scenario": match.scenario_id,
        "route": route,
        "tool_sequence": [
            event.get("tool")
            for event in output.get("tool_gateway_events") or []
            if event.get("tool")
        ],
        "policy_sequence": [
            event.get("policy_version")
            for event in output.get("policy_events") or []
            if event.get("policy_version")
        ],
        "final_status": (output.get("business_request") or {}).get("status"),
    }
