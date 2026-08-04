"""FastAPI application for WorkSense AI dashboard."""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from database.session import init_db, SessionLocal
from tracker.tracker import ActivityTracker
from tracker.git_watcher import GitWatcher
from tracker.file_watcher import FileEditWatcher
from tracker.search_extractor import SearchExtractor
from ocr.screen_capture import ScreenCaptureWorker, is_consent_granted, grant_consent, revoke_consent
import os
import sys
from backend import templates
from datetime import datetime, timedelta
from reports.generator import generate_markdown, generate_pdf, generate_docx, summarize_events
from reports.emailer import send_report as email_report, EmailNotConfiguredError
from reports.briefing import generate_morning_briefing
from config.settings import settings

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="WorkSense AI")
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

tracker       = ActivityTracker(interval=5.0)
git_watcher   = GitWatcher(interval=30)
screen_worker = ScreenCaptureWorker(interval=30)
file_watcher  = FileEditWatcher(interval=5.0)
search_ext    = SearchExtractor(interval=5.0)


@app.on_event("startup")
def startup():
    init_db()
    tracker.start()
    git_watcher.start()
    screen_worker.start()  # Only activates if consent is granted
    file_watcher.start()
    search_ext.start()


@app.on_event("shutdown")
def shutdown():
    # Save session state before shutting down
    try:
        from tracker.session_memory import save_session_state
        save_session_state()
    except Exception:
        pass
    tracker.stop()
    git_watcher.stop()
    screen_worker.stop()
    file_watcher.stop()
    search_ext.stop()


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Return 204 No Content so browsers don't log 404 for favicon."""
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        return templates.render_index()
    except Exception as exc:
        import traceback
        return HTMLResponse(
            content=f"<pre style='color:red'>Server error:\n{traceback.format_exc()}</pre>",
            status_code=500,
        )


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


# ─── Dev Files API ────────────────────────────────────────────────────────────

@app.get('/api/files/recent')
def api_recent_files(limit: int = 30, days: int = 7):
    """Return recently edited files for the Dev Files tab."""
    try:
        session = SessionLocal()
        from database.models import FileEdit
        since = datetime.now() - timedelta(days=days)
        rows = (session.query(FileEdit)
                .filter(FileEdit.timestamp >= since)
                .order_by(FileEdit.timestamp.desc())
                .limit(limit).all())
        # Deduplicate by file_name, keeping most recent
        seen = {}
        for r in rows:
            key = r.file_name or r.file_path
            if key not in seen:
                seen[key] = {
                    'file': r.file_name,
                    'path': r.file_path,
                    'project': r.project,
                    'language': r.language,
                    'duration_min': round((r.duration_sec or 0) / 60, 1),
                    'timestamp': r.timestamp.isoformat() if r.timestamp else None,
                    'date': r.session_date,
                }
        session.close()
        return JSONResponse(list(seen.values()))
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/api/files/heatmap')
def api_files_heatmap(days: int = 30):
    """Return daily edit counts for the contribution heatmap."""
    try:
        session = SessionLocal()
        from database.models import FileEdit
        from sqlalchemy import func
        since = datetime.now() - timedelta(days=days)
        rows = (session.query(FileEdit.session_date,
                              func.count(FileEdit.id).label('count'))
                .filter(FileEdit.timestamp >= since)
                .group_by(FileEdit.session_date)
                .all())
        session.close()
        return JSONResponse({r.session_date: r.count for r in rows})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


# ─── Research / Search API ────────────────────────────────────────────────────

@app.get('/api/research/queries')
def api_research_queries(limit: int = 50, days: int = 1):
    """Return recent search queries for the Research tab."""
    try:
        session = SessionLocal()
        from database.models import SearchQuery
        since = datetime.now() - timedelta(days=days)
        rows = (session.query(SearchQuery)
                .filter(SearchQuery.timestamp >= since)
                .order_by(SearchQuery.timestamp.desc())
                .limit(limit).all())
        data = [{
            'id': r.id,
            'query': r.query,
            'source': r.source,
            'timestamp': r.timestamp.isoformat() if r.timestamp else None,
            'session_id': r.research_session_id,
            'bookmarked': r.bookmarked,
        } for r in rows]
        session.close()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/api/research/sessions')
