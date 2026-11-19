"""Filesystem watcher acceptance tests.

These tests cover real watchdog events (create/modify/delete/move), multiple
roots, ignored paths, debounce coalescing, and idempotent lifecycle.  A temp
SQLite database is injected so the tests never touch the user's real
``%APPDATA%/WorkSense/events.db``.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tracker.fs_watcher as fs_watcher
from database.models import Base, FileChangeEvent
from tracker.fs_watcher import FileSystemWatcher, WATCHDOG_AVAILABLE

pytestmark = pytest.mark.skipif(
    not WATCHDOG_AVAILABLE, reason="watchdog is not installed"
)

# The default ignore list contains "temp"/"tmp", which would also ignore pytest
# temp directories on Windows.  Tests use a narrower list on purpose.
TEST_IGNORED_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


class _FakeEvent:
    def __init__(self, src_path: str, dest_path: str = "", is_directory: bool = False):
        self.src_path = src_path
        self.dest_path = dest_path
        self.is_directory = is_directory


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "watcher_events.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_classifier(monkeypatch):
    """Never mutate the user's real workspace rules during tests."""
    monkeypatch.setattr(fs_watcher.classifier, "update_rules", lambda updates: None)


def _snapshot(factory):
    session = factory()
    try:
        return [
            {
                "event_type": row.event_type,
                "file_path": row.file_path,
                "file_name": row.file_name,
                "workspace": row.workspace,
                "old_path": row.old_path,
                "source": row.source,
                "relative_path": row.relative_path,
            }
            for row in session.query(FileChangeEvent).order_by(FileChangeEvent.id).all()
        ]
    finally:
        session.close()


def _count(factory) -> int:
    session = factory()
    try:
        return session.query(FileChangeEvent).count()
    finally:
        session.close()


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _watcher(root, factory, *, roots=None, debounce=0.1):
    return FileSystemWatcher(
        roots=roots if roots is not None else [{"path": str(root), "workspace": "development"}],
        debounce_seconds=debounce,
        startup_grace=0.0,
        ignored_dirs=TEST_IGNORED_DIRS,
        session_factory=factory,
    )


