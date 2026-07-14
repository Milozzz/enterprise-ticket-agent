"""
LangGraph 状态定义 — 使用 TypedDict 确保 state 始终为 dict
"""

from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

# H3（长对话优化）：事件类列表的累积上限。这些列表只是运行时便利
# （SSE 消费的是节点输出流，权威审计在 audit_logs 表），但 checkpointer
# 每个超步都会全量序列化 state——不设上限的话，长对话里每走一个节点都在
# 往 checkpoint 写一个越来越大的 blob。保留最近 100 条足够任何运行时用途。
_EVENT_HISTORY_CAP = 100


def capped_add(left: list | None, right: list | None) -> list:
    """operator.add 的有界版本：合并后只保留最近 _EVENT_HISTORY_CAP 条。"""
    combined = list(left or []) + list(right or [])
    return combined[-_EVENT_HISTORY_CAP:]


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
    currency: str
    tenant_id: str
    connector_id: str
    open_item_id: str
    refund_request_id: str
    risk_score: int
    risk_level: str
    risk_reasons: list
    risk_precedents: dict  # G1 判例摘要（历史类似案件的人工批准率等），供审批参考
    requires_human_approval: bool
    refund_id: str
    refund_success: bool
    refund_message: str
    saga_id: str
    saga_status: str
    saga_steps: list
    credit_memo_id: str
    clearing_document_id: str
    compensation: dict
    return_authorization_id: str
    return_validation: dict
    inventory_inspection: dict
    inventory_restoration: dict
    inventory_movement_id: str
    reconciliation_result: dict
    final_reconciliation: dict
    notification_sent: bool
    notification_email_id: str
    human_decision: str
    reviewer_id: str
    review_comment: str
    approval_id: str
    approval_stage_index: int
    approval_history: Annotated[list, capped_add]
    current_step: str
    error_message: str
    reply_text: str   # answer_node 的正常回复文本（区别于错误信息）
    is_completed: bool
    ui_events: Annotated[list, capped_add]
    llm_degraded: bool  # 本轮存在 LLM 降级决策（规则 fallback），风控将强制人工审批
    user_role: str  # 注入当前用户角色 (AGENT/MANAGER/USER)
    thread_id: str  # LangGraph thread_id，供节点生成 ApprovalPanel 时传回前端
    trace_id: str   # 端到端追踪 ID，由前端生成，贯穿 UI / DB / Langfuse 三处
    scenario_id: str
    scenario_name: str
    supervisor_decision: dict
    business_request: dict
    approval_type: str
    approval_required: bool
    permission_system: str
    permission_level: str
    reimbursement_amount: float
    reimbursement_category: str
    tool_gateway_events: Annotated[list, capped_add]
    policy_events: Annotated[list, capped_add]
    prompt_events: Annotated[list, capped_add]
    specialist_handoffs: Annotated[list, capped_add]
    task_spec: dict
    plan_graph: dict
    evidence_graph: dict
    evidence_persistence: dict
    human_fallback_task: dict
    verification_result: dict
    replan_count: int
    plan_precedents: list
    plan_generation_method: str
    execution_budget: dict
    execution_journal: Annotated[list, capped_add]
    agent_id: str
    delegation_token: str
    delegation_grant: dict
    data_provenance: dict
    information_flow_events: Annotated[list, capped_add]
    # RAG 政策检索结果（answer_policy_node 填充）
    policy_results: list
    policy_citations: list
    # 用户历史退款记录（fetch_user_history_node 并行填充）
    user_history: dict
