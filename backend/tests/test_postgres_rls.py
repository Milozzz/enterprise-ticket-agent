from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Order, User, UserRole
from app.db.tenant_context import tenant_scope


@pytest.mark.skipif(
    not os.environ.get("POSTGRES_RLS_TEST_URL"),
    reason="POSTGRES_RLS_TEST_URL is only configured by the PostgreSQL CI job",
)
def test_postgres_rls_isolates_tenants_and_rejects_cross_tenant_writes():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_RLS_TEST_URL"])
        Session = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid.uuid4().hex[:10]

        with tenant_scope("TENANT-A"):
            async with Session() as session:
                user = User(
                    name=f"RLS {suffix}",
                    email=f"rls-{suffix}@example.com",
                    role=UserRole.USER,
                )
                session.add(user)
                await session.flush()
                user_id = user.id
                session.add(
                    Order(
                        id=f"RLS-A-{suffix}",
                        tenant_id="TENANT-A",
                        source_system="RLS_TEST",
                        external_order_id=f"A-{suffix}",
                        user_id=user_id,
                        amount=10,
                        status="created",
                        items=[],
                        shipping_address=None,
                    )
                )
                await session.commit()

        with tenant_scope("TENANT-B"):
            async with Session() as session:
                rows = (await session.execute(select(Order).where(Order.id == f"RLS-A-{suffix}"))).scalars().all()
                assert rows == []
                session.add(
                    Order(
                        id=f"RLS-BAD-{suffix}",
                        tenant_id="TENANT-A",
                        source_system="RLS_TEST",
                        external_order_id=f"BAD-{suffix}",
                        user_id=user_id,
                        amount=10,
                        status="created",
                        items=[],
                        shipping_address=None,
                    )
                )
                try:
                    await session.commit()
                except DBAPIError:
                    await session.rollback()
                else:
                    raise AssertionError("RLS must reject a cross-tenant write")
        await engine.dispose()

    asyncio.run(run())
