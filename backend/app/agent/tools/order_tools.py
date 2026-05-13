"""
订单相关工具 (Function Calling Tools)
使用 Pydantic + Field(description) 确保 LLM 准确理解参数含义
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.db.database import AsyncSessionLocal
from app.db.models import Order
from sqlalchemy import select
import asyncio
import threading


# ── 后台事件循环（供同步 @tool 调用异步 DB）──────────────────────
_loop = asyncio.new_event_loop()

def _start_background_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

_thread = threading.Thread(target=_start_background_loop, args=(_loop,), daemon=True)
_thread.start()


# ── Input Schema ──────────────────────────────────────────────────
class GetOrderDetailInput(BaseModel):
    order_id: str = Field(
        description="订单号，纯数字字符串，例如 '789012'。从用户消息中提取。"
    )


# ── Tool ──────────────────────────────────────────────────────────
@tool(args_schema=GetOrderDetailInput)
def get_order_detail(order_id: str) -> dict:
    """
    查询指定订单的完整详情。
    返回商品列表、订单状态、实付金额、收货地址及物流单号。
    在处理退款申请前必须先调用此工具获取订单信息。
    """
    future = asyncio.run_coroutine_threadsafe(
        _get_order_detail_async(order_id), _loop
    )
    return future.result()


async def _get_order_detail_async(order_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        stmt = select(Order).where(Order.id == order_id)
        result = await session.execute(stmt)
        order = result.scalar_one_or_none()

        if not order:
            return {"error": f"未找到订单 #{order_id}，请确认订单号是否正确"}

        return {
            "id": order.id,
            "userId": str(order.user_id),
            "status": order.status,
            "items": order.items,
            "totalAmount": order.amount,
            "shippingAddress": order.shipping_address,
            "createdAt": order.created_at.isoformat() if order.created_at else None,
            "trackingNumber": getattr(order, "tracking_number", None),
            "carrier": getattr(order, "carrier", None),
        }
