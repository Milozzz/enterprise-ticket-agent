"""Persist and retrieve successful, governed plan templates as procedural memory."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.agent.long_term_memory import list_active_memories, upsert_long_term_memory


async def store_successful_plan(
    state: Mapping[str, Any],
    *,
    session_factory,
) -> str | None:
    task = dict(state.get("task_spec") or {})
    plan = dict(state.get("plan_graph") or {})
    if not task or not plan or plan.get("status") != "completed":
        return None
    scenario_id = str(task.get("scenario_id") or "unknown")
    signature_source = json.dumps(
        {
            "scenario": scenario_id,
            "steps": [
                {
                    "specialist": step.get("specialist"),
                    "capability": step.get("capability"),
                    "tool": step.get("tool"),
                }
                for step in plan.get("steps") or []
            ],
        },
        sort_keys=True,
        default=str,
    )
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:16]
    await upsert_long_term_memory(
        user_id=str(task.get("requester_id") or "anonymous"),
        memory_type="successful_plan",
        memory_key=f"{scenario_id}:{signature}",
        content=f"{scenario_id} governed plan completed successfully",
        attributes={
            "scenario_id": scenario_id,
            "plan_signature": signature,
            "steps": [
                {
                    "step_id": step.get("step_id"),
                    "specialist": step.get("specialist"),
                    "capability": step.get("capability"),
                    "tool": step.get("tool"),
                    "arguments": dict(step.get("arguments") or {}),
                    "side_effect": step.get("side_effect"),
                    "dependencies": list(step.get("dependencies") or []),
                    "preconditions": list(step.get("preconditions") or []),
                    "expected_output_schema": dict(
                        step.get("expected_output_schema") or {}
                    ),
                    "postconditions": list(step.get("postconditions") or []),
                    "expected_evidence": list(step.get("expected_evidence") or []),
                    "approval_required": bool(step.get("approval_required")),
                    "compensation": step.get("compensation"),
                    "idempotency_key_template": step.get(
                        "idempotency_key_template"
                    ),
                    "timeout_seconds": float(step.get("timeout_seconds") or 10.0),
                    "retry_limit": int(step.get("retry_limit") or 0),
                    "status": "planned",
                }
                for step in plan.get("steps") or []
            ],
            "success_criteria": [
                item.get("criterion_id") for item in task.get("success_criteria") or []
            ],
        },
        confidence=1.0,
        importance=75,
        source_thread_id=str(task.get("thread_id") or ""),
        source_type="verified_successful_execution",
        tenant_id=str(task.get("tenant_id") or "default"),
        session_factory=session_factory,
    )
    return signature


async def retrieve_plan_precedents(
    *,
    user_id: str,
    tenant_id: str,
    scenario_id: str,
    goal: str,
    session_factory,
    limit: int = 3,
) -> list[dict[str, Any]]:
    memories = await list_active_memories(
        user_id,
        tenant_id=tenant_id,
        memory_types=["successful_plan"],
        query=f"{scenario_id} {goal}",
        limit=limit,
        session_factory=session_factory,
    )
    return [
        item
        for item in memories
        if (item.get("attributes") or {}).get("scenario_id") == scenario_id
    ]
