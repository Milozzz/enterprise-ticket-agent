"""Permission request scenario node backed by generic runtime config."""

from __future__ import annotations

from app.agent.generic_runtime import run_configured_scenario
from app.agent.state import AgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


async def permission_request_node(state: AgentState) -> dict:
    logger.info("node_start", node="permission_request")
    return await run_configured_scenario(state, "permission_request")