def test_focused_editor_title_attributes_application(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    root.mkdir()
    watcher = FileSystemWatcher(
        roots=[{"path": str(root), "workspace": "development"}],
        debounce_seconds=0.1,
        startup_grace=0.0,
        ignored_dirs=TEST_IGNORED_DIRS,
        session_factory=session_factory,
        active_window_provider=lambda: ("Code.exe", "app.py - Visual Studio Code"),
    )
    target = root / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")

    watcher._handle_event(_FakeEvent(str(target)), "created")
    watcher._flush_pending(force=True)

    session = session_factory()
    try:
        row = session.query(FileChangeEvent).one()
        assert row.source == "filesystem"
        assert row.attribution == "focused_window"
        assert row.application == "VS Code"
    finally:
        session.close()


def test_non_editor_focus_does_not_invent_attribution(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    root.mkdir()
    watcher = FileSystemWatcher(
        roots=[{"path": str(root), "workspace": "development"}],
        debounce_seconds=0.1,
        startup_grace=0.0,
        ignored_dirs=TEST_IGNORED_DIRS,
        session_factory=session_factory,
        active_window_provider=lambda: ("powershell.exe", "app.py - Windows PowerShell"),
    )
    target = root / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")

    watcher._handle_event(_FakeEvent(str(target)), "created")
    watcher._flush_pending(force=True)

    session = session_factory()
    try:
        row = session.query(FileChangeEvent).one()
        assert row.source == "filesystem"
        assert row.attribution is None
        assert row.application is None
    finally:
        session.close()


def test_real_create_and_modify_events_are_persisted(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    root.mkdir()
    watcher = _watcher(root, session_factory)

    assert watcher.start() is True
    try:
        source = root / "main.py"
        source.write_text("print('first')\n", encoding="utf-8")
        assert _wait_until(lambda: _count(session_factory) >= 1), "create was not recorded"

        # Let the create debounce/dedupe window expire before the second write.
        time.sleep(0.4)
        source.write_text("print('second')\n", encoding="utf-8")
        assert _wait_until(
            lambda: any(r["event_type"] == "modified" for r in _snapshot(session_factory))
        ), "modify was not recorded"
    finally:
        watcher.stop()

    rows = _snapshot(session_factory)
    assert {row["event_type"] for row in rows} >= {"created", "modified"}
    assert all(row["source"] == "filesystem" for row in rows)
    assert all(row["workspace"] == "development" for row in rows)
    assert all(row["file_name"] == "main.py" for row in rows)
    assert rows[0]["relative_path"] == "main.py"
    status = watcher.get_status()
    assert status["events_recorded"] >= 2
    assert status["events_seen"] >= 2
    assert status["last_event_at"] is not None


def test_delete_and_move_events_recorded(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    root.mkdir()
    watcher = _watcher(root, session_factory)

    source = root / "old_name.py"
    destination = root / "new_name.py"
    source.write_text("value = 1\n", encoding="utf-8")

    watcher._handle_event(_FakeEvent(str(source)), "created")
    watcher._flush_pending(force=True)

    watcher._handle_event(_FakeEvent(str(source), str(destination)), "moved")
    watcher._flush_pending(force=True)

    watcher._handle_event(_FakeEvent(str(destination)), "deleted")
    watcher._flush_pending(force=True)

    rows = {row["event_type"]: row for row in _snapshot(session_factory)}
    assert set(rows) == {"created", "moved", "deleted"}
    assert rows["moved"]["file_path"] == os.path.abspath(destination)
    assert rows["moved"]["old_path"] == os.path.abspath(source)
    assert rows["deleted"]["file_path"] == os.path.abspath(destination)


def test_ignored_directories_and_files_are_not_recorded(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    root.mkdir()
    watcher = _watcher(root, session_factory)

    ignored = [
        root / "node_modules" / "package" / "index.js",
        root / "__pycache__" / "module.py",
        root / ".git" / "config.py",
        root / "debug.log",
        root / "archive.py.bak",
        root / "~$report.docx",
    ]
    for path in ignored:
        watcher._handle_event(_FakeEvent(str(path)), "created")

    tracked = root / "src" / "app.py"
    watcher._handle_event(_FakeEvent(str(tracked)), "created")
    watcher._flush_pending(force=True)

    rows = _snapshot(session_factory)
    assert [row["file_path"] for row in rows] == [os.path.abspath(tracked)]
    status = watcher.get_status()
    assert status["events_ignored"] >= len(ignored)
    assert status["events_recorded"] == 1


def test_multiple_roots_and_workspace_assignment(tmp_path: Path, session_factory):
    company_root = tmp_path / "company"
    dev_root = tmp_path / "development"
    company_root.mkdir()
    dev_root.mkdir()

    watcher = _watcher(
        company_root,
        session_factory,
        roots=[
            {"path": str(company_root), "workspace": "company"},
            {"path": str(dev_root), "workspace": "development"},
        ],
    )

    watcher._handle_event(_FakeEvent(str(company_root / "report.md")), "created")
    watcher._handle_event(_FakeEvent(str(dev_root / "service.py")), "created")
    watcher._flush_pending(force=True)

    rows = {row["file_path"]: row for row in _snapshot(session_factory)}
    assert rows[os.path.abspath(company_root / "report.md")]["workspace"] == "company"
    assert rows[os.path.abspath(dev_root / "service.py")]["workspace"] == "development"

    roots = watcher.get_roots()
    assert {root["workspace"] for root in roots} == {"company", "development"}


def test_create_and_modify_are_coalesced_by_debounce(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    root.mkdir()
    watcher = _watcher(root, session_factory, debounce=0.15)
    target = root / "coalesced.py"

    watcher._handle_event(_FakeEvent(str(target)), "created")
    watcher._handle_event(_FakeEvent(str(target)), "modified")

    # Not old enough yet: the debounce window must hold the event back.
    watcher._flush_pending(force=False)
    assert _snapshot(session_factory) == []

    time.sleep(0.25)
    watcher._flush_pending(force=False)

    rows = _snapshot(session_factory)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "created"


def test_lifecycle_is_idempotent_and_roots_are_managed(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()

    watcher = _watcher(root, session_factory)
    assert watcher.start() is True
    assert watcher.start() is True  # second start must be a no-op
    assert watcher.get_status()["running"] is True

    watcher.add_root(str(other), workspace="company")
    assert {item["path"] for item in watcher.get_roots()} == {
        os.path.abspath(root),
        os.path.abspath(other),
    }

    watcher.remove_root(str(other))
    assert {item["path"] for item in watcher.get_roots()} == {os.path.abspath(root)}

    watcher.stop()
    watcher.stop()  # second stop must be a no-op
    assert watcher.get_status()["running"] is False


def test_nested_roots_are_collapsed_to_parent(tmp_path: Path, session_factory):
    root = tmp_path / "project"
    nested = root / "packages" / "api"
    nested.mkdir(parents=True)

    watcher = _watcher(
        root,
        session_factory,
        roots=[
            {"path": str(root), "workspace": "development"},
            {"path": str(nested), "workspace": "company"},
        ],
    )

    roots = watcher.get_roots()
    assert len(roots) == 1
    assert roots[0]["path"] == os.path.abspath(root)


def test_ignored_directory_names_only_apply_below_the_root(tmp_path: Path, session_factory):
    """A project living under a parent named \"temp\" must still be watched."""
    root = tmp_path / "temp" / "project"
    root.mkdir(parents=True)
    watcher = FileSystemWatcher(
        roots=[{"path": str(root), "workspace": "development"}],
        debounce_seconds=0.1,
        startup_grace=0.0,
        ignored_dirs={"temp", "node_modules"},
        session_factory=session_factory,
    )

    watcher._handle_event(_FakeEvent(str(root / "main.py")), "created")
    watcher._handle_event(
        _FakeEvent(str(root / "node_modules" / "package" / "index.js")), "created"
    )
    watcher._flush_pending(force=True)

    rows = _snapshot(session_factory)
    assert [row["file_path"] for row in rows] == [
        os.path.abspath(root / "main.py")
    ]
    assert watcher.get_status()["events_ignored"] == 1
