"""Side-effect-free comparison of model, prompt, policy and plan variants."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Mapping, Sequence
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.plan_graph import validate_plan_graph
from app.core.config import get_settings
from app.core.masking import mask_dict
from app.core.policy import (
    evaluate_permission_request_policy_document,
    evaluate_refund_review_policy_document,
    evaluate_reimbursement_policy_document,
    load_policy,
)
from app.db.models import AuditLog, CounterfactualExperiment
from app.llm.gateway import LLMCallContext, LLMGateway, ModelCandidate


class CounterfactualPlanOutput(BaseModel):
    plan_graph: dict[str, Any]
    rationale: str = Field(default="", max_length=2000)


async def materialize_counterfactual_variants(
    *,
    baseline_snapshot: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]],
    tenant_id: str,
    thread_id: str | None,
) -> list[dict[str, Any]]:
    """Resolve supplied or live shadow model outputs into candidate plans.

    Live shadow calls are opt-in per variant.  They can only propose PlanGraph;
    the normal deterministic validator still decides whether the plan is valid.
    """

    materialized: list[dict[str, Any]] = []
    for index, raw in enumerate(variants):
        variant = dict(raw)
        model_output = variant.get("model_output")
        if isinstance(model_output, Mapping):
            if isinstance(model_output.get("plan_graph"), Mapping):
                variant["plan_graph"] = dict(model_output["plan_graph"])
            variant["model_execution"] = {
                "status": "supplied",
                "source": "precomputed_shadow_output",
            }
        elif variant.get("run_model"):
            variant = await _run_shadow_planner(
                baseline_snapshot=baseline_snapshot,
                variant=variant,
                tenant_id=tenant_id,
                thread_id=thread_id,
                index=index,
            )
        materialized.append(variant)
    return materialized


async def _run_shadow_planner(
    *,
    baseline_snapshot: Mapping[str, Any],
    variant: dict[str, Any],
    tenant_id: str,
    thread_id: str | None,
    index: int,
) -> dict[str, Any]:
    from app.core.config import get_settings

    settings = get_settings()
    provider = str(variant.get("provider") or settings.llm_default_provider).lower()
    default_models = {
        "gemini": settings.gemini_model,
        "openai": settings.openai_model,
        "anthropic": settings.anthropic_model,
    }
    model = str(variant.get("model") or default_models.get(provider) or "")
    prompt_template = str(
        variant.get("prompt_template")
        or "Generate a bounded PlanGraph for the supplied TaskSpec. Use only existing Tool Gateway tools, read before write, and require approval, compensation, and idempotency for every side effect."
    )
    candidate = {**variant, "provider": provider, "model": model}
    try:
        gateway = LLMGateway()
        evidence_graph = dict(baseline_snapshot.get("evidence_graph") or {})
        shadow_input = mask_dict(
            {
                "task_spec": baseline_snapshot.get("task_spec") or {},
                "baseline_plan": baseline_snapshot.get("plan_graph") or {},
                "evidence_summary": {
                    "claims": list(evidence_graph.get("claims") or [])[:30],
                    "relations": list(evidence_graph.get("relations") or [])[:30],
                },
            }
        )
        result = await gateway.ainvoke(
            "counterfactual_planner",
            [
                SystemMessage(content=prompt_template),
                HumanMessage(
                    content=json.dumps(
                        shadow_input,
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            ],
            context=LLMCallContext(
                thread_id=thread_id or f"counterfactual-{index + 1}",
                trace_id=str(baseline_snapshot.get("trace_id") or "counterfactual"),
                tenant_id=tenant_id,
            ),
            schema=CounterfactualPlanOutput,
            temperature=0.0,
            timeout_seconds=float(variant.get("timeout_seconds") or 20),
            prompt_version=str(variant.get("prompt_version") or "counterfactual"),
            prompt_variant=str(variant.get("variant_id") or f"variant_{index + 1}"),
            candidate_overrides=[ModelCandidate(provider=provider, model=model)],
        )
        output = result.output
        if isinstance(output, CounterfactualPlanOutput):
            candidate["plan_graph"] = output.plan_graph
            rationale = output.rationale
        elif isinstance(output, Mapping):
            candidate["plan_graph"] = dict(output.get("plan_graph") or {})
            rationale = str(output.get("rationale") or "")
        else:
            raise ValueError("shadow planner returned no structured PlanGraph")
        candidate["model_execution"] = {
            "status": "completed",
            "provider": result.provider,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "total_tokens": result.total_tokens,
            "estimated_cost_usd": str(result.estimated_cost_usd),
            "rationale": rationale,
        }
    except Exception as exc:
        candidate["model_execution"] = {
            "status": "failed",
            "provider": provider,
            "model": model,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return candidate


def run_counterfactual_replay(
    *,
    baseline_snapshot: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare variants without invoking an LLM or any Tool Gateway handler."""

    max_variants = get_settings().counterfactual_max_variants
    if not variants:
        raise ValueError("at least one counterfactual variant is required")
    if len(variants) > max_variants:
        raise ValueError(f"counterfactual replay supports at most {max_variants} variants")
    baseline = _evaluate_variant("baseline", baseline_snapshot, {})
    evaluated = [
        _evaluate_variant(
            str(variant.get("variant_id") or f"variant_{index + 1}"),
            baseline_snapshot,
            variant,
        )
        for index, variant in enumerate(variants)
    ]
    return {
        "mode": "counterfactual",
        "side_effects": "disabled",
        "dry_run": True,
        "baseline": baseline,
        "variants": [
            {
                **item,
                "difference": _decision_difference(baseline, item),
            }
            for item in evaluated
        ],
        "summary": {
            "variant_count": len(evaluated),
            "decision_change_count": sum(item["decision"] != baseline["decision"] for item in evaluated),
            "invalid_plan_count": sum(bool(item["plan_errors"]) for item in evaluated),
        },
    }


