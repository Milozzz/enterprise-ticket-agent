"""
Ticket 数据访问层
将工单相关的 DB 操作集中在此，节点只调用函数，不直接操作 session。
"""

import hashlib

from sqlalchemy import update, select
from sqlalchemy.exc import IntegrityError

from app.db.database import AsyncSessionLocal
from app.db.models import RefundLog, Ticket, TicketStatus
from app.core.logging import get_logger

logger = get_logger(__name__)


def ticket_idempotency_key(order_id: str, thread_id: str, reason: str) -> str:
    raw = f"ticket:{order_id}:{thread_id}:{reason}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def refund_idempotency_key(order_id: str, ticket_id: str, amount: float) -> str:
    raw = f"refund:{order_id}:{ticket_id}:{amount:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def deterministic_refund_id(order_id: str, ticket_id: str, amount: float) -> str:
    return f"REFUND_{refund_idempotency_key(order_id, ticket_id, amount)[:12].upper()}"


async def complete_ticket(ticket_id: str, order_id: str) -> int | None:
    """
    将工单状态更新为 COMPLETED。

    ticket_id 可能是整数字符串或 UUID（尚未持久化时），解析策略：
    1. 优先按整数 ID 直接更新
    2. 若解析失败则按 order_id 查找最新工单

    Returns:
        成功时返回 ticket 的整数 ID，找不到工单时返回 None。
    """
    ticket_int_id: int | None = None
    try:
        ticket_int_id = int(ticket_id)
    except (ValueError, TypeError):
        pass

    async with AsyncSessionLocal() as session:
        if ticket_int_id is None and order_id:
            row = (await session.execute(
                select(Ticket)
                .where(Ticket.order_id == order_id)
                .order_by(Ticket.id.desc())
            )).scalars().first()
            if row:
                ticket_int_id = row.id

        if ticket_int_id is None:
            logger.warning("complete_ticket_skipped_no_id", ticket_id=ticket_id, order_id=order_id)
            return None

        await session.execute(
            update(Ticket)
            .where(Ticket.id == ticket_int_id)
            .values(status=TicketStatus.COMPLETED)
        )
        await session.commit()
        logger.info("ticket_completed", ticket_id=ticket_int_id)
        return ticket_int_id


async def get_refund_log_by_refund_id(refund_id: str) -> RefundLog | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RefundLog).where(RefundLog.refund_id == refund_id)
        )
        return result.scalar_one_or_none()


async def record_refund_once(ticket_id: int, refund_id: str, amount: float) -> bool:
    """
    Persist the refund side effect once. RefundLog.refund_id is unique, so DB is
    the source-of-truth idempotency guard even if Redis is unavailable.
    """
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(RefundLog.id).where(RefundLog.refund_id == refund_id)
        )
        if existing:
            logger.info("refund_log_duplicate_skipped", refund_id=refund_id)
            return False

        session.add(RefundLog(ticket_id=ticket_id, refund_id=refund_id, amount=amount))
        try:
            await session.commit()
            logger.info("refund_log_recorded", ticket_id=ticket_id, refund_id=refund_id)
            return True
        except IntegrityError:
            await session.rollback()
            logger.info("refund_log_integrity_duplicate", refund_id=refund_id)
            return False


async def update_ticket_status(ticket_id: int, status: TicketStatus) -> bool:
    """通用工单状态更新，供状态机集成点使用。返回是否成功更新。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Ticket)
            .where(Ticket.id == ticket_id)
            .values(status=status)
        )
        await session.commit()
        updated = result.rowcount > 0
        if updated:
            logger.info("ticket_status_updated", ticket_id=ticket_id, status=status.value)
        else:
            logger.warning("ticket_status_update_miss", ticket_id=ticket_id)
        return updated
