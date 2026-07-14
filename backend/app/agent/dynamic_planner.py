"""Optional LLM plan proposal constrained by deterministic PlanGraph validation."""

from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import BaseModel, Field

from app.agent.dependencies import get_agent_dependencies
from app.agent.plan_graph import PlanStep, SPECIALIST_CATALOG, validate_plan_graph
from app.agent.dynamic_plan_runtime import plan_is_executable
from app.core.logging import get_logger
from app.llm.gateway import LLMCallContext
from app.llm.task_budget import use_task_budget

logger = get_logger(__name__)


class PlanProposal(BaseModel):
    rationale: str = ""
    steps: list[PlanStep] = Field(default_factory=list)


async def propose_validated_plan(
    *,
    task_spec: Mapping[str, Any],
    base_plan: Mapping[str, Any],
    precedents: list[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Return a validated candidate or the deterministic base plan."""
    from langchain_core.messages import HumanMessage, SystemMessage

    scenario_id = str(task_spec.get("scenario_id") or "unknown")
    allowed_specialists = _scenario_specialists(scenario_id)
    catalog = {
        name: SPECIALIST_CATALOG[name].model_dump(mode="json")
        for name in sorted(allowed_specialists)
    }
    try:
        budget = dict(task_spec.get("budget") or {})
        with use_task_budget(
            max_calls=int(budget.get("max_llm_calls") or 4),
            max_cost_usd=budget.get("max_cost_usd") or 0.05,
        ):
            response = await get_agent_dependencies().llm.runnable(
                "task_plan_proposal",
                context=LLMCallContext(
                    thread_id=str(task_spec.get("thread_id") or "unknown"),
                    tenant_id=str(task_spec.get("tenant_id") or "default"),
                ),
                schema=PlanProposal,
                temperature=0.0,
                timeout_seconds=6.0,
            ).ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你是受控企业任务规划器。只能使用给定 Specialist 和工具。"
                            "可以在白名单内增加、删除、拆分或重排 read/decision 步骤。"
                            "所有 write/external 步骤必须 approval_required=true、声明幂等键和补偿，"
                            "不得删除成功标准要求的副作用或证据。"
                        )
                    ),
                    HumanMessage(
                        content=json.dumps(
                            {
                                "task": task_spec,
                                "specialists": catalog,
                                "deterministic_base_plan": base_plan,
                                "verified_precedents": list(precedents or []),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                ]
            )
        proposal = response if isinstance(response, PlanProposal) else PlanProposal(**response)
        candidate = {
            **dict(base_plan),
            "revision": int(base_plan.get("revision") or 1) + 1,
            "steps": [step.model_dump(mode="json") for step in proposal.steps],
            "planner_rationale": proposal.rationale,
        }
        errors = validate_plan_candidate(
            task_spec,
            candidate,
            allowed_specialists,
            required_side_effect_tools={
                str(step.get("tool"))
                for step in base_plan.get("steps") or []
                if str(step.get("side_effect") or "read") in {"write", "external"}
                and step.get("tool")
            },
        )
        if errors:
            logger.warning("dynamic_plan_rejected", scenario=scenario_id, errors=errors)
            return dict(base_plan), "deterministic_fallback_invalid_proposal"
        return candidate, "llm_validated"
    except Exception as exc:
        logger.warning("dynamic_plan_failed", scenario=scenario_id, error=str(exc))
        return dict(base_plan), "deterministic_fallback_unavailable"


def validate_plan_candidate(
    task_spec: Mapping[str, Any],
    plan: Mapping[str, Any],
    allowed_specialists: set[str] | None = None,
    required_step_ids: set[str] | None = None,
    required_side_effect_tools: set[str] | None = None,
) -> list[str]:
    errors = validate_plan_graph(plan, task_spec)
    errors.extend(plan_is_executable(plan))
    steps = [dict(item) for item in plan.get("steps") or []]
    if required_step_ids is not None:
        proposed_ids = {str(step.get("step_id") or "") for step in steps}
        missing_steps = sorted(required_step_ids - proposed_ids)
        if missing_steps:
            errors.append(f"plan omits runtime-required steps {missing_steps}")
    if required_side_effect_tools is not None:
        proposed_tools = {
            str(step.get("tool") or "")
            for step in steps
            if str(step.get("side_effect") or "read") in {"write", "external"}
        }
        missing_tools = sorted(required_side_effect_tools - proposed_tools)
        if missing_tools:
            errors.append(f"plan omits required side effects {missing_tools}")
    if len(steps) > int((task_spec.get("budget") or {}).get("max_steps") or 12):
        errors.append("plan exceeds task step budget")
    allowed = allowed_specialists or _scenario_specialists(
        str(task_spec.get("scenario_id") or "unknown")
    )
    disallowed = sorted(
        {str(step.get("specialist") or "") for step in steps} - allowed
    )
    if disallowed:
        errors.append(f"scenario does not allow specialists {disallowed}")
    produced_evidence = {
        str(predicate)
        for step in steps
        for predicate in step.get("expected_evidence") or []
    }
    required_evidence = {
        str(predicate)
        for criterion in task_spec.get("success_criteria") or []
        for predicate in criterion.get("required_evidence") or []
        if not (
            str(criterion.get("criterion_id")) == "approval_satisfied"
            and str(task_spec.get("scenario_id")) == "refund"
        )
    }
    missing = sorted(required_evidence - produced_evidence)
    if missing:
        errors.append(f"plan does not produce success evidence {missing}")
    return errors


def _scenario_specialists(scenario_id: str) -> set[str]:
    return {
        "refund": {
            "supervisor",
            "operations_specialist",
            "risk_specialist",
            "policy_specialist",
            "finance_specialist",
            "inventory_specialist",
            "verifier",
            "executor",
        },
        "permission_request": {"supervisor", "access_specialist", "policy_specialist", "verifier"},
        "reimbursement": {"supervisor", "expense_specialist", "policy_specialist", "verifier"},
    }.get(scenario_id, {"supervisor", "verifier"})
