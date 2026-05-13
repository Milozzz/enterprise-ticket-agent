"""
工单状态查询工具 (Function Calling Tools)
使用 Pydantic + Field(description) 确保 LLM 准确理解参数含义
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, User, Order, TicketStatus
from sqlalchemy import case, select
import asyncio
import threading


# ── 后台事件循环 ──────────────────────────────────────────────────
_loop = asyncio.new_event_loop()

def _start_background_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

_thread = threading.Thread(target=_start_background_loop, args=(_loop,), daemon=True)
_thread.start()


# ── Input Schema ──────────────────────────────────────────────────
class GetTicketStatusInput(BaseModel):
    order_id: str = Field(
        description=(
            "订单号，用于查询该订单关联的退款工单状态（多笔时优先展示已完成的记录）。"
            "例如 '789012'。"
        )
    )


# ── Status Label Map ──────────────────────────────────────────────
_STATUS_LABELS = {
    TicketStatus.PENDING:           ("⏳", "待处理", "工单已创建，等待处理"),
    TicketStatus.APPROVED:          ("✅", "已批准", "退款申请已获批，正在处理"),
    TicketStatus.REJECTED:          ("❌", "已拒绝", "退款申请未通过审核"),
    TicketStatus.COMPLETED:         ("🎉", "已完成", "退款已成功退至原支付账户"),
}


# ── Tool ──────────────────────────────────────────────────────────
@tool(args_schema=GetTicketStatusInput)
def get_ticket_status(order_id: str) -> dict:
    """
    查询指定订单的退款工单状态。
    同一订单若有多条工单，优先返回已完成/已批准等终态记录，避免新会话产生的待审草稿盖住历史结果。
    """
    future = asyncio.run_coroutine_threadsafe(
        _get_ticket_status_async(order_id), _loop
    )
    return future.result()


async def _get_ticket_status_async(order_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        # 同一订单可能有多条工单（每次新 thread 会新建 PENDING）。按业务优先级取「最应展示」的一条：
        # COMPLETED > APPROVED > REJECTED > PENDING，同优先级再按创建时间新→旧
        _prio = case(
            (Ticket.status == TicketStatus.COMPLETED, 4),
            (Ticket.status == TicketStatus.APPROVED, 3),
            (Ticket.status == TicketStatus.REJECTED, 2),
            else_=1,
        )
        stmt = (
            select(Ticket)
            .where(Ticket.order_id == order_id)
            .order_by(_prio.desc(), Ticket.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        ticket = result.scalar_one_or_none()

        if not ticket:
            # 没有工单，查询订单本身是否存在
            order_stmt = select(Order).where(Order.id == order_id)
            order_result = await session.execute(order_stmt)
            order = order_result.scalar_one_or_none()

            if not order:
                return {"error": f"未找到订单 #{order_id}，请确认订单号是否正确"}

            return {
                "orderId": order_id,
                "orderStatus": order.status,
                "hasTicket": False,
                "message": f"订单 #{order_id} 目前没有退款工单记录",
            }

        # 查询审批人姓名
        operator_name = None
        if ticket.operator_id:
            op_stmt = select(User).where(User.id == ticket.operator_id)
            op_result = await session.execute(op_stmt)
            operator = op_result.scalar_one_or_none()
            operator_name = operator.name if operator else f"用户#{ticket.operator_id}"

        # 查询申请人姓名
        requester_name = None
        if ticket.requester_id:
            req_stmt = select(User).where(User.id == ticket.requester_id)
            req_result = await session.execute(req_stmt)
            requester = req_result.scalar_one_or_none()
            requester_name = requester.name if requester else None

        status = ticket.status
        emoji, label, desc = _STATUS_LABELS.get(
            status, ("📋", str(status), "处理中")
        )

        return {
            "hasTicket": True,
            "ticketId": ticket.id,
            "orderId": order_id,
            "status": status.value if hasattr(status, "value") else str(status),
            "statusLabel": label,
            "statusEmoji": emoji,
            "statusDescription": desc,
            "reason": ticket.reason,
            "requesterName": requester_name,
            "operatorName": operator_name,
            "createdAt": ticket.created_at.isoformat() if ticket.created_at else None,
            "message": (
                f"订单 #{order_id} 工单状态：{emoji} {label}\n{desc}"
                + (f"\n审批人：{operator_name}" if operator_name else "")
            ),
        }
