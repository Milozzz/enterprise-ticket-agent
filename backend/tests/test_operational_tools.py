from __future__ import annotations

import asyncio
import sqlite3

from sqlalchemy import create_engine

from app.db.database import Base
from app.db import models as _models  # noqa: F401 - registers ORM metadata
from scripts.backup_restore import (
    create_backup,
    disaster_recovery_drill,
    restore_backup,
    verify_backup,
)
from scripts.manage_partitions import _month_bounds


def test_sqlite_backup_restore_checksum_and_dr_drill(tmp_path):
    source = tmp_path / "source.db"
    source_url = f"sqlite+aiosqlite:///{source.as_posix()}"
    sync_engine = create_engine(f"sqlite:///{source.as_posix()}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    with sqlite3.connect(source) as connection:
        connection.execute(
            "INSERT INTO users (name, email, role, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("Backup User", "backup@example.com", "USER"),
        )
        connection.commit()

    backup = asyncio.run(create_backup(source_url, tmp_path / "backups"))
    manifest = verify_backup(backup)
    assert manifest["verified"] is True
    assert manifest["counts"]["orders"] == 0

    restored = tmp_path / "restored.db"
    restored_url = f"sqlite+aiosqlite:///{restored.as_posix()}"
    restore_backup(backup, restored_url, "restored.db")
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1

    drill_target = tmp_path / "dr-target.db"
    report = asyncio.run(
        disaster_recovery_drill(
            source_url,
            f"sqlite+aiosqlite:///{drill_target.as_posix()}",
            tmp_path / "drill",
            "dr-target.db",
            60,
        )
    )
    assert report["passed"] is True
    assert report["schema_match"] is True
    assert report["counts_match"] is True


def test_partition_month_bounds_handle_year_rollover():
    start, end = _month_bounds(2026, 12)
    assert start.isoformat() == "2026-12-01T00:00:00"
    assert end.isoformat() == "2027-01-01T00:00:00"
