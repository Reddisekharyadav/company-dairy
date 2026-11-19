"""SQLite WAL/concurrency and backup-under-write tests."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.backup import create_backup, restore_backup
from database.models import Base, Event


def _engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))
    Base.metadata.create_all(engine)
    return engine


def test_concurrent_writers_and_wal_mode(tmp_path: Path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    errors = []
    barrier = threading.Barrier(6)

    def writer(worker: int):
        try:
            barrier.wait(timeout=10)
            session = Session()
            try:
                for index in range(25):
                    session.add(Event(
                        timestamp=datetime(2026, 5, 10, 12, 0, 0),
                        workspace="company",
                        application=f"worker-{worker}",
                        window_title=f"row {index}",
                        duration=1,
                    ))
                    session.commit()
            finally:
                session.close()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors
    session = Session()
    try:
        assert session.query(Event).count() == 150
    finally:
        session.close()

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA integrity_check")).scalar().lower() == "ok"

    engine.dispose()


def test_backup_is_consistent_during_concurrent_writes_and_restores(tmp_path: Path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    stop = threading.Event()
    errors = []

    def writer():
        session = Session()
        try:
            index = 0
            while not stop.is_set():
                session.add(Event(
                    timestamp=datetime.now(), workspace="company",
                    application="writer", window_title=f"live {index}", duration=1,
                ))
                session.commit()
                index += 1
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            session.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        backup = None
        for _ in range(20):
            backup = create_backup(engine, reason="concurrency-test",
                                   backup_dir=tmp_path / "backups", keep=3)
            if backup:
                break
        assert backup is not None
    finally:
        stop.set()
        thread.join(timeout=30)

    assert not errors
    with sqlite3.connect(backup) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert rows >= 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0].lower() == "ok"

    engine.dispose()
    restored = tmp_path / "restored.db"
    restore_backup(backup, restored, safety_backup=False, backup_dir=tmp_path / "backups")
    with sqlite3.connect(restored) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0].lower() == "ok"
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == rows