def api_research_sessions(days: int = 7):
    """Return research sessions (topic clusters) for the Research tab."""
    try:
        session = SessionLocal()
        from database.models import ResearchSession, SearchQuery
        import json
        since = datetime.now() - timedelta(days=days)
        rows = (session.query(ResearchSession)
                .filter(ResearchSession.start_time >= since)
                .order_by(ResearchSession.start_time.desc())
                .limit(50).all())
        result = []
        for r in rows:
            samples = (session.query(SearchQuery)
                       .filter(SearchQuery.research_session_id == r.id)
                       .order_by(SearchQuery.timestamp)
                       .limit(6).all())
            topic = r.topic_label or (samples[0].query[:40] if samples else 'Unknown')
            result.append({
                'id': r.id,
                'topic': topic,
                'date': r.date,
                'start': r.start_time.isoformat() if r.start_time else None,
                'end': r.end_time.isoformat() if r.end_time else None,
                'query_count': r.query_count,
                'duration_min': round((r.total_duration_sec or 0) / 60, 1),
                'sources': json.loads(r.sources) if r.sources else [],
                'sample_queries': [s.query for s in samples],
            })
        session.close()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.post('/api/research/bookmark/{query_id}')
def api_bookmark_query(query_id: int):
    """Toggle bookmark on a search query."""
    try:
        session = SessionLocal()
        from database.models import SearchQuery
        row = session.query(SearchQuery).filter(SearchQuery.id == query_id).first()
        if not row:
            session.close()
            return JSONResponse({'error': 'Not found'}, status_code=404)
        row.bookmarked = not row.bookmarked
        session.commit()
        bookmarked = row.bookmarked
        session.close()
        return JSONResponse({'bookmarked': bookmarked})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/api/research/bookmarks')
def api_research_bookmarks():
    """Return all bookmarked search queries."""
    try:
        session = SessionLocal()
        from database.models import SearchQuery
        rows = (session.query(SearchQuery)
                .filter(SearchQuery.bookmarked == True)
                .order_by(SearchQuery.timestamp.desc())
                .limit(100).all())
        data = [{
            'id': r.id,
            'query': r.query,
            'source': r.source,
            'timestamp': r.timestamp.isoformat() if r.timestamp else None,
        } for r in rows]
        session.close()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


# ─── AI Context Export ────────────────────────────────────────────────────────

@app.get('/api/ai_context')
def api_ai_context():
    """Generate and return the AI context export paths + content."""
    try:
        from tracker.session_memory import generate_ai_context, CONTEXT_MD
        md_path, json_path = generate_ai_context()
        content = CONTEXT_MD.read_text(encoding='utf-8') if CONTEXT_MD.exists() else ''
        return JSONResponse({
            'md_path': md_path,
            'json_path': json_path,
            'content': content,
            'generated_at': datetime.now().isoformat(),
        })
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/api/ai_context/download')
def api_ai_context_download():
    """Download the context.md file directly."""
    try:
        from tracker.session_memory import CONTEXT_MD, save_session_state
        if not CONTEXT_MD.exists():
            save_session_state()
        return FileResponse(
            str(CONTEXT_MD),
            media_type='text/markdown',
            filename='worksense_context.md',
        )
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


# ─── Briefing & Session Memory ────────────────────────────────────────────────

@app.get('/api/briefing')
def api_briefing():
    """Return the morning briefing for the Company Worker mode."""
    try:
        briefing = generate_morning_briefing()
        return JSONResponse(briefing)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/api/session_context')
def api_session_context():
    """Return the last session snapshot for the Handoff Card."""
    try:
        from tracker.session_memory import load_last_session
        snap = load_last_session()
        return JSONResponse(snap or {})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.post('/api/save_session')
def api_save_session():
    """Manually trigger session state save (also exports AI context files)."""
    try:
        from tracker.session_memory import save_session_state, CONTEXT_MD, CONTEXT_JSON
        snap = save_session_state()
        return JSONResponse({
            'status': 'saved',
            'context_md': str(CONTEXT_MD),
            'context_json': str(CONTEXT_JSON),
            'snapshot_date': snap.get('snapshot_date'),
        })
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/api/projects/summary')
def api_projects_summary(days: int = 7):
    """Return time spent per project for the company/dev view."""
    try:
        session = SessionLocal()
        from database.models import Event, FileEdit
        since = datetime.now() - timedelta(days=days)
        events = (session.query(Event)
                  .filter(Event.timestamp >= since, Event.idle == False)
                  .all())
        proj_map: dict = {}
        for e in events:
            p = e.project or 'Unknown'
            if p not in proj_map:
                proj_map[p] = {'seconds': 0, 'languages': set(), 'files': set()}
            proj_map[p]['seconds'] += e.duration or 0
            if e.language:
                proj_map[p]['languages'].add(e.language)
            if e.opened_file:
                proj_map[p]['files'].add(e.opened_file)
        session.close()
        result = [{
            'project': k,
            'hours': round(v['seconds'] / 3600, 2),
            'languages': list(v['languages']),
            'file_count': len(v['files']),
        } for k, v in sorted(proj_map.items(), key=lambda x: -x[1]['seconds'])]
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
