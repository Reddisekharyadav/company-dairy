import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import pytest

from database.session import init_db, SessionLocal
from database.models import Event, GitActivity, OCRText
from tracker.tracker import ActivityTracker
from tracker.categorizer import categorize_activity, extract_website_name
from tracker.git_watcher import GitWatcher
from ocr.screen_capture import is_consent_granted, grant_consent, revoke_consent
from reports.generator import generate_markdown, generate_pdf, generate_docx, summarize_events
from main import _resolve_output_dir
from backend import app as backend_app


@pytest.fixture(autouse=True)
def setup_database():
    """Ensure database schema is created before tests."""
    init_db()


def test_categorize_activity_and_website_extraction():
    # Test coding category
    cat = categorize_activity("code.exe", "main.py - companydairy - Visual Studio Code")
    assert cat == "Coding"

    # Test website extraction and web coding category
    site = extract_website_name("GitHub - myrepo - Google Chrome", "chrome.exe")
    assert site == "GitHub"
    cat_web = categorize_activity("chrome.exe", "GitHub - myrepo - Google Chrome")
    assert cat_web == "Coding/Research"

    # Test communication
    site_wa = extract_website_name("WhatsApp Web - Google Chrome", "chrome.exe")
    assert site_wa == "WhatsApp"
    cat_wa = categorize_activity("chrome.exe", "WhatsApp Web - Google Chrome")
    assert cat_wa == "Communication"


def test_tracker_saving_and_database_persistence():
    session = SessionLocal()
    initial_count = session.query(Event).count()

    # Manually log an event to verify schema and saving
    ev = Event(
        timestamp=datetime.now(),
        duration=5.0,
        application="code.exe",
        window_title="main.py - TestProject - Visual Studio Code",
        process_name="code.exe",
        project="TestProject",
        opened_file="main.py",
        language="Python",
        cpu=12.5,
        idle=False,
        category="Coding",
        website=None,
    )
    session.add(ev)
    session.commit()

    new_count = session.query(Event).count()
    assert new_count == initial_count + 1

    last_event = session.query(Event).order_by(Event.id.desc()).first()
    assert last_event.application == "code.exe"
    assert last_event.category == "Coding"
    assert last_event.opened_file == "main.py"
    session.close()


def test_git_watcher_discovery_and_saving():
    watcher = GitWatcher(interval=30)
    repos = watcher._discover_repos()
    assert isinstance(repos, list)
    # Since current directory is a git repo (or contains .git), it should discover it
    assert len(repos) >= 0


def test_consent_grant_and_revoke():
    grant_consent()
    assert is_consent_granted() is True

    revoke_consent()
    assert is_consent_granted() is False


def test_report_generation(tmp_path):
    out_dir = str(tmp_path)
    now = datetime.now()
    start = now - timedelta(days=1)

    md_file = generate_markdown(start, now, out_dir)
    pdf_file = generate_pdf(start, now, out_dir)
    docx_file = generate_docx(start, now, out_dir)

    assert os.path.exists(md_file)
    assert os.path.exists(pdf_file)
    assert os.path.exists(docx_file)

    summary_text = summarize_events(start, now)
    assert "WorkSense AI" in summary_text or "Report:" in summary_text


def test_desktop_output_directory_resolution():
    desktop = _resolve_output_dir()
    assert isinstance(desktop, Path)
    assert desktop.exists()


def test_fastapi_backend_endpoints_directly():
    # 1. Index route
    html_res = backend_app.index()
    assert "WorkSense AI" in html_res

    # 2. Status route
    status_res = backend_app.status()
    body_status = json.loads(status_res.body)
    assert body_status["tracking"] is True

    # 3. Events route
    events_res = backend_app.api_events(limit=10)
    body_events = json.loads(events_res.body)
    assert isinstance(body_events, list)

    # 4. Categories route
    cats_res = backend_app.api_categories(period="daily")
    body_cats = json.loads(cats_res.body)
    assert isinstance(body_cats, list)

    # 5. Websites route
    sites_res = backend_app.api_websites(period="daily")
    body_sites = json.loads(sites_res.body)
    assert isinstance(body_sites, list)

    # 6. Timeline route
    timeline_res = backend_app.api_timeline(period="daily")
    body_timeline = json.loads(timeline_res.body)
    assert isinstance(body_timeline, dict)

    # 7. Consent endpoints
    grant_res = backend_app.consent_grant()
    assert json.loads(grant_res.body)["status"] == "granted"

    status_consent = backend_app.consent_status()
    assert json.loads(status_consent.body)["screen_capture_enabled"] is True

    revoke_res = backend_app.consent_revoke()
    assert json.loads(revoke_res.body)["status"] == "revoked"

    # 8. Report endpoints
    report_content_res = backend_app.api_report_content(period="daily")
    assert "report" in json.loads(report_content_res.body)

    gen_report_res = backend_app.api_generate_report(period="daily")
    assert "pdf" in json.loads(gen_report_res.body)

    # 9. Screenshots endpoint
    screenshots_res = backend_app.api_screenshots(limit=5)
    assert isinstance(json.loads(screenshots_res.body), list)
