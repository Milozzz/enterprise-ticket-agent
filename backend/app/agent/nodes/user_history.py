"""
fetch_user_history_node：获取用户历史退款记录

与 check_risk_node 并行执行（LangGraph fan-out/fan-in 模式）。
查询用户的退款历史，注入风控评分参考。
"""

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, TicketStatus, Order
from sqlalchemy import select, func

logger = get_logger(__name__)


async def fetch_user_history_node(state: AgentState) -> dict:
    """
    并行节点：查询用户历史退款记录

    与 check_risk_node 同时执行（LangGraph fan-out 并行）。
    结果合并到 make_risk_decision_node 做最终风控判断。
    """
    user_id = get_state_val(state, "user_id", "unknown")
    order_id = get_state_val(state, "order_id", "")

    logger.info("node_start", node="fetch_user_history", user_id=user_id)

    try:
        async with AsyncSessionLocal() as session:
            # 查询该用户历史工单数量
            result = await session.execute(
                select(func.count(Ticket.id)).join(Order, Ticket.order_id == Order.id)
                .where(Order.user_id == _try_int(user_id))
            )
            total_tickets = result.scalar_one_or_none() or 0

            # 查询已完成退款次数
            result = await session.execute(
                select(func.count(Ticket.id)).join(Order, Ticket.order_id == Order.id)
                .where(
                    Order.user_id == _try_int(user_id),
                    Ticket.status == TicketStatus.COMPLETED,
                )
            )
            completed_refunds = result.scalar_one_or_none() or 0

            # 查询被拒绝次数
            result = await session.execute(
                select(func.count(Ticket.id)).join(Order, Ticket.order_id == Order.id)
                .where(
                    Order.user_id == _try_int(user_id),
                    Ticket.status == TicketStatus.REJECTED,
                )
            )
            rejected_count = result.scalar_one_or_none() or 0

        user_history = {
            "total_tickets": total_tickets,
            "completed_refunds": completed_refunds,
            "rejected_count": rejected_count,
            # 高频用户：历史退款 >= 3 次
            "is_high_frequency": completed_refunds >= 3,
            # 高风险：拒绝次数 >= 2
            "has_fraud_flag": rejected_count >= 2,
        }

        logger.info(
            "user_history_fetched",
            user_id=user_id,
            total=total_tickets,
            completed=completed_refunds,
            rejected=rejected_count,
        )

        return {
            "user_history": user_history,
            "current_step": "fetch_user_history_done",
        }

    except Exception as e:
        logger.warning("fetch_user_history_error", error=str(e), user_id=user_id)
        # 历史记录查询失败不阻断主流程，返回空记录
        return {
            "user_history": {
                "total_tickets": 0,
                "completed_refunds": 0,
                "rejected_count": 0,
                "is_high_frequency": False,
                "has_fraud_flag": False,
            },
            "current_step": "fetch_user_history_done",
        }


def _try_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
