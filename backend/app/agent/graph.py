"""
LangGraph 工单处理 Agent 主图（多意图版本 + 并行节点 + 真 ReAct ToolNode）

流程：
  START
    │
    ▼
  [classify_intent]  ← 意图识别，提取订单号、原因、intent
    │
    ├── intent=refund       ──► [lookup_order] → [check_risk] ──┐
    │                                                 ↕ (并行)  │
    │                              [fetch_user_history] ────────┘
    │                                           ↓ fan-in
    │                               (低风险)  [make_risk_decision]  (高风险)
    │                                   ↙                         ↘
    │                         [execute_refund]             [human_review] ← INTERRUPT
    │                                │                   批准↗  拒绝↘
    │                                ▼                            │
    │                       [send_notification]                   │
    │                                └────────────────────────────┘
    │                                           ↓
    │                               [summarize_session] → END
    │
    ├── intent=query_order  ──► [answer_node] ──► (tool_calls?) ──► [answer_tools]
    │                                                                      │  ↑
    │                                                             loop ────┘  │
    │                                ▼ (no tool_calls)                        │
    │                        [summarize_session?] → END                       │
    │
    └── intent=other        ──► [answer_node] → [summarize_session?] → END
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

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

from app.agent.state import AgentState
from app.agent.utils import get_state_val

from app.agent.nodes.classifier import classify_intent_node
from app.agent.nodes.order_lookup import lookup_order_node
from app.agent.nodes.risk_check import check_risk_node
from app.agent.nodes.user_history import fetch_user_history_node
from app.agent.nodes.human_review import human_review_node, should_continue_after_review
from app.agent.nodes.refund import execute_refund_node
from app.agent.nodes.notification import send_notification_node
from app.agent.nodes.answer import answer_node, route_answer, ANSWER_TOOLS
from app.agent.nodes.policy import answer_policy_node
from app.agent.nodes.summarize import summarize_session_node, should_summarize
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def route_after_classify(state: dict) -> str:
    """
    意图识别之后的路由逻辑：
    - refund        → lookup_order（退款流程）
    - query_order   → answer_node（查询工单状态）
    - query_policy  → answer_policy_node（RAG 政策查询）
    - 其他          → answer_node（通用回答）
    """
    intent = get_state_val(state, "intent", "other")
    logger.info("intent_routing", intent=intent, order_id=get_state_val(state, "order_id"))
    if intent == "refund":
        return "lookup_order"
    if intent == "query_policy":
        return "answer_policy_node"
    return "answer_node"


def route_after_lookup(state: dict) -> str:
    """
    订单查询之后的路由逻辑：
    - 查询失败 → summarize_session
    - 查询成功 → 并行执行 check_risk + fetch_user_history
    """
    step = get_state_val(state, "current_step", "")
    if "error" in step or not get_state_val(state, "order_amount"):
        logger.info("lookup_failed_stopping", step=step)
        return "end"
    return "parallel_risk"


def route_after_risk(state: dict) -> str:
    """
    风控节点之后的路由逻辑（合并 user_history 后决策）：
    - 需要人工审批 → human_review
    - 自动审批 → execute_refund
    """
    user_history = get_state_val(state, "user_history") or {}
    requires_human = get_state_val(state, "requires_human_approval", False)

    if user_history.get("has_fraud_flag"):
        logger.info("risk_routing", decision="human_review_fraud_flag")
        return "human_review"

    if requires_human:
        logger.info(
            "risk_routing",
            decision="human_review",
            risk_score=get_state_val(state, "risk_score"),
        )
        return "human_review"

    logger.info(
        "risk_routing",
        decision="auto_approve",
        risk_score=get_state_val(state, "risk_score"),
    )
    return "execute_refund"


def build_graph(checkpointer=None) -> StateGraph:
    """
    构建 LangGraph 工单处理图（含并行节点 + 真 ReAct ToolNode 循环）

    Args:
        checkpointer: LangGraph Checkpointer 实例

    Returns:
        编译后的 CompiledGraph
    """
    builder = StateGraph(AgentState)

    # ---- 注册所有节点 ----
    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("answer_node", answer_node)
    # ToolNode：自动执行 answer_node 的 tool_calls，结果写入 state.messages
    builder.add_node("answer_tools", ToolNode(tools=ANSWER_TOOLS))
    builder.add_node("answer_policy_node", answer_policy_node)
    builder.add_node("lookup_order", lookup_order_node)
    # 并行节点：check_risk + fetch_user_history 同时执行（fan-out）
    builder.add_node("check_risk", check_risk_node)
    builder.add_node("fetch_user_history", fetch_user_history_node)
    builder.add_node("human_review", human_review_node)
    builder.add_node("execute_refund", execute_refund_node)
    builder.add_node("send_notification", send_notification_node)
    # 会话摘要（仅对实质性会话触发）
    builder.add_node("summarize_session", summarize_session_node)

    # ---- 定义边（流程）----
    builder.set_entry_point("classify_intent")

    # 意图路由：classify_intent 后分叉
    builder.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {
            "lookup_order":        "lookup_order",
            "answer_node":         "answer_node",
            "answer_policy_node":  "answer_policy_node",
        },
    )

    # answer_node ReAct 循环：
    #   有 tool_calls → answer_tools → answer_node（继续）
    #   无 tool_calls → should_summarize → summarize_session or END
    builder.add_conditional_edges(
        "answer_node",
        route_answer,
        {
            "tools":     "answer_tools",
            "summarize": "summarize_session",
        },
    )
    builder.add_edge("answer_tools", "answer_node")  # 工具执行后回到 answer_node

    # policy 节点：有实质内容 → 摘要
    builder.add_conditional_edges(
        "answer_policy_node",
        should_summarize,
        {
            "summarize": "summarize_session",
            "end":       END,
        },
    )

    # 摘要节点统一结束
    builder.add_edge("summarize_session", END)

    # 订单查询后：成功 → 并行风控；失败 → 摘要
    builder.add_conditional_edges(
        "lookup_order",
        route_after_lookup,
        {
            # LangGraph 0.6.x 的条件边目标需为单节点，不能直接返回节点列表
            # 先进入用户历史节点，再通过既有边汇入 check_risk
            "parallel_risk": "fetch_user_history",
            "end": "summarize_session",
        },
    )

    # 风控 + 用户历史汇合后（fan-in）→ 路由决策
    builder.add_conditional_edges(
        "check_risk",
        route_after_risk,
        {
            "human_review":   "human_review",
            "execute_refund": "execute_refund",
        },
    )
    builder.add_edge("fetch_user_history", "check_risk")

    # 人工审批后：批准 → 退款；拒绝 → 摘要
    builder.add_conditional_edges(
        "human_review",
        should_continue_after_review,
        {
            "execute_refund": "execute_refund",
            "end":            "summarize_session",
        },
    )

    builder.add_edge("execute_refund", "send_notification")
    builder.add_edge("send_notification", "summarize_session")

    # ---- 编译（注入 Checkpointer）----
    compile_kwargs = {}
    if checkpointer:
        compile_kwargs["checkpointer"] = checkpointer
        compile_kwargs["interrupt_before"] = ["human_review"]

    return builder.compile(**compile_kwargs)


# ---- 全局图实例（延迟初始化，避免模块导入时连接失败阻断启动）----
# 真正的初始化在 main.py lifespan 里完成，这里只提供同步的 fallback 供导入时使用
def _build_default_graph():
    upstash_url = settings.upstash_redis_url or ""
    if upstash_url and _REDIS_CP_AVAILABLE:
        try:
            redis_cp = RedisSaver(redis_url=upstash_url)
            redis_cp.setup()
            logger.info("checkpointer_redis_active", url=upstash_url[:30] + "...")
            return build_graph(checkpointer=redis_cp)
        except Exception as e:
            logger.warning("redis_checkpointer_failed_fallback_memory", error=str(e))
    logger.info("checkpointer_memory_active")
    return build_graph(checkpointer=MemorySaver())


ticket_graph = _build_default_graph()


async def create_postgres_graph(db_connection_string: str):
    """
    生产环境：使用 PostgreSQL Checkpointer
    状态持久化到数据库，支持服务重启后恢复
    """
    if not _POSTGRES_AVAILABLE:
        raise RuntimeError("langgraph-checkpoint-postgres 未安装")
    async with await AsyncPostgresSaver.from_conn_string(
        db_connection_string.replace("+asyncpg", "")
    ) as checkpointer:
        await checkpointer.setup()
        return build_graph(checkpointer=checkpointer)
