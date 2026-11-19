"""Workspace isolation and date-filter tests for the activity service.

Every query is executed against a private temp SQLite database.  The assertions
check the SQL-level filters, not frontend filtering, so a regression here means
workspace data could leak between scopes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    ActivityInsight,
    Base,
    BrowserHistory,
    DailyNote,
    Event,
    FileChangeEvent,
    FileEdit,
    GitActivity,
    Meeting,
    ResearchSession,
    ScreenFrame,
    SearchQuery,
)
from reports.model import build_report
from services.activity_service import ActivityService, resolve_date_range

DAY = datetime(2026, 5, 10, 12, 0, 0)
OUTSIDE = datetime(2026, 5, 1, 12, 0, 0)


@pytest.fixture
def db(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'activity.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    _seed(session)
    yield session
    session.close()
    engine.dispose()


def _seed(session) -> None:
    def event(workspace, when, duration, category, application, project="Demo"):
        session.add(Event(
            timestamp=when,
            workspace=workspace,
            duration=duration,
            category=category,
            application=application,
            project=project,
            idle=False,
        ))

    event("company", DAY.replace(hour=10), 3600, "Email", "outlook.exe", "Company Portal")
    event("development", DAY.replace(hour=11), 1800, "Coding", "code.exe", "demo_app")
    event("personal", DAY.replace(hour=9), 600, "Entertainment", "spotify.exe", "Personal")
    event("unclassified", DAY.replace(hour=8), 300, "Other", "unknown.exe", "Mystery")
    event("company", OUTSIDE, 999, "Email", "outlook.exe", "Old Company")

    session.add(FileChangeEvent(
        timestamp=DAY, workspace="company", event_type="modified",
        file_path="C:/work/report.docx", file_name="report.docx", project="Company Portal",
        language="Word", source="filesystem", session_date="2026-05-10",
    ))
    session.add(FileChangeEvent(
        timestamp=DAY, workspace="development", event_type="created",
        file_path="C:/dev/app.py", file_name="app.py", project="demo_app",
        language="Python", source="filesystem", session_date="2026-05-10",
    ))
    session.add(FileChangeEvent(
        timestamp=DAY, workspace="personal", event_type="created",
        file_path="C:/home/photo.jpg", file_name="photo.jpg", project="Personal",
        language="Image", source="filesystem", session_date="2026-05-10",
    ))
    session.add(FileChangeEvent(
        timestamp=DAY, workspace="unclassified", event_type="created",
        file_path="C:/misc/notes.txt", file_name="notes.txt", project="Mystery",
        language="Text", source="filesystem", session_date="2026-05-10",
    ))
    session.add(FileChangeEvent(
        timestamp=OUTSIDE, workspace="company", event_type="modified",
        file_path="C:/work/old.docx", file_name="old.docx", project="Company Portal",
        source="filesystem", session_date="2026-05-01",
    ))

    session.add(FileEdit(
        timestamp=DAY, workspace="company", file_path="C:/work/report.docx",
        file_name="report.docx", project="Company Portal", duration_sec=60,
        editor="Word", language="Word", source="active_window_inference", session_date="2026-05-10",
    ))
    session.add(FileEdit(
        timestamp=DAY, workspace="development", file_path="C:/dev/app.py",
        file_name="app.py", project="demo_app", duration_sec=120,
        editor="VS Code", language="Python", source="active_window_inference",
        session_date="2026-05-10",
    ))
    session.add(FileEdit(
        timestamp=DAY, workspace="personal", file_path="C:/home/photo.jpg",
        file_name="photo.jpg", project="Personal", duration_sec=30,
        editor="Photos", language="Image", source="active_window_inference", session_date="2026-05-10",
    ))

    session.add(Meeting(
        start_time=DAY.replace(hour=10), end_time=DAY.replace(hour=11),
        duration_sec=3600, platform="Teams", title="Client sync",
        workspace="company", session_date="2026-05-10",
    ))
    session.add(Meeting(
        start_time=DAY.replace(hour=15), end_time=DAY.replace(hour=16),
        duration_sec=3600, platform="Meet", title="Personal call",
        workspace="personal", session_date="2026-05-10",
    ))
    session.add(Meeting(
        start_time=DAY.replace(hour=13), end_time=DAY.replace(hour=14),
        duration_sec=3600, platform="Zoom", title="Sprint review",
        workspace="development", session_date="2026-05-10",
    ))

    session.add(GitActivity(
        timestamp=DAY, workspace="development", repo="demo_app",
        commit_hash="abc123", message="Add feature", author="Dev",
        branch="main", files_changed=2, insertions=10, deletions=1,
    ))
    session.add(SearchQuery(
        timestamp=DAY, workspace="development", query="sqlalchemy filter",
        source="Chrome", raw_title="google.com", bookmarked=True,
    ))
    session.add(SearchQuery(
        timestamp=DAY, workspace="personal", query="weekend recipes",
        source="Chrome", raw_title="google.com", bookmarked=False,
    ))
    session.add(ResearchSession(
        start_time=DAY, end_time=DAY.replace(hour=13), topic_label="SQLAlchemy",
        query_count=3, total_duration_sec=900, workspace="development",
    ))
    session.add(BrowserHistory(
        timestamp=DAY, workspace="personal", url="https://example.com",
        title="Example", domain="example.com", duration_sec=30,
        browser="Chrome", session_date="2026-05-10",
    ))
    session.add(DailyNote(
        timestamp=DAY, workspace="company", content="Company note",
        source="typed", date="2026-05-10",
    ))
    session.add(ActivityInsight(
        timestamp=DAY, workspace="development", app="code.exe",
        window_title="app.py", summary="Editing code", duration_on_tab=45,
        engagement_type="active_typing", session_date="2026-05-10",
    ))
    session.add(ScreenFrame(
        timestamp=DAY, workspace="unclassified", session_date="2026-05-10",
        process_name="unknown.exe", window_title="Unknown",
    ))
    session.commit()


def _day_range():
    return resolve_date_range(start="2026-05-10", end="2026-05-10")


def _workspaces(rows):
    return {row.workspace for row in rows}


def test_scope_isolation_for_events(db):
    service = ActivityService(session=db)
    rng = _day_range()

    assert _workspaces(service.events("company", rng)) == {"company"}
    assert _workspaces(service.events("development", rng)) == {"development"}
    assert _workspaces(service.events("personal", rng)) == {"personal"}
    assert _workspaces(service.events("unclassified", rng)) == {"unclassified"}
    assert _workspaces(service.events("combined", rng)) == {"company", "development"}
    assert _workspaces(service.events("all", rng)) == {
        "company", "development", "personal", "unclassified",
    }


def test_combined_never_includes_personal_or_unclassified(db):
    service = ActivityService(session=db)
    rng = _day_range()

    combined_events = service.events("combined", rng)
    combined_files = service.file_changes("combined", rng)
    combined_meetings = service.meetings("combined", rng)
    combined_queries = service.search_queries("combined", rng)

    for rows in (combined_events, combined_files, combined_meetings, combined_queries):
        assert "personal" not in _workspaces(rows)
        assert "unclassified" not in _workspaces(rows)


def test_date_range_excludes_out_of_range_rows(db):
    service = ActivityService(session=db)
    rng = _day_range()

    company_events = service.events("company", rng)
    assert all(row.timestamp.date().isoformat() == "2026-05-10" for row in company_events)
    assert all(row.duration != 999 for row in company_events)

    company_files = service.file_changes("company", rng)
    assert [row.file_name for row in company_files] == ["report.docx"]

    all_range = resolve_date_range("all")
    assert all_range.start.year == 1970
    assert len(service.events("company", all_range)) == 2


def test_summary_counts_are_isolated(db):
    service = ActivityService(session=db)
    rng = _day_range()

    company = service.summary("company", rng)
    assert company["workspace"] == "company"
    assert company["file_changes"] == 1
    assert company["files_touched"] == 1
    assert company["git_commits"] == 0
    assert company["research_queries"] == 0
    assert company["meetings"] == 1
    assert company["active_seconds"] == 3600.0

    development = service.summary("development", rng)
    assert development["file_changes"] == 1
    assert development["git_commits"] == 1
    assert development["research_queries"] == 1
    assert development["meetings"] == 1

    combined = service.summary("combined", rng)
    assert combined["file_changes"] == 2
    assert combined["git_commits"] == 1
    assert combined["research_queries"] == 1
    assert combined["meetings"] == 2

    personal = service.summary("personal", rng)
    assert personal["file_changes"] == 1
    assert personal["git_commits"] == 0
    assert personal["meetings"] == 1

    everything = service.summary("all", rng)
    assert everything["file_changes"] == 4
    assert everything["meetings"] == 3


def test_file_change_filters(db):
    service = ActivityService(session=db)
    rng = _day_range()

    assert [row.file_name for row in service.file_changes("development", rng)] == ["app.py"]
    assert service.file_changes("development", rng, event_type="created")[0].file_name == "app.py"
    assert service.file_changes("development", rng, event_type="deleted") == []
    assert [row.file_name for row in service.file_changes("all", rng, search="app")] == ["app.py"]
    assert {row.project for row in service.file_changes("combined", rng)} == {
        "Company Portal", "demo_app",
    }


def test_project_and_language_totals(db):
    service = ActivityService(session=db)
    rng = _day_range()

    projects = service.project_totals("combined", rng)
    names = {row["project"] for row in projects}
    assert {"Company Portal", "demo_app"} <= names
    assert "Personal" not in names
    assert "Mystery" not in names

    languages = service.language_totals("combined", rng)
    language_names = {row["language"] for row in languages}
    assert "Python" in language_names
    assert "Unknown" not in language_names


def test_build_report_respects_scope(db):
    company = build_report("company", start="2026-05-10", end="2026-05-10", session=db)
    assert company.meta.workspace == "company"
    assert company.summary["workspace"] == "company"
    assert company.timeline and all(row["workspace"] == "company" for row in company.timeline)
    assert company.file_changes and all(row["workspace"] == "company" for row in company.file_changes)
    assert "app.py" not in {row["file_name"] for row in company.file_changes}
    assert any("estimate" in note.lower() or "not proof" in note.lower() for note in company.limitations)

    combined = build_report("combined", start="2026-05-10", end="2026-05-10", session=db)
    combined_workspaces = {row["workspace"] for row in combined.timeline}
    assert combined_workspaces == {"company", "development"}
    assert "app.py" in {row["file_name"] for row in combined.file_changes}

    everything = build_report("all", start="2026-05-10", end="2026-05-10", session=db)
    assert {row["workspace"] for row in everything.timeline} == {
        "company", "development", "personal", "unclassified",
    }


def test_invalid_scope_defaults_to_company(db):
    service = ActivityService(session=db)
    summary = service.summary("not-a-workspace", _day_range())
    assert summary["workspace"] == "company"
    assert summary["file_changes"] == 1


def test_midnight_crossing_range_includes_both_days(db):
    late = datetime(2026, 5, 10, 23, 30, 0)
    early = datetime(2026, 5, 11, 0, 30, 0)
    outside = datetime(2026, 5, 11, 2, 0, 0)
    for when, title in ((late, "Late company work"), (early, "Early company work"),
                        (outside, "Outside range")):
        db.add(Event(timestamp=when, workspace="company", duration=600,
                     category="Email", application="outlook.exe",
                     window_title=title, idle=False))
    db.commit()

    date_range = resolve_date_range(
        start="2026-05-10T23:00:00", end="2026-05-11T01:00:00"
    )
    service = ActivityService(session=db)
    titles = {row.window_title for row in service.events("company", date_range)}
    assert titles == {"Late company work", "Early company work"}

    report = build_report(
        "company",
        start="2026-05-10T23:00:00",
        end="2026-05-11T01:00:00",
        session=db,
    )
    report_titles = {
        row["window_title"] for row in report.timeline
        if row["window_title"] in {"Late company work", "Early company work", "Outside range"}
    }
    assert report_titles == {"Late company work", "Early company work"}
    assert report.meta.start.startswith("2026-05-10T23:00:00")
    assert report.meta.end.startswith("2026-05-11T01:00:00")
