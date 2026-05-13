"""
answer_node：真正的 ReAct 节点（bind_tools + LangGraph ToolNode 模式）

架构：
  answer_node → (has tool_calls?) → answer_tools (ToolNode) → answer_node (loop)
                                 → (no tool_calls) → summarize_session

answer_node 只负责调用 LLM，把 AIMessage 写入 state.messages。
工具执行由 LangGraph ToolNode 自动完成，结果作为 ToolMessage 写回 messages。
下一轮 answer_node 重新拿到带工具结果的 messages，让 LLM 生成最终回复。
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tools.ticket_tools import get_ticket_status
from app.core.logging import get_logger
from app.core.config import get_settings

logger = get_logger(__name__)
settings = get_settings()

# 供 ToolNode 使用（必须与 bind_tools 传入的列表一致）
ANSWER_TOOLS = [get_ticket_status]

_SYSTEM_PROMPT = """你是企业智能客服助手，专注于退款和订单查询服务。

你有以下工具可以使用：
- get_ticket_status：查询订单的退款工单状态（当用户询问某个订单的处理进度时调用）

使用规则：
1. 如果用户询问具体订单的状态/进度，调用 get_ticket_status 工具获取最新数据
2. 拿到工具结果后，用自然语言向用户解释工单状态
3. 如果用户发来问候或其他请求，直接引导使用退款/查询功能
4. 回复简洁友好，不超过3句话
"""

_llm_with_tools = None


def _get_llm():
    global _llm_with_tools
    if _llm_with_tools is None and settings.google_api_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        base = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=0.3,
        )
        _llm_with_tools = base.bind_tools(ANSWER_TOOLS)
    return _llm_with_tools


async def answer_node(state: AgentState) -> dict:
    """
    ReAct answer 节点：调用 LLM（bind_tools），将 AIMessage 写入 messages。

    LangGraph ToolNode 会检测 messages 里最后一条 AIMessage 的 tool_calls，
    自动执行工具，再把 ToolMessage 追加到 messages，然后路由回此节点。
    没有 tool_calls 时路由到 summarize_session。
    """
    intent = get_state_val(state, "intent", "other")
    logger.info("node_start", node="answer", intent=intent)

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [{
                "step": "answering",
                "label": "处理请求",
                "status": "running",
                "detail": "正在分析请求...",
            }]
        },
    }

    llm = _get_llm()
    if not llm:
        return {
            "current_step": "answer_done",
            "is_completed": True,
            "reply_text": _fallback_reply(),
            "ui_events": [ui_thinking],
        }

    # 取 state.messages（已包含历史 + 工具结果，由 add_messages 累积）
    messages = list(state.get("messages", []))

    # 首次进入：在消息前插入 system prompt
    has_system = any(getattr(m, "type", "") == "system" for m in messages)
    if not has_system:
        messages = [SystemMessage(content=_SYSTEM_PROMPT)] + messages

    try:
        import asyncio
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=10.0)
    except Exception as e:
        logger.warning("answer_node_llm_error", error=str(e))
        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = "已生成回复"
        return {
            "current_step": "answer_done",
            "is_completed": True,
            "reply_text": _fallback_reply(),
            "ui_events": [ui_thinking],
        }

    ui_thinking["data"]["steps"][0]["status"] = "done"
    tool_calls = getattr(response, "tool_calls", None) or []

    if tool_calls:
        # 有工具调用：把 AIMessage 写入 messages，等 ToolNode 执行
        ui_thinking["data"]["steps"][0]["detail"] = f"调用工具：{tool_calls[0].get('name', '')}"
        return {
            "messages": [response],  # add_messages 会追加
            "current_step": "answer_tool_calling",
            "ui_events": [ui_thinking],
        }
    else:
        # 无工具调用：最终文本回复
        ui_thinking["data"]["steps"][0]["detail"] = "已生成回复"
        return {
            "messages": [response],
            "current_step": "answer_done",
            "is_completed": True,
            "reply_text": response.content,
            "ui_events": [ui_thinking],
        }


def _fallback_reply() -> str:
    return (
        "您好！我目前支持以下操作：\n\n"
        "- 📦 **退款申请**：「订单号 789012 申请退款，质量问题」\n"
        "- 🔍 **查询状态**：「查询订单 789012 的退款进度」\n\n"
        "请重新描述您的需求。"
    )


def route_answer(state: dict) -> str:
    """
    answer_node 后的路由：
    - 最后一条 AI message 有 tool_calls → 执行工具（answer_tools）
    - 否则，检查 intent：实质性意图 → summarize；闲聊 → 直接 END
    """
    from app.agent.utils import get_state_val
    messages = state.get("messages", [])
    if messages:
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls:
            return "tools"

    intent = get_state_val(state, "intent", "other")
    if intent in ("query_order", "query_policy", "refund"):
        return "summarize"
    return "summarize"  # 总走摘要，由 should_summarize 内部过滤
