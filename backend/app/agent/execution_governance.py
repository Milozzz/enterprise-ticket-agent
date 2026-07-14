"""Durable PlanGraph step authorization and task execution budgets."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Mapping

from app.agent.evidence_graph import collect_refund_evidence, evidence_predicates
from app.agent.plan_graph import evaluate_plan_conditions, validate_plan_graph
from app.agent.utils import get_state_val
from app.llm.task_budget import use_task_budget


class PlanExecutionBlocked(RuntimeError):
    pass


def initialize_execution_budget(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    limits = dict(task_spec.get("budget") or {})
    return {
        "task_id": str(task_spec.get("task_id") or "unknown"),
        "active_elapsed_ms": 0,
        "tool_calls": 0,
        "failures": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "limits": {
            "deadline_ms": max(100, int(limits.get("deadline_ms") or 30000)),
            "max_tool_calls": max(1, int(limits.get("max_tool_calls") or 16)),
            "max_failures": max(0, int(limits.get("max_failures") or 2)),
            "max_llm_calls": max(0, int(limits.get("max_llm_calls") or 4)),
            "max_cost_usd": max(0.0, float(limits.get("max_cost_usd") or 0.05)),
        },
    }


async def bootstrap_execution_budget_node(state) -> dict[str, Any]:
    existing = dict(get_state_val(state, "execution_budget", {}) or {})
    if existing:
        return {"execution_budget": existing}
    return {
        "execution_budget": initialize_execution_budget(
            {
                "task_id": f"bootstrap:{get_state_val(state, 'thread_id', 'unknown')}",
                "budget": {},
            }
        )
    }


def authorize_plan_step(
    state: Mapping[str, Any],
    step_id: str,
) -> tuple[dict[str, Any], dict[str, Any], float]:
    plan = dict(get_state_val(state, "plan_graph", {}) or {})
    errors = validate_plan_graph(plan)
    if errors:
        raise PlanExecutionBlocked("Invalid PlanGraph: " + "; ".join(errors))
    step = next(
        (dict(item) for item in plan.get("steps") or [] if item.get("step_id") == step_id),
        None,
    )
    if step is None:
        raise PlanExecutionBlocked(f"Runtime step '{step_id}' is absent from PlanGraph")

    by_id = {str(item.get("step_id")): dict(item) for item in plan.get("steps") or []}
    incomplete = [
        dependency
        for dependency in step.get("dependencies") or []
        if str(by_id.get(str(dependency), {}).get("status")) != "completed"
    ]
    if incomplete:
        raise PlanExecutionBlocked(
            f"Step '{step_id}' has incomplete dependencies: {', '.join(map(str, incomplete))}"
        )

    budget = dict(
        get_state_val(state, "execution_budget", {})
        or initialize_execution_budget(get_state_val(state, "task_spec", {}) or {})
    )
    limits = dict(budget.get("limits") or {})
    if int(budget.get("active_elapsed_ms") or 0) >= int(limits.get("deadline_ms") or 30000):
        raise PlanExecutionBlocked("Task active-execution deadline exceeded")
    if step.get("tool") and int(budget.get("tool_calls") or 0) >= int(
        limits.get("max_tool_calls") or 16
    ):
        raise PlanExecutionBlocked("Task tool-call budget exhausted")
    if int(budget.get("failures") or 0) > int(limits.get("max_failures") or 2):
        raise PlanExecutionBlocked("Task failure budget exhausted")

    if step.get("side_effect") in {"write", "external"}:
        predicates = evidence_predicates(get_state_val(state, "evidence_graph", {}) or {})
        if "policy.decision" not in predicates:
            raise PlanExecutionBlocked(f"Step '{step_id}' lacks policy authorization evidence")
        if get_state_val(state, "requires_human_approval", False):
            if get_state_val(state, "human_decision", "") != "approve":
                raise PlanExecutionBlocked(f"Step '{step_id}' lacks human approval")
            if "approval.decision" not in predicates:
                raise PlanExecutionBlocked(f"Step '{step_id}' lacks approval evidence")

    condition_failures = evaluate_plan_conditions(
        step.get("preconditions") or [],
        state=state,
    )
    if condition_failures:
        raise PlanExecutionBlocked(
            "; ".join(
                f"{item['code']}: {item['message']}" for item in condition_failures
            )
        )

    plan["steps"] = [
        {**dict(item), "status": "running" if item.get("step_id") == step_id else item.get("status")}
        for item in plan.get("steps") or []
    ]
    plan["status"] = "running"
    return plan, budget, perf_counter()


def finish_plan_step(
    state: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    step_id: str,
    started: float,
) -> dict[str, Any]:
    plan = dict(result.get("plan_graph") or get_state_val(state, "plan_graph", {}) or {})
    budget = dict(
        result.get("execution_budget")
        or get_state_val(state, "execution_budget", {})
        or initialize_execution_budget(get_state_val(state, "task_spec", {}) or {})
    )
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))
    step = next(
        (dict(item) for item in plan.get("steps") or [] if item.get("step_id") == step_id),
        {},
    )
    merged_state = {**dict(state), **dict(result)}
    if str((merged_state.get("task_spec") or {}).get("scenario_id") or "") == "refund":
        merged_state["evidence_graph"] = collect_refund_evidence(merged_state)
    output_failures = _validate_expected_output(
        step.get("expected_output_schema") or {},
        merged_state,
    )
    condition_failures = output_failures + evaluate_plan_conditions(
        step.get("postconditions") or [],
        state=merged_state,
        step_output=result,
    )
    failed = (
        bool(result.get("error_message"))
        or "blocked" in str(result.get("current_step") or "")
        or bool(condition_failures)
    )
    tool_calls = len(list(result.get("tool_gateway_events") or []))
    budget["active_elapsed_ms"] = int(budget.get("active_elapsed_ms") or 0) + elapsed_ms
    budget["tool_calls"] = int(budget.get("tool_calls") or 0) + tool_calls
    budget["failures"] = int(budget.get("failures") or 0) + (1 if failed else 0)

    plan["steps"] = [
        {
            **dict(item),
            "status": (
                "blocked"
                if failed
                else "completed"
                if item.get("step_id") == step_id and item.get("status") == "running"
                else item.get("status")
            ),
        }
        for item in plan.get("steps") or []
    ]
    if failed:
        plan["status"] = "blocked"

    limits = dict(budget.get("limits") or {})
    budget_error = ""
    if budget["active_elapsed_ms"] > int(limits.get("deadline_ms") or 30000):
        budget_error = "Task active-execution deadline exceeded"
    elif budget["tool_calls"] > int(limits.get("max_tool_calls") or 16):
        budget_error = "Task tool-call budget exceeded"
    elif budget["failures"] > int(limits.get("max_failures") or 2):
        budget_error = "Task failure budget exceeded"
    if budget_error:
        plan["status"] = "blocked"

    journal_item = {
        "step_id": step_id,
        "status": "blocked" if failed or budget_error else "completed",
        "duration_ms": elapsed_ms,
        "tool_calls": tool_calls,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "condition_failures": condition_failures,
    }
    condition_error = (
        "; ".join(f"{item['code']}: {item['message']}" for item in condition_failures)
        if condition_failures
        else ""
    )
    return {
        "plan_graph": plan,
        "execution_budget": budget,
        "execution_journal": [journal_item],
        **(
            {"evidence_graph": merged_state["evidence_graph"]}
            if merged_state.get("evidence_graph")
            else {}
        ),
        **(
            {
                "error_message": condition_error,
                "current_step": "plan_postcondition_blocked",
                "verification_result": {
                    "status": "block",
                    "issues": condition_failures,
                },
            }
            if condition_error
            else {}
        ),
        **({"error_message": budget_error, "current_step": "execution_budget_blocked"} if budget_error else {}),
    }


def _validate_expected_output(
    schema: Mapping[str, Any],
    output: Mapping[str, Any],
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for path in schema.get("required") or []:
        current: Any = output
        for part in str(path).split("."):
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(part)
        if current in (None, ""):
            failures.append(
                {
                    "condition_id": f"expected_output:{path}",
                    "code": "TOOL_RESULT_INCONSISTENT",
                    "message": f"Required PlanStep output '{path}' is missing",
                }
            )
    return failures


def governed_node(executable: Any, step_ids: str | list[str]):
    """Wrap a trusted LangGraph node with PlanGraph and budget enforcement."""

    ids = [step_ids] if isinstance(step_ids, str) else list(step_ids)

    async def run(state, config=None):
        working = dict(state)
        starts: list[tuple[str, float]] = []
        try:
            for index, step_id in enumerate(ids):
                plan, budget, started = authorize_plan_step(working, step_id)
                working.update({"plan_graph": plan, "execution_budget": budget})
                starts.append((step_id, started))
                if index < len(ids) - 1:
                    completed = finish_plan_step(working, {}, step_id=step_id, started=started)
                    working.update(completed)
            limits = dict((working.get("execution_budget") or {}).get("limits") or {})
            with use_task_budget(
                max_calls=int(limits.get("max_llm_calls") or 4),
                max_cost_usd=limits.get("max_cost_usd") or 0.05,
            ):
                if hasattr(executable, "ainvoke"):
                    result = await executable.ainvoke(working, config)
                else:
                    result = executable(working)
                    if hasattr(result, "__await__"):
                        result = await result
            result = dict(result or {})
            step_id, started = starts[-1]
            return {**result, **finish_plan_step(working, result, step_id=step_id, started=started)}
        except PlanExecutionBlocked as exc:
            plan = dict(get_state_val(working, "plan_graph", {}) or {})
            plan["status"] = "blocked"
            return {
                "plan_graph": plan,
                "execution_budget": get_state_val(working, "execution_budget", {}) or {},
                "error_message": str(exc),
                "current_step": "plan_execution_blocked",
                "verification_result": {
                    "status": "block",
                    "issues": [{"code": "plan.execution_blocked", "message": str(exc)}],
                },
            }

    run.__name__ = f"governed_{'_'.join(ids)}"
    return run


def budgeted_node(executable: Any):
    """Apply task LLM limits to nodes that are not themselves plan steps."""

    async def run(state, config=None):
        budget = dict(
            get_state_val(state, "execution_budget", {})
            or initialize_execution_budget(get_state_val(state, "task_spec", {}) or {})
        )
        limits = dict(budget.get("limits") or {})
        started = perf_counter()
        with use_task_budget(
            max_calls=int(limits.get("max_llm_calls") or 4),
            max_cost_usd=limits.get("max_cost_usd") or 0.05,
        ):
            if hasattr(executable, "ainvoke"):
                result = await executable.ainvoke(state, config)
            else:
                result = executable(state)
                if hasattr(result, "__await__"):
                    result = await result
        result = dict(result or {})
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
        output_budget = dict(result.get("execution_budget") or budget)
        output_budget["active_elapsed_ms"] = int(
            output_budget.get("active_elapsed_ms") or 0
        ) + elapsed_ms
        failed = bool(result.get("error_message"))
        output_budget["failures"] = int(output_budget.get("failures") or 0) + (
            1 if failed else 0
        )
        result["execution_budget"] = output_budget
        output_limits = dict(output_budget.get("limits") or limits)
        if output_budget["active_elapsed_ms"] > int(
            output_limits.get("deadline_ms") or 30000
        ):
            result.update(
                error_message="Task active-execution deadline exceeded",
                current_step="execution_budget_blocked",
            )
        elif output_budget["failures"] > int(
            output_limits.get("max_failures") or 2
        ):
            result.update(
                error_message="Task failure budget exceeded",
                current_step="execution_budget_blocked",
            )
        return result

    run.__name__ = f"budgeted_{getattr(executable, '__name__', 'node')}"
    return run
