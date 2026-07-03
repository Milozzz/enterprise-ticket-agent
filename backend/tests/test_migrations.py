from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine

from app.db.database import Base
from app.db import models as _models  # noqa: F401 - registers ORM metadata
from scripts.migrate import detect_legacy_revision


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def _run_alembic(database_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_fresh_upgrade_rollback_and_schema_check(tmp_path):
    database = tmp_path / "fresh.db"
    _run_alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "07a8b9c0d1e2"
        amount_type = next(
            row[2] for row in connection.execute("PRAGMA table_info(orders)") if row[1] == "amount"
        )
        assert amount_type == "NUMERIC(18, 2)"
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='erp_pii_records'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='knowledge_chunks'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='llm_usage_records'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='approval_tasks'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='user_long_term_memories'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='agent_execution_jobs'"
        ).fetchone()[0] == 1

    _run_alembic(database, "downgrade", "a902ebde4f49")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='erp_pii_records'"
        ).fetchone()[0] == 0

    _run_alembic(database, "upgrade", "head")
    check = _run_alembic(database, "check")
    assert "No new upgrade operations" in check.stdout


def test_legacy_create_all_database_is_safely_detected_and_stamped(tmp_path):
    database = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {
            table: {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for table in tables
        }
    assert detect_legacy_revision({"tables": tables, "columns": columns, "version": None}) == "d4e5f6a7b8c9"

    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}"}
    result = subprocess.run(
        [sys.executable, "scripts/migrate.py", "baseline", "--apply"],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "stamped legacy database at d4e5f6a7b8c9" in result.stdout
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d4e5f6a7b8c9"


def test_unknown_legacy_schema_is_refused():
    snapshot = {
        "tables": {"some_unrelated_table"},
        "columns": {"some_unrelated_table": {"id"}},
        "version": None,
    }
    assert detect_legacy_revision(snapshot) is None


def test_upgrade_backfills_legacy_order_identity_and_currency(tmp_path):
    database = tmp_path / "legacy-order.db"
    _run_alembic(database, "upgrade", "a902ebde4f49")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (id, name, email, role, created_at) VALUES (1, 'Legacy', 'legacy@example.com', 'USER', CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO orders (id, user_id, amount, status, items, shipping_address, created_at) "
            "VALUES ('123456', 1, 299.0, 'delivered', '[]', NULL, CURRENT_TIMESTAMP)"
        )
        connection.commit()

    _run_alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        order = connection.execute(
            "SELECT source_system, external_order_id, currency FROM orders WHERE id='123456'"
        ).fetchone()
        alias = connection.execute(
            "SELECT canonical_id FROM erp_business_object_aliases WHERE external_id='123456'"
        ).fetchone()
    assert order == ("LEGACY_DEMO", "123456", "CNY")
    assert alias == ("123456",)
