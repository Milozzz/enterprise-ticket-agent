"""
LangGraph 状态定义 — 使用 TypedDict 确保 state 始终为 dict
"""

from typing import Annotated, Literal
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
import operator


class AgentState(TypedDict, total=False):
    """工单处理 Agent 的完整状态"""

    messages: Annotated[list[BaseMessage], add_messages]
    # 意图识别结果：refund / query_order / other
    intent: str
    ticket_id: str
    order_id: str
    user_id: str
    refund_reason: str
    refund_description: str
    order_detail: dict
    order_amount: float
    risk_score: int
    risk_level: str
    risk_reasons: list
    requires_human_approval: bool
    refund_id: str
    refund_success: bool
    refund_message: str
    notification_sent: bool
    notification_email_id: str
    human_decision: str
    reviewer_id: str
    review_comment: str
    current_step: str
    error_message: str
    reply_text: str   # answer_node 的正常回复文本（区别于错误信息）
    is_completed: bool
    ui_events: Annotated[list, operator.add]
    user_role: str  # 注入当前用户角色 (AGENT/MANAGER/USER)
    thread_id: str  # LangGraph thread_id，供节点生成 ApprovalPanel 时传回前端
    trace_id: str   # 端到端追踪 ID，由前端生成，贯穿 UI / DB / Langfuse 三处
    # RAG 政策检索结果（answer_policy_node 填充）
    policy_results: list
    policy_citations: list
    # 用户历史退款记录（fetch_user_history_node 并行填充）
    user_history: dict
