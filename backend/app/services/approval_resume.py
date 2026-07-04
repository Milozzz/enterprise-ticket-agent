from __future__ import annotations

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, TicketStatus, User

logger = get_logger(__name__)


def synthetic_timeline(reviewer_id: str) -> dict:
    return {
        "type": "RefundTimeline",
        "props": {
            "steps": [
                {"label": "提交退款申请", "status": "completed", "description": ""},
                {"label": "审批通过", "status": "completed", "description": f"审批人：{reviewer_id}"},
                {"label": "退款完成", "status": "completed", "description": "已退至原支付账户"},
            ]
        },
    }


async def resolve_operator_id(reviewer_id: str | None, session) -> int | None:
    if not reviewer_id:
        return None
    row = (await session.execute(select(User).where(User.name == reviewer_id))).scalars().first()
    if row:
        return row.id
    row = (await session.execute(select(User).where(User.email == reviewer_id))).scalars().first()
    return row.id if row else None


async def direct_db_approve(
    ticket_id: str | None,
    action: str,
    thread_id: str,
    reviewer_id: str | None = None,
) -> dict:
    try:
        ticket_int_id: int | None = None
        if ticket_id:
            try:
                ticket_int_id = int(ticket_id)
            except (ValueError, TypeError):
                pass

        if ticket_int_id is None:
            async with AsyncSessionLocal() as session:
                ticket = (
                    await session.execute(
                        select(Ticket)
                        .where(Ticket.thread_id == thread_id)
                        .order_by(Ticket.id.desc())
                    )
                ).scalars().first()
                if ticket is None:
                    return {"ok": False, "reason": "no_ticket", "thread_id": thread_id}
                ticket_int_id = ticket.id

        final_status = TicketStatus.COMPLETED if action == "approve" else TicketStatus.REJECTED
        async with AsyncSessionLocal() as session:
            operator_id = await resolve_operator_id(reviewer_id, session)
            result = await session.execute(
                update(Ticket)
                .where(Ticket.id == ticket_int_id)
                .values(status=final_status, operator_id=operator_id)
            )
            await session.commit()
        return {
            "ok": True,
            "ticket_id": ticket_int_id,
            "status": final_status.value,
            "operator_id": operator_id,
            "rowcount": result.rowcount,
            "thread_id": thread_id,
            "action": action,
            "reviewer_id": reviewer_id,
        }
    except Exception as exc:
        logger.error("direct_db_approve_failed", error=str(exc), thread_id=thread_id)
        return {"ok": False, "reason": "db_error", "thread_id": thread_id}
