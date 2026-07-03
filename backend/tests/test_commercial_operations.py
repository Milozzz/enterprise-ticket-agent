import asyncio
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commercial.operations import (
    ONBOARDING_ITEMS,
    commercial_snapshot,
    provision_tenant,
    record_usage,
    update_onboarding,
)
from app.db.database import Base
from app.db.models import UsageEvent
from app.db.tenant_context import tenant_scope


def test_tenant_onboarding_usage_meter_and_operations_snapshot():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        tenant_id = "TENANT-COMMERCIAL-TEST"
        with tenant_scope(tenant_id):
            async with Session() as session:
                provisioned = await provision_tenant(
                    session,
                    tenant_id=tenant_id,
                    name="Commercial Test",
                    industry="retail",
                    region="CN",
                    monthly_action_quota=100,
                )
                assert provisioned["plan_code"] == "PILOT"
                onboarding = await update_onboarding(
                    session,
                    tenant_id=tenant_id,
                    updates={
                        key: True
                        for key in ONBOARDING_ITEMS
                        if key != "production_change_approved"
                    },
                    environment="sandbox",
                )
                assert onboarding.status == "READY"

                first = await record_usage(
                    session,
                    tenant_id=tenant_id,
                    metric_name="refund_finance_action",
                    source_type="saga",
                    source_id="SAGA-1",
                    quantity=Decimal("1.0000"),
                )
                second = await record_usage(
                    session,
                    tenant_id=tenant_id,
                    metric_name="refund_finance_action",
                    source_type="saga",
                    source_id="SAGA-1",
                    quantity=Decimal("1.0000"),
                )
                assert first.usage_event_id == second.usage_event_id
                await session.commit()

                assert await session.scalar(select(func.count()).select_from(UsageEvent)) == 1
                snapshot = await commercial_snapshot(session, tenant_id=tenant_id)
                assert snapshot["onboarding"]["status"] == "READY"
                assert snapshot["subscription"]["consumed"] == 1.0
                assert snapshot["subscription"]["remaining"] == 99.0
                assert len(snapshot["slos"]) == 3

        await engine.dispose()

    asyncio.run(run())
