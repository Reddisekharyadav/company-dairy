"""Test isolation for WorkSense AI.

The application stores data under ``%APPDATA%/WorkSense`` and writes workspace
rules/config beside it.  Tests must never touch that production data, so this
conftest redirects every writable location to a temporary directory *before*
the application modules are imported by test collection.

A second, autouse fixture resets the schema before each test.  That keeps tests
independent while still using the same engine/session objects imported by the
application modules.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="worksense-tests-"))
_TEST_APPDATA = _TEST_ROOT / "AppData"
_TEST_APPDATA.mkdir(parents=True, exist_ok=True)

# Must be set before importing database.session / config.settings.
os.environ["APPDATA"] = str(_TEST_APPDATA)
os.environ["WS_TESTING"] = "1"
os.environ["WS_DB_PATH"] = str(_TEST_ROOT / "events.db")
os.environ["WS_BACKUP_DIR"] = str(_TEST_ROOT / "backups")
os.environ["WS_EXPORT"] = str(_TEST_ROOT / "exports")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_root():
    yield
    try:
        from database.session import engine

        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_database():
    """Recreate a clean database for every test, never the production DB."""
    from sqlalchemy import text

    from database.models import Base
    from database.session import DB_PATH, engine, init_db

    resolved = Path(DB_PATH).resolve()
    assert resolved.is_relative_to(_TEST_ROOT.resolve()), (
        f"Tests attempted to use a non-isolated database: {resolved}"
    )

    # FTS/vec tables are not SQLAlchemy models; drop them explicitly so stale
    # index rows never leak from one test into another.
    with engine.begin() as conn:
        for name in ("ocr_fts", "screen_frames_fts", "screen_frames_vec"):
            conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
    Base.metadata.drop_all(bind=engine)
    init_db()

    yield
