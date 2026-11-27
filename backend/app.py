"""FastAPI application for WorkSense AI dashboard."""
import logging
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from database.session import init_db, SessionLocal
from tracker.tracker import ActivityTracker
from tracker.git_watcher import GitWatcher
from tracker.file_watcher import FileEditWatcher
from tracker.fs_watcher import FileSystemWatcher
from tracker.active_window import get_active_window
from tracker.search_extractor import SearchExtractor
from tracker.browser_history import BrowserHistoryTracker
from tracker.meeting_tracker import MeetingTracker
from tracker.smart_summarizer import SmartSummarizer
from ocr.screen_capture import (
    SCREENSHOT_DIR,
    ScreenCaptureWorker,
    grant_consent,
    is_consent_granted,
    revoke_consent,
)
from backend import templates
from reports.generator import generate_markdown, generate_pdf, generate_docx, summarize_events
from reports.model import build_report
from reports.emailer import send_report as email_report, EmailNotConfiguredError
from reports.briefing import generate_morning_briefing
from config.settings import SESSION_ID, settings
from config.workspace_rules import (
    COMPANY,
    COMBINED,
    UNCLASSIFIED,
    VALID_SCOPES,
    WORKSPACES,
    classifier,
    normalize_scope,
    normalize_workspace,
    scope_workspaces,
)
from services.activity_service import ActivityService, resolve_date_range

from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("backend")


def _safe_json(value, fallback):
    """Parse a JSON text column without letting one malformed legacy row
    break an entire dashboard endpoint."""
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


# Interactive API docs are useful in development but expose schema and debug
# tooling.  Keep them opt-in for local development.
_ENABLE_DOCS = os.environ.get("WS_ENABLE_DOCS") == "1"
app = FastAPI(
    title="WorkSense AI",
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
)

# Local-only origins.  Credentials are enabled, so a wildcard origin is not
# permitted.  The Capacitor/Android shells use a localhost-style origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
        "http://[::1]",
        "https://[::1]",
        "capacitor://localhost",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "testserver"}


def _hostname(value: str) -> Optional[str]:
    """Extract a hostname from a Host/Origin/Referer value."""
    if not value:
        return None
    try:
        return urlsplit(f"//{value}" if "://" not in value else value).hostname
    except Exception:
        return None


def _is_local_origin(value: str) -> bool:
    if not value:
        return False
    if value == "capacitor://localhost":
        return True
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    return (
        parsed.scheme in ("http", "https")
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )


@app.middleware("http")
async def local_access_guard(request: Request, call_next):
    """Protect the local API from DNS-rebinding and cross-site requests.

    The desktop app is bound to 127.0.0.1, but a web page in the user's browser
    can still issue requests to localhost.  Host validation defeats DNS
    rebinding; Origin/Referer/Sec-Fetch-Site checks stop cross-site requests,
    including state-changing GET endpoints triggered by an <img> tag.
    """
    hostname = _hostname(request.headers.get("host", ""))
    if request.headers.get("host") and hostname not in _LOCAL_HOSTNAMES:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    origin = request.headers.get("origin")
    if origin and not _is_local_origin(origin):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    referer = request.headers.get("referer")
    if not origin and referer and not _is_local_origin(referer):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    return await call_next(request)


def _api_error(exc: Exception, status_code: int = 500, **extra):
    """Return a generic client error while keeping diagnostics local.

    Exception text can contain filesystem paths, SQL fragments or window
    titles.  It is logged only at debug level and never sent to the browser.
    """
    log.error("API request failed (%s, status=%s)", type(exc).__name__, status_code)
    log.debug("API failure details", exc_info=True)
    message = (
        "Invalid request."
        if 400 <= status_code < 500
        else "Internal error. Details are in the local WorkSense log."
    )
    payload = {"error": message}
    payload.update(extra)
    return JSONResponse(payload, status_code=status_code)


def _screenshot_roots() -> list[Path]:
    return [
        Path(SCREENSHOT_DIR),
        Path.cwd() / "screenshots",
    ]


def _safe_screenshot_file(raw_path: str) -> Optional[Path]:
    """Resolve a stored screenshot path and require it to be inside a known root."""
    if not raw_path:
        return None
    try:
        candidate = Path(raw_path).expanduser().resolve(strict=True)
        if not candidate.is_file():
            return None
        for root in _screenshot_roots():
            try:
                root = root.expanduser().resolve()
            except Exception:
                continue
            if candidate.is_relative_to(root):
                return candidate
    except (OSError, ValueError):
        return None
    return None


