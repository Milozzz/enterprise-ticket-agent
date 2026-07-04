"""Create future PostgreSQL monthly partitions before events arrive."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.db.database import engine


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


async def create_partition(year: int, month: int) -> str:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("partition management requires PostgreSQL")
    start, end = _month_bounds(year, month)
    name = f"erp_event_archive_{year:04d}_{month:02d}"
    async with engine.begin() as connection:
        exists = await connection.scalar(
            text("SELECT to_regclass(:name)"), {"name": name}
        )
        if exists:
            return f"{name} already exists"
        rows_in_default = await connection.scalar(
            text(
                "SELECT count(*) FROM erp_event_archive_default "
                "WHERE occurred_at >= :start AND occurred_at < :end"
            ),
            {"start": start, "end": end},
        )
        if rows_in_default:
            raise RuntimeError(
                f"default partition contains {rows_in_default} rows for {year:04d}-{month:02d}; migrate them before attaching"
            )
        # Identifiers are generated solely from validated integers.
        await connection.execute(
            text(
                f"CREATE TABLE {name} PARTITION OF erp_event_archive "
                f"FOR VALUES FROM ('{start.date().isoformat()}') TO ('{end.date().isoformat()}')"
            )
        )
    return f"created {name}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, choices=range(1, 13), required=True)
    args = parser.parse_args()
    if not 2020 <= args.year <= 2100:
        raise SystemExit("year must be between 2020 and 2100")
    print(asyncio.run(create_partition(args.year, args.month)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
