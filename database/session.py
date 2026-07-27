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
    # Migrate existing DB: add new columns if missing
    with engine.connect() as conn:
        _add_column_if_missing(conn, 'events', 'category', 'VARCHAR(64)')
        _add_column_if_missing(conn, 'events', 'website', 'VARCHAR(256)')
        _add_column_if_missing(conn, 'ocr', 'screenshot_path', 'VARCHAR(2048)')
        conn.commit()

