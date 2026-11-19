"""Small, idempotent SQLite migrations for the workspace upgrade.

The application historically used ``Base.metadata.create_all`` plus ad-hoc
``ALTER TABLE ... ADD COLUMN`` statements.  We keep that approach (no Alembic
runtime dependency) but move the workspace additions into one tested module.

Migration policy:
* never drop or recreate a table;
* never overwrite a non-empty workspace value;
* legacy rows become ``unclassified`` so they remain visible but are never
  silently attributed to company or development work;
* a consistent backup is created before any column is changed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("migrations")

# table -> [(column, sqlite_type)]
WORKSPACE_COLUMNS: Dict[str, List[Tuple[str, str]]] = {
    "events": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "git": [
        ("workspace", "VARCHAR(24) DEFAULT 'unclassified'"),
        ("branch", "VARCHAR(256)"),
        ("files_changed", "INTEGER"),
        ("insertions", "INTEGER"),
        ("deletions", "INTEGER"),
        ("source", "VARCHAR(32) DEFAULT 'git_commit'"),
    ],
    "ocr": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "reports": [
        ("workspace", "VARCHAR(24) DEFAULT 'unclassified'"),
        ("report_type", "VARCHAR(32)"),
    ],
    "file_edits": [
        ("workspace", "VARCHAR(24) DEFAULT 'unclassified'"),
        ("source", "VARCHAR(32) DEFAULT 'active_window_inference'"),
        ("project_root", "VARCHAR(1024)"),
        ("relative_path", "VARCHAR(2048)"),
        ("extension", "VARCHAR(32)"),
    ],
    "search_queries": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "research_sessions": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "browser_history": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "daily_notes": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "meetings": [("workspace", "VARCHAR(24) DEFAULT 'company'")],
    "activity_insights": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "screen_frames": [("workspace", "VARCHAR(24) DEFAULT 'unclassified'")],
    "session_snapshots": [("workspace_scope", "VARCHAR(24) DEFAULT 'combined'")],
}

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS ix_events_workspace_timestamp ON events(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_git_workspace_timestamp ON git(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_git_repo_hash ON git(repo, commit_hash)",
    "CREATE INDEX IF NOT EXISTS ix_file_edits_workspace_timestamp ON file_edits(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_file_edits_path_date ON file_edits(file_path, session_date)",
    "CREATE INDEX IF NOT EXISTS ix_file_change_workspace_timestamp ON file_change_events(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_file_change_path_timestamp ON file_change_events(file_path, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_file_change_project ON file_change_events(project)",
    "CREATE INDEX IF NOT EXISTS ix_search_workspace_timestamp ON search_queries(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_research_workspace_start ON research_sessions(workspace, start_time)",
    "CREATE INDEX IF NOT EXISTS ix_browser_workspace_timestamp ON browser_history(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_meetings_workspace_start ON meetings(workspace, start_time)",
    "CREATE INDEX IF NOT EXISTS ix_insights_workspace_timestamp ON activity_insights(workspace, timestamp)",
    "CREATE INDEX IF NOT EXISTS ix_screen_frames_workspace_timestamp ON screen_frames(workspace, timestamp)",
]

# Tables whose legacy rows must remain explicitly unclassified.
BACKFILL_TABLES = [
    "events",
    "git",
    "ocr",
    "reports",
    "file_edits",
    "search_queries",
    "research_sessions",
    "browser_history",
    "daily_notes",
    "activity_insights",
    "screen_frames",
]


def _table_columns(conn, table: str) -> List[str]:
    try:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return [row[1] for row in rows]
    except Exception:
        return []


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row is not None


def has_pending_workspace_migrations(engine: Engine) -> bool:
    """Return True when at least one workspace/related column is missing."""
    with engine.connect() as conn:
        for table, columns in WORKSPACE_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            existing = set(_table_columns(conn, table))
            if any(column not in existing for column, _ in columns):
                return True
    return False


def _ensure_workspace_columns(conn) -> Dict[str, int]:
    added = 0
    skipped = 0
    for table, columns in WORKSPACE_COLUMNS.items():
        if not _table_exists(conn, table):
            skipped += 1
            continue
        existing = set(_table_columns(conn, table))
        for column, col_type in columns:
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            added += 1
            log.info("Migrated: added %s.%s", table, column)
    for statement in INDEX_STATEMENTS:
        try:
            conn.execute(text(statement))
        except Exception as exc:
            # Index creation is an optimization; never make a table unusable
            # because an optional index could not be created.
            log.debug("Index migration skipped: %s", exc)
    return {"added_columns": added, "missing_tables": skipped}


def _backfill_workspace_rows(conn) -> Dict[str, int]:
    updated: Dict[str, int] = {}
    for table in BACKFILL_TABLES:
        if not _table_exists(conn, table):
            continue
        columns = _table_columns(conn, table)
        if "workspace" not in columns:
            continue
        result = conn.execute(text(
            f"UPDATE {table} SET workspace='unclassified' "
            "WHERE workspace IS NULL OR TRIM(workspace)=''"
        ))
        count = result.rowcount or 0
        if count:
            updated[table] = count
    return updated


def ensure_workspace_columns(engine: Engine) -> Dict[str, int]:
    """Add the workspace/related columns if missing.  Returns counts."""
    with engine.begin() as conn:
        return _ensure_workspace_columns(conn)


def backfill_legacy_workspaces(engine: Engine) -> Dict[str, int]:
    """Explicitly mark legacy NULL/empty workspaces as unclassified."""
    with engine.begin() as conn:
        return _backfill_workspace_rows(conn)


def run_workspace_migrations(
    engine: Engine,
    *,
    backup: bool = True,
    backup_dir: Optional[Path | str] = None,
) -> Dict[str, object]:
    """Run all idempotent workspace migrations with failure recovery.

    A consistent backup is made before the first column change.  SQLite DDL is
    not reliably rolled back by every driver, so if any statement fails the
    pre-upgrade backup is restored and the exception is re-raised.  This keeps
    a failed upgrade from leaving a half-migrated database behind.
    """
    pending = has_pending_workspace_migrations(engine)
    backup_path = None
    if pending and backup:
        from database.backup import create_backup

        backup_path = create_backup(engine, reason="workspace-migration", backup_dir=backup_dir)

    try:
        with engine.begin() as conn:
            result = _ensure_workspace_columns(conn)
            result["backfilled"] = _backfill_workspace_rows(conn)
    except Exception:
        if backup_path:
            from database.backup import database_path, restore_backup

            db_path = database_path(engine)
            engine.dispose()
            if db_path:
                restore_backup(
                    backup_path,
                    db_path,
                    safety_backup=False,
                    backup_dir=backup_dir,
                )
                log.warning("Migration failed; restored pre-upgrade backup %s", backup_path)
        raise
    result["backup_path"] = str(backup_path) if backup_path else None
    return result
