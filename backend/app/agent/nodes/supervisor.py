"""Supervisor router node for multi-scenario workflows."""

from __future__ import annotations

from app.agent.scenario_registry import get_default_registry
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.logging import get_logger

logger = get_logger(__name__)


def _latest_human_message(state: AgentState) -> str:
    messages = get_state_val(state, "messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            return str(msg.content or "")
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


async def supervisor_router_node(state: AgentState) -> dict:
    """Pick a scenario before entering a concrete workflow."""

    user_message = _latest_human_message(state)
    match = get_default_registry().match(user_message)
    config = match.config

    decision = {
        "scenario_id": match.scenario_id,
        "scenario_name": config.name,
        "workflow": match.workflow,
        "confidence": match.confidence,
        "matched_keywords": list(match.matched_keywords),
        "reason": match.reason,
        "required_tools": list(config.tools),
        "policies": list(config.policies),
        "hitl_enabled": config.hitl.enabled,
        "review_roles": list(config.hitl.review_roles),
    }

    logger.info(
        "supervisor_routed",
        scenario_id=match.scenario_id,
        workflow=match.workflow,
        confidence=match.confidence,
        matched_keywords=list(match.matched_keywords),
    )

    return {
        "scenario_id": match.scenario_id,
        "scenario_name": config.name,
        "supervisor_decision": decision,
        "current_step": "supervisor_routed",
        "ui_events": [
            {
                "type": "thinking_stream",
                "data": {
                    "steps": [
                        {
                            "step": "supervisor_routing",
                            "label": "Supervisor 路由",
                            "status": "done",
                            "detail": f"已选择 {config.name} 场景，工作流：{match.workflow}",
                        }
                    ]
                },
            }
        ],
    }
