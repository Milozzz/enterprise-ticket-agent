"""Supervisor router node for multi-scenario workflows.

A3 语义路由：关键词匹配仍是快路径；当关键词置信度低于阈值且启用了
SUPERVISOR_LLM_ROUTING_ENABLED 时，调用 LLM 做结构化场景路由；
最终置信度仍低于澄清阈值时，主动向用户澄清而非硬猜。
LLM 不可用时无损降级回关键词结果（并标注 degraded）。
"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph.types import interrupt
from pydantic import BaseModel

from app.agent.dependencies import get_agent_dependencies
from app.agent.nodes.classifier import classify_intent_fast_path
from app.agent.scenario_registry import get_default_registry
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.gateway import LLMCallContext

logger = get_logger(__name__)


class ScenarioRoute(BaseModel):
    scenario_id: str
    confidence: float = 0.5
    reason: str = ""


def _scenario_catalog() -> str:
    lines = []
    for config in get_default_registry().list():
        if config.status != "active":
            continue
        keywords = ", ".join(list(config.keywords)[:8])
        lines.append(
            f"- id: {config.id} | 名称: {config.name} | 描述: {config.description} | 关键词示例: {keywords}"
        )
    return "\n".join(lines)


_ROUTING_PROMPT = """你是企业工作流路由器。根据用户消息，从下列业务场景中选择最匹配的一个。

可选场景：
{catalog}

