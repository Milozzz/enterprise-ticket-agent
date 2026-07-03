import asyncio
from datetime import datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog, RefundLog, Ticket, TicketStatus, User, UserRole
from app.erp.process_generator import seed_return_to_refund_demo


async def seed_data():
    settings = get_settings()
    print(f"Using database: {settings.database_url}")

    async with AsyncSessionLocal() as session:
        users = [
            User(name="Agent Li", email="agent@example.com", role=UserRole.AGENT),
            User(name="Manager Wang", email="manager@example.com", role=UserRole.MANAGER),
            User(name="Demo User", email="user@example.com", role=UserRole.USER),
        ]

        for user in users:
            result = await session.execute(select(User).where(User.email == user.email))
            if not result.scalar_one_or_none():
                print(f"Adding user: {user.email}")
                session.add(user)
        await session.commit()

        user_demo = (
            await session.execute(select(User).where(User.email == "user@example.com"))
        ).scalar_one()

        erp_seed = await seed_return_to_refund_demo(session)
        print(f"Seeded canonical Mini ERP dataset: {erp_seed['case_count']} cases")

        demo_tickets = [
            Ticket(order_id="ERP-ORD-1001", requester_id=user_demo.id, thread_id="demo-thread-auto-refund", status=TicketStatus.COMPLETED, reason="damaged"),
            Ticket(order_id="ERP-ORD-1003", requester_id=user_demo.id, thread_id="demo-thread-high-risk", status=TicketStatus.PENDING, reason="other"),
            Ticket(order_id="ERP-ORD-1002", requester_id=user_demo.id, thread_id="demo-thread-rejected", status=TicketStatus.REJECTED, reason="not_received"),
        ]
        for ticket in demo_tickets:
            result = await session.execute(select(Ticket).where(Ticket.thread_id == ticket.thread_id))
            if not result.scalar_one_or_none():
                print(f"Adding ticket: {ticket.thread_id}")
                session.add(ticket)
        await session.commit()

        completed_ticket = (
            await session.execute(select(Ticket).where(Ticket.thread_id == "demo-thread-auto-refund"))
        ).scalar_one_or_none()
        if completed_ticket:
            result = await session.execute(select(RefundLog).where(RefundLog.refund_id == "REFUND_DEMO123456"))
            if not result.scalar_one_or_none():
                session.add(RefundLog(ticket_id=completed_ticket.id, refund_id="REFUND_DEMO123456", amount=299.0))

        audit_rows = [
            ("demo-thread-auto-refund", "classify_intent", 120, True, {"token_usage": {"total_tokens": 180}}),
            ("demo-thread-auto-refund", "lookup_order", 45, True, {}),
            ("demo-thread-auto-refund", "check_risk", 80, True, {}),
            ("demo-thread-auto-refund", "execute_refund", 160, True, {}),
            ("demo-thread-auto-refund", "send_notification", 210, True, {}),
            ("demo-thread-high-risk", "check_risk", 95, True, {}),
            ("demo-thread-rejected", "human_review", 70, False, {"error_message": "Demo rejected case"}),
        ]
        for thread_id, node, duration_ms, success, output in audit_rows:
            result = await session.execute(
                select(AuditLog).where(AuditLog.thread_id == thread_id, AuditLog.node_name == node)
            )
            if not result.scalar_one_or_none():
                session.add(AuditLog(
                    thread_id=thread_id,
                    trace_id=thread_id,
                    node_name=node,
                    event_type="seed",
                    input_data={},
                    output_data=output,
                    duration_ms=duration_ms,
                    success=success,
                ))
        await session.commit()
        print("Seed data inserted successfully!")


if __name__ == "__main__":
    asyncio.run(seed_data())
