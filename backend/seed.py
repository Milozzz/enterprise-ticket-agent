import asyncio
from datetime import datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog, Order, RefundLog, Ticket, TicketStatus, User, UserRole


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

        orders = [
            Order(
                id="789012",
                user_id=user_demo.id,
                amount=1299.0,
                status="delivered",
                items=[{
                    "id": "item_1",
                    "name": "Noise Cancelling Headphones Pro",
                    "quantity": 1,
                    "price": 1299.0,
                    "imageUrl": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=200&h=200&fit=crop",
                }],
                shipping_address="Beijing Chaoyang Demo Street 101",
                created_at=datetime.utcnow(),
            ),
            Order(
                id="123456",
                user_id=user_demo.id,
                amount=299.0,
                status="delivered",
                items=[{
                    "id": "item_2",
                    "name": "Wireless Power Bank 20000mAh",
                    "quantity": 1,
                    "price": 299.0,
                    "imageUrl": "https://images.unsplash.com/photo-1609091839311-d536801ff141?w=200&h=200&fit=crop",
                }],
                shipping_address="Shanghai Pudong Demo Road 202",
                created_at=datetime.utcnow(),
            ),
            Order(
                id="456789",
                user_id=user_demo.id,
                amount=4500.0,
                status="delivered",
                items=[{
                    "id": "item_3",
                    "name": "Flagship Phone X1",
                    "quantity": 1,
                    "price": 4500.0,
                    "imageUrl": "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=200&h=200&fit=crop",
                }],
                shipping_address="Guangzhou Tianhe Demo Avenue 303",
                created_at=datetime.utcnow(),
            ),
        ]

        for order in orders:
            result = await session.execute(select(Order).where(Order.id == order.id))
            if not result.scalar_one_or_none():
                print(f"Adding order: {order.id}")
                session.add(order)
        await session.commit()

        demo_tickets = [
            Ticket(order_id="123456", requester_id=user_demo.id, thread_id="demo-thread-auto-refund", status=TicketStatus.COMPLETED, reason="damaged"),
            Ticket(order_id="456789", requester_id=user_demo.id, thread_id="demo-thread-high-risk", status=TicketStatus.PENDING, reason="other"),
            Ticket(order_id="789012", requester_id=user_demo.id, thread_id="demo-thread-rejected", status=TicketStatus.REJECTED, reason="not_received"),
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
