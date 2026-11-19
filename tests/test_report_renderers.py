"""Report model/renderer acceptance tests.

Markdown must be clean (no code fences), DOCX must use native Word styles, PDF
must paginate long reports, and XLSX must contain the required sheets with only
the requested workspace's rows.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base,
    Event,
    FileChangeEvent,
    GitActivity,
    Meeting,
    SearchQuery,
)
from reports.excel_exporter import generate_excel
from reports.model import ReportData, ReportMeta
from reports.renderers import (
    render_markdown,
    report_filename,
    write_docx,
    write_markdown,
    write_pdf,
    write_text_summary,
)


def make_report(file_change_count: int = 0) -> ReportData:
    meta = ReportMeta(
        title="WorkSense AI - Company Report",
        workspace="company",
        workspace_label="Company Work",
        period="daily",
        start="2026-05-10T00:00:00",
        end="2026-05-10T23:59:59",
        generated_at="2026-05-10T23:59:59",
    )
    file_changes = [
        {
            "timestamp": f"2026-05-10T12:{index % 60:02d}:00",
            "event_type": "modified",
            "file_name": f"document_{index}.docx",
            "file_path": f"C:/work/document_{index}.docx",
            "relative_path": f"document_{index}.docx",
            "project": "Company Portal",
            "language": "Word",
            "workspace": "company",
            "application": "WINWORD.EXE",
            "attribution": "focused_window",
            "file_size": 1024,
        }
        for index in range(file_change_count)
    ]
    return ReportData(
        meta=meta,
        summary={
            "active_seconds": 7260,
            "meetings": 2,
            "meeting_seconds": 5400,
            "file_changes": file_change_count,
            "files_touched": 3,
            "git_commits": 1,
            "research_queries": 4,
            "projects": 2,
            "sites": 5,
        },
        categories=[
            {"category": "Email", "seconds": 3600, "hours": 1.0},
            {"category": "Coding", "seconds": 3660, "hours": 1.02},
        ],
        timeline=[
            {
                "timestamp": "2026-05-10T10:00:00",
                "application": "outlook.exe",
                "window_title": "Inbox" + ("x" * 1000),
                "category": "Email",
                "project": "Company Portal",
                "website": "",
                "duration_sec": 3600,
                "workspace": "company",
            }
        ],
        file_changes=file_changes,
        file_durations=[
            {
                "file_name": "report.docx",
                "file_path": "C:/work/report.docx",
                "project": "Company Portal",
                "language": "Word",
                "editor": "Word",
                "duration_sec": 60,
                "workspace": "company",
                "source": "active_window_inference",
                "timestamp": "2026-05-10T12:00:00",
            }
        ],
        projects=[
            {
                "project": "Company Portal",
                "changes": file_change_count,
                "hours": 1.5,
                "duration_sec": 5400,
                "languages": ["Word"],
                "file_count": 3,
            }
        ],
        languages=[{"language": "Word", "seconds": 3600, "hours": 1.0, "changes": 2}],
        meetings=[
            {
                "start_time": "2026-05-10T10:00:00",
                "end_time": "2026-05-10T11:00:00",
                "platform": "Teams",
                "title": "Client sync",
                "duration_sec": 3600,
                "workspace": "company",
            }
        ],
        research_queries=[
            {
                "timestamp": "2026-05-10T12:00:00",
                "query": "quarterly forecast | pipeline",
                "source": "Chrome",
                "bookmarked": True,
                "workspace": "company",
            }
        ],
        git_commits=[
            {
                "timestamp": "2026-05-10T14:00:00",
                "repo": "company-portal",
                "hash": "abcdef1234567890",
                "message": "Update forecast",
                "author": "Worker",
                "branch": "main",
                "files_changed": 2,
                "insertions": 12,
                "deletions": 3,
                "workspace": "company",
            }
        ],
        limitations=[
            "Focused-window file time is an estimate, not proof of typing.",
            "Filesystem events prove a file changed, not who changed it.",
        ],
    )


def test_markdown_is_clean_and_never_uses_code_fences(tmp_path: Path):
    report = make_report(file_change_count=3)
    markdown = render_markdown(report)

    assert markdown.startswith("# WorkSense AI - Company Report")
    assert "```" not in markdown
    assert "| Metric | Value | Notes |" in markdown
    assert "## File changes (observed filesystem events)" in markdown
    assert "not from keystrokes" in markdown
    assert "This is inferred from the focused editor window" in markdown
    # Pipe characters in user content must not break the table layout.
    assert "quarterly forecast / pipeline" in markdown

    path = write_markdown(report, str(tmp_path))
    assert Path(path).exists()
    assert Path(path).name == report_filename(report, "md")
    assert "```" not in Path(path).read_text(encoding="utf-8")


def test_markdown_handles_empty_and_very_long_content(tmp_path: Path):
    empty_markdown = render_markdown(make_report())
    assert "WorkSense AI" in empty_markdown
    assert "## File changes" not in empty_markdown

    report = make_report(file_change_count=2)
    report.timeline[0]["window_title"] = "Long title " * 5000
    report.categories[0]["category"] = "Category " * 3000

    markdown = render_markdown(report)
    # Timeline rows are rendered in the workbook, not in Markdown; the long
    # category and file rows must still render without truncation errors.
    assert "Category Category" in markdown
    assert len(markdown) > 1000

    path = write_markdown(report, str(tmp_path), "long.md")
    assert Path(path).stat().st_size > 0


def test_docx_uses_native_styles_and_tables(tmp_path: Path):
    from docx import Document

    report = make_report(file_change_count=3)
    path = write_docx(report, str(tmp_path))
    document = Document(path)

    style_names = {paragraph.style.name for paragraph in document.paragraphs}
    assert "Heading 1" in style_names
    assert any(
        paragraph.text == "Key metrics" and paragraph.style.name == "Heading 1"
        for paragraph in document.paragraphs
    )
    assert any("File changes" == paragraph.text for paragraph in document.paragraphs)
    assert len(document.tables) >= 3

    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Workspace: Company Work" in text
    assert "not proof of typing" in text


def _pdf_pages(path: str) -> int:
    data = Path(path).read_bytes()
    assert data.startswith(b"%PDF")
    return len(re.findall(rb"/Type\s*/Page[^s]", data))


def test_pdf_is_valid_and_paginates_long_reports(tmp_path: Path):
    short_path = write_pdf(make_report(), str(tmp_path), "short.pdf")
    assert _pdf_pages(short_path) >= 1

    long_path = write_pdf(make_report(file_change_count=300), str(tmp_path), "long.pdf")
    assert _pdf_pages(long_path) >= 2
    assert Path(long_path).stat().st_size > 1000


def test_pdf_and_text_handle_unsafe_unicode(tmp_path: Path):
    report = make_report()
    report.meta.title = "WorkSense AI Company Report Q2"
    report.categories[0]["category"] = "E-mail • Follow up — “urgent”"

    pdf_path = write_pdf(report, str(tmp_path), "unicode.pdf")
    assert _pdf_pages(pdf_path) >= 1

    text = write_text_summary(report)
    assert "WorkSense AI" in text
    assert "Workspace: Company Work" in text
    assert "→" not in text


@pytest.fixture
def excel_session(tmp_path: Path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'reports.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    when = datetime(2026, 5, 10, 10, 0, 0)

    session.add(Event(timestamp=when, workspace="company", duration=3600,
                      category="Email", application="outlook.exe", idle=False))
    session.add(Event(timestamp=when, workspace="development", duration=1800,
                      category="Coding", application="code.exe", idle=False))
    session.add(Event(timestamp=when, workspace="personal", duration=600,
                      category="Entertainment", application="spotify.exe", idle=False))

    session.add(FileChangeEvent(timestamp=when, workspace="company", event_type="modified",
                                file_path="C:/work/report.docx", file_name="report.docx",
                                project="Company Portal", language="Word"))
    session.add(FileChangeEvent(timestamp=when, workspace="development", event_type="created",
                                file_path="C:/dev/app.py", file_name="app.py",
                                project="demo_app", language="Python"))

    session.add(Meeting(start_time=when, end_time=when.replace(hour=11),
                        duration_sec=3600, platform="Teams", title="Client sync",
                        workspace="company"))
    session.add(GitActivity(timestamp=when, workspace="development", repo="demo_app",
                            commit_hash="abc123", message="Commit", author="Dev",
                            branch="main", files_changed=1, insertions=2, deletions=0))
    session.add(SearchQuery(timestamp=when, workspace="company", query="forecast",
                            source="Chrome", raw_title="google.com"))
    session.commit()
    session.close()

    monkeypatch.setattr("services.activity_service.SessionLocal", Session)
    yield Session
    engine.dispose()


def test_excel_has_required_sheets_and_company_only_rows(tmp_path: Path, excel_session):
    from openpyxl import load_workbook

    path = generate_excel(
        datetime(2026, 5, 10, 0, 0, 0),
        datetime(2026, 5, 10, 23, 59, 59),
        str(tmp_path / "exports"),
        workspace="company",
    )

    workbook = load_workbook(path)
    assert set(workbook.sheetnames) == {
        "Summary", "Timeline", "File Changes", "Meetings", "Research", "Git",
        "Projects", "Languages",
    }

    timeline = workbook["Timeline"]
    assert timeline.max_row >= 7
    assert {timeline.cell(row=row, column=8).value for row in range(7, timeline.max_row + 1)} == {"company"}

    files = workbook["File Changes"]
    assert {
        files.cell(row=row, column=9).value for row in range(7, files.max_row + 1)
    } == {"company"}
    assert files.cell(row=7, column=3).value == "report.docx"

    meetings = workbook["Meetings"]
    assert {
        meetings.cell(row=row, column=6).value for row in range(7, meetings.max_row + 1)
    } == {"company"}

    research = workbook["Research"]
    assert {
        research.cell(row=row, column=5).value for row in range(7, research.max_row + 1)
    } == {"company"}

    git = workbook["Git"]
    assert git.max_row == 6  # header only; development commits are excluded


def test_excel_development_scope_is_isolated(tmp_path: Path, excel_session):
    from openpyxl import load_workbook

    path = generate_excel(
        datetime(2026, 5, 10, 0, 0, 0),
        datetime(2026, 5, 10, 23, 59, 59),
        str(tmp_path / "exports"),
        workspace="development",
    )

    workbook = load_workbook(path)
    timeline = workbook["Timeline"]
    assert {timeline.cell(row=row, column=8).value for row in range(7, timeline.max_row + 1)} == {"development"}

    files = workbook["File Changes"]
    file_rows = [
        (files.cell(row=row, column=3).value, files.cell(row=row, column=9).value)
        for row in range(7, files.max_row + 1)
    ]
    assert file_rows == [("app.py", "development")]

    git = workbook["Git"]
    assert git.max_row == 7
    assert git.cell(row=7, column=2).value == "abc123"
    assert git.cell(row=7, column=10).value == "development"


def test_excel_blocks_formula_injection_and_enables_filtering(tmp_path: Path, excel_session):
    from openpyxl import load_workbook

    session = excel_session()
    when = datetime(2026, 5, 10, 15, 0, 0)
    malicious_title = '=HYPERLINK("http://evil.example","click")'
    malicious_message = "+cmd|' /C calc'!A0"
    malicious_filename = "@SUM(1+1)"
    session.add(Event(
        timestamp=when, workspace="company", duration=60, category="Other",
        application="evil.exe", window_title=malicious_title, idle=False,
    ))
    session.add(GitActivity(
        timestamp=when, workspace="company", repo="demo", commit_hash="deadbeef",
        message=malicious_message, author="Dev", branch="main",
        files_changed=1, insertions=1, deletions=0,
    ))
    session.add(FileChangeEvent(
        timestamp=when, workspace="company", event_type="modified",
        file_path="C:/work/" + malicious_filename, file_name=malicious_filename,
        project="Company Portal", language="Text",
    ))
    session.commit()

    path = generate_excel(
        datetime(2026, 5, 10, 0, 0, 0),
        datetime(2026, 5, 10, 23, 59, 59),
        str(tmp_path / "exports"),
        workspace="company",
    )
    workbook = load_workbook(path)

    title_cell = None
    message_cell = None
    filename_cell = None
    for row in range(7, workbook["Timeline"].max_row + 1):
        value = workbook["Timeline"].cell(row=row, column=3).value
        if value and malicious_title in value:
            title_cell = workbook["Timeline"].cell(row=row, column=3)
    for row in range(7, workbook["Git"].max_row + 1):
        value = workbook["Git"].cell(row=row, column=3).value
        if value and malicious_message in value:
            message_cell = workbook["Git"].cell(row=row, column=3)
    for row in range(7, workbook["File Changes"].max_row + 1):
        value = workbook["File Changes"].cell(row=row, column=3).value
        if value and malicious_filename in value:
            filename_cell = workbook["File Changes"].cell(row=row, column=3)
    assert title_cell is not None
    assert message_cell is not None
    assert filename_cell is not None
    assert title_cell.value == "'" + malicious_title
    assert message_cell.value == "'" + malicious_message
    assert filename_cell.value == "'" + malicious_filename
    for cell in (title_cell, message_cell, filename_cell):
        assert cell.data_type == "s"
        assert not cell.value.startswith("=")

    # Excel filtering/freezing is available on the data sheets.
    for sheet in ("Timeline", "File Changes", "Meetings", "Research", "Git", "Projects", "Languages"):
        ws = workbook[sheet]
        assert ws.auto_filter.ref
        assert ws.freeze_panes == "A7"


def test_report_filenames_are_collision_safe():
    first = make_report()
    second = make_report()
    second.meta.generated_at = "2026-05-10T23:59:59.123456"
    assert report_filename(first, "pdf") != report_filename(second, "pdf")
    assert report_filename(first, "pdf") == report_filename(first, "pdf")


def test_pdf_uses_unicode_font_and_handles_non_latin_text(tmp_path: Path):
    from reports.renderers import _pdf_fonts

    report = make_report()
    report.meta.title = "WorkSense – Café report"
    report.categories[0]["category"] = "Café — naïve — Ελληνικά — Русский — 日本語"
    path = write_pdf(report, str(tmp_path), "unicode-font.pdf")
    data = Path(path).read_bytes()
    assert _pdf_pages(path) >= 1
    body_font, _bold_font = _pdf_fonts()
    if body_font != "Helvetica":
        assert b"/FontFile2" in data


def test_pdf_renders_cjk_greek_arabic_and_labels_emoji(tmp_path: Path):
    from pypdf import PdfReader

    from reports.renderers import _pdf_rich_text, _pdf_script_fonts

    report = make_report()
    report.meta.title = "Unicode report"
    report.meetings[0]["title"] = (
        "Weekly Sync — Résumé — 日本語 — Ελληνικά — العربية — 🚀"
    )
    path = write_pdf(report, str(tmp_path), "unicode-all.pdf")
    text = "".join(
        page.extract_text() or "" for page in PdfReader(path).pages
    )

    assert "日本語" in text
    assert "Ελληνικά" in text
    assert "[rocket]" in text
    assert "\x00" not in text
    # Arabic is reshaped into presentation forms; extraction is visual order.
    assert any(0xFE70 <= ord(ch) <= 0xFEFF for ch in text)

    fonts = _pdf_script_fonts()
    markup = _pdf_rich_text("日本語 🚀", fonts)
    if fonts.get("cjk"):
        assert '<font name="WorkSenseCJK">日本語</font>' in markup
    assert "[rocket]" in markup


def test_empty_report_renders_in_every_format(tmp_path: Path):
    meta = ReportMeta(
        title="WorkSense AI - Empty Report",
        workspace="company",
        workspace_label="Company Work",
        period="daily",
        start="2026-05-10T00:00:00",
        end="2026-05-10T23:59:59",
        generated_at="2026-05-10T23:59:59",
    )
    report = ReportData(meta=meta)
    assert write_pdf(report, str(tmp_path)).endswith(".pdf")
    assert write_docx(report, str(tmp_path)).endswith(".docx")
    assert write_markdown(report, str(tmp_path)).endswith(".md")
    assert "WorkSense AI" in write_text_summary(report)
