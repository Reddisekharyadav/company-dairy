from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
import logging

log = logging.getLogger('session')

def _get_db_path() -> str:
    """Return a stable DB path that works both in dev and as a frozen exe."""
    # When frozen by PyInstaller, store data in AppData so it survives across runs
    app_data = os.environ.get("APPDATA") or os.path.expanduser("~")
    data_dir = os.path.join(app_data, "WorkSense")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "events.db")

DB_PATH = _get_db_path()
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})

from sqlalchemy import event
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

def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """SQLite-compatible: add a column only if it doesn't exist."""
    try:
        result = conn.execute(text(f"PRAGMA table_info({table})"))
        cols = [row[1] for row in result.fetchall()]
        if column not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            log.info("Added column %s.%s", table, column)
    except Exception as e:
        log.debug("Column migration skipped: %s", e)


def init_db():
    from .models import Base
    Base.metadata.create_all(bind=engine)

    # Enable WAL mode for better concurrent write performance
    with engine.connect() as conn:
        try:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=5000"))
        except Exception as e:
            log.debug("WAL mode setup: %s", e)

    # Migrate existing DB: add new columns if missing
    with engine.connect() as conn:
        _add_column_if_missing(conn, 'events', 'category', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'events', 'website', 'VARCHAR(256)')
        _add_column_if_missing(conn, 'events', 'input_state', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'ocr', 'screenshot_path', 'VARCHAR(2048)')
        # v2.1: file_edits.editor — which IDE was used
        _add_column_if_missing(conn, 'file_edits', 'editor', 'VARCHAR(128)')
        _add_column_if_missing(conn, 'daily_notes', 'context_data', 'TEXT')
        _add_column_if_missing(conn, 'daily_notes', 'screenshot_path', 'VARCHAR(2048)')
        # v3.1: session_id for all trackers
        _add_column_if_missing(conn, 'events', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'git', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'ocr', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'daily_notes', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'file_edits', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'browser_history', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'meetings', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'activity_insights', 'session_id', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'activity_insights', 'engagement_type', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'activity_insights', 'ocr_summary', 'TEXT')
        # v3.2: auto_generated flag for screen analysis notes
        _add_column_if_missing(conn, 'daily_notes', 'auto_generated', 'BOOLEAN DEFAULT 0')

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

        conn.commit()

