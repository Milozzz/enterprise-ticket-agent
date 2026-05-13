"""
节点2：订单查询
调用 get_order_detail() 工具，获取订单信息
"""

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tools.order_tools import get_order_detail
from app.core.logging import get_logger

logger = get_logger(__name__)


async def lookup_order_node(state: AgentState) -> dict:
    """
    订单查询节点
    调用工具函数查询订单，并生成 OrderCard UI 组件渲染指令
    """
    order_id = get_state_val(state, "order_id")
    logger.info("node_start", node="lookup_order", order_id=order_id)

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "looking_up_order",
                    "label": "查询订单",
                    "status": "running",
                    "detail": f"正在查询订单 #{order_id}...",
                }
            ]
        },
    }

    if not order_id:
        return {
            "error_message": "未找到订单号，请提供有效的订单号",
            "current_step": "lookup_order_error",
            "ui_events": [ui_thinking],
        }

    try:
        # 调用工具（Function Calling）
        # 注意：这里直接调用 invoke，工具内部已经处理了事件循环桥接
        logger.info("invoking_get_order_detail", order_id=order_id)
        order_data = get_order_detail.invoke({"order_id": order_id})
        logger.info("tool_output", data=order_data)

        if not order_data or "error" in order_data:
            raw_msg = order_data.get("error") if order_data else "订单查询返回空数据"
            ui_thinking["data"]["steps"][0]["status"] = "done"
            ui_thinking["data"]["steps"][0]["detail"] = f"未找到订单 #{order_id}"
            return {
                "error_message": f"未找到订单 #{order_id}，请确认订单号是否正确（原因：{raw_msg}）",
                "reply_text": f"未找到订单 **#{order_id}**，请确认订单号是否正确。\n\n您可以尝试：「订单号 789012 申请退款，质量问题」",
                "current_step": "lookup_order_error",
                "ui_events": [ui_thinking],
            }

        # 更新思考流状态
        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = (
            f"订单金额：¥{order_data.get('totalAmount')}，状态：{order_data.get('status')}"
        )

        # 生成 OrderCard 渲染指令
        ui_order_card = {
            "type": "order_card",
            "data": order_data,
        }

        return {
            "order_detail": order_data,
            "order_amount": order_data.get("totalAmount", 0.0),
            "user_id": order_data.get("userId", get_state_val(state, "user_id", "unknown")),
            "current_step": "lookup_order_done",
            "ui_events": [ui_thinking, ui_order_card],
        }

    except Exception as e:
        logger.error("lookup_order_error", error=str(e), order_id=order_id)
        import traceback
        traceback.print_exc()
        return {
            "error_message": f"订单查询失败: {str(e)}",
            "current_step": "lookup_order_error",
            "ui_events": [ui_thinking],
        }
