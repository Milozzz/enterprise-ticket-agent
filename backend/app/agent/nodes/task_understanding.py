"""Create the canonical TaskSpec, bounded PlanGraph, and initial evidence."""

from __future__ import annotations

from app.agent.evidence_graph import initialize_evidence_graph
from app.agent.dynamic_planner import propose_validated_plan
from app.agent.plan_graph import build_plan_graph, mark_plan_steps
from app.agent.scenario_registry import get_default_registry
from app.agent.procedural_memory import retrieve_plan_precedents
from app.agent.dependencies import resolve_session_factory
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.agent.state import AgentState
from app.agent.task_spec import build_task_spec
from app.agent.execution_governance import initialize_execution_budget
from app.agent.utils import get_state_val


async def task_understanding_node(state: AgentState) -> dict:
    scenario_id = str(get_state_val(state, "scenario_id", "refund") or "refund")
    scenario = get_default_registry().get(scenario_id)
    task_spec = build_task_spec(state, scenario)
    precedents = []
    if get_settings().agent_procedural_memory_enabled:
        try:
            precedents = await retrieve_plan_precedents(
                user_id=str(task_spec["requester_id"]),
                tenant_id=str(task_spec["tenant_id"]),
                scenario_id=scenario_id,
                goal=str(task_spec["goal"]),
                session_factory=resolve_session_factory(AsyncSessionLocal),
            )
        except Exception:
            precedents = []
    plan_graph = build_plan_graph(task_spec, precedents=precedents)
    plan_method = str(plan_graph.get("generation_source") or "deterministic_base")
    if get_settings().agent_dynamic_plan_enabled:
        plan_graph, plan_method = await propose_validated_plan(
            task_spec=task_spec,
            base_plan=plan_graph,
            precedents=precedents,
        )
    plan_graph = mark_plan_steps(
        plan_graph,
        {"understand": "completed"},
        graph_status="running",
    )
    evidence_graph = initialize_evidence_graph(state, task_spec)
    plan_graph["precedent_ids"] = [int(item["id"]) for item in precedents]
    return {
        "task_spec": task_spec,
        "plan_graph": plan_graph,
        "evidence_graph": evidence_graph,
        "plan_precedents": precedents,
        "plan_generation_method": plan_method,
        "execution_budget": (
            get_state_val(state, "execution_budget", {})
            or initialize_execution_budget(task_spec)
        ),
        "current_step": "task_understood",
        "specialist_handoffs": [
            {
                "agent": "supervisor",
                "status": "completed",
                "task_id": task_spec["task_id"],
                "plan_revision": plan_graph["revision"],
                "plan_generation_method": plan_method,
            }
        ],
        "ui_events": [
            {
                "type": "thinking_stream",
                "data": {
                    "steps": [
                        {
                            "step": "task_understanding",
                            "label": "任务建模与计划",
                            "status": "done",
                            "detail": (
                                f"已生成 {len(plan_graph['steps'])} 步受控计划，"
                                f"包含 {len(task_spec['success_criteria'])} 项成功标准，"
                                f"复用 {len(precedents)} 条历史计划证据"
                            ),
                        }
                    ]
                },
            }
        ],
    }