要求：
- scenario_id 必须严格取自上面列出的 id
- confidence 取 0~1：明确匹配给 0.8+，模糊匹配给 0.4~0.6，完全不确定给 0.4 以下
- reason 用一句中文说明依据
"""


async def _llm_route(user_message: str, state: AgentState) -> ScenarioRoute | None:
    """LLM 结构化路由。任何失败返回 None（调用方降级回关键词结果）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = await get_agent_dependencies().llm.runnable(
            "supervisor_route",
            context=LLMCallContext(
                thread_id=str(get_state_val(state, "thread_id", "unknown")),
                trace_id=str(get_state_val(state, "trace_id", "") or "") or None,
                tenant_id=str(get_state_val(state, "tenant_id", "") or "") or None,
            ),
            schema=ScenarioRoute,
            temperature=0.0,
            timeout_seconds=5.0,
        ).ainvoke([
            SystemMessage(content=_ROUTING_PROMPT.format(catalog=_scenario_catalog())),
            HumanMessage(content=user_message),
        ])
        if isinstance(response, ScenarioRoute):
            route = response
        elif isinstance(response, dict):
            route = ScenarioRoute(**response)
        else:
            return None
        # scenario_id 必须真实存在且 active，否则视为无效路由
        registry = get_default_registry()
        valid_ids = {c.id for c in registry.list() if c.status == "active"}
        if route.scenario_id not in valid_ids:
            logger.warning("llm_route_invalid_scenario", scenario_id=route.scenario_id)
            return None
        route.confidence = max(0.0, min(1.0, float(route.confidence)))
        return route
    except Exception as exc:
        logger.warning("llm_route_failed_fallback_keyword", error=str(exc))
        return None


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

    settings = get_settings()
    user_message = _latest_human_message(state)
    registry = get_default_registry()
    match = registry.match(user_message)

    scenario_id = match.scenario_id
    confidence = float(match.confidence)
    reason = match.reason
    method = "keyword"
    degraded = False

    # A3：关键词置信度不足时升级为 LLM 结构化语义路由
    route_threshold = float(settings.supervisor_route_confidence_threshold)
    if settings.supervisor_llm_routing_enabled and confidence < route_threshold:
        llm_route = await _llm_route(user_message, state)
        if llm_route is not None and llm_route.confidence > confidence:
            scenario_id = llm_route.scenario_id
            confidence = llm_route.confidence
            reason = f"LLM 语义路由: {llm_route.reason}"
            method = "llm_semantic"
        elif llm_route is None:
            degraded = True  # LLM 不可用，本次路由是降级产物

    config = registry.get(scenario_id)
    clarify_threshold = float(settings.supervisor_clarify_confidence_threshold)
    needs_clarification = confidence < clarify_threshold
    clarification_answer = ""

    # A3 完整形态：置信度过低时真正中断对话向用户澄清（interrupt 语义与
    # 人工审批一致：graph 暂停 → 前端提问 → 用户下一条消息作为 resume 恢复）。
    # 只澄清一轮：拿到补充信息后重路由，仍不确定就按最优猜测继续，绝不循环追问。
    if needs_clarification and settings.supervisor_clarification_enabled:
        active_names = [
            c.name for c in registry.list() if c.status == "active"
        ]
        resume_value = interrupt(
            {
                "kind": "clarification",
                "question": (
                    "我还不太确定您要办理哪类业务，方便补充说明吗？"
                    f"目前支持：{'、'.join(active_names)}"
                ),
                "options": active_names,
                "confidence": confidence,
                "thread_id": str(get_state_val(state, "thread_id", "") or ""),
            }
        )
        if isinstance(resume_value, Mapping):
            clarification_answer = str(resume_value.get("answer") or "")
        else:
            clarification_answer = str(resume_value or "")

        if clarification_answer:
            combined = f"{user_message}\n（用户补充说明：{clarification_answer}）"
            rematch = registry.match(combined)
            scenario_id = rematch.scenario_id
            confidence = float(rematch.confidence)
            reason = f"澄清后重路由: {rematch.reason}"
            method = f"{method}+clarified"
            if settings.supervisor_llm_routing_enabled and confidence < route_threshold:
                llm_route = await _llm_route(combined, state)
                if llm_route is not None and llm_route.confidence > confidence:
                    scenario_id = llm_route.scenario_id
                    confidence = llm_route.confidence
                    reason = f"澄清后 LLM 语义路由: {llm_route.reason}"
                    method = "llm_semantic+clarified"
            config = registry.get(scenario_id)
        # 一轮澄清后无论置信度如何都继续执行（needs_clarification 只用于记录）
        needs_clarification = confidence < clarify_threshold

    decision = {
        "scenario_id": scenario_id,
        "scenario_name": config.name,
        "workflow": config.workflow,
        "confidence": confidence,
        "matched_keywords": list(match.matched_keywords),
        "reason": reason,
        "routing_method": method,
        "routing_degraded": degraded,
        "needs_clarification": needs_clarification,
        "clarification_answered": bool(clarification_answer),
        "required_tools": list(config.tools),
        "policies": list(config.policies),
        "hitl_enabled": config.hitl.enabled,
        "review_roles": list(config.hitl.review_roles),
    }

    classification_prefill: dict = {}
    if scenario_id == "refund":
        fast_classification = classify_intent_fast_path(user_message)
        if fast_classification is not None:
            decision["classification_prefilled"] = True
            decision["sub_intent"] = fast_classification.get("intent")
            decision["sub_intent_method"] = "rules_fast_path"
            classification_prefill = {
                "intent": str(fast_classification.get("intent") or "other"),
                "order_id": str(fast_classification.get("order_id") or ""),
                "refund_reason": str(fast_classification.get("reason") or "other"),
                "refund_description": str(
                    fast_classification.get("description") or user_message
                ),
            }

    logger.info(
        "supervisor_routed",
        scenario_id=scenario_id,
        workflow=config.workflow,
        confidence=confidence,
        method=method,
        needs_clarification=needs_clarification,
        matched_keywords=list(match.matched_keywords),
    )

    detail = f"已选择 {config.name} 场景，工作流：{config.workflow}"
    if method.startswith("llm_semantic"):
        detail += "（语义路由）"
    if clarification_answer:
        detail = f"已根据您的补充说明选择 {config.name} 场景"
    elif needs_clarification:
        detail = (
            f"意图不够明确（置信度 {confidence:.0%}），暂按 {config.name} 处理；"
            "如不符请补充说明，例如「申请退款」「查询订单」「申请权限」「报销」"
        )
    if degraded:
        detail += "（LLM 不可用，已降级为关键词路由）"

    result: dict = {
        "scenario_id": scenario_id,
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
                            "detail": detail,
                        }
                    ]
                },
            }
        ],
        **classification_prefill,
    }
    if degraded:
        result["llm_degraded"] = True
    return result
