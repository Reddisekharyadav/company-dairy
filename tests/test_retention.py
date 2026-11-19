"""Retention cleanup tests: expiry, screenshot deletion and path safety."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    ActivityInsight,
    Base,
    DailyNote,
    Event,
    OCRText,
    Report,
    ResearchSession,
    ScreenFrame,
    SearchQuery,
    SessionSnapshot,
)
from tracker.tracker import ActivityTracker


def test_retention_removes_old_rows_and_only_allowed_screenshot_files(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'retention.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    shots = tmp_path / "shots"
    shots.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    old = datetime.now() - timedelta(days=60)
    now = datetime.now()

    def make_file(folder: Path, name: str) -> Path:
        path = folder / name
        path.write_bytes(b"image")
        old_ts = (datetime.now() - timedelta(days=60)).timestamp()
        os.utime(path, (old_ts, old_ts))
        return path

    old_frame_file = make_file(shots, "old_frame.webp")
    old_ocr_file = make_file(shots, "old_ocr.webp")
    old_note_file = make_file(shots, "old_note.webp")
    new_file = shots / "new.webp"
    new_file.write_bytes(b"image")
    outside_file = make_file(outside, "secret.webp")

    session.add_all([
        Event(timestamp=old, workspace="company", duration=60, category="Email"),
        Event(timestamp=now, workspace="company", duration=60, category="Email"),
        SearchQuery(timestamp=old, workspace="company", query="old search"),
        SearchQuery(timestamp=now, workspace="company", query="new search"),
        ResearchSession(start_time=old, workspace="company", topic_label="old"),
        ResearchSession(start_time=now, workspace="company", topic_label="new"),
        ActivityInsight(timestamp=old, workspace="company", summary="old"),
        ActivityInsight(timestamp=now, workspace="company", summary="new"),
        Report(created_at=old, period_start=old, workspace="company", format="pdf"),
        SessionSnapshot(created_at=old, snapshot_date="2026-01-01", workspace_scope="company"),
        ScreenFrame(timestamp=old, workspace="company", screenshot_path=str(old_frame_file)),
        ScreenFrame(timestamp=old, workspace="company", screenshot_path=str(outside_file)),
        OCRText(timestamp=old, workspace="company", text="old", screenshot_path=str(old_ocr_file)),
        DailyNote(timestamp=old, workspace="company", date="2026-01-01",
                  content="old note", screenshot_path=str(old_note_file)),
        ScreenFrame(timestamp=now, workspace="company", screenshot_path=str(new_file)),
    ])
    session.commit()

    deleted = ActivityTracker()._cleanup_old_data(
        session, retention_days=30, max_storage_mb=2048, screenshot_dirs=[shots],
    )

    assert deleted["events"] == 1
    assert deleted["search_queries"] == 1
    assert deleted["research_sessions"] == 1
    assert deleted["activity_insights"] == 1
    assert deleted["screen_frames"] == 2
    assert deleted["ocr"] == 1
    assert deleted["daily_notes"] == 1

    assert session.query(Event).count() == 1
    assert session.query(SearchQuery).count() == 1
    assert session.query(ResearchSession).count() == 1
    assert session.query(ActivityInsight).count() == 1
    assert session.query(Report).count() == 0
    assert session.query(SessionSnapshot).count() == 0
    assert session.query(ScreenFrame).count() == 1

    # Files inside the configured screenshot root are deleted with their rows.
    assert not old_frame_file.exists()
    assert not old_ocr_file.exists()
    assert not old_note_file.exists()
    assert new_file.exists()
    # A malicious/corrupt path outside the root is never unlinked.
    assert outside_file.exists()

    session.close()
    engine.dispose()
