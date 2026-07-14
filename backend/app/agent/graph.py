"""Supervisor root graph and independently resumable scenario subgraphs."""

from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    _POSTGRES_AVAILABLE = True
except ImportError:
    _POSTGRES_AVAILABLE = False

try:
    from langgraph.checkpoint.redis import RedisSaver

    _REDIS_CP_AVAILABLE = True
except ImportError:
    _REDIS_CP_AVAILABLE = False

from app.agent.generic_runtime import run_configured_scenario
from app.agent.nodes.generic_approval import (
    finalize_business_request_node,
    generic_human_review_node,
    route_after_generic_prepare,
    route_after_generic_review,
)
from app.agent.nodes.supervisor import supervisor_router_node
from app.agent.nodes.task_understanding import task_understanding_node
from app.agent.execution_governance import (
    bootstrap_execution_budget_node,
    budgeted_node,
)
from app.agent.scenario_graph_runtime import build_declarative_scenario_graph
from app.agent.scenario_registry import ScenarioConfig, get_default_registry
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _configured_scenario_runner(scenario_id: str):
    async def run(state: AgentState) -> dict:
        return await run_configured_scenario(state, scenario_id)

    run.__name__ = f"run_{scenario_id}_scenario"
    return run


def _build_configured_subgraph(scenario_id: str):
    builder = StateGraph(AgentState)
    builder.add_node(scenario_id, _configured_scenario_runner(scenario_id))
    builder.add_node("generic_human_review", generic_human_review_node)
    builder.add_node("finalize_business_request", finalize_business_request_node)
    builder.set_entry_point(scenario_id)
    builder.add_conditional_edges(
        scenario_id,
        route_after_generic_prepare,
        {"review": "generic_human_review", "finalize": "finalize_business_request"},
    )
    builder.add_conditional_edges(
        "generic_human_review",
        route_after_generic_review,
        {
            "review": "generic_human_review",
            "finalize": "finalize_business_request",
            "end": END,
        },
    )
    builder.add_edge("finalize_business_request", END)
    return builder.compile()


def build_scenario_subgraph(scenario: ScenarioConfig):
    """Build a scenario child graph solely from its declared runtime contract."""
    runtime = dict(scenario.runtime or {})
    if str(runtime.get("schema_version")) == "3":
        return build_declarative_scenario_graph(scenario)
    return _build_configured_subgraph(scenario.id)


def build_graph(checkpointer=None):
    """Build the root graph; active scenarios become independently resumable children."""
    builder = StateGraph(AgentState)
    builder.add_node("budget_bootstrap", bootstrap_execution_budget_node)
    builder.add_node("supervisor_router", budgeted_node(supervisor_router_node))
    builder.add_node("task_understanding", budgeted_node(task_understanding_node))
    builder.set_entry_point("budget_bootstrap")
    builder.add_edge("budget_bootstrap", "supervisor_router")
    builder.add_edge("supervisor_router", "task_understanding")

    scenario_routes: dict[str, str] = {}
    for scenario in get_default_registry().list():
        if scenario.status != "active":
            continue
        subgraph = build_scenario_subgraph(scenario)
        builder.add_node(scenario.id, subgraph)
        builder.add_edge(scenario.id, END)
        scenario_routes[scenario.id] = scenario.id

    def route_to_subgraph(state: AgentState) -> str:
        scenario_id = str(get_state_val(state, "scenario_id", "refund"))
        return get_default_registry().get(scenario_id).id

    builder.add_conditional_edges("task_understanding", route_to_subgraph, scenario_routes)
    compile_kwargs = {"checkpointer": checkpointer} if checkpointer else {}
    return builder.compile(**compile_kwargs)


# 当前 serving graph 背后的 checkpointer 类型：memory | redis | postgres。
# HITL 依赖 interrupt() 状态持久化，MemorySaver 下进程重启会丢失所有待审批流程，
# 因此生产环境必须在 lifespan 中替换为 postgres（见 main.py 的 fail-fast 校验）。
_ACTIVE_CHECKPOINTER_KIND = "memory"


def active_checkpointer_kind() -> str:
    return _ACTIVE_CHECKPOINTER_KIND


def _build_default_graph():
    global _ACTIVE_CHECKPOINTER_KIND
    if settings.environment != "development":
        # 仅为进程启动期的占位 graph；lifespan 会用 AsyncPostgresSaver 替换。
        # 若替换失败 main.py 会直接抛错拒绝启动，而不是静默用内存版对外服务。
        logger.info("checkpointer_bootstrap_memory", replacement="postgres_lifespan")
        _ACTIVE_CHECKPOINTER_KIND = "memory"
        return build_graph(checkpointer=MemorySaver())

    explicit_redis = os.getenv("REDIS_URL", "")
    redis_url = settings.upstash_redis_url or explicit_redis
    if redis_url and _REDIS_CP_AVAILABLE:
        try:
            redis_cp = RedisSaver(redis_url=redis_url)
            redis_cp.setup()
            logger.info("checkpointer_redis_active", url=redis_url[:30] + "...")
            _ACTIVE_CHECKPOINTER_KIND = "redis"
            return build_graph(checkpointer=redis_cp)
        except Exception as exc:
            logger.warning("redis_checkpointer_failed_fallback_memory", error=str(exc))
    logger.info("checkpointer_memory_active")
    _ACTIVE_CHECKPOINTER_KIND = "memory"
    return build_graph(checkpointer=MemorySaver())


ticket_graph = _build_default_graph()


async def open_postgres_graph(db_connection_string: str):
    """Open a production graph and keep its async PostgreSQL resource alive."""
    if not _POSTGRES_AVAILABLE:
        raise RuntimeError("langgraph-checkpoint-postgres is not installed")
    manager = AsyncPostgresSaver.from_conn_string(
        db_connection_string.replace("+asyncpg", "")
    )
    checkpointer = await manager.__aenter__()
    try:
        await checkpointer.setup()
        return build_graph(checkpointer=checkpointer), manager
    except Exception:
        await manager.__aexit__(None, None, None)
        raise


def set_ticket_graph(graph, checkpointer_kind: str = "postgres") -> None:
    global ticket_graph, _ACTIVE_CHECKPOINTER_KIND
    ticket_graph = graph
    _ACTIVE_CHECKPOINTER_KIND = checkpointer_kind