static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir) and hasattr(sys, '_MEIPASS'):
    static_dir = os.path.join(sys._MEIPASS, "backend", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

tracker       = ActivityTracker(interval=5.0)
git_watcher   = GitWatcher(interval=30)
screen_worker = ScreenCaptureWorker(interval=30)
file_watcher  = FileEditWatcher(interval=5.0)
fs_watcher    = FileSystemWatcher(
    debounce_seconds=getattr(settings, "watcher_debounce", 1.5),
    startup_grace=getattr(settings, "watcher_startup_grace", 1.0),
    active_window_provider=get_active_window,
)
search_ext    = SearchExtractor(interval=5.0)
browser_hist  = BrowserHistoryTracker(interval=300.0)  # Read browser history every 5 min
meeting_trk   = MeetingTracker(interval=10.0)
smart_sum     = SmartSummarizer(interval=30.0, summary_interval=300.0)


def _start_component(name: str, starter) -> None:
    """Start one background component without taking the dashboard down.

    A tracker can fail for host-specific reasons (no desktop session, no
    xdotool, read-only home).  The web server must still come up so the user
    can see what is available instead of the process exiting silently.
    """
    try:
        starter()
    except Exception as exc:
        log.warning("%s did not start: %s", name, exc)


@app.on_event("startup")
def startup():
    init_db()
    # The filesystem watcher is idempotent; starting twice is safe.
    if getattr(settings, "watcher_enabled", True):
        _start_component("Filesystem watcher", fs_watcher.start)
    _start_component("Activity tracker", tracker.start)
    _start_component("Git watcher", git_watcher.start)
    # Only activates if consent is granted
    _start_component("Screen capture worker", screen_worker.start)
    _start_component("File edit watcher", file_watcher.start)
    _start_component("Search extractor", search_ext.start)
    _start_component("Browser history tracker", browser_hist.start)
    _start_component("Meeting tracker", meeting_trk.start)
    _start_component("Smart summarizer", smart_sum.start)


@app.on_event("shutdown")
def shutdown():
    # Save session state before shutting down
    try:
        from tracker.session_memory import save_session_state
        save_session_state()
    except Exception:
        pass
    for name, component in (
        ("Activity tracker", tracker),
        ("Git watcher", git_watcher),
        ("Screen capture worker", screen_worker),
        ("File edit watcher", file_watcher),
        ("Filesystem watcher", fs_watcher),
        ("Search extractor", search_ext),
        ("Browser history tracker", browser_hist),
        ("Meeting tracker", meeting_trk),
        ("Smart summarizer", smart_sum),
    ):
        try:
            component.stop()
        except Exception as exc:
            log.warning("%s did not stop cleanly: %s", name, exc)


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
        log.error("Dashboard render failed (%s)", type(exc).__name__)
        log.debug("Dashboard render details", exc_info=True)
        return HTMLResponse(
            content=(
                "<h1>WorkSense AI</h1>"
                "<p>The local dashboard could not be rendered.</p>"
                "<p>Check the WorkSense log in %APPDATA%/WorkSense for details.</p>"
            ),
            status_code=500,
        )

@app.get("/api/live_status")
def get_live_status():
    from database.session import SessionLocal
    db = SessionLocal()
    try:
        from database.models import Event
        ev = db.query(Event).order_by(Event.timestamp.desc()).first()
        from tracker.input_tracker import input_tracker
        state = input_tracker.get_current_state()
        if ev:
            return {
                "app": ev.application or "",
                "title": ev.window_title or "",
                "state": state,
                "idle": ev.idle
            }
        return {"state": state, "app": "", "title": "", "idle": False}
    finally:
        if db:
            db.close()



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


# ─── Shared workspace/date filters ────────────────────────────────────────────

def _filters(workspace: Optional[str] = None, period: str = "daily",
             start: Optional[str] = None, end: Optional[str] = None,
             days: Optional[int] = None):
    """Validate a workspace scope and resolve a local date range."""
    scope = normalize_scope(workspace or getattr(settings, "default_workspace", COMPANY), COMPANY)
    date_range = resolve_date_range(period=period, start=start, end=end, days=days)
    return scope, date_range


def _service() -> ActivityService:
    return ActivityService()


def _scope_payload(scope: str) -> dict:
    return {
        "workspace": scope,
        "workspaces": scope_workspaces(scope),
        "default": getattr(settings, "default_workspace", COMPANY),
    }


def _iso(value):
    return value.isoformat() if value else None


# ─── Workspace & watcher APIs ─────────────────────────────────────────────────

@app.get("/api/workspaces")
def api_workspaces():
    """Return canonical workspaces, valid scopes, roots and classification rules."""
    return JSONResponse({
        "workspaces": list(WORKSPACES),
        "scopes": list(VALID_SCOPES),
        "default_scope": getattr(settings, "default_workspace", COMPANY),
        "project_roots": list(settings.project_roots),
        "rules": classifier.get_rules(),
        "watcher": fs_watcher.get_status(),
    })


@app.get("/api/workspace/rules")
def api_workspace_rules():
    return JSONResponse(classifier.get_rules())


@app.post("/api/workspace/rules")
async def api_update_workspace_rules(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return JSONResponse({"error": "A JSON object is required"}, status_code=400)
        updated = classifier.update_rules(data)
        return JSONResponse({"status": "updated", "rules": updated})
    except Exception as exc:
        return _api_error(exc, 400)


@app.post("/api/workspace/override")
async def api_workspace_override(request: Request):
    try:
        data = await request.json()
        path = str(data.get("path") or "").strip()
        workspace = normalize_workspace(data.get("workspace"), UNCLASSIFIED)
        if not path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        classifier.set_path_override(path, workspace)
        return JSONResponse({"status": "saved", "path": path, "workspace": workspace})
    except Exception as exc:
        return _api_error(exc, 400)


@app.delete("/api/workspace/override")
async def api_remove_workspace_override(request: Request):
    try:
        data = await request.json()
        classifier.remove_path_override(str(data.get("path") or ""))
        return JSONResponse({"status": "removed"})
    except Exception as exc:
        return _api_error(exc, 400)


@app.get("/api/watcher/status")
def api_watcher_status():
    status = fs_watcher.get_status()
    status["duration_tracker"] = file_watcher.get_status()
    return JSONResponse(status)


@app.post("/api/watcher/start")
def api_watcher_start():
    started = fs_watcher.start()
    if not started:
        return JSONResponse({"status": "error", "detail": fs_watcher.get_status()}, status_code=400)
    settings.watcher_enabled = True
    settings.save()
    return JSONResponse({"status": "started", "watcher": fs_watcher.get_status()})


@app.post("/api/watcher/stop")
def api_watcher_stop():
    fs_watcher.stop()
    settings.watcher_enabled = False
    settings.save()
    return JSONResponse({"status": "stopped", "watcher": fs_watcher.get_status()})


@app.post("/api/watcher/roots")
async def api_watcher_add_root(request: Request):
    try:
        data = await request.json()
        path = str(data.get("path") or "").strip()
        workspace = normalize_workspace(data.get("workspace"), "development")
        if not path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        fs_watcher.add_root(path, workspace)
        roots = [r["path"] for r in fs_watcher.get_roots() if r.get("workspace") == "development"]
        settings.set_project_roots(roots)
        return JSONResponse({"status": "added", "roots": fs_watcher.get_roots()})
    except Exception as exc:
        return _api_error(exc, 400)


@app.delete("/api/watcher/roots")
async def api_watcher_remove_root(request: Request):
    try:
        data = await request.json()
        path = str(data.get("path") or "").strip()
        if not path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        fs_watcher.remove_root(path)
        roots = [r["path"] for r in fs_watcher.get_roots() if r.get("workspace") == "development"]
        settings.set_project_roots(roots)
        return JSONResponse({"status": "removed", "roots": fs_watcher.get_roots()})
    except Exception as exc:
        return _api_error(exc, 400)


@app.get("/api/file_changes")
def api_file_changes(workspace: str = None, period: str = "daily",
                     start: str = None, end: str = None,
                     limit: int = 200, offset: int = 0,
                     project: str = None, event_type: str = None,
                     search: str = None, order_by: str = "timestamp"):
    try:
        scope, date_range = _filters(workspace, period, start, end)
        rows = _service().file_changes(
            scope, date_range, limit=limit, offset=offset,
            project=project, event_type=event_type, search=search, order_by=order_by,
        )
        return JSONResponse({
            **_scope_payload(scope),
            "period": date_range.as_dict(),
            "changes": [{
                "id": r.id,
                "timestamp": _iso(r.timestamp),
                "event_type": r.event_type,
                "source": r.source,
                "attribution": r.attribution,
                "application": r.application,
                "project": r.project,
                "project_root": r.project_root,
                "file_name": r.file_name,
                "file_path": r.file_path,
                "relative_path": r.relative_path,
                "language": r.language,
                "file_size": r.file_size,
                "workspace": r.workspace,
            } for r in rows],
        })
    except Exception as exc:
        return _api_error(exc, 500)


@app.get("/api/file_changes/projects")
def api_file_change_projects(workspace: str = None, period: str = "daily",
                             start: str = None, end: str = None):
    try:
        scope, date_range = _filters(workspace, period, start, end)
        return JSONResponse({
            **_scope_payload(scope),
            "period": date_range.as_dict(),
            "projects": _service().project_totals(scope, date_range),
            "languages": _service().language_totals(scope, date_range),
            "git": [{
                "repo": r.repo, "hash": r.commit_hash,
                "message": (r.message or "").split("\n")[0],
                "author": r.author, "branch": r.branch,
                "timestamp": _iso(r.timestamp),
                "workspace": r.workspace,
            } for r in _service().git_commits(scope, date_range, limit=50)],
        })
    except Exception as exc:
        return _api_error(exc, 500)


@app.get("/api/summary")
def api_summary(workspace: str = None, period: str = "daily",
                start: str = None, end: str = None):
    scope, date_range = _filters(workspace, period, start, end)
    return JSONResponse({**_scope_payload(scope), **build_report(
        scope, start=date_range.start.isoformat(), end=date_range.end.isoformat()
    ).summary})


@app.get("/api/settings")
def api_get_settings():
    return JSONResponse({
        "default_workspace": getattr(settings, "default_workspace", COMPANY),
        "retention_days": getattr(settings, "retention_days", 30),
        "max_storage_mb": getattr(settings, "max_storage_mb", 2048),
        "watcher_enabled": getattr(settings, "watcher_enabled", True),
        "watcher_debounce": getattr(settings, "watcher_debounce", 1.5),
        "watcher_startup_grace": getattr(settings, "watcher_startup_grace", 1.0),
        "project_roots": list(settings.project_roots),
        "export_folder": settings.export_folder,
        "interval": settings.interval,
        "ocr_enabled": settings.ocr_enabled,
        "idle_timeout": settings.idle_timeout,
        "email_to": settings.email_to,
        "email_user": settings.email_user,
        "email_smtp": settings.email_smtp,
        "email_port": settings.email_port,
        "ai_configured": bool(settings.ai_api_key),
        "screen_capture": is_consent_granted(),
        "workspaces": list(WORKSPACES),
        "scopes": list(VALID_SCOPES),
    })


@app.post("/api/settings")
async def api_save_settings(request: Request):
    try:
        data = await request.json()
        for key in ("email_to", "email_from", "email_smtp", "email_user", "email_pass",
                    "ai_api_key", "export_folder", "default_workspace"):
            if key in data and data[key] is not None:
                setattr(settings, key, str(data[key]))
        if "email_port" in data:
            settings.email_port = int(data["email_port"])
        if "retention_days" in data:
            settings.retention_days = max(1, min(int(data["retention_days"]), 3650))
        if "max_storage_mb" in data:
            settings.max_storage_mb = max(64, int(data["max_storage_mb"]))
        if "idle_timeout" in data:
            settings.idle_timeout = max(30, int(data["idle_timeout"]))
        if "interval" in data:
            settings.interval = max(1.0, float(data["interval"]))
        if "watcher_enabled" in data:
            settings.watcher_enabled = bool(data["watcher_enabled"])
        if "watcher_debounce" in data:
            settings.watcher_debounce = max(0.1, float(data["watcher_debounce"]))
        if "default_workspace" in data:
            settings.default_workspace = normalize_scope(data["default_workspace"], COMPANY)
        if "project_roots" in data and isinstance(data["project_roots"], list):
            settings.set_project_roots(data["project_roots"])
            fs_watcher.set_roots([{"path": root, "workspace": "development"} for root in settings.project_roots])
        settings.save()
        return JSONResponse({"status": "saved", **_scope_payload(settings.default_workspace)})
    except Exception as exc:
        return _api_error(exc, 400)


# ─── Events & Activity ────────────────────────────────────────────────────────

@app.get('/api/events')
def api_events(limit: int = 50, workspace: str = None, period: str = "daily",
               start: str = None, end: str = None, offset: int = 0):
    """Return workspace-filtered activity events."""
    scope, date_range = _filters(workspace, period, start, end)
    rows = _service().events(scope, date_range, limit=limit, offset=offset)
    return JSONResponse([{
        "id": e.id,
        "timestamp": _iso(e.timestamp),
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
        "workspace": e.workspace,
        "duration_sec": e.duration,
    } for e in rows])


@app.get('/api/categories')
def api_categories(period: str = 'daily', workspace: str = None,
                   start: str = None, end: str = None):
    """Return time spent per activity category."""
    scope, date_range = _filters(workspace, period, start, end)
    return JSONResponse(_service().category_totals(scope, date_range))


@app.get('/api/insights')
def api_insights(days: int = 1, workspace: str = None):
    """Return AI insights for the given period."""
    scope, date_range = _filters(workspace, period="custom", days=days)
    rows = _service().insights(scope, date_range)
    return JSONResponse([{
        "id": r.id,
        "timestamp": _iso(r.timestamp),
        "app": r.app,
        "title": r.window_title,
        "insight_text": r.summary,
        "topic": r.topic_keywords,
        "duration_sec": r.duration_on_tab,
        "engagement_type": getattr(r, 'engagement_type', None),
        "ocr_summary": getattr(r, 'ocr_summary', None),
        "workspace": r.workspace,
    } for r in rows])


@app.get('/api/search/memory')
def api_search_memory(query: str, days: int = 7, limit: int = 50, workspace: str = None):
    """Search OCR memory for text matches, filtered by workspace."""
    if not query or len(query.strip()) < 3:
        return JSONResponse([])

    scope, date_range = _filters(workspace, period="custom", days=days)
    values = scope_workspaces(scope)
    session = SessionLocal()
    try:
        from database.models import ScreenFrame, OCRText
        start = date_range.start
        q = f"%{query.strip()}%"

        # 1. Search ScreenFrame (Recall frames)
        frames = session.query(ScreenFrame).filter(
            ScreenFrame.timestamp >= start,
            ScreenFrame.workspace.in_(values),
            (ScreenFrame.ocr_text.ilike(q) | ScreenFrame.window_title.ilike(q) | ScreenFrame.analysis_summary.ilike(q))
        ).order_by(ScreenFrame.timestamp.desc()).limit(limit).all()

        results = []
        for f in frames:
            results.append({
                "id": f.id,
                "timestamp": _iso(f.timestamp),
                "source": f.process_name or f.window_title or 'Screen capture',
                "text": f.ocr_text,
                "screenshot_path": f.screenshot_path,
                "summary": f.analysis_summary,
                "category": f.detected_category or 'unknown',
                "workspace": f.workspace,
            })

        if results:
            return JSONResponse(results)

        # 2. Fallback to legacy OCRText
        rows = session.query(OCRText).filter(
            OCRText.timestamp >= start,
            OCRText.workspace.in_(values),
            OCRText.text.ilike(q)
        ).order_by(OCRText.timestamp.desc()).limit(limit).all()

        for r in rows:
            results.append({
                "id": r.id,
                "timestamp": _iso(r.timestamp),
                "source": r.source,
                "text": r.text,
                "screenshot_path": r.screenshot_path,
                "workspace": r.workspace,
            })
        return JSONResponse(results)
    finally:
        session.close()


@app.get('/api/websites')
def api_websites(period: str = 'daily', workspace: str = None,
                 start: str = None, end: str = None):
    """Return websites/apps visited with time spent."""
    scope, date_range = _filters(workspace, period, start, end)
    return JSONResponse(_service().website_totals(scope, date_range))


@app.get('/api/timeline/chart')
def api_timeline_chart(period: str = 'daily', workspace: str = None,
                       start: str = None, end: str = None):
    """Return activity bucketed by hour for chart display."""
    scope, date_range = _filters(workspace, period, start, end)
    return JSONResponse(_service().hour_buckets(scope, date_range))


@app.get('/api/screenshots')
def api_screenshots(limit: int = 12, workspace: str = None):
    """Return list of recent screenshot thumbnails for one workspace scope."""
    scope, _date_range = _filters(workspace, period="all")
    values = scope_workspaces(scope)
    session = SessionLocal()
    try:
        from database.models import ScreenFrame, OCRText
        # 1. Query ScreenFrame (Recall visual timeline frames)
        frames = session.query(ScreenFrame).filter(
            ScreenFrame.screenshot_path != None,  # noqa: E711
            ScreenFrame.workspace.in_(values),
        ).order_by(ScreenFrame.timestamp.desc()).limit(limit).all()

        if frames:
            return JSONResponse([{
                "id": f.id,
                "timestamp": _iso(f.timestamp),
                "source": f.process_name or f.window_title or 'Screen capture',
                "path": f.screenshot_path,
                "text_preview": (f.ocr_text or '')[:200],
                "summary": f.analysis_summary,
                "category": f.detected_category or 'unknown',
                "workspace": f.workspace,
            } for f in frames])

        # 2. Fallback to legacy OCRText
        rows = session.query(OCRText).filter(
            OCRText.screenshot_path != None,  # noqa: E711
            OCRText.workspace.in_(values),
        ).order_by(OCRText.timestamp.desc()).limit(limit).all()
        return JSONResponse([{
            "id": r.id,
            "timestamp": _iso(r.timestamp),
            "source": r.source,
            "path": r.screenshot_path,
            "text_preview": (r.text or '')[:200],
            "workspace": r.workspace,
        } for r in rows])
    finally:
        session.close()


@app.get('/api/screenshot/{item_id}')
def serve_screenshot(item_id: int):
    """Serve a screenshot file by ScreenFrame ID or legacy OCRText ID.

    Only files inside the configured WorkSense screenshot directories are
    served.  This prevents a corrupted/imported database row from turning the
    endpoint into an arbitrary-file reader.
    """
    session = SessionLocal()
    try:
        from database.models import ScreenFrame, OCRText
        # 1. Check ScreenFrame (Visual Timeline / Microsoft Recall frames)
        frame = session.query(ScreenFrame).filter(ScreenFrame.id == item_id).first()
        path = _safe_screenshot_file(frame.screenshot_path) if frame else None
        if path:
            media_type = 'image/webp' if path.suffix.lower() == '.webp' else 'image/jpeg'
            return FileResponse(str(path), media_type=media_type)

        # 2. Check legacy OCRText
        row = session.query(OCRText).filter(OCRText.id == item_id).first()
        path = _safe_screenshot_file(row.screenshot_path) if row else None
        if path:
            media_type = 'image/webp' if path.suffix.lower() == '.webp' else 'image/jpeg'
            return FileResponse(str(path), media_type=media_type)

        return JSONResponse({"error": "Screenshot not found"}, status_code=404)
    finally:
        session.close()


# ─── Reports ──────────────────────────────────────────────────────────────────

@app.get('/api/generate_report')
def api_generate_report(period: str = 'daily', workspace: str = None,
                        start: str = None, end: str = None,
                        formats: str = "md,pdf,docx"):
    """Generate report files for an explicit workspace scope."""
    scope, date_range = _filters(workspace, period, start, end)
    if scope == COMBINED:
        pass  # explicit request only
    os.makedirs(settings.export_folder, exist_ok=True)
    requested = {fmt.strip().lower() for fmt in (formats or "md,pdf,docx").split(",") if fmt.strip()}
    result = {}
    if "md" in requested:
        result["md"] = generate_markdown(date_range.start, date_range.end, settings.export_folder, workspace=scope)
    if "pdf" in requested:
        result["pdf"] = generate_pdf(date_range.start, date_range.end, settings.export_folder, workspace=scope)
    if "docx" in requested:
        result["docx"] = generate_docx(date_range.start, date_range.end, settings.export_folder, workspace=scope)
    result.update(_scope_payload(scope))
    result["period"] = date_range.as_dict()
    return JSONResponse(result)


@app.post('/api/send_email')
def api_send_email(period: str = 'daily', workspace: str = None,
                   start: str = None, end: str = None):
    """Generate a PDF report and send it by email."""
    scope, date_range = _filters(workspace, period, start, end)
    os.makedirs(settings.export_folder, exist_ok=True)

    try:
        pdf = generate_pdf(date_range.start, date_range.end, settings.export_folder, workspace=scope)
        summary = summarize_events(date_range.start, date_range.end, workspace=scope)
        email_report(pdf, period=period, extra_body=summary)
        return JSONResponse({"status": "sent", "pdf": pdf, **_scope_payload(scope)})
    except EmailNotConfiguredError as exc:
        log.warning("Email report requested without complete configuration")
        return JSONResponse(
            {"status": "not_configured",
             "detail": "Email is not configured. Set the SMTP and recipient settings."},
            status_code=400,
        )
    except Exception as exc:
        log.error("Email report failed (%s)", type(exc).__name__)
        log.debug("Email report failure details", exc_info=True)
        return JSONResponse(
            {"status": "error",
             "detail": "Email delivery failed. Details are in the local WorkSense log."},
            status_code=500,
        )


@app.get('/api/report_content')
def api_report_content(period: str = 'daily', workspace: str = None,
                       start: str = None, end: str = None):
    """Return a structured report plus its readable text."""
    scope, date_range = _filters(workspace, period, start, end)
    report = build_report(scope, start=date_range.start.isoformat(), end=date_range.end.isoformat())
    return JSONResponse({
        "period": period,
        "start": date_range.start.isoformat(),
        "end": date_range.end.isoformat(),
        **_scope_payload(scope),
        "report": summarize_events(date_range.start, date_range.end, workspace=scope),
        "structured": report.to_dict(),
    })


# ─── Excel Export ─────────────────────────────────────────────────────────────

@app.get('/api/export/excel')
def api_export_excel(period: str = 'daily', workspace: str = None,
                     start: str = None, end: str = None):
    """Generate and serve a workspace-isolated Excel workbook."""
    from reports.excel_exporter import generate_excel
    scope, date_range = _filters(workspace, period, start, end)
    os.makedirs(settings.export_folder, exist_ok=True)
    try:
        xlsx_path = generate_excel(date_range.start, date_range.end, settings.export_folder, workspace=scope)
        return FileResponse(
            xlsx_path,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            filename=os.path.basename(xlsx_path),
        )
    except Exception as exc:
        return _api_error(exc, 500)


@app.get('/api/export/excel/open')
def api_export_excel_open(period: str = 'daily', workspace: str = None,
                          start: str = None, end: str = None):
    """Generate an Excel workbook and open it with the system default app."""
    from reports.excel_exporter import generate_excel
    import platform
    import subprocess
    scope, date_range = _filters(workspace, period, start, end)
    os.makedirs(settings.export_folder, exist_ok=True)
    try:
        xlsx_path = generate_excel(date_range.start, date_range.end, settings.export_folder, workspace=scope)
        system = platform.system()
        if system == 'Windows':
            os.startfile(xlsx_path)
        elif system == 'Darwin':
            subprocess.Popen(['open', xlsx_path])
        else:
            subprocess.Popen(['xdg-open', xlsx_path])
        return JSONResponse({'status': 'opened', 'path': xlsx_path, **_scope_payload(scope)})
    except Exception as exc:
        return _api_error(exc, 500)


# ─── Research Deep Dive API ──────────────────────────────────────────────────

@app.get('/api/research/deep')
def api_research_deep(days: int = 7, workspace: str = None):
    """Return research sessions enriched with OCR context and screen notes."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        values = scope_workspaces(scope)
        session = SessionLocal()
        from database.models import ResearchSession, SearchQuery, OCRText, DailyNote, ActivityInsight
        import json

        since = date_range.start

        # Get research sessions
        sessions_list = (session.query(ResearchSession)
                    .filter(ResearchSession.start_time >= since,
                            ResearchSession.workspace.in_(values))
                    .order_by(ResearchSession.start_time.desc())
                    .limit(30).all())

        result = []
        for rs in sessions_list:
            # Get queries in this session
            queries = (session.query(SearchQuery)
                      .filter(SearchQuery.research_session_id == rs.id)
                      .order_by(SearchQuery.timestamp)
                      .limit(10).all())

            # Get OCR context around the session time
            if rs.start_time and rs.end_time:
                ocr_entries = (session.query(OCRText)
                              .filter(OCRText.timestamp >= rs.start_time,
                                      OCRText.timestamp <= rs.end_time)
                              .limit(5).all())
            elif rs.start_time:
                ocr_entries = (session.query(OCRText)
                              .filter(OCRText.timestamp >= rs.start_time,
                                      OCRText.timestamp <= rs.start_time + timedelta(minutes=30))
                              .limit(5).all())
            else:
                ocr_entries = []

            # Get auto-generated screen notes around this time
            screen_notes = []
            if rs.start_time:
                sn_start = rs.start_time - timedelta(minutes=2)
                sn_end = (rs.end_time or rs.start_time) + timedelta(minutes=5)
                screen_notes = (session.query(DailyNote)
                               .filter(DailyNote.source == 'auto_screen',
                                       DailyNote.timestamp >= sn_start,
                                       DailyNote.timestamp <= sn_end)
                               .limit(10).all())

            # Get insights for this period
            insights = []
            if rs.start_time:
                i_start = rs.start_time - timedelta(minutes=2)
                i_end = (rs.end_time or rs.start_time) + timedelta(minutes=5)
                insights = (session.query(ActivityInsight)
                           .filter(ActivityInsight.timestamp >= i_start,
                                   ActivityInsight.timestamp <= i_end)
                           .limit(5).all())

            topic = rs.topic_label or (queries[0].query[:40] if queries else 'Unknown')

            result.append({
                'id': rs.id,
                'topic': topic,
                'date': rs.date,
                'start': rs.start_time.isoformat() if rs.start_time else None,
                'end': rs.end_time.isoformat() if rs.end_time else None,
                'query_count': rs.query_count,
                'duration_min': round((rs.total_duration_sec or 0) / 60, 1),
                'sources': _safe_json(rs.sources, []),
                'queries': [{'query': q.query, 'source': q.source,
                            'time': q.timestamp.isoformat() if q.timestamp else None}
                           for q in queries],
                'screen_context': [
                    {'time': o.timestamp.isoformat() if o.timestamp else None,
                     'text_preview': (o.text or '')[:200],
                     'source': o.source}
                    for o in ocr_entries
                ],
                'screen_notes': [
                    {'time': n.timestamp.isoformat() if n.timestamp else None,
                     'note': n.content}
                    for n in screen_notes
                ],
                'insights': [
                    {'summary': i.summary, 'keywords': i.topic_keywords,
                     'duration_min': round((i.duration_on_tab or 0) / 60, 1)}
                    for i in insights
                ],
            })

        session.close()
        return JSONResponse(result)
    except Exception as e:
        return _api_error(e, 500)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _period_start(period: str, now: datetime) -> datetime:
    if period == 'weekly':
        return (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ─── Dev Files API ────────────────────────────────────────────────────────────

@app.get('/api/files/recent')
def api_recent_files(limit: int = 30, days: int = 7, workspace: str = None):
    """Return inferred file-duration rows for the Dev Files tab."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        rows = _service().file_durations(scope, date_range, limit=limit)
        return JSONResponse([{
            "file": r.file_name,
            "path": r.file_path,
            "project": r.project,
            "language": r.language,
            "duration_min": round((r.duration_sec or 0) / 60, 1),
            "timestamp": _iso(r.timestamp),
            "date": r.session_date,
            "editor": r.editor,
            "workspace": r.workspace,
            "source": r.source or "active_window_inference",
            "insight": None,
        } for r in rows])
    except Exception as exc:
        return _api_error(exc, 500)


@app.get('/api/files/heatmap')
def api_files_heatmap(days: int = 30, workspace: str = None):
    """Return daily observed file-change counts for the contribution heatmap."""
    try:
        from sqlalchemy import func
        from database.models import FileChangeEvent
        scope, date_range = _filters(workspace, period="custom", days=days)
        values = scope_workspaces(scope)
        session = SessionLocal()
        try:
            rows = (session.query(FileChangeEvent.session_date,
                                  func.count(FileChangeEvent.id).label('count'))
                    .filter(FileChangeEvent.timestamp >= date_range.start,
                            FileChangeEvent.workspace.in_(values))
                    .group_by(FileChangeEvent.session_date)
                    .all())
            return JSONResponse({r.session_date: r.count for r in rows if r.session_date})
        finally:
            session.close()
    except Exception as exc:
        return _api_error(exc, 500)


# ─── Research / Search API ────────────────────────────────────────────────────

@app.get('/api/research/queries')
def api_research_queries(limit: int = 50, days: int = 1, workspace: str = None):
    """Return recent search queries for the Research tab."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        rows = _service().search_queries(scope, date_range, limit=limit)
        return JSONResponse([{
            'id': r.id,
            'query': r.query,
            'source': r.source,
            'timestamp': _iso(r.timestamp),
            'session_id': r.research_session_id,
            'bookmarked': r.bookmarked,
            'workspace': r.workspace,
        } for r in rows])
    except Exception as exc:
        return _api_error(exc, 500)


@app.get('/api/research/sessions')
def api_research_sessions(days: int = 7, workspace: str = None):
    """Return research sessions (topic clusters) for the Research tab."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        rows = _service().research_sessions(scope, date_range, limit=50)
        session = SessionLocal()
        try:
            from database.models import SearchQuery
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
                    'start': _iso(r.start_time),
                    'end': _iso(r.end_time),
                    'query_count': r.query_count,
                    'duration_min': round((r.total_duration_sec or 0) / 60, 1),
                    'sources': _safe_json(r.sources, []),
                    'sample_queries': [s.query for s in samples],
                    'workspace': r.workspace,
                })
            return JSONResponse(result)
        finally:
            session.close()
    except Exception as exc:
        return _api_error(exc, 500)


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
        return _api_error(e, 500)


