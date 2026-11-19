"""Non-destructive SQLite migration tests.

The migration must be idempotent, must add missing columns, and must never
attribute legacy rows to company/development.  Legacy NULL/empty workspace
values become explicit ``unclassified`` while existing values are preserved.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import database.migrations as migrations
from database.backup import restore_backup
from database.migrations import run_workspace_migrations

LEGACY_SCHEMA = [
    "CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp DATETIME, workspace VARCHAR(24))",
    "CREATE TABLE git (id INTEGER PRIMARY KEY, timestamp DATETIME, workspace VARCHAR(24))",
    "CREATE TABLE file_edits (id INTEGER PRIMARY KEY, timestamp DATETIME, workspace VARCHAR(24))",
    "CREATE TABLE meetings (id INTEGER PRIMARY KEY, timestamp DATETIME, workspace VARCHAR(24))",
    "CREATE TABLE search_queries (id INTEGER PRIMARY KEY, timestamp DATETIME, workspace VARCHAR(24))",
    "CREATE TABLE reports (id INTEGER PRIMARY KEY, created_at DATETIME)",
]

# A fuller pre-workspace database, used to verify the upgrade against a copy
# that resembles a real installation rather than a two-column toy schema.
REALISTIC_LEGACY_SCHEMA = [
    """CREATE TABLE events (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        application VARCHAR(256), process_name VARCHAR(256), window_title VARCHAR(1024),
        duration REAL, cpu REAL, idle BOOLEAN, project VARCHAR(256),
        opened_file VARCHAR(2048), language VARCHAR(64), category VARCHAR(64),
        website VARCHAR(256), input_state VARCHAR(64)
    )""",
    """CREATE TABLE git (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        repo VARCHAR(1024), commit_hash VARCHAR(64), message TEXT, author VARCHAR(256)
    )""",
    """CREATE TABLE file_edits (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        file_path VARCHAR(2048), file_name VARCHAR(512), project VARCHAR(256),
        duration_sec INTEGER, editor VARCHAR(128), language VARCHAR(64),
        session_date VARCHAR(16)
    )""",
    """CREATE TABLE meetings (
        id INTEGER PRIMARY KEY, session_id VARCHAR(64), start_time DATETIME,
        end_time DATETIME, duration_sec INTEGER, platform VARCHAR(64),
        title VARCHAR(512), session_date VARCHAR(16)
    )""",
    """CREATE TABLE browser_history (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        url TEXT, title VARCHAR(512), domain VARCHAR(256), duration_sec INTEGER,
        browser VARCHAR(64), session_date VARCHAR(16)
    )""",
    """CREATE TABLE ocr (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        text TEXT, source VARCHAR(128), screenshot_path VARCHAR(2048)
    )""",
    """CREATE TABLE search_queries (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        query TEXT, source VARCHAR(64), raw_title VARCHAR(512),
        bookmarked BOOLEAN, research_session_id INTEGER
    )""",
    """CREATE TABLE research_sessions (
        id INTEGER PRIMARY KEY, start_time DATETIME, end_time DATETIME,
        date VARCHAR(16), topic_label VARCHAR(256), query_count INTEGER,
        total_duration_sec INTEGER, sources TEXT
    )""",
    """CREATE TABLE daily_notes (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        content TEXT, source VARCHAR(64), date VARCHAR(16), context_data TEXT,
        screenshot_path VARCHAR(2048), auto_generated BOOLEAN DEFAULT 0
    )""",
    """CREATE TABLE activity_insights (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        app VARCHAR(256), window_title VARCHAR(1024), summary TEXT,
        duration_on_tab INTEGER, engagement_type VARCHAR(64), ocr_summary TEXT,
        session_date VARCHAR(16)
    )""",
    """CREATE TABLE screen_frames (
        id INTEGER PRIMARY KEY, timestamp DATETIME, session_id VARCHAR(64),
        screenshot_path VARCHAR(2048), phash VARCHAR(64), ocr_text TEXT,
        embedding_json TEXT, privacy_scrubbed BOOLEAN, detected_category VARCHAR(64),
        process_name VARCHAR(256), window_title VARCHAR(1024), analysis_summary TEXT,
        file_size_bytes INTEGER, is_duplicate BOOLEAN, omniparser_json TEXT,
        embedding_vector BLOB, session_date VARCHAR(16)
    )""",
    "CREATE TABLE reports (id INTEGER PRIMARY KEY, created_at DATETIME, path VARCHAR(2048), format VARCHAR(16))",
    "CREATE TABLE session_snapshots (id INTEGER PRIMARY KEY, snapshot_date VARCHAR(16), context_data TEXT)",
]


def _seed_realistic_rows(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO events (id, timestamp, application, window_title, duration, idle) "
            "VALUES (1, '2026-05-01 09:00:00', 'outlook.exe', 'Inbox', 3600, 0), "
            "(2, '2026-05-01 10:00:00', 'code.exe', 'main.py', 1800, 0)"
        ))
        conn.execute(text(
            "INSERT INTO git (id, timestamp, repo, commit_hash, message, author) "
            "VALUES (1, '2026-05-01 11:00:00', 'demo', 'abc123', 'Initial', 'Dev')"
        ))
        conn.execute(text(
            "INSERT INTO file_edits (id, timestamp, file_path, file_name, project, duration_sec) "
            "VALUES (1, '2026-05-01 11:30:00', 'C:/work/app.py', 'app.py', 'demo', 120)"
        ))
        conn.execute(text(
            "INSERT INTO meetings (id, start_time, end_time, duration_sec, platform, title) "
            "VALUES (1, '2026-05-01 13:00:00', '2026-05-01 14:00:00', 3600, 'Teams', 'Sync')"
        ))
        conn.execute(text(
            "INSERT INTO screen_frames (id, timestamp, screenshot_path) "
            "VALUES (1, '2026-05-01 12:00:00', 'C:/shots/1.webp')"
        ))
        conn.execute(text(
            "INSERT INTO session_snapshots (id, snapshot_date, context_data) "
            "VALUES (1, '2026-05-01', '{}')"
        ))



def _engine(tmp_path: Path):
    return create_engine(
        f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )


def _columns(engine, table: str):
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def _workspaces(engine):
    with engine.connect() as conn:
        return [
            row[0]
            for row in conn.execute(text("SELECT workspace FROM events ORDER BY id")).fetchall()
        ]


def _seed_legacy_rows(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events (id, timestamp, workspace) VALUES "
                "(1, '2026-05-01 10:00:00', NULL), "
                "(2, '2026-05-02 10:00:00', ''), "
                "(3, '2026-05-03 10:00:00', 'company'), "
                "(4, '2026-05-04 10:00:00', 'development')"
            )
        )
        conn.execute(
            text("INSERT INTO git (id, timestamp, workspace) VALUES (1, '2026-05-01 10:00:00', NULL)")
        )


def test_migration_adds_columns_and_backfills_without_data_loss(tmp_path: Path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(text(statement))
    _seed_legacy_rows(engine)

    result = run_workspace_migrations(engine)

    assert result["added_columns"] > 0
    assert result["backfilled"].get("events") == 2
    assert result["backfilled"].get("git") == 1

    # Existing rows are preserved; legacy rows are never silently attributed.
    assert _count(engine, "events") == 4
    assert _workspaces(engine) == ["unclassified", "unclassified", "company", "development"]

    # Missing workspace-related columns were added to legacy tables.
    git_columns = _columns(engine, "git")
    assert {"workspace", "branch", "files_changed", "insertions", "deletions", "source"} <= git_columns

    edit_columns = _columns(engine, "file_edits")
    assert {"workspace", "source", "project_root", "relative_path", "extension"} <= edit_columns

    report_columns = _columns(engine, "reports")
    assert {"workspace", "report_type"} <= report_columns


def test_workspace_index_is_created(tmp_path: Path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(text(statement))
    _seed_legacy_rows(engine)

    run_workspace_migrations(engine)

    with engine.connect() as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            ).fetchall()
        }
    assert "ix_events_workspace_timestamp" in indexes
    assert "ix_file_edits_workspace_timestamp" in indexes


def test_migration_is_idempotent(tmp_path: Path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(text(statement))
    _seed_legacy_rows(engine)

    first = run_workspace_migrations(engine)
    second = run_workspace_migrations(engine)

    assert first["added_columns"] > 0
    assert second["added_columns"] == 0
    assert second["backfilled"] == {}
    assert _count(engine, "events") == 4
    assert _workspaces(engine) == ["unclassified", "unclassified", "company", "development"]


def test_migration_with_empty_database_is_safe(tmp_path: Path):
    engine = _engine(tmp_path)

    result = run_workspace_migrations(engine)

    # No tables means nothing to add, but the call must not raise.
    assert result["added_columns"] == 0
    assert result["backfilled"] == {}


def test_backup_is_created_before_upgrade_and_can_be_restored(tmp_path: Path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(text(statement))
    _seed_legacy_rows(engine)

    backup_dir = tmp_path / "backups"
    result = run_workspace_migrations(engine, backup_dir=backup_dir)

    backup = Path(result["backup_path"])
    assert backup.exists()
    assert backup.with_suffix(backup.suffix + ".sha256").exists()

    # The backup must represent the pre-upgrade schema and data.  The legacy
    # workspace column exists here but its legacy NULL/empty values have not
    # been backfilled yet.
    with sqlite3.connect(backup) as conn:
        backup_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        backup_workspaces = [row[0] for row in conn.execute(
            "SELECT workspace FROM events ORDER BY id"
        ).fetchall()]
        backup_columns = {row[1] for row in conn.execute("PRAGMA table_info(git)").fetchall()}
    assert backup_events == 4
    assert backup_workspaces[:2] == [None, ""]
    assert "branch" not in backup_columns
    assert "workspace" in backup_columns

    engine.dispose()

    restored_path = tmp_path / "restored" / "events.db"
    restore_backup(backup, restored_path, safety_backup=False, backup_dir=backup_dir)

    restored_engine = create_engine(
        f"sqlite:///{restored_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    try:
        assert _count(restored_engine, "events") == 4
        assert _workspaces(restored_engine) == [None, "", "company", "development"]
        restored_git_columns = _columns(restored_engine, "git")
        assert "branch" not in restored_git_columns

        # A restored legacy database can be upgraded successfully and is then
        # backfilled to explicit unclassified values.
        second = run_workspace_migrations(restored_engine, backup_dir=backup_dir)
        assert second["added_columns"] > 0
        assert "branch" in _columns(restored_engine, "git")
        assert _count(restored_engine, "events") == 4
        assert _workspaces(restored_engine) == [
            "unclassified", "unclassified", "company", "development",
        ]
    finally:
        restored_engine.dispose()


def test_failed_migration_rolls_back_schema_changes(tmp_path: Path, monkeypatch):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(text(statement))
    _seed_legacy_rows(engine)

    def boom(conn):
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(migrations, "_backfill_workspace_rows", boom)

    with pytest.raises(RuntimeError, match="simulated migration failure"):
        run_workspace_migrations(engine, backup_dir=tmp_path / "backups")

    # The failed upgrade must leave the original schema and rows intact.  The
    # migration restores its verified pre-upgrade backup (SQLite ALTER TABLE is
    # not reliably transactional across drivers).
    assert "branch" not in _columns(engine, "git")
    assert "source" not in _columns(engine, "file_edits")
    assert _count(engine, "events") == 4


def test_realistic_legacy_copy_upgrade_preserves_rows(tmp_path: Path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for statement in REALISTIC_LEGACY_SCHEMA:
            conn.execute(text(statement))
    _seed_realistic_rows(engine)

    result = run_workspace_migrations(engine, backup_dir=tmp_path / "backups")

    assert result["added_columns"] > 0
    assert _count(engine, "events") == 2
    assert _count(engine, "git") == 1
    assert _count(engine, "file_edits") == 1
    assert _count(engine, "screen_frames") == 1

    assert _columns(engine, "screen_frames") >= {"workspace"}
    assert _columns(engine, "session_snapshots") >= {"workspace_scope"}
    assert _columns(engine, "git") >= {
        "workspace", "branch", "files_changed", "insertions", "deletions", "source",
    }
    # SQLite applies the column default to existing rows when a new column is
    # added; the explicit backfill then has nothing left to change.
    assert _workspaces(engine) == ["unclassified", "unclassified"]

    # Original application data is unchanged apart from the migration metadata.
    with engine.connect() as conn:
        title = conn.execute(text("SELECT window_title FROM events WHERE id=1")).scalar_one()
        commit = conn.execute(text("SELECT message FROM git WHERE id=1")).scalar_one()
    assert title == "Inbox"
    assert commit == "Initial"
