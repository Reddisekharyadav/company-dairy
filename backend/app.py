"""FastAPI application for WorkSense AI dashboard."""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from database.session import init_db, SessionLocal
from tracker.tracker import ActivityTracker
from tracker.git_watcher import GitWatcher
from ocr.screen_capture import ScreenCaptureWorker, is_consent_granted, grant_consent, revoke_consent
import os
import sys
from backend import templates
from datetime import datetime, timedelta
from reports.generator import generate_markdown, generate_pdf, generate_docx, summarize_events
from reports.emailer import send_report as email_report, EmailNotConfiguredError
from config.settings import settings

app = FastAPI(title="WorkSense AI")

tracker = ActivityTracker(interval=5.0)
git_watcher = GitWatcher(interval=30)
screen_worker = ScreenCaptureWorker(interval=30)


@app.on_event("startup")
def startup():
    init_db()
    tracker.start()
    git_watcher.start()
    screen_worker.start()  # Only activates if consent is granted


@app.on_event("shutdown")
def shutdown():
    tracker.stop()
    git_watcher.stop()
    screen_worker.stop()


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return templates.render_index()


# ─── Status & Consent ─────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    return JSONResponse({
        "tracking": True,
        "interval": tracker.interval,
        "screen_capture": is_consent_granted(),
    })


@app.post("/api/consent/grant")
def consent_grant():
    grant_consent()
    screen_worker.start()
    return JSONResponse({"status": "granted", "message": "Screen capture enabled. Screenshots will be taken every 30 seconds."})


@app.post("/api/consent/revoke")
def consent_revoke():
    revoke_consent()
    screen_worker.stop()
    return JSONResponse({"status": "revoked", "message": "Screen capture disabled."})


@app.get("/api/consent/status")
def consent_status():
    return JSONResponse({"screen_capture_enabled": is_consent_granted()})


# ─── Events & Activity ────────────────────────────────────────────────────────

@app.get('/api/events')
def api_events(limit: int = 50):
    """Return the last N tracked activity events."""
    session = SessionLocal()
    try:
        from database.models import Event
        events = session.query(Event).order_by(Event.timestamp.desc()).limit(limit).all()
        return JSONResponse([{
            "id": e.id,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "application": e.application,
            "window_title": e.window_title,
            "process_name": e.process_name,
            "project": e.project,
            "opened_file": e.opened_file,
            "language": e.language,
            "category": e.category,
            "website": e.website,
            "cpu": e.cpu,
            "idle": e.idle,
            "duration_sec": e.duration,
        } for e in events])
    finally:
        session.close()


@app.get('/api/categories')
def api_categories(period: str = 'daily'):
    """Return time spent per activity category."""
    session = SessionLocal()
    try:
        from database.models import Event
        now = datetime.now()
        start = _period_start(period, now)
        events = session.query(Event).filter(
            Event.timestamp >= start, Event.timestamp <= now, Event.idle == False
        ).all()
        cat_map = {}
        for e in events:
            cat = e.category or 'Other'
            cat_map.setdefault(cat, 0)
            cat_map[cat] += e.duration or 0
        return JSONResponse([
            {"category": k, "seconds": v, "hours": round(v / 3600, 3)}
            for k, v in sorted(cat_map.items(), key=lambda x: -x[1])
        ])
    finally:
        session.close()


@app.get('/api/websites')
def api_websites(period: str = 'daily'):
    """Return websites/apps visited with time spent."""
    session = SessionLocal()
    try:
        from database.models import Event
        now = datetime.now()
        start = _period_start(period, now)
        events = session.query(Event).filter(
            Event.timestamp >= start,
            Event.timestamp <= now,
            Event.website != None,
            Event.idle == False,
        ).all()
        web_map = {}
        for e in events:
            if e.website:
                web_map.setdefault(e.website, {"seconds": 0, "category": e.category or "Other"})
                web_map[e.website]["seconds"] += e.duration or 0
        return JSONResponse([
            {"site": k, "minutes": round(v["seconds"] / 60, 1), "category": v["category"]}
            for k, v in sorted(web_map.items(), key=lambda x: -x[1]["seconds"])
        ])
    finally:
        session.close()


@app.get('/api/timeline')
def api_timeline(period: str = 'daily'):
    """Return activity bucketed by hour for chart display."""
    session = SessionLocal()
    try:
        from database.models import Event
        now = datetime.now()
        start = _period_start(period, now)
        events = session.query(Event).filter(
            Event.timestamp >= start, Event.timestamp <= now
        ).all()
        # Group by hour and category
        buckets = {}  # {hour: {category: seconds}}
        for e in events:
            if not e.timestamp:
                continue
            hour = e.timestamp.hour
            cat = e.category or 'Other'
            buckets.setdefault(hour, {})
            buckets[hour].setdefault(cat, 0)
            buckets[hour][cat] += e.duration or 0
        return JSONResponse({str(h): v for h, v in sorted(buckets.items())})
    finally:
        session.close()


@app.get('/api/screenshots')
def api_screenshots(limit: int = 12):
    """Return list of recent screenshot thumbnails."""
    session = SessionLocal()
    try:
        from database.models import OCRText
        rows = session.query(OCRText).filter(
            OCRText.screenshot_path != None
        ).order_by(OCRText.timestamp.desc()).limit(limit).all()
        return JSONResponse([{
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "source": r.source,
            "path": r.screenshot_path,
            "text_preview": (r.text or '')[:200],
        } for r in rows])
    finally:
        session.close()


@app.get('/api/screenshot/{ocr_id}')
def serve_screenshot(ocr_id: int):
    """Serve a screenshot file by OCR record ID."""
    session = SessionLocal()
    try:
        from database.models import OCRText
        row = session.query(OCRText).filter(OCRText.id == ocr_id).first()
        if row and row.screenshot_path and os.path.exists(row.screenshot_path):
            return FileResponse(row.screenshot_path, media_type='image/jpeg')
        return JSONResponse({"error": "Not found"}, status_code=404)
    finally:
        session.close()


# ─── Reports ──────────────────────────────────────────────────────────────────

@app.get('/api/generate_report')
def api_generate_report(period: str = 'daily'):
    now = datetime.now()
    start = _period_start(period, now)
    os.makedirs(settings.export_folder, exist_ok=True)
    md = generate_markdown(start, now, settings.export_folder)
    pdf = generate_pdf(start, now, settings.export_folder)
    docx = generate_docx(start, now, settings.export_folder)
    return JSONResponse({"md": md, "pdf": pdf, "docx": docx})


@app.post('/api/send_email')
def api_send_email(period: str = 'daily'):
    """Generate report and send it by email."""
    now = datetime.now()
    start = _period_start(period, now)
    os.makedirs(settings.export_folder, exist_ok=True)

    try:
        pdf = generate_pdf(start, now, settings.export_folder)
        summary = summarize_events(start, now)
        email_report(pdf, period=period, extra_body=summary)
        return JSONResponse({"status": "sent", "pdf": pdf})
    except EmailNotConfiguredError as e:
        return JSONResponse({"status": "not_configured", "detail": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


@app.get('/api/report_content')
def api_report_content(period: str = 'daily'):
    """Return the report as readable text."""
    now = datetime.now()
    start = _period_start(period, now)
    summary = summarize_events(start, now)
    return JSONResponse({
        "period": period,
        "start": start.isoformat(),
        "end": now.isoformat(),
        "report": summary,
    })


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _period_start(period: str, now: datetime) -> datetime:
    if period == 'weekly':
        return (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