@app.get('/api/research/bookmarks')
def api_research_bookmarks(workspace: str = None):
    """Return bookmarked search queries in the selected workspace scope."""
    try:
        scope = normalize_scope(workspace or settings.default_workspace, COMPANY)
        rows = _service().search_queries(scope, None, limit=100, bookmarked_only=True)
        return JSONResponse([{
            'id': r.id,
            'query': r.query,
            'source': r.source,
            'timestamp': _iso(r.timestamp),
            'workspace': r.workspace,
        } for r in rows])
    except Exception as exc:
        return _api_error(exc, 500)


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
        return _api_error(e, 500)


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
        return _api_error(e, 500)


# ─── Briefing & Session Memory ────────────────────────────────────────────────

@app.get('/api/briefing')
def api_briefing():
    """Return the morning briefing for the Company Worker mode."""
    try:
        briefing = generate_morning_briefing()
        return JSONResponse(briefing)
    except Exception as e:
        return _api_error(e, 500)


@app.get('/api/session_context')
def api_session_context():
    """Return the last session snapshot for the Handoff Card."""
    try:
        from tracker.session_memory import load_last_session
        snap = load_last_session()
        return JSONResponse(snap or {})
    except Exception as e:
        return _api_error(e, 500)


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
        return _api_error(e, 500)


