"""
节点3：风控评估
调用 check_risk_level() 工具，决定是否需要人工审批
"""

import asyncio

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tools.refund_tools import check_risk_level
from app.core.idempotency import acquire_idempotency_key, stable_idempotency_key
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, TicketStatus, User, UserRole, UserMemory
from sqlalchemy import select

logger = get_logger(__name__)


async def _upsert_ticket(order_id: str, user_role: str, thread_id: str, reason: str) -> int:
    """
    幂等创建工单记录：同一 thread_id 不重复插入。
    返回 Ticket DB 主键（integer）。
    """
    idem_key = stable_idempotency_key("ticket", order_id, thread_id, reason)
    acquired = await acquire_idempotency_key(idem_key, ttl_seconds=86400)
    if not acquired:
        for _ in range(5):
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Ticket).where(Ticket.thread_id == thread_id))
                existing = result.scalar_one_or_none()
                if existing:
                    logger.info("ticket_duplicate_redis_hit", ticket_id=existing.id, idempotency_key=idem_key)
                    return existing.id
            await asyncio.sleep(0.05)
        logger.warning("ticket_duplicate_in_flight", thread_id=thread_id, idempotency_key=idem_key)
        return 0

    async with AsyncSessionLocal() as session:
        # 如果同一 thread 已有工单，直接复用
        stmt = select(Ticket).where(Ticket.thread_id == thread_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info("ticket_duplicate_db_hit", ticket_id=existing.id, idempotency_key=idem_key)
            return existing.id

        # 根据前端角色找对应的 DB 用户
        _role_map = {
            "MANAGER": UserRole.MANAGER,
            "AGENT":   UserRole.AGENT,
            "USER":    UserRole.USER,
        }
        db_role = _role_map.get(user_role.upper() if user_role else "AGENT", UserRole.AGENT)
        stmt2 = select(User).where(User.role == db_role)
        r2 = await session.execute(stmt2)
        db_user = r2.scalars().first()

        # 如果找不到对应角色的用户，退而求其次取第一个用户
        if not db_user:
            stmt3 = select(User)
            r3 = await session.execute(stmt3)
            db_user = r3.scalars().first()

        if not db_user:
            logger.warning("no_user_found_for_ticket_creation")
            return 0

        ticket = Ticket(
            order_id=order_id,
            requester_id=db_user.id,
            thread_id=thread_id,
            status=TicketStatus.PENDING,
            reason=reason,
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        logger.info("ticket_created", ticket_id=ticket.id, order_id=order_id, idempotency_key=idem_key)
        return ticket.id


async def _load_user_memory(user_id: str) -> dict:
    """
    读取 user_memory 表中的持久化用户画像。
    找不到记录时返回空默认值（不阻断主流程）。
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {}

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            mem = result.scalar_one_or_none()
            if mem is None:
                return {}
            return {
                "refund_count": mem.refund_count,
                "rejected_count": mem.rejected_count,
                "fraud_flag": mem.fraud_flag,
                "last_refund_at": mem.last_refund_at.isoformat() if mem.last_refund_at else None,
                "notes": mem.notes,
            }
    except Exception as e:
        logger.warning("load_user_memory_failed", error=str(e), user_id=user_id)
        return {}


async def check_risk_node(state: AgentState) -> dict:
    """
    风控评估节点
    根据金额、用户历史、退款原因计算风险分
    决定走自动审批还是人工审批流程
    """
    logger.info(
        "node_start",
        node="check_risk",
        order_id=get_state_val(state, "order_id"),
        amount=get_state_val(state, "order_amount", 0),
    )

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "checking_risk",
                    "label": "风控评估",
                    "status": "running",
                    "detail": f"正在评估退款风险（金额 ¥{get_state_val(state, 'order_amount', 0)}）...",
                }
            ]
        },
    }

    try:
        risk_data = check_risk_level.invoke({
            "order_id": get_state_val(state, "order_id", ""),
            "amount": get_state_val(state, "order_amount", 0),
            "user_id": get_state_val(state, "user_id", "unknown"),
            "reason": get_state_val(state, "refund_reason", "other"),
        })

        # 读取跨会话用户记忆，补充风控评分
        user_id = get_state_val(state, "user_id", "unknown")
        user_mem = await _load_user_memory(user_id)
        if user_mem:
            if user_mem.get("fraud_flag"):
                # 欺诈标记：强制人工，风险分拉满
                risk_data["riskScore"] = max(risk_data.get("riskScore", 0), 90)
                risk_data["riskLevel"] = "high"
                risk_data.setdefault("reasons", []).append("用户画像：历史欺诈标记")
            elif user_mem.get("refund_count", 0) >= 5:
                # 高频退款用户：+15 分
                risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 15)
                risk_data.setdefault("reasons", []).append(
                    f"用户画像：历史退款 {user_mem['refund_count']} 次（高频）"
                )
            elif user_mem.get("rejected_count", 0) >= 2:
                # 多次被拒：+10 分
                risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 10)
                risk_data.setdefault("reasons", []).append(
                    f"用户画像：历史被拒 {user_mem['rejected_count']} 次"
                )
            logger.info("user_memory_applied", user_id=user_id, mem=user_mem)

        # 重新计算 autoApprove（分数 >= 50 需人工）
        risk_score = risk_data.get("riskScore", 0)
        if risk_score >= 50:
            risk_data["autoApprove"] = False

        requires_human = not risk_data.get("autoApprove", True)

        logger.info(
            "risk_evaluated",
            risk_score=risk_data.get("riskScore"),
            risk_level=risk_data.get("riskLevel"),
            requires_human=requires_human,
        )

        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = (
            f"风险评分：{risk_data.get('riskScore')} 分（{risk_data.get('riskLevel')}），"
            f"{'需要人工审批' if requires_human else '自动审批'}"
        )

        # 生成 RiskAlert UI 组件
        ui_risk_alert = {
            "type": "risk_alert",
            "data": risk_data,
        }

        events = [ui_thinking, ui_risk_alert]

        # 在 DB 中幂等创建工单（同一 thread 不重复）
        order_id   = get_state_val(state, "order_id", "")
        user_role  = get_state_val(state, "user_role", "AGENT")
        thread_id  = get_state_val(state, "thread_id", "")
        reason     = get_state_val(state, "refund_reason", "other")
        try:
            db_ticket_id = await _upsert_ticket(order_id, user_role, thread_id, reason)
            logger.info("upsert_ticket_result", db_ticket_id=db_ticket_id, order_id=order_id)
        except Exception as ticket_err:
            logger.error("upsert_ticket_failed", error=str(ticket_err), order_id=order_id)
            import traceback; traceback.print_exc()
            db_ticket_id = 0

        # 如果需要人工审批，额外发送 ApprovalPanel 组件
        if requires_human:
            events.append({
                "type": "approval_panel",
                "data": {
                    "ticketId": db_ticket_id or order_id,
                    "threadId": thread_id,
                    "orderAmount": get_state_val(state, "order_amount", 0),
                    "riskScore": risk_data.get("riskScore"),
                },
            })

        return {
            "ticket_id": str(db_ticket_id) if db_ticket_id else order_id,
            "risk_score": risk_data.get("riskScore", 0),
            "risk_level": risk_data.get("riskLevel", "low"),
            "risk_reasons": risk_data.get("reasons", []),
            "requires_human_approval": requires_human,
            "current_step": "check_risk_done",
            "ui_events": events,
        }

    except Exception as e:
        logger.error("check_risk_error", error=str(e))
        return {
            "error_message": f"风控评估失败: {e}",
            "current_step": "check_risk_error",
            "ui_events": [ui_thinking],
        }
