from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.tools.inventory_tools import (
    inspect_return_inventory,
    restore_return_inventory,
    reverse_inventory_movement,
    validate_return,
)
from app.db.database import Base
from app.erp.process_generator import seed_return_to_refund_demo


@pytest.fixture
async def inventory_session_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'inventory-specialist.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_return_to_refund_demo(session)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_inventory_specialist_validates_replays_and_compensates_return(
    inventory_session_factory,
):
    tenant_id = "TENANT-DEMO-COMMERCE"
    order_id = "ERP-ORD-1001"
    validation = await validate_return(
        order_id=order_id,
        tenant_id=tenant_id,
        session_factory=inventory_session_factory,
    )
    inspection = await inspect_return_inventory(
        order_id=order_id,
        tenant_id=tenant_id,
        session_factory=inventory_session_factory,
    )
    restoration = await restore_return_inventory(
        order_id=order_id,
        rma_id=validation["rma_id"],
        tenant_id=tenant_id,
        session_factory=inventory_session_factory,
    )
    compensation = await reverse_inventory_movement(
        movement_id=restoration["movement_id"],
        tenant_id=tenant_id,
        session_factory=inventory_session_factory,
    )
    compensation_replay = await reverse_inventory_movement(
        movement_id=restoration["movement_id"],
        tenant_id=tenant_id,
        session_factory=inventory_session_factory,
    )

    assert validation["valid"] is True
    assert inspection["consistent"] is True
    assert restoration["success"] is True
    assert restoration["replayed"] is True
    assert compensation["quantity_delta"] == -1
    assert compensation_replay["replayed"] is True

    with pytest.raises(ValueError, match="new approval and idempotency key"):
        await restore_return_inventory(
            order_id=order_id,
            rma_id=validation["rma_id"],
            tenant_id=tenant_id,
            session_factory=inventory_session_factory,
        )