@app.get('/api/projects/summary')
def api_projects_summary(days: int = 7, workspace: str = None):
    """Return time spent per project for the selected workspace."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        return JSONResponse(_service().project_totals(scope, date_range))
    except Exception as exc:
        return _api_error(exc, 500)


# ── Browser History API ───────────────────────────────────────────────────────

@app.get('/api/browser/history')
def api_browser_history(days: int = 1, limit: int = 100, workspace: str = None):
    """Return browser history with real URLs, filtered by workspace."""
    try:
        scope = normalize_scope(workspace or settings.default_workspace, COMPANY)
        return JSONResponse(browser_hist.get_history(days=days, limit=limit, workspace=scope))
    except Exception as exc:
        return _api_error(exc, 500)


@app.get('/api/browser/top-sites')
def api_browser_top_sites(days: int = 7, workspace: str = None):
    """Return most visited sites aggregated by domain."""
    try:
        scope = normalize_scope(workspace or settings.default_workspace, COMPANY)
        return JSONResponse(browser_hist.get_top_sites(days=days, workspace=scope))
    except Exception as exc:
        return _api_error(exc, 500)


# ── Meetings API ──────────────────────────────────────────────────────────────

@app.get('/api/meetings')
def api_meetings(days: int = 7, workspace: str = None, period: str = None):
    """Return meetings for the last N days or an explicit period."""
    try:
        if period:
            scope, date_range = _filters(workspace, period)
        else:
            scope, date_range = _filters(workspace, period="custom", days=days)
        rows = _service().meetings(scope, date_range, limit=500)
        return JSONResponse([{
            'id': r.id,
            'start_time': _iso(r.start_time),
            'end_time': _iso(r.end_time),
            'timestamp': _iso(r.start_time),
            'duration_sec': r.duration_sec,
            'duration_min': round((r.duration_sec or 0) / 60, 1),
            'platform': r.platform,
            'app': r.platform,
            'title': r.title,
            'date': r.session_date,
            'workspace': r.workspace,
        } for r in rows])
    except Exception as exc:
        return _api_error(exc, 500)


# ── AI Activity Insights API ─────────────────────────────────────────────────

@app.get('/api/insights/detailed')
def api_insights_detailed(days: int = 1, limit: int = 50, workspace: str = None):
    """Return AI-generated activity insights with OCR summaries and engagement type."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        rows = _service().insights(scope, date_range, limit=limit)
        return JSONResponse([{
            'id': r.id,
            'timestamp': _iso(r.timestamp),
            'app': r.app,
            'summary': r.summary,
            'keywords': r.topic_keywords,
            'duration_min': round((r.duration_on_tab or 0) / 60, 1),
            'date': r.session_date,
            'engagement_type': getattr(r, 'engagement_type', None),
            'ocr_summary': getattr(r, 'ocr_summary', None),
            'workspace': r.workspace,
        } for r in rows])
    except Exception as exc:
        return _api_error(exc, 500)


