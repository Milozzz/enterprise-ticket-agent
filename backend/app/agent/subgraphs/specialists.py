from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agent.nodes.policy import answer_policy_node
from app.agent.nodes.risk_check import check_risk_node
from app.agent.nodes.summarize import should_summarize, summarize_session_node
from app.agent.nodes.user_history import fetch_user_history_node
from app.agent.state import AgentState


async def _risk_dispatch(state: AgentState) -> dict:
    return {
        "current_step": "risk_agent_dispatched",
        "specialist_handoffs": [{"agent": "risk_agent", "status": "started"}],
    }


async def _parallel_risk(state: AgentState) -> dict:
    result = await check_risk_node(state)
    result.pop("current_step", None)
    return result


async def _parallel_history(state: AgentState) -> dict:
    result = await fetch_user_history_node(state)
    result.pop("current_step", None)
    return result


async def _risk_join(state: AgentState) -> dict:
    history = state.get("user_history") or {}
    memory_risk = any(
        item.get("type") in {"dispute_history", "risk_signal"}
        and int(item.get("importance", 0)) >= 80
        for item in history.get("long_term_memories", [])
    )
    risk_score = max(int(state.get("risk_score", 0) or 0), 75 if memory_risk else 0)
    return {
        "current_step": "risk_agent_completed",
        "risk_score": risk_score,
        "risk_level": "high" if memory_risk else state.get("risk_level", "low"),
        "requires_human_approval": bool(state.get("requires_human_approval", False) or memory_risk),
        "risk_reasons": list(state.get("risk_reasons", []) or [])
        + (["长期记忆：历史纠纷/风险信号"] if memory_risk else []),
        "specialist_handoffs": [
            {
                "agent": "risk_agent",
                "status": "completed",
                "risk_score": risk_score,
            }
        ],
    }


async def _policy_qa(state: AgentState) -> dict:
    result = await answer_policy_node(state)
    result["specialist_handoffs"] = [
        {
            "agent": "policy_qa_agent",
            "status": "completed",
            "citation_count": len(result.get("policy_citations", [])),
        }
    ]
    return result


def build_risk_agent():
    """Parallel, deterministic specialist for risk and customer history."""

    builder = StateGraph(AgentState)
    builder.add_node("dispatch", _risk_dispatch)
    builder.add_node("check_risk", _parallel_risk)
    builder.add_node("fetch_user_history", _parallel_history)
    builder.add_node("join", _risk_join)
    builder.set_entry_point("dispatch")
    builder.add_edge("dispatch", "check_risk")
    builder.add_edge("dispatch", "fetch_user_history")
    builder.add_edge(["check_risk", "fetch_user_history"], "join")
    builder.add_edge("join", END)
    return builder.compile()


def build_policy_qa_agent():
    """Grounded policy specialist; no business side-effect tools are exposed."""

    builder = StateGraph(AgentState)
    builder.add_node("retrieve_and_answer", _policy_qa)
    builder.add_node("summarize", summarize_session_node)
    builder.set_entry_point("retrieve_and_answer")
    builder.add_conditional_edges(
        "retrieve_and_answer",
        should_summarize,
        {"summarize": "summarize", "end": END},
    )
    builder.add_edge("summarize", END)
    return builder.compile()
