"""Compile declarative scenario graph configs into resumable LangGraph subgraphs.

Runtime v3 deliberately keeps the executable node/router catalog in code while
moving topology into versioned scenario configuration.  Configuration can
compose approved building blocks, but cannot import arbitrary Python objects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.nodes.answer import ANSWER_TOOLS, answer_node, route_answer
from app.agent.nodes.classifier import classify_intent_node
from app.agent.nodes.human_review import human_review_node, should_continue_after_review
from app.agent.nodes.generic_approval import (
    finalize_business_request_node,
    generic_human_review_node,
    route_after_generic_prepare,
    route_after_generic_review,
)
from app.agent.nodes.notification import send_notification_node
from app.agent.nodes.order_lookup import lookup_order_node
from app.agent.nodes.refund import execute_refund_node
from app.agent.nodes.reconciliation import (
    reconcile_refund_node,
    route_after_reconciliation,
    route_after_refund_execution,
)
from app.agent.nodes.return_inventory import (
    inspect_return_inventory_node,
    restore_return_inventory_node,
    route_after_inventory_inspection,
    route_after_inventory_restoration,
    route_after_return_validation,
    validate_return_node,
)
from app.agent.nodes.final_reconciliation import final_reconciliation_node
from app.agent.nodes.summarize import summarize_session_node
from app.agent.scenario_registry import ScenarioConfig
from app.agent.state import AgentState
from app.agent.subgraphs import build_policy_qa_agent, build_risk_agent
from app.agent.utils import get_state_val
from app.agent.verifier import execution_verifier_node, route_after_verification
from app.agent.dynamic_plan_runtime import (
    dynamic_plan_dispatch_node,
    route_after_dynamic_plan_step,
)
from app.agent.generic_runtime import run_configured_scenario
from app.agent.execution_governance import budgeted_node, governed_node
from app.core.logging import get_logger

logger = get_logger(__name__)

END_TOKEN = "$end"


def route_refund_after_classify(state: AgentState) -> str:
    intent = get_state_val(state, "intent", "other")
    if intent == "refund":
        return "dynamic_dispatch"
    if intent == "query_policy":
        return "policy_qa_agent"
    return "answer_node"


def route_refund_after_lookup(state: AgentState) -> str:
    step = get_state_val(state, "current_step", "")
    if "error" in step or not get_state_val(state, "order_amount"):
        return "summarize_session"
    return "validate_return"


def route_refund_after_risk(state: AgentState) -> str:
    user_history = get_state_val(state, "user_history") or {}
    if user_history.get("has_fraud_flag"):
        return "human_review"
    if get_state_val(state, "requires_human_approval", False):
        return "human_review"
    return "execute_refund"


NodeFactory = Callable[[ScenarioConfig], Any]
Router = Callable[[AgentState], str]


def _configured_runner(scenario: ScenarioConfig):
    async def run(state: AgentState) -> dict:
        return await run_configured_scenario(state, scenario.id)

    run.__name__ = f"configured_prepare_{scenario.id}"
    return run


NODE_FACTORIES: dict[str, NodeFactory] = {
    "classify_intent": lambda _scenario: classify_intent_node,
    "answer_node": lambda _scenario: answer_node,
    "answer_tools": lambda _scenario: ToolNode(tools=ANSWER_TOOLS),
    "policy_qa_specialist": lambda _scenario: build_policy_qa_agent(),
    "lookup_order": lambda _scenario: lookup_order_node,
    "validate_return": lambda _scenario: validate_return_node,
    "inspect_return_inventory": lambda _scenario: inspect_return_inventory_node,
    "risk_specialist": lambda _scenario: build_risk_agent(),
    "human_review": lambda _scenario: human_review_node,
    "execute_refund": lambda _scenario: execute_refund_node,
    "send_notification": lambda _scenario: send_notification_node,
    "summarize_session": lambda _scenario: summarize_session_node,
    "execution_verifier": lambda _scenario: execution_verifier_node,
    "refund_reconciliation": lambda _scenario: reconcile_refund_node,
    "restore_return_inventory": lambda _scenario: restore_return_inventory_node,
    "final_reconciliation": lambda _scenario: final_reconciliation_node,
    "configured_prepare": _configured_runner,
    "generic_human_review": lambda _scenario: generic_human_review_node,
    "generic_finalize": lambda _scenario: finalize_business_request_node,
    "dynamic_plan_dispatch": lambda _scenario: dynamic_plan_dispatch_node,
}

ROUTERS: dict[str, Router] = {
    "refund_after_classify": route_refund_after_classify,
    "answer_after_model": route_answer,
    "refund_after_lookup": route_refund_after_lookup,
    "refund_after_risk": route_refund_after_risk,
    "refund_after_review": should_continue_after_review,
    "verification_route": route_after_verification,
    "generic_after_prepare": route_after_generic_prepare,
    "generic_after_review": route_after_generic_review,
    "refund_after_reconciliation": route_after_reconciliation,
    "refund_after_execution": route_after_refund_execution,
    "refund_after_return_validation": route_after_return_validation,
    "refund_after_inventory_inspection": route_after_inventory_inspection,
    "refund_after_inventory_restoration": route_after_inventory_restoration,
    "dynamic_plan_step": route_after_dynamic_plan_step,
}

INTERNALLY_GOVERNED_HANDLERS = {"dynamic_plan_dispatch"}


def list_graph_node_handlers() -> set[str]:
    return set(NODE_FACTORIES)


def list_graph_routers() -> set[str]:
    return set(ROUTERS)


def build_declarative_scenario_graph(scenario: ScenarioConfig):
    """Compile one trusted runtime-v3 graph config.

    Validation normally runs before publish.  The compiler repeats essential
    checks so a malformed file can never become an executable graph merely by
    bypassing the admin API.
    """

    runtime = dict(scenario.runtime or {})
    if str(runtime.get("schema_version")) != "3" or runtime.get("engine") != "langgraph":
        raise ValueError(
            f"Scenario '{scenario.id}' does not declare runtime v3 langgraph engine."
        )

    node_configs = runtime.get("nodes") or []
    if not isinstance(node_configs, list) or not node_configs:
        raise ValueError(f"Scenario '{scenario.id}' graph must define nodes.")

    builder = StateGraph(AgentState)
    node_ids: set[str] = set()
    for raw_node in node_configs:
        if not isinstance(raw_node, Mapping):
            raise ValueError(f"Scenario '{scenario.id}' contains an invalid node entry.")
        node_id = str(raw_node.get("id") or "").strip()
        handler_name = str(raw_node.get("handler") or "").strip()
        if not node_id or node_id in node_ids:
            raise ValueError(f"Scenario '{scenario.id}' has a missing or duplicate node id '{node_id}'.")
        factory = NODE_FACTORIES.get(handler_name)
        if factory is None:
            raise ValueError(
                f"Scenario '{scenario.id}' references unregistered node handler '{handler_name}'."
            )
        executable = factory(scenario)
        plan_binding = raw_node.get("plan_steps") or raw_node.get("plan_step")
        if handler_name in INTERNALLY_GOVERNED_HANDLERS:
            executable = executable
        elif plan_binding:
            executable = governed_node(executable, plan_binding)
        else:
            executable = budgeted_node(executable)
        builder.add_node(node_id, executable)
        node_ids.add(node_id)

    entry_node = str(runtime.get("entry_node") or "")
    if entry_node not in node_ids:
        raise ValueError(
            f"Scenario '{scenario.id}' entry node '{entry_node}' is not declared."
        )
    builder.set_entry_point(entry_node)

    for raw_edge in runtime.get("edges") or []:
        source, target = _edge_endpoints(scenario.id, raw_edge, node_ids)
        builder.add_edge(source, END if target == END_TOKEN else target)

    for raw_conditional in runtime.get("conditional_edges") or []:
        if not isinstance(raw_conditional, Mapping):
            raise ValueError(f"Scenario '{scenario.id}' contains an invalid conditional edge.")
        source = str(raw_conditional.get("from") or "")
        router_name = str(raw_conditional.get("router") or "")
        if source not in node_ids:
            raise ValueError(
                f"Scenario '{scenario.id}' conditional source '{source}' is not declared."
            )
        router = ROUTERS.get(router_name)
        if router is None:
            raise ValueError(
                f"Scenario '{scenario.id}' references unregistered router '{router_name}'."
            )
        raw_routes = raw_conditional.get("routes") or {}
        if not isinstance(raw_routes, Mapping) or not raw_routes:
            raise ValueError(
                f"Scenario '{scenario.id}' router '{router_name}' must define routes."
            )
        routes: dict[str, Any] = {}
        for outcome, target_value in raw_routes.items():
            target = str(target_value)
            if target != END_TOKEN and target not in node_ids:
                raise ValueError(
                    f"Scenario '{scenario.id}' route target '{target}' is not declared."
                )
            routes[str(outcome)] = END if target == END_TOKEN else target
        builder.add_conditional_edges(source, router, routes)

    logger.info(
        "declarative_scenario_graph_compiled",
        scenario_id=scenario.id,
        node_count=len(node_ids),
        schema_version="3",
    )
    return builder.compile()


def _edge_endpoints(
    scenario_id: str,
    raw_edge: Any,
    node_ids: set[str],
) -> tuple[str, str]:
    if not isinstance(raw_edge, Mapping):
        raise ValueError(f"Scenario '{scenario_id}' contains an invalid edge.")
    source = str(raw_edge.get("from") or "")
    target = str(raw_edge.get("to") or "")
    if source not in node_ids:
        raise ValueError(f"Scenario '{scenario_id}' edge source '{source}' is not declared.")
    if target != END_TOKEN and target not in node_ids:
        raise ValueError(f"Scenario '{scenario_id}' edge target '{target}' is not declared.")
    return source, target
