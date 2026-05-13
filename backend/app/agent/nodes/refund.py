"""
节点5：执行退款
调用 execute_refund() 工具，实际发起退款
"""

import uuid
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tools.refund_tools import execute_refund
from app.core.idempotency import acquire_idempotency_key, release_idempotency_key, stable_idempotency_key
from app.core.logging import get_logger
from app.db.ticket_repository import complete_ticket, deterministic_refund_id, get_refund_log_by_refund_id, record_refund_once
from app.db.database import AsyncSessionLocal
from app.db.models import UserMemory
from datetime import datetime
from sqlalchemy import select

logger = get_logger(__name__)


async def _update_user_memory_refund(user_id: str) -> None:
    """退款成功后，更新用户跨会话记忆（refund_count + last_refund_at）"""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            mem = result.scalar_one_or_none()
            now = datetime.utcnow()
            if mem is None:
                mem = UserMemory(
                    user_id=uid,
                    refund_count=1,
                    rejected_count=0,
                    fraud_flag=False,
                    last_refund_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(mem)
            else:
                mem.refund_count = (mem.refund_count or 0) + 1
                mem.last_refund_at = now
                mem.updated_at = now
            await session.commit()
    except Exception as e:
        logger.warning("update_user_memory_refund_failed", error=str(e), user_id=user_id)


async def execute_refund_node(state: AgentState) -> dict:
    """
    退款执行节点
    调用退款接口，并生成 RefundTimeline UI 组件
    """
    logger.info(
        "node_start",
        node="execute_refund",
        order_id=get_state_val(state, "order_id"),
        amount=get_state_val(state, "order_amount", 0),
    )

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "executing_refund",
                    "label": "执行退款",
                    "status": "running",
                    "detail": f"正在退款 ¥{get_state_val(state, 'order_amount', 0)} 到原支付账户...",
                }
            ]
        },
    }

    ticket_id = get_state_val(state, "ticket_id") or str(uuid.uuid4())
    order_id = get_state_val(state, "order_id", "")
    amount = float(get_state_val(state, "order_amount", 0) or 0)
    refund_id = deterministic_refund_id(order_id, str(ticket_id), amount)
    idem_key = stable_idempotency_key("refund", order_id, ticket_id, f"{amount:.2f}")

    try:
        try:
            existing_refund = await get_refund_log_by_refund_id(refund_id)
        except Exception as e:
            logger.warning("refund_db_idempotency_lookup_failed", error=str(e), refund_id=refund_id)
            existing_refund = None
        if existing_refund:
            logger.info("refund_duplicate_db_hit", refund_id=refund_id, ticket_id=ticket_id)
            ui_thinking["data"]["steps"][0]["status"] = "done"
            ui_thinking["data"]["steps"][0]["detail"] = f"退款单号 {refund_id} 已存在，跳过重复执行"
            return {
                "ticket_id": ticket_id,
                "refund_id": refund_id,
                "refund_success": True,
                "refund_message": "Duplicate refund request skipped by DB idempotency key.",
                "current_step": "execute_refund_done",
                "idempotency_key": idem_key,
                "idempotent_replay": True,
                "ui_events": [ui_thinking],
            }

        acquired = await acquire_idempotency_key(idem_key, ttl_seconds=86400)
        if not acquired:
            logger.warning("refund_duplicate_redis_hit", refund_id=refund_id, idem_key=idem_key)
            ui_thinking["data"]["steps"][0]["status"] = "done"
            ui_thinking["data"]["steps"][0]["detail"] = f"退款请求正在处理或已处理，幂等键 {idem_key[-8:]}"
            return {
                "ticket_id": ticket_id,
                "refund_id": refund_id,
                "refund_success": True,
                "refund_message": "Duplicate refund request skipped by Redis idempotency key.",
                "current_step": "execute_refund_done",
                "idempotency_key": idem_key,
                "idempotent_replay": True,
                "ui_events": [ui_thinking],
            }

        refund_data = execute_refund.invoke({
            "order_id": order_id,
            "amount": amount,
            "ticket_id": ticket_id,
        })

        logger.info(
            "refund_executed",
            refund_id=refund_data.get("refundId"),
            success=refund_data.get("success"),
        )

        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = (
            f"退款单号 {refund_data.get('refundId')} 已提交"
        )

        # 生成退款进度时间线
        now = datetime.now().isoformat()
        ui_timeline = {
            "type": "refund_timeline",
            "data": {
                "steps": [
                    {
                        "label": "提交退款申请",
                        "description": f"工单 #{ticket_id[:8]}",
                        "status": "completed",
                        "timestamp": now,
                    },
                    {
                        "label": "风控审核通过",
                        "description": f"风险评分：{get_state_val(state, 'risk_score', 0)} 分",
                        "status": "completed",
                        "timestamp": now,
                    },
                    {
                        "label": "退款处理中",
                        "description": f"退款单号：{refund_data.get('refundId')}",
                        "status": "current",
                        "timestamp": now,
                    },
                    {
                        "label": "退款到账",
                        "description": f"预计 {refund_data.get('estimatedDays', 3)} 个工作日",
                        "status": "pending",
                    },
                ]
            },
        }

        # 更新 Ticket 状态为 COMPLETED
        try:
            db_ticket_id = await complete_ticket(
                ticket_id=ticket_id,
                order_id=order_id,
            )
            if isinstance(db_ticket_id, int):
                await record_refund_once(
                    ticket_id=db_ticket_id,
                    refund_id=refund_data.get("refundId", refund_id),
                    amount=amount,
                )
        except Exception as e:
            logger.error("ticket_complete_error", error=str(e))

        # 更新用户跨会话记忆（退款计数 + 最后退款时间）
        await _update_user_memory_refund(get_state_val(state, "user_id", "unknown"))

        return {
            "ticket_id": ticket_id,
            "refund_id": refund_data.get("refundId", ""),
            "refund_success": refund_data.get("success", False),
            "refund_message": refund_data.get("message", ""),
            "current_step": "execute_refund_done",
            "idempotency_key": idem_key,
            "ui_events": [ui_thinking, ui_timeline],
        }

    except Exception as e:
        await release_idempotency_key(idem_key)
        logger.error("execute_refund_error", error=str(e))
        return {
            "error_message": f"退款执行失败: {e}",
            "refund_success": False,
            "current_step": "execute_refund_error",
            "ui_events": [ui_thinking],
        }
