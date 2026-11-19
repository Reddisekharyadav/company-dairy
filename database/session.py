from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
import logging

log = logging.getLogger('session')

def _get_db_path() -> str:
    """Return a stable DB path that works both in dev and as a frozen exe."""
    # Explicit override for tests, portable installs and recovery tools.
    override = os.environ.get("WS_DB_PATH")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path
    # When frozen by PyInstaller, store data in AppData so it survives across runs
    app_data = os.environ.get("APPDATA") or os.path.expanduser("~")
    data_dir = os.path.join(app_data, "WorkSense")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "events.db")

DB_PATH = _get_db_path()
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)

from sqlalchemy import event


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Apply per-connection safety/concurrency pragmas."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()

try:
    import sqlite_vec
    @event.listens_for(engine, "connect")
    def _load_sqlite_vec(dbapi_connection, connection_record):
        try:
            dbapi_connection.enable_load_extension(True)
            sqlite_vec.load(dbapi_connection)
            dbapi_connection.enable_load_extension(False)
        except Exception as e:
            log.warning("Could not load sqlite-vec extension: %s", e)
    log.info("sqlite-vec extension registered.")
except Exception as e:
    log.warning("sqlite-vec not available: %s", e)

SessionLocal = sessionmaker(bind=engine)

# Columns added by older releases.  Kept here because the historical upgrade
# path uses plain ALTER TABLE statements rather than Alembic revisions.
LEGACY_COLUMNS = [
    ('events', 'category', 'VARCHAR(64)'),
    ('events', 'website', 'VARCHAR(256)'),
    ('events', 'input_state', 'VARCHAR(64)'),
    ('ocr', 'screenshot_path', 'VARCHAR(2048)'),
    # v2.1: file_edits.editor — which IDE was used
    ('file_edits', 'editor', 'VARCHAR(128)'),
    ('daily_notes', 'context_data', 'TEXT'),
    ('daily_notes', 'screenshot_path', 'VARCHAR(2048)'),
    # v3.1: session_id for all trackers
    ('events', 'session_id', 'VARCHAR(64)'),
    ('git', 'session_id', 'VARCHAR(64)'),
    ('ocr', 'session_id', 'VARCHAR(64)'),
    ('daily_notes', 'session_id', 'VARCHAR(64)'),
    ('file_edits', 'session_id', 'VARCHAR(64)'),
    ('browser_history', 'session_id', 'VARCHAR(64)'),
    ('meetings', 'session_id', 'VARCHAR(64)'),
    ('activity_insights', 'session_id', 'VARCHAR(64)'),
    ('activity_insights', 'engagement_type', 'VARCHAR(64)'),
    ('activity_insights', 'ocr_summary', 'TEXT'),
    # v3.2: auto_generated flag for screen analysis notes
    ('daily_notes', 'auto_generated', 'BOOLEAN DEFAULT 0'),
]


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return column in {row[1] for row in rows}


def _pending_legacy_columns() -> list:
    pending = []
    with engine.connect() as conn:
        for table, column, _col_type in LEGACY_COLUMNS:
            if not _column_exists(conn, table, column):
                pending.append((table, column))
    return pending


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> bool:
    """SQLite-compatible: add a column only if it doesn't exist."""
    if _column_exists(conn, table, column):
        return False
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
    log.info("Added column %s.%s", table, column)
    return True


def init_db():
    from .models import Base

    # Capture existence before create_all creates an empty file.
    db_existed = os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0
    Base.metadata.create_all(bind=engine)

    # Enable WAL mode for better concurrent write performance.  The journal
    # mode is persistent; busy_timeout/foreign_keys are set per connection by
    # the connect listener above.
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
    except Exception as e:
        log.debug("WAL mode setup: %s", e)

    from .migrations import (
        has_pending_workspace_migrations,
        run_workspace_migrations,
    )

    workspace_pending = has_pending_workspace_migrations(engine)
    legacy_pending = _pending_legacy_columns()

    # Never change an existing database without a verified, consistent backup.
    backup_path = None
    if db_existed and (workspace_pending or legacy_pending):
        from .backup import create_backup

        backup_path = create_backup(engine, reason="schema-upgrade")

    try:
        if legacy_pending:
            with engine.begin() as conn:
                for table, column, col_type in LEGACY_COLUMNS:
                    _add_column_if_missing(conn, table, column, col_type)

        # The backup was already taken for this startup.  run_workspace_migrations
        # still runs atomically for callers that invoke it directly.
        migration_result = run_workspace_migrations(engine, backup=False)
        if migration_result.get('added_columns') or migration_result.get('backfilled'):
            log.info('Workspace migration complete: %s', migration_result)
    except Exception:
        if backup_path:
            from .backup import restore_backup

            engine.dispose()
            restore_backup(backup_path, DB_PATH, safety_backup=False)
            log.warning("Database upgrade failed; restored %s", backup_path)
        raise

    with engine.begin() as conn:
        # v4.0: FTS5 full-text search index for OCR text
        try:
            conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
                    text, source, content='ocr', content_rowid='id'
                )
            """))
            log.info("FTS5 virtual table 'ocr_fts' ready.")
        except Exception as e:
            log.debug("FTS5 setup skipped (may need newer SQLite): %s", e)

        # v4.0: FTS5 for screen_frames OCR text
        try:
            conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS screen_frames_fts USING fts5(
                    ocr_text, window_title, analysis_summary,
                    content='screen_frames', content_rowid='id'
                )
            """))
            log.info("FTS5 virtual table 'screen_frames_fts' ready.")
        except Exception as e:
            log.debug("screen_frames FTS5 setup skipped: %s", e)

        # v4.0: sqlite-vec vector table for semantic search
        try:
            conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS screen_frames_vec USING vec0(
                    embedding float[384]
                )
            """))
            log.info("sqlite-vec virtual table 'screen_frames_vec' ready.")
        except Exception as e:
            log.debug("sqlite-vec setup skipped: %s", e)

