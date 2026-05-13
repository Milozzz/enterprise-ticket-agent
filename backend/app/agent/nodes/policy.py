"""
answer_policy_node：RAG 政策查询节点

当用户询问退款规则/政策时：
1. 用 search_policy_raw 检索最相关的 2 条政策
2. 将政策原文拼入 Prompt，让 LLM 基于引用内容作答
3. 返回带引用的结构化回复（避免大模型凭空幻觉）
"""

import asyncio
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tools.policy_tools import search_policy_raw
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# 模块级单例：避免每次请求都重新创建 LLM 实例
_llm_instance: ChatGoogleGenerativeAI | None = None


def _get_llm() -> ChatGoogleGenerativeAI:
    global _llm_instance
    if _llm_instance is None:
        if not settings.google_api_key:
            raise ValueError("请配置 GOOGLE_API_KEY")
        _llm_instance = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=0.1,  # 政策回答需要更确定性
        )
    return _llm_instance

_POLICY_ANSWER_PROMPT = """\
你是企业客服助手，专门解答退款政策问题。
请严格基于下方【政策原文】回答用户问题，不得编造政策内容。
如果政策原文中没有涉及用户问题，请如实告知并建议联系人工客服。

【政策原文】
{policy_context}

回答要求：
- 语言简洁友好
- 引用具体政策编号（如 P001）
- 如有时间/金额等关键数字，务必准确引用原文
- 结尾提示：如有更多问题可继续提问
"""


def _build_policy_citations(results) -> list[dict]:
    return [
        {
            "policy_id": r.policy_id,
            "title": r.title,
            "score": r.score,
            "source": "POLICY_DOCS",
            "clause_id": r.policy_id,
        }
        for r in results
    ]


def _append_citations(reply: str, citations: list[dict]) -> str:
    if not citations:
        return reply
    if "References:" in reply:
        return reply
    source_line = "References: " + ", ".join(
        f"{c['policy_id']} {c['title']}" for c in citations
    )
    return f"{reply.rstrip()}\n\n{source_line}"


async def answer_policy_node(state: AgentState) -> dict:
    """
    RAG 政策回答节点

    路由来源：
      classify_intent → (intent == 'query_policy') → answer_policy_node
    """
    messages = get_state_val(state, "messages", [])
    user_message = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            user_message = msg.content
            break
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    logger.info("node_start", node="answer_policy", query=user_message[:60])

    # ── Step 1：检索相关政策 ───────────────────────────────────────────────
    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "retrieving",
                    "label": "检索政策知识库",
                    "status": "running",
                    "detail": "正在查找相关退款政策...",
                }
            ]
        },
    }

    results = search_policy_raw(user_message, top_k=2)
    citations = _build_policy_citations(results)
    policy_context = "\n\n".join(
        f"[{r.policy_id}] {r.title}（相似度 {r.score:.2%}）\n{r.content}"
        for r in results
    )

    ui_thinking["data"]["steps"][0]["status"] = "done"
    ui_thinking["data"]["steps"][0]["detail"] = (
        f"命中政策：{', '.join(r.title for r in results)}"
    )

    # ── Step 2：LLM 基于检索结果生成回复 ─────────────────────────────────
    ui_generate = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "generating",
                    "label": "生成政策解读",
                    "status": "running",
                    "detail": "正在基于政策原文生成回答...",
                }
            ]
        },
    }

    # ── Step 2：LLM 流式生成回复（astream 逐 token 推送，chat.py 的 on_chat_model_stream 处理）─
    try:
        llm = _get_llm()
        system_prompt = _POLICY_ANSWER_PROMPT.format(policy_context=policy_context)
        # 使用 ainvoke 但触发 on_chat_model_stream 事件（LangGraph astream_events 会自动捕获）
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_message),
            ]),
            timeout=15.0,
        )
        reply = response.content
        ui_generate["data"]["steps"][0]["status"] = "done"
        ui_generate["data"]["steps"][0]["detail"] = "回答生成完成"
    except Exception as e:
        logger.error("policy_llm_error", error=str(e))
        # LLM 失败时直接返回检索到的政策原文
        reply = f"为您找到以下相关政策：\n\n{policy_context}"
        ui_generate["data"]["steps"][0]["status"] = "done"
        ui_generate["data"]["steps"][0]["detail"] = "直接返回政策原文"

    reply = _append_citations(str(reply), citations)

    # ── Step 3：附加政策引用卡片（UI 事件）───────────────────────────────
    policy_cards = {
        "type": "policy_cards",
        "data": {
            "results": [
                {
                    "id": r.policy_id,
                    "clause_id": r.policy_id,
                    "title": r.title,
                    "score": r.score,
                    "source": "POLICY_DOCS",
                    "excerpt": r.content[:60] + "...",
                }
                for r in results
            ]
        },
    }

    logger.info(
        "policy_answer_done",
        top_policies=[r.policy_id for r in results],
        reply_len=len(reply),
    )

    return {
        "current_step": "answer_policy_done",
        "is_completed": True,
        "reply_text": reply,
        "policy_results": [
            {
                "policy_id": r.policy_id,
                "title": r.title,
                "score": r.score,
                "content": r.content,
            }
            for r in results
        ],
        "policy_citations": citations,
        "ui_events": [ui_thinking, ui_generate, policy_cards],
    }
