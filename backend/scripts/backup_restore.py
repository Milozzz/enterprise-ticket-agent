"""Verified backup, restore, and disaster-recovery drill utility."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


CRITICAL_TABLES = [
    "orders",
    "tickets",
    "erp_refund_requests",
    "erp_outbox_events",
    "erp_business_object_aliases",
    "erp_data_contracts",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _database_name(url: str) -> str:
    parsed = make_url(url)
    database = parsed.database or ""
    if parsed.drivername.startswith("sqlite") and database:
        return Path(database).name
    return database


async def _database_snapshot(url: str) -> dict:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            existing = {
                row[0]
                for row in (
                    await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                            if connection.dialect.name == "postgresql"
                            else "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    )
                ).all()
            }
            counts = {}
            for table in CRITICAL_TABLES:
                if table in existing:
                    counts[table] = int(
                        await connection.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0
                    )
            version = None
            if "alembic_version" in existing:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            return {"alembic_version": version, "counts": counts}
    finally:
        await engine.dispose()


def _sqlite_path(url: str) -> Path:
    database = make_url(url).database
    if not database or database == ":memory:":
        raise ValueError("SQLite backup requires a file database")
    return Path(database).resolve()


async def create_backup(url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    is_postgres = url.startswith("postgresql")
    backup_path = output_dir / (f"ticketdb-{stamp}.dump" if is_postgres else f"ticketdb-{stamp}.sqlite3")
    if is_postgres:
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--file", str(backup_path), _pg_url(url)],
            check=True,
        )
    else:
        source = sqlite3.connect(_sqlite_path(url))
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    snapshot = await _database_snapshot(url)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database_type": "postgresql" if is_postgres else "sqlite",
        "database_name": _database_name(url),
        "backup_file": backup_path.name,
        "sha256": _sha256(backup_path),
        "size_bytes": backup_path.stat().st_size,
        **snapshot,
    }
    backup_path.with_suffix(backup_path.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return backup_path


def verify_backup(backup_path: Path) -> dict:
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    if not backup_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("backup or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = _sha256(backup_path)
    if actual != manifest.get("sha256"):
        raise RuntimeError("backup checksum does not match manifest")
    return {**manifest, "verified": True}


def restore_backup(backup_path: Path, target_url: str, confirmation: str) -> None:
    manifest = verify_backup(backup_path)
    target_name = _database_name(target_url)
    if not target_name or confirmation != target_name:
        raise RuntimeError("--confirm-target must exactly match the target database name")
    if target_url == get_settings().database_url:
        raise RuntimeError("refusing to restore over the configured source database")
    if target_url.startswith("postgresql"):
        if manifest["database_type"] != "postgresql":
            raise RuntimeError("backup type does not match PostgreSQL target")
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--dbname",
                _pg_url(target_url),
                str(backup_path),
            ],
            check=True,
        )
    else:
        if manifest["database_type"] != "sqlite":
            raise RuntimeError("backup type does not match SQLite target")
        target_path = _sqlite_path(target_url)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(backup_path)
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()


async def disaster_recovery_drill(
    source_url: str,
    target_url: str,
    output_dir: Path,
    confirmation: str,
    rto_seconds: int,
) -> dict:
    started = time.perf_counter()
    backup = await create_backup(source_url, output_dir)
    restore_backup(backup, target_url, confirmation)
    source_snapshot = await _database_snapshot(source_url)
    target_snapshot = await _database_snapshot(target_url)
    elapsed = time.perf_counter() - started
    counts_match = source_snapshot["counts"] == target_snapshot["counts"]
    schema_match = source_snapshot["alembic_version"] == target_snapshot["alembic_version"]
    report = {
        "backup": str(backup),
        "elapsed_seconds": round(elapsed, 3),
        "rto_seconds": rto_seconds,
        "rto_met": elapsed <= rto_seconds,
        "schema_match": schema_match,
        "counts_match": counts_match,
        "source": source_snapshot,
        "target": target_snapshot,
        "passed": elapsed <= rto_seconds and schema_match and counts_match,
    }
    report_path = backup.with_suffix(backup.suffix + ".dr-report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--output-dir", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--target-url", required=True)
    restore.add_argument("--confirm-target", required=True)
    drill = subparsers.add_parser("drill")
    drill.add_argument("--target-url", required=True)
    drill.add_argument("--confirm-target", required=True)
    drill.add_argument("--output-dir", type=Path, required=True)
    drill.add_argument("--rto-seconds", type=int, default=900)
    args = parser.parse_args()
    source_url = get_settings().database_url
    if args.command == "backup":
        print(asyncio.run(create_backup(source_url, args.output_dir)))
    elif args.command == "verify":
        print(json.dumps(verify_backup(args.backup), indent=2))
    elif args.command == "restore":
        restore_backup(args.backup, args.target_url, args.confirm_target)
        print("restore completed")
    else:
        print(
            json.dumps(
                asyncio.run(
                    disaster_recovery_drill(
                        source_url,
                        args.target_url,
                        args.output_dir,
                        args.confirm_target,
                        args.rto_seconds,
                    )
                ),
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
