"""Safe Alembic migration and legacy-baseline helper.

Examples (run from backend):
  python scripts/migrate.py status
  python scripts/migrate.py baseline
  python scripts/migrate.py baseline --apply
  python scripts/migrate.py upgrade
  python scripts/migrate.py rollback --target a902ebde4f49
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


@dataclass(frozen=True)
class RevisionFingerprint:
    revision: str
    required_tables: frozenset[str]
    required_columns: dict[str, frozenset[str]]


FINGERPRINTS = [
    RevisionFingerprint(
        "d4e5f6a7b8c9",
        frozenset({"erp_saga_executions", "erp_saga_steps"}),
        {
            "erp_saga_executions": frozenset({"tenant_id", "status", "command_payload"}),
            "erp_credit_memos": frozenset({"tenant_id"}),
        },
    ),
    RevisionFingerprint(
        "c3d4e5f6a7b8",
        frozenset({"erp_business_object_aliases", "erp_pii_records", "erp_data_contracts"}),
        {"orders": frozenset({"tenant_id", "source_system", "external_order_id"})},
    ),
    RevisionFingerprint(
        "a902ebde4f49",
        frozenset({"erp_outbox_events", "erp_external_system_connectors", "erp_credit_memos"}),
        {"erp_outbox_events": frozenset({"idempotency_key", "next_attempt_at"})},
    ),
    RevisionFingerprint(
        "6f481a79627c",
        frozenset({"erp_tenant_organizations", "erp_order_lines", "erp_payment_transactions"}),
        {},
    ),
    RevisionFingerprint("b2c3d4e5f6a7", frozenset({"user_memory"}), {},),
    RevisionFingerprint(
        "a1b2c3d4e5f6",
        frozenset({"audit_logs"}),
        {"audit_logs": frozenset({"trace_id"})},
    ),
    RevisionFingerprint("0a9de19714cd", frozenset({"audit_logs"}), {},),
    RevisionFingerprint(
        "8b66a065f61b",
        frozenset({"users", "orders", "tickets", "refund_logs"}),
        {"tickets": frozenset({"operator_id"})},
    ),
    RevisionFingerprint("31e398c9ae24", frozenset({"users", "orders", "tickets", "refund_logs"}), {},),
]


def _alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


async def inspect_database() -> dict:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            def read(sync_connection):
                inspector = inspect(sync_connection)
                tables = set(inspector.get_table_names())
                columns = {
                    table: {column["name"] for column in inspector.get_columns(table)}
                    for table in tables
                }
                version = None
                if "alembic_version" in tables:
                    row = sync_connection.exec_driver_sql(
                        "SELECT version_num FROM alembic_version"
                    ).first()
                    version = row[0] if row else None
                return tables, columns, version

            tables, columns, version = await connection.run_sync(read)
    finally:
        await engine.dispose()
    return {"tables": tables, "columns": columns, "version": version}


def detect_legacy_revision(snapshot: dict) -> str | None:
    tables: set[str] = snapshot["tables"]
    columns: dict[str, set[str]] = snapshot["columns"]
    if not tables:
        return "base"
    for fingerprint in FINGERPRINTS:
        if not fingerprint.required_tables.issubset(tables):
            continue
        if all(required.issubset(columns.get(table, set())) for table, required in fingerprint.required_columns.items()):
            return fingerprint.revision
    return None


async def main_async(args: argparse.Namespace) -> int:
    snapshot = await inspect_database()
    public = {
        "table_count": len(snapshot["tables"]),
        "alembic_version": snapshot["version"],
        "detected_legacy_revision": None,
    }
    if snapshot["version"] is None:
        public["detected_legacy_revision"] = detect_legacy_revision(snapshot)

    if args.command == "status":
        print(json.dumps(public, indent=2))
        return 0

    if args.command == "baseline":
        if snapshot["version"]:
            raise SystemExit(f"database is already versioned at {snapshot['version']}")
        detected = detect_legacy_revision(snapshot)
        print(json.dumps(public, indent=2))
        if detected is None:
            raise SystemExit("schema fingerprint is unknown; refusing to stamp")
        if detected == "base":
            raise SystemExit("database is empty; use the upgrade command instead")
        if args.apply:
            await asyncio.to_thread(command.stamp, _alembic_config(), detected)
            print(f"stamped legacy database at {detected}")
        else:
            print("dry-run only; add --apply after reviewing the detected revision")
        return 0

    if args.command == "upgrade":
        if snapshot["tables"] and snapshot["version"] is None:
            raise SystemExit("legacy database has no baseline; run baseline --apply first")
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        return 0

    if args.command == "rollback":
        if not snapshot["version"]:
            raise SystemExit("cannot rollback an unversioned database")
        await asyncio.to_thread(command.downgrade, _alembic_config(), args.target)
        return 0

    raise SystemExit(f"unsupported command: {args.command}")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    baseline = subparsers.add_parser("baseline")
    baseline.add_argument("--apply", action="store_true")
    subparsers.add_parser("upgrade")
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--target", required=True)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