async def persist_counterfactual_experiment(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str | None,
    task_id: str | None,
    requested_by: str,
    baseline_snapshot: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> CounterfactualExperiment:
    experiment = CounterfactualExperiment(
        experiment_id=f"cfx_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        thread_id=thread_id,
        task_id=task_id,
        requested_by=requested_by,
        baseline_snapshot=mask_dict(dict(baseline_snapshot)),
        variants=(mask_dict({"items": [dict(item) for item in variants]}) or {}).get("items", []),
        result=mask_dict(dict(result)),
        status="completed",
        completed_at=datetime.utcnow(),
    )
    session.add(experiment)
    await session.flush()
    return experiment


async def reconstruct_counterfactual_snapshot(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
) -> dict[str, Any]:
    logs = list(
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id, AuditLog.thread_id == thread_id)
                .order_by(AuditLog.created_at.asc())
            )
        ).scalars()
    )
    if not logs:
        raise ValueError("no audit snapshot exists for this thread")
    snapshot: dict[str, Any] = {"thread_id": thread_id}
    for log in logs:
        output = log.output_data if isinstance(log.output_data, dict) else {}
        for key in (
            "task_spec",
            "plan_graph",
            "evidence_graph",
            "order_id",
            "order_amount",
            "currency",
            "risk_score",
            "risk_level",
            "user_history",
            "requires_human_approval",
            "verification_result",
        ):
            if key in output:
                snapshot[key] = output[key]
    snapshot["trace_id"] = next((log.trace_id for log in logs if log.trace_id), None)
    return snapshot


def serialize_experiment(experiment: CounterfactualExperiment) -> dict[str, Any]:
    return {
        "experiment_id": experiment.experiment_id,
        "thread_id": experiment.thread_id,
        "task_id": experiment.task_id,
        "status": experiment.status,
        "result": experiment.result,
        "created_at": experiment.created_at.isoformat(),
        "completed_at": experiment.completed_at.isoformat() if experiment.completed_at else None,
    }


def _evaluate_variant(
    variant_id: str,
    snapshot: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    task_spec = dict(snapshot.get("task_spec") or {})
    scenario_id = str(task_spec.get("scenario_id") or "refund")
    plan = dict(overrides.get("plan_graph") or snapshot.get("plan_graph") or {})
    plan_errors = validate_plan_graph(plan, task_spec) if plan else ["plan snapshot is missing"]
    policy = dict(overrides.get("policy_document") or load_policy())
    if scenario_id == "permission_request":
        decision = evaluate_permission_request_policy_document(
            policy,
            system=str(snapshot.get("permission_system") or "unknown"),
            permission_level=str(snapshot.get("permission_level") or "read"),
            policy_variant=f"counterfactual:{variant_id}",
        )
    elif scenario_id == "reimbursement":
        decision = evaluate_reimbursement_policy_document(
            policy,
            amount=float(snapshot.get("reimbursement_amount") or snapshot.get("order_amount") or 0),
            category=str(snapshot.get("reimbursement_category") or "other"),
            policy_variant=f"counterfactual:{variant_id}",
        )
    else:
        decision = evaluate_refund_review_policy_document(
            policy,
            amount=float(snapshot.get("order_amount") or 0),
            risk_score=int(snapshot.get("risk_score") or 0),
            risk_level=str(snapshot.get("risk_level") or "low"),
            user_history=dict(snapshot.get("user_history") or {}),
            policy_variant=f"counterfactual:{variant_id}",
        )
    evidence_graph = dict(snapshot.get("evidence_graph") or {})
    evidence_count = len(evidence_graph.get("claims") or [])
    verification_status = str((snapshot.get("verification_result") or {}).get("status") or "unknown")
    model_execution = dict(overrides.get("model_execution") or {})
    if model_execution.get("status") == "failed":
        disposition = "model_unavailable"
    elif plan_errors:
        disposition = "reject_plan"
    elif verification_status not in {"pass", "unknown"} or evidence_count == 0:
        disposition = "block_missing_evidence"
    elif decision.requires_human_review:
        disposition = "human_review"
    else:
        disposition = "dry_run_execute"
    return {
        "variant_id": variant_id,
        "model": overrides.get("model") or "baseline",
        "prompt_version": overrides.get("prompt_version") or "baseline",
        "policy_version": decision.policy_version,
        "plan_revision": plan.get("revision"),
        "plan_errors": plan_errors,
        "evidence_count": evidence_count,
        "decision": disposition,
        "requires_human_review": decision.requires_human_review,
        "matched_policy_rules": list(decision.matched_rules),
        "model_execution": model_execution or None,
        "side_effects_executed": 0,
    }


def _decision_difference(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("decision", "requires_human_review", "policy_version", "plan_revision", "plan_errors")
    return {
        field: {"baseline": baseline.get(field), "candidate": candidate.get(field)}
        for field in fields
        if baseline.get(field) != candidate.get(field)
    }
