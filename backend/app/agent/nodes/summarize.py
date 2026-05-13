"""
summarize_session_node：会话结束后压缩对话历史写入 UserMemory.notes

条件触发（should_summarize）：
  - 仅当会话有实质内容时才压缩（intent 为 refund/query_order/query_policy）
  - 纯问候/"other" 意图不触发，避免无意义 LLM 调用增加延迟
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.logging import get_logger
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import UserMemory

logger = get_logger(__name__)
settings = get_settings()

_SUMMARIZE_SYSTEM = """你是一个摘要助手。
将用户和客服的对话内容压缩成一句话摘要（不超过80个字）。
只保留关键信息：用户意图、订单号（若有）、结果。
不要加任何前缀，直接输出摘要文本。
"""

_MERGE_SYSTEM = """你是一个摘要助手。
将两段对话摘要合并成一段，保留所有关键历史信息，不超过150个字。
直接输出合并后的摘要，不要加前缀。
"""


async def _call_llm(system: str, user: str) -> str | None:
    """调用 LLM 生成摘要（8s 超时）"""
    if not settings.google_api_key:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=0.0,
        )
        response = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)]),
            timeout=8.0,
        )
        return response.content.strip()
    except Exception as e:
        logger.warning("summarize_llm_error", error=str(e))
        return None


def _format_messages(messages: list) -> str:
    """将 LangChain messages 转为可读对话文本"""
    lines = []
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "")
        if not content:
            continue
        label = "用户" if role == "human" else "客服"
        lines.append(f"{label}：{content[:200]}")  # 截断超长消息
    return "\n".join(lines) if lines else ""


async def summarize_session_node(state: AgentState) -> dict:
    """
    会话摘要节点

    读取 state.messages → LLM 压缩 → 与旧摘要合并 → 写回 UserMemory.notes
    """
    user_id = get_state_val(state, "user_id", "")

    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        logger.info("summarize_skip_no_user_id", user_id=user_id)
        return {"current_step": "summarize_done"}

    messages = get_state_val(state, "messages", [])
    conversation_text = _format_messages(messages)

    if not conversation_text:
        return {"current_step": "summarize_done"}

    logger.info("summarize_session_start", user_id=user_id, msg_count=len(messages))

    # 1. 压缩本次对话
    new_summary = await _call_llm(_SUMMARIZE_SYSTEM, conversation_text)
    if not new_summary:
        return {"current_step": "summarize_done"}

    # 2. 读取旧摘要，合并
    try:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            mem = result.scalar_one_or_none()
            old_notes = (mem.notes or "") if mem else ""

        if old_notes:
            merged = await _call_llm(
                _MERGE_SYSTEM,
                f"旧摘要：{old_notes}\n\n新摘要：{new_summary}",
            )
            final_notes = merged or f"{old_notes} | {new_summary}"
        else:
            final_notes = new_summary

        # 3. 写回 DB
        now = datetime.utcnow()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            mem = result.scalar_one_or_none()
            if mem is None:
                mem = UserMemory(
                    user_id=uid,
                    refund_count=0,
                    rejected_count=0,
                    fraud_flag=False,
                    notes=final_notes,
                    created_at=now,
                    updated_at=now,
                )
                session.add(mem)
            else:
                mem.notes = final_notes[:500]  # 列最大 500 字符
                mem.updated_at = now
            await session.commit()

        logger.info("summarize_session_done", user_id=user_id, summary_len=len(final_notes))

    except Exception as e:
        logger.warning("summarize_session_db_error", error=str(e), user_id=user_id)

    return {"current_step": "summarize_done"}


def should_summarize(state: dict) -> str:
    """
    路由函数：判断是否需要触发会话摘要。

    只有实质性会话（退款/查询/政策）才写摘要，避免纯闲聊也触发 LLM 调用。
    供 graph.py 中 answer_policy_node 的 conditional_edges 使用。
    """
    from app.agent.utils import get_state_val
    intent = get_state_val(state, "intent", "other")
    messages = state.get("messages", [])

    # 实质性意图 + 至少有一轮对话 → 摘要
    if intent in ("refund", "query_order", "query_policy") and len(messages) >= 2:
        return "summarize"
    return "end"
