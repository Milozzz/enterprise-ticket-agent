"""
节点6：发送通知
调用 send_notification() 工具，邮件通知财务
"""

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tools.notification_tools import send_notification
from app.agent.tools.notification_tools import _idempotency_key
from app.core.config import get_settings
from app.core.idempotency import acquire_idempotency_key, stable_idempotency_key
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


async def send_notification_node(state: AgentState) -> dict:
    """
    通知发送节点
    退款成功后发邮件通知财务，并生成 EmailPreview UI 组件
    """
    logger.info("node_start", node="send_notification", refund_id=get_state_val(state, "refund_id"))

    if not get_state_val(state, "refund_success"):
        logger.warning("skip_notification", reason="refund_not_successful")
        return {"current_step": "skip_notification"}

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "sending_notification",
                    "label": "发送通知",
                    "status": "running",
                    "detail": "正在发送邮件通知财务团队...",
                }
            ]
        },
    }

    finance_email = settings.gmail_user or "finance@company.com"
    order_id = get_state_val(state, "order_id", "")
    refund_id = get_state_val(state, "refund_id", "")
    notify_key = stable_idempotency_key("notification", _idempotency_key(order_id, refund_id))

    try:
        acquired = await acquire_idempotency_key(notify_key, ttl_seconds=86400 * 7)
        if not acquired:
            logger.warning("notification_duplicate_redis_skipped", refund_id=refund_id, idem_key=notify_key)
            ui_thinking["data"]["steps"][0]["status"] = "done"
            ui_thinking["data"]["steps"][0]["detail"] = "通知已发送过，跳过重复邮件"
            return {
                "notification_sent": True,
                "notification_email_id": f"DEDUP_{notify_key[-12:]}",
                "notification_to": finance_email,
                "is_completed": True,
                "current_step": "completed",
                "idempotency_key": notify_key,
                "idempotent_replay": True,
                "ui_events": [ui_thinking],
            }

        notif_data = send_notification.invoke({
            "to_email": finance_email,
            "order_id": order_id,
            "refund_amount": get_state_val(state, "order_amount", 0),
            "refund_id": refund_id,
            "ticket_id": get_state_val(state, "ticket_id", ""),
        })

        logger.info("notification_sent", email_id=notif_data.get("email_id"))

        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = (
            f"邮件已发送至 {finance_email}"
        )

        # 生成邮件预览 UI（直接使用工具返回的 subject/body，避免依赖 state 字段）
        ui_email = {
            "type": "email_preview",
            "data": {
                "to": notif_data.get("to"),
                "subject": notif_data.get("subject"),
                "body": (
                    f"订单号：{get_state_val(state, 'order_id', '')} 退款处理完成\n"
                    f"退款金额：¥{get_state_val(state, 'order_amount', 0)}\n"
                    f"退款单号：{get_state_val(state, 'refund_id', '')}\n"
                    f"工单编号：{get_state_val(state, 'ticket_id', '')}"
                ),
                "sentAt": notif_data.get("sent_at"),
                "status": "sent",
            },
        }

        return {
            "notification_sent": True,
            "notification_email_id": notif_data.get("email_id", ""),
            "notification_to": finance_email,          # 供链路回放展示（写库前会被脱敏）
            "is_completed": True,
            "current_step": "completed",
            "idempotency_key": notify_key,
            "ui_events": [ui_thinking, ui_email],
        }

    except Exception as e:
        import traceback
        logger.error("send_notification_error", error=str(e), traceback=traceback.format_exc())
        # 通知发送失败不影响退款结果，只记录日志
        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = f"通知发送失败（不影响退款）：{e}"
        return {
            "notification_sent": False,
            "is_completed": True,
            "current_step": "completed_notification_failed",
            "ui_events": [ui_thinking],
        }