# ── Notes API (Context-Aware) ─────────────────────────────────────────────────
import json

@app.get('/api/notes')
def api_get_notes(limit: int = 50, workspace: str = None, days: int = None):
    try:
        scope, date_range = _filters(workspace, period="custom", days=days or 3650)
        rows = _service().notes(scope, date_range, limit=limit)
        return JSONResponse([{
            'id': n.id,
            'timestamp': _iso(n.timestamp),
            'date': n.date,
            'content': n.content,
            'source': n.source,
            'category': n.category,
            'workspace': n.workspace,
            'context_data': _safe_json(n.context_data, None),
            'screenshot_path': n.screenshot_path
        } for n in rows])
    except Exception as exc:
        return _api_error(exc, 500)

@app.post('/api/notes')
async def api_post_note(request: Request):
    try:
        data = await request.json()
        content = data.get('content', '').strip()
        source = data.get('source', 'typed')
        if not content:
            return JSONResponse({'error': 'content required'}, status_code=400)
        workspace = normalize_scope(data.get('workspace') or settings.default_workspace, COMPANY)

        session = SessionLocal()
        from database.models import DailyNote, Event, GitActivity
        
        # ── Capture Current Context ──
        context = {}
        latest_event = session.query(Event).order_by(Event.timestamp.desc()).first()
        if latest_event:
            context['app'] = latest_event.application
            context['window'] = latest_event.window_title
            context['project'] = latest_event.project
            context['file'] = latest_event.opened_file
        
        latest_git = session.query(GitActivity).order_by(GitActivity.timestamp.desc()).first()
        if latest_git:
            context['git_repo'] = latest_git.repo
            context['git_commit'] = latest_git.commit_hash[:8] if latest_git.commit_hash else None
            context['workspace'] = latest_git.workspace

        note = DailyNote(
            timestamp=datetime.now(),
            session_id=SESSION_ID,
            date=datetime.now().strftime('%Y-%m-%d'),
            content=content,
            source=source,
            context_data=json.dumps(context) if context else None,
            workspace=workspace,
        )
        session.add(note)
        session.commit()
        session.close()
        return JSONResponse({'status': 'saved', 'workspace': workspace})
    except Exception as exc:
        return _api_error(exc, 500)

