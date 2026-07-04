"""Repeatable order-table load and index benchmark (supports 1M+ rows)."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import delete, insert, select, text

from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import Order, User, UserRole
from app.db.tenant_context import tenant_scope


TENANT_ID = "TENANT-PERF"
ORDER_PREFIX = "PERF-ORD-"


async def _benchmark_user(session) -> int:
    user = await session.scalar(select(User).where(User.email == "perf-benchmark@example.invalid"))
    if user is None:
        user = User(
            name="Performance Benchmark",
            email="perf-benchmark@example.invalid",
            role=UserRole.USER,
        )
        session.add(user)
        await session.flush()
    return user.id


async def load_rows(*, rows: int, batch_size: int) -> float:
    started = time.perf_counter()
    with tenant_scope(TENANT_ID):
        async with AsyncSessionLocal() as session:
            user_id = await _benchmark_user(session)
            for offset in range(0, rows, batch_size):
                size = min(batch_size, rows - offset)
                payload = [
                    {
                        "id": f"{ORDER_PREFIX}{offset + index:09d}",
                        "tenant_id": TENANT_ID,
                        "source_system": "PERF_GENERATOR",
                        "external_order_id": f"EXT-{offset + index:09d}",
                        "user_id": user_id,
                        "amount": Decimal(f"{(offset + index) % 10000}.{(offset + index) % 100:02d}"),
                        "status": "delivered" if (offset + index) % 2 else "created",
                        "items": [],
                        "shipping_address": None,
                        "created_at": datetime.utcnow() - timedelta(seconds=(offset + index) % 2_592_000),
                    }
                    for index in range(size)
                ]
                await session.execute(insert(Order), payload)
                await session.commit()
    return time.perf_counter() - started


async def benchmark_queries(*, rows: int, iterations: int) -> dict:
    point_samples: list[float] = []
    range_samples: list[float] = []
    with tenant_scope(TENANT_ID):
        async with AsyncSessionLocal() as session:
            for index in range(iterations):
                target = f"EXT-{(index * 7919) % max(rows, 1):09d}"
                started = time.perf_counter()
                await session.scalar(
                    select(Order.id).where(
                        Order.tenant_id == TENANT_ID,
                        Order.source_system == "PERF_GENERATOR",
                        Order.external_order_id == target,
                    )
                )
                point_samples.append((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                (
                    await session.execute(
                        select(Order.id)
                        .where(Order.tenant_id == TENANT_ID)
                        .order_by(Order.created_at.desc())
                        .limit(100)
                    )
                ).all()
                range_samples.append((time.perf_counter() - started) * 1000)

            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                explain_sql = (
                    "EXPLAIN (FORMAT JSON) SELECT id FROM orders "
                    "WHERE tenant_id=:tenant AND source_system='PERF_GENERATOR' AND external_order_id=:external"
                )
            else:
                explain_sql = (
                    "EXPLAIN QUERY PLAN SELECT id FROM orders "
                    "WHERE tenant_id=:tenant AND source_system='PERF_GENERATOR' AND external_order_id=:external"
                )
            plan = (
                await session.execute(
                    text(explain_sql),
                    {"tenant": TENANT_ID, "external": "EXT-000000000"},
                )
            ).all()

    def metrics(samples: list[float]) -> dict[str, float]:
        ordered = sorted(samples)
        p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        return {
            "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[p95_index], 3),
            "max_ms": round(max(ordered), 3),
        }

    return {
        "point_lookup": metrics(point_samples),
        "recent_orders": metrics(range_samples),
        "query_plan": [list(row) for row in plan],
    }


async def cleanup() -> int:
    with tenant_scope(TENANT_ID):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(Order).where(
                    Order.tenant_id == TENANT_ID,
                    Order.id.like(f"{ORDER_PREFIX}%"),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)


async def main_async(args: argparse.Namespace) -> int:
    if args.cleanup:
        if not args.confirm_write:
            raise SystemExit("--cleanup requires --confirm-write")
        print(json.dumps({"deleted": await cleanup()}))
        return 0
    if args.rows > 0 and not args.query_only:
        if not args.confirm_write:
            raise SystemExit("data generation requires --confirm-write")
        load_seconds = await load_rows(rows=args.rows, batch_size=args.batch_size)
    else:
        load_seconds = 0.0
    report = {
        "database": get_settings().database_url.split("@")[-1],
        "rows": args.rows,
        "load_seconds": round(load_seconds, 3),
        "queries": await benchmark_queries(rows=args.rows, iterations=args.iterations),
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--query-only", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
