"""Governed return and inventory tools used by the Inventory Specialist."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import (
    ErpInspectionResult,
    ErpInventoryMovementType,
    ErpOrderLine,
    ErpReturnStatus,
    InventoryItem,
    InventoryMovement,
    ReturnAuthorization,
    ReturnInspection,
)
from app.db.tenant_context import tenant_scope


async def validate_return(
    *,
    order_id: str,
    tenant_id: str = "default",
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            authorization = await session.scalar(
                select(ReturnAuthorization)
                .where(
                    ReturnAuthorization.tenant_id == tenant_id,
                    ReturnAuthorization.order_id == order_id,
                )
                .order_by(ReturnAuthorization.created_at.desc())
            )
            inspection = None
            if authorization is not None:
                inspection = await session.scalar(
                    select(ReturnInspection)
                    .where(
                        ReturnInspection.tenant_id == tenant_id,
                        ReturnInspection.rma_id == authorization.rma_id,
                    )
                    .order_by(ReturnInspection.inspected_at.desc())
                )
    if authorization is None:
        return {
            "valid": False,
            "reason": "RETURN_AUTHORIZATION_MISSING",
            "order_id": order_id,
        }
    received = authorization.status in {
        ErpReturnStatus.RECEIVED,
        ErpReturnStatus.INSPECTED,
        ErpReturnStatus.RESTOCKED,
    } or authorization.received_at is not None
    inspected = inspection is not None and inspection.result != ErpInspectionResult.NOT_RECEIVED
    return {
        "valid": bool(received and inspected),
        "order_id": order_id,
        "rma_id": authorization.rma_id,
        "warehouse_id": authorization.warehouse_id,
        "status": authorization.status.value,
        "received": received,
        "received_at": authorization.received_at.isoformat() if authorization.received_at else None,
        "inspection_id": inspection.inspection_id if inspection else None,
        "inspection_result": inspection.result.value if inspection else None,
        "restockable": bool(inspection and inspection.restockable),
        "reason": "RETURN_VERIFIED" if received and inspected else "RETURN_NOT_READY",
    }


async def inspect_return_inventory(
    *,
    order_id: str,
    tenant_id: str = "default",
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            authorization = await session.scalar(
                select(ReturnAuthorization).where(
                    ReturnAuthorization.tenant_id == tenant_id,
                    ReturnAuthorization.order_id == order_id,
                )
            )
            lines = (
                await session.execute(
                    select(ErpOrderLine).where(
                        ErpOrderLine.tenant_id == tenant_id,
                        ErpOrderLine.order_id == order_id,
                    )
                )
            ).scalars().all()
            inventory = []
            if authorization and lines:
                inventory = (
                    await session.execute(
                        select(InventoryItem).where(
                            InventoryItem.tenant_id == tenant_id,
                            InventoryItem.warehouse_id == authorization.warehouse_id,
                            InventoryItem.product_id.in_([line.product_id for line in lines]),
                        )
                    )
                ).scalars().all()
            movements = (
                await session.execute(
                    select(InventoryMovement).where(
                        InventoryMovement.tenant_id == tenant_id,
                        InventoryMovement.order_id == order_id,
                    )
                )
            ).scalars().all()
    inventory_by_product = {item.product_id: item for item in inventory}
    missing_products = sorted(
        {line.product_id for line in lines} - set(inventory_by_product)
    )
    invalid_balances = [
        item.product_id
        for item in inventory
        if item.quantity_on_hand < 0
        or item.quantity_reserved < 0
        or item.quantity_reserved > item.quantity_on_hand
    ]
    consistent = bool(authorization and lines) and not missing_products and not invalid_balances
    return {
        "consistent": consistent,
        "order_id": order_id,
        "rma_id": authorization.rma_id if authorization else None,
        "warehouse_id": authorization.warehouse_id if authorization else None,
        "items": [
            {
                "inventory_id": item.inventory_id,
                "product_id": item.product_id,
                "quantity_on_hand": item.quantity_on_hand,
                "quantity_reserved": item.quantity_reserved,
            }
            for item in inventory
        ],
        "order_lines": [
            {
                "line_id": line.line_id,
                "product_id": line.product_id,
                "quantity": line.quantity,
            }
            for line in lines
        ],
        "existing_movements": [movement.movement_id for movement in movements],
        "discrepancies": {
            "missing_products": missing_products,
            "invalid_balances": invalid_balances,
        },
    }


async def restore_return_inventory(
    *,
    order_id: str,
    rma_id: str,
    tenant_id: str = "default",
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            authorization = await session.scalar(
                select(ReturnAuthorization)
                .where(
                    ReturnAuthorization.tenant_id == tenant_id,
                    ReturnAuthorization.rma_id == rma_id,
                    ReturnAuthorization.order_id == order_id,
                )
                .with_for_update()
            )
            if authorization is None:
                raise ValueError("Return authorization does not match the order")
            inspection = await session.scalar(
                select(ReturnInspection).where(
                    ReturnInspection.tenant_id == tenant_id,
                    ReturnInspection.rma_id == rma_id,
                )
            )
            if inspection is None or inspection.result == ErpInspectionResult.NOT_RECEIVED:
                raise ValueError("Return inspection is missing or not received")
            lines = (
                await session.execute(
                select(ErpOrderLine)
                .where(
                    ErpOrderLine.tenant_id == tenant_id,
                    ErpOrderLine.order_id == order_id,
                )
                .order_by(ErpOrderLine.line_id)
                )
            ).scalars().all()
            if not lines:
                raise ValueError("Order line is required for inventory restoration")
            product_ids = sorted({line.product_id for line in lines})
            inventory_rows = (
                await session.execute(
                select(InventoryItem)
                .where(
                    InventoryItem.tenant_id == tenant_id,
                    InventoryItem.product_id.in_(product_ids),
                    InventoryItem.warehouse_id == authorization.warehouse_id,
                )
                .with_for_update()
                )
            ).scalars().all()
            inventory_by_product = {item.product_id: item for item in inventory_rows}
            missing_products = sorted(set(product_ids) - set(inventory_by_product))
            if missing_products:
                raise ValueError(
                    f"Warehouse inventory records are missing for products {missing_products}"
                )

            movement_ids = [
                _stable_id("RESTOCK", tenant_id, order_id, rma_id, line.line_id)
                for line in lines
            ]
            prior_rma_movements = (
                await session.execute(
                    select(InventoryMovement).where(
                        InventoryMovement.tenant_id == tenant_id,
                        InventoryMovement.rma_id == rma_id,
                        InventoryMovement.movement_type.in_(
                            [
                                ErpInventoryMovementType.RESTOCK,
                                ErpInventoryMovementType.ADJUSTMENT,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            if prior_rma_movements:
                compensated_ids = {
                    item.reason.removeprefix("Compensation for ")
                    for item in prior_rma_movements
                    if item.reason.startswith("Compensation for ")
                }
                if compensated_ids:
                    raise ValueError(
                        "Previous inventory restoration was compensated; a new approval and idempotency key are required"
                    )
                restored_products = {
                    item.product_id for item in prior_rma_movements
                }
                if restored_products != set(product_ids):
                    raise ValueError(
                        "Partial prior inventory restoration requires manual reconciliation"
                    )
                return _movement_batch_result(prior_rma_movements, replayed=True)
            existing = (
                await session.execute(
                    select(InventoryMovement).where(
                        InventoryMovement.tenant_id == tenant_id,
                        InventoryMovement.movement_id.in_(movement_ids),
                    )
                )
            ).scalars().all()
            if existing:
                if len(existing) != len(movement_ids):
                    raise ValueError("Partial inventory restoration requires manual reconciliation")
                return _movement_batch_result(existing, replayed=True)

            movements: list[InventoryMovement] = []
            for line, movement_id in zip(lines, movement_ids, strict=True):
                inventory = inventory_by_product[line.product_id]
                quantity_delta = int(line.quantity) if inspection.restockable else 0
                inventory.quantity_on_hand += quantity_delta
                movement = InventoryMovement(
                    movement_id=movement_id,
                    tenant_id=tenant_id,
                    product_id=line.product_id,
                    warehouse_id=authorization.warehouse_id,
                    order_id=order_id,
                    rma_id=rma_id,
                    movement_type=(
                        ErpInventoryMovementType.RESTOCK
                        if inspection.restockable
                        else ErpInventoryMovementType.ADJUSTMENT
                    ),
                    quantity_delta=quantity_delta,
                    balance_after=inventory.quantity_on_hand,
                    reason=(
                        "Return inspected and restored to available stock"
                        if inspection.restockable
                        else "Return inspected and routed to quarantine"
                    ),
                    occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                session.add(movement)
                movements.append(movement)
            if inspection.restockable:
                authorization.status = ErpReturnStatus.RESTOCKED
            await session.commit()
            return _movement_batch_result(movements, replayed=False)


async def reverse_inventory_movement(
    *,
    movement_id: str,
    tenant_id: str = "default",
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    reversal_id = _stable_id("REVERSE", tenant_id, movement_id)
    with tenant_scope(tenant_id):
        async with session_factory() as session:
            existing = await session.get(InventoryMovement, reversal_id)
            if existing is not None:
                return _movement_result(existing, replayed=True)
            original = await session.scalar(
                select(InventoryMovement).where(
                    InventoryMovement.tenant_id == tenant_id,
                    InventoryMovement.movement_id == movement_id,
                )
            )
            if original is None:
                raise ValueError("Inventory movement does not exist")
            inventory = await session.scalar(
                select(InventoryItem).where(
                    InventoryItem.tenant_id == tenant_id,
                    InventoryItem.product_id == original.product_id,
                    InventoryItem.warehouse_id == original.warehouse_id,
                ).with_for_update()
            )
            if inventory is None:
                raise ValueError("Warehouse inventory record is missing")
            next_balance = inventory.quantity_on_hand - original.quantity_delta
            if next_balance < 0:
                raise ValueError("Inventory compensation would create a negative balance")
            inventory.quantity_on_hand = next_balance
            reversal = InventoryMovement(
                movement_id=reversal_id,
                tenant_id=tenant_id,
                product_id=original.product_id,
                warehouse_id=original.warehouse_id,
                order_id=original.order_id,
                rma_id=original.rma_id,
                movement_type=ErpInventoryMovementType.ADJUSTMENT,
                quantity_delta=-original.quantity_delta,
                balance_after=inventory.quantity_on_hand,
                reason=f"Compensation for {movement_id}",
                occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(reversal)
            await session.commit()
            return _movement_result(reversal, replayed=False)


def _movement_result(movement: InventoryMovement, *, replayed: bool) -> dict[str, Any]:
    return {
        "success": True,
        "movement_id": movement.movement_id,
        "order_id": movement.order_id,
        "rma_id": movement.rma_id,
        "product_id": movement.product_id,
        "warehouse_id": movement.warehouse_id,
        "quantity_delta": movement.quantity_delta,
        "balance_after": movement.balance_after,
        "movement_type": movement.movement_type.value,
        "replayed": replayed,
    }


def _movement_batch_result(
    movements: list[InventoryMovement],
    *,
    replayed: bool,
) -> dict[str, Any]:
    ordered = sorted(movements, key=lambda item: item.movement_id)
    serialized = [_movement_result(item, replayed=replayed) for item in ordered]
    return {
        "success": bool(serialized),
        "movement_id": serialized[0]["movement_id"] if serialized else None,
        "movement_ids": [item["movement_id"] for item in serialized],
        "movements": serialized,
        "order_id": serialized[0]["order_id"] if serialized else None,
        "rma_id": serialized[0]["rma_id"] if serialized else None,
        "total_quantity_delta": sum(item["quantity_delta"] for item in serialized),
        "replayed": replayed,
    }


def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:24].upper()}"