@app.delete('/api/notes/{note_id}')
def api_delete_note(note_id: int):
    try:
        session = SessionLocal()
        from database.models import DailyNote
        session.query(DailyNote).filter(DailyNote.id == note_id).delete()
        session.commit()
        session.close()
        return JSONResponse({'status': 'deleted'})
    except Exception as e:
        return _api_error(e, 500)


@app.get('/api/files/detailed')
def api_files_detailed(days: int = 7, limit: int = 30, workspace: str = None):
    """Return inferred file durations with IDE/editor info."""
    try:
        scope, date_range = _filters(workspace, period="custom", days=days)
        rows = _service().file_durations(scope, date_range, limit=limit)
        return JSONResponse([{
            'file': r.file_name,
            'path': r.file_path,
            'project': r.project,
            'language': r.language,
            'editor': r.editor,
            'duration_min': round((r.duration_sec or 0) / 60, 1),
            'timestamp': _iso(r.timestamp),
            'date': r.session_date,
            'workspace': r.workspace,
            'source': r.source or 'active_window_inference',
        } for r in rows])
    except Exception as exc:
        return _api_error(exc, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# ── Microsoft Recall-Inspired API Endpoints (v4.0) ────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════


@app.get('/api/timeline/dates')
def api_timeline_dates(workspace: str = None):
    """Return list of dates that have captured screen frames."""
    try:
        from database.models import ScreenFrame
        from sqlalchemy import func, distinct
        scope = normalize_scope(workspace or settings.default_workspace, COMPANY)
        values = scope_workspaces(scope)
        session = SessionLocal()
        dates = (session.query(distinct(ScreenFrame.session_date))
                 .filter(ScreenFrame.workspace.in_(values))
                 .order_by(ScreenFrame.session_date.desc())
                 .limit(90).all())
        session.close()
        return JSONResponse({
            'dates': [d[0] for d in dates if d[0]],
            **_scope_payload(scope),
        })
    except Exception as exc:
        return _api_error(exc, 500, dates=[])


@app.get('/api/frame/{frame_id}')
def api_frame_detail(frame_id: int):
    """Get full detail of a specific screen frame."""
    try:
        from database.models import ScreenFrame
        session = SessionLocal()
        frame = session.query(ScreenFrame).filter(ScreenFrame.id == frame_id).first()
        if not frame:
            session.close()
            return JSONResponse({'error': 'Frame not found'}, status_code=404)

        result = {
            'id': frame.id,
            'timestamp': frame.timestamp.isoformat() if frame.timestamp else None,
            'screenshot_path': frame.screenshot_path,
            'process_name': frame.process_name,
            'window_title': frame.window_title,
            'category': frame.detected_category,
            'summary': frame.analysis_summary,
            'ocr_text': frame.ocr_text,
            'ocr_text_length': frame.ocr_text_length,
            'phash': frame.phash,
            'file_size_bytes': frame.file_size_bytes,
            'privacy_scrubbed': frame.privacy_scrubbed,
            'has_embedding': frame.embedding_json is not None,
        }
        session.close()
        return JSONResponse(result)
    except Exception as e:
        return _api_error(e, 500)




@app.get('/api/privacy/rules')
def api_privacy_rules():
    """Get current privacy exclusion rules."""
    try:
        from config.privacy_rules import PrivacyRules
        rules = PrivacyRules()
        return JSONResponse(rules.get_rules())
    except Exception as e:
        return _api_error(e, 500)


@app.post('/api/privacy/rules')
async def api_update_privacy_rules(request: Request):
    """Update privacy exclusion rules."""
    try:
        from config.privacy_rules import PrivacyRules
        data = await request.json()
        rules = PrivacyRules()
        if 'add_process' in data:
            rules.add_process_rule(data['add_process'])
        if 'add_pattern' in data:
            rules.add_title_pattern(data['add_pattern'])
        rules.save()
        return JSONResponse({'status': 'updated', 'rules': rules.get_rules()})
    except Exception as exc:
        return _api_error(exc, 500)


@app.get('/api/privacy/export')
def api_privacy_export(workspace: str = None, period: str = "daily",
                       start: str = None, end: str = None):
    """Export only the selected scope's records as local JSON (no file contents)."""
    scope, date_range = _filters(workspace, period, start, end)
    report = build_report(scope, start=date_range.start.isoformat(), end=date_range.end.isoformat())
    return JSONResponse({
        "exported_at": datetime.now().isoformat(),
        "scope": scope,
        "period": date_range.as_dict(),
        "data": report.to_dict(),
        "notice": "Contains metadata only: no document contents, email bodies, message bodies or credentials.",
    })


@app.post('/api/privacy/delete')
async def api_privacy_delete(request: Request):
    """Delete records for an explicitly selected workspace/date range."""
    try:
        data = await request.json()
        if not data.get("confirm"):
            return JSONResponse({"error": "confirm=true is required"}, status_code=400)
        scope, date_range = _filters(
            data.get("workspace"), data.get("period", "custom"),
            data.get("start"), data.get("end"), data.get("days"),
        )
        if scope in (COMBINED, "all"):
            return JSONResponse({"error": "Choose one concrete workspace to delete."}, status_code=400)
        values = scope_workspaces(scope)
        from database.models import (
            ActivityInsight, BrowserHistory, DailyNote, Event, FileChangeEvent,
            FileEdit, GitActivity, Meeting, OCRText, Report, ResearchSession,
            ScreenFrame, SearchQuery,
        )
        session = SessionLocal()
        deleted = {}
        try:
            # Remove screenshot files that belong to the deleted metadata rows.
            for model in (ScreenFrame, OCRText, DailyNote):
                rows = (session.query(model)
                        .filter(model.workspace.in_(values),
                                model.timestamp >= date_range.start,
                                model.timestamp <= date_range.end)
                        .all())
                for row in rows:
                    path = _safe_screenshot_file(getattr(row, "screenshot_path", None))
                    if path:
                        try:
                            path.unlink()
                        except OSError:
                            pass

            specs = [
                (Event, Event.timestamp), (BrowserHistory, BrowserHistory.timestamp),
                (FileEdit, FileEdit.timestamp), (FileChangeEvent, FileChangeEvent.timestamp),
                (GitActivity, GitActivity.timestamp), (Meeting, Meeting.start_time),
                (SearchQuery, SearchQuery.timestamp),
                (ResearchSession, ResearchSession.start_time),
                (ActivityInsight, ActivityInsight.timestamp),
                (OCRText, OCRText.timestamp),
                (DailyNote, DailyNote.timestamp), (ScreenFrame, ScreenFrame.timestamp),
                (Report, Report.period_start),
            ]
            for model, column in specs:
                count = (session.query(model)
                         .filter(model.workspace.in_(values), column >= date_range.start,
                                 column <= date_range.end)
                         .delete(synchronize_session=False))
                if count:
                    deleted[model.__tablename__] = count
            session.commit()
        finally:
            session.close()
        return JSONResponse({"status": "deleted", "scope": scope,
                             "period": date_range.as_dict(), "deleted": deleted})
    except Exception as exc:
        return _api_error(exc, 400)


@app.get('/api/storage/stats')
def api_storage_stats():
    """Get storage statistics: total frames, disk usage, dedup rate."""
    try:
        from database.models import ScreenFrame
        from sqlalchemy import func
        session = SessionLocal()

        total_frames = session.query(func.count(ScreenFrame.id)).scalar() or 0
        total_bytes = session.query(func.sum(ScreenFrame.file_size_bytes)).scalar() or 0
        deduped_frames = session.query(func.count(ScreenFrame.id)).filter(
            ScreenFrame.is_duplicate == True
        ).scalar() or 0
        frames_with_embedding = session.query(func.count(ScreenFrame.id)).filter(
            ScreenFrame.embedding_json.isnot(None)
        ).scalar() or 0

        # Category breakdown
        categories = (session.query(
            ScreenFrame.detected_category,
            func.count(ScreenFrame.id)
        ).group_by(ScreenFrame.detected_category).all())

        session.close()

        return JSONResponse({
            'total_frames': total_frames,
            'total_size_mb': round(total_bytes / (1024 * 1024), 2),
            'deduped_frames': deduped_frames,
            'dedup_rate_pct': round(deduped_frames / max(1, total_frames + deduped_frames) * 100, 1),
            'frames_with_embeddings': frames_with_embedding,
            'embedding_coverage_pct': round(frames_with_embedding / max(1, total_frames) * 100, 1),
            'categories': {c[0] or 'unknown': c[1] for c in categories},
        })
    except Exception as e:
        return _api_error(e, 500)


@app.get('/api/search/rebuild_index')
def api_rebuild_index():
    """Rebuild FTS5 search index from existing screen_frames data."""
    try:
        from sqlalchemy import text as sa_text
        session = SessionLocal()
        session.execute(sa_text("DELETE FROM screen_frames_fts"))
        session.execute(sa_text("""
            INSERT INTO screen_frames_fts(rowid, ocr_text, window_title, analysis_summary)
            SELECT id, COALESCE(ocr_text, ''), COALESCE(window_title, ''), COALESCE(analysis_summary, '')
            FROM screen_frames
        """))
        session.commit()
        session.close()
        return JSONResponse({'status': 'FTS5 index rebuilt successfully'})
    except Exception as e:
        return _api_error(e, 500)

@app.get('/api/timeline')
def api_timeline(limit: int = 50, offset: int = 0, date: str = None,
                 workspace: str = None, period: str = None,
                 start: str = None, end: str = None):
    """Return paginated frames with WebP URLs and activity analysis."""
    try:
        from database.models import ScreenFrame
        scope = normalize_scope(workspace or settings.default_workspace, COMPANY)
        values = scope_workspaces(scope)
        session = SessionLocal()

        query = session.query(ScreenFrame).filter(ScreenFrame.workspace.in_(values))
        if date:
            query = query.filter(ScreenFrame.session_date == date)
        elif period:
            _scope, date_range = _filters(scope, period, start, end)
            query = query.filter(ScreenFrame.timestamp >= date_range.start,
                                 ScreenFrame.timestamp <= date_range.end)
        total_count = query.count()
        frames = query.order_by(ScreenFrame.timestamp.desc()).offset(offset).limit(limit).all()
        result = []
        for f in frames:
            ocr_text = f.ocr_text or ''
            omni_data = None
            if f.omniparser_json:
                try:
                    omni_data = json.loads(f.omniparser_json)
                except Exception:
                    omni_data = None
            result.append({
                'id': f.id,
                'timestamp': _iso(f.timestamp),
                'process_name': f.process_name or '',
                'window_title': f.window_title or '',
                'ocr_preview': ocr_text[:300] if ocr_text else '',
                'ocr_text_length': f.ocr_text_length or 0,
                'analysis_summary': f.analysis_summary or '',
                'detected_category': f.detected_category or 'unknown',
                'is_duplicate': f.is_duplicate,
                'file_size_kb': round((f.file_size_bytes or 0) / 1024, 1),
                'omniparser_json': omni_data,
                'workspace': f.workspace,
            })
            
        session.close()
        return JSONResponse({
            'frames': result, 'total': total_count, 'offset': offset,
            **_scope_payload(scope),
        })
    except Exception as exc:
        return _api_error(exc, 500)

@app.get('/api/timeline/reanalyze')
def api_reanalyze():
    """Retroactively OCR and analyze all ScreenFrames that are missing text or summary.
    Extracts text using native WinOCR if ocr_text was empty, generates human-readable
    summaries and categories, and syncs into the FTS5 search index."""
    try:
        from database.models import ScreenFrame
        from ocr.screen_analyzer import analyze_screen, detect_category
        from sqlalchemy import text as sa_text
        try:
            from ocr.win_ocr import extract_text as win_ocr_extract
        except Exception:
            win_ocr_extract = None

        session = SessionLocal()
        
        frames = session.query(ScreenFrame).all()
        
        updated = 0
        for f in frames:
            # 1. Backfill OCR if empty
            if (not f.ocr_text or len(f.ocr_text.strip()) == 0) and f.screenshot_path and os.path.exists(f.screenshot_path) and win_ocr_extract:
                try:
                    ocr_res = win_ocr_extract(f.screenshot_path)
                    extracted = ocr_res.get('text', '') if isinstance(ocr_res, dict) else ''
                    if extracted:
                        f.ocr_text = extracted
                        f.ocr_text_length = len(extracted)
                except Exception as oe:
                    log.debug('Reanalyze OCR error for frame %s: %s', f.id, oe)

            ocr = f.ocr_text or ''
            proc = f.process_name or ''
            title = f.window_title or ''
            
            summary = analyze_screen(ocr, proc, title)
            category = detect_category(proc, title, summary)
            
            if summary:
                f.analysis_summary = summary
            if category:
                f.detected_category = category
            
            # Sync to FTS5
            try:
                session.execute(
                    sa_text("INSERT OR REPLACE INTO screen_frames_fts(rowid, ocr_text, window_title, analysis_summary) VALUES (:id, :txt, :title, :summary)"),
                    {"id": f.id, "txt": ocr, "title": title, "summary": summary or ""}
                )
            except Exception:
                pass
                
            updated += 1
            
        session.commit()
        session.close()
        return JSONResponse({'status': 'ok', 'frames_updated': updated})
    except Exception as e:
        return _api_error(e, 500)

@app.get('/api/search')
def api_search(q: str = '', limit: int = 20, workspace: str = None):
    """Hybrid semantic + keyword search, filtered to a workspace scope."""
    try:
        from ocr.search_engine import search_engine
        results = search_engine.hybrid_search(query=q, limit=max(limit, 60) if workspace else limit)
        if workspace:
            scope = normalize_scope(workspace, COMPANY)
            values = scope_workspaces(scope)
            session = SessionLocal()
            try:
                from database.models import ScreenFrame
                allowed = {
                    row[0] for row in session.query(ScreenFrame.id)
                    .filter(ScreenFrame.workspace.in_(values)).all()
                }
            finally:
                session.close()
            results = [row for row in results if row.get("id") in allowed][:limit]
        return JSONResponse({'results': results})
    except Exception as exc:
        return _api_error(exc, 500)

