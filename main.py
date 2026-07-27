"""
WorkSense AI — Background Activity Tracker
Runs silently in the system tray.
Right-click the tray icon to generate a report or exit.
Reports are saved to the Desktop.
"""
import os
import sys
import logging
import multiprocessing
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# Load .env file from project root (works both from source and frozen exe)
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent / '.env'
    if not _env_file.exists():
        # When frozen as exe, look next to the exe
        _env_file = Path(sys.executable).resolve().parent / '.env'
    if _env_file.exists():
        load_dotenv(str(_env_file), override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on system env vars

from workspace.workspace_manager import WorkspaceManager


# ── Logging to file (always visible even with console=False) ─────────────────
def _setup_logging():
    app_data = Path(os.environ.get('APPDATA') or Path.home()) / 'WorkSense'
    app_data.mkdir(parents=True, exist_ok=True)
    log_file = app_data / 'worksense.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[logging.FileHandler(str(log_file), encoding='utf-8')],
    )
    return str(log_file)


LOG_FILE = _setup_logging()
log = logging.getLogger('worksense')
log.info('WorkSense starting (Python %s)', sys.version.split()[0])


# ── Import guard ─────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw
    import pystray
    from pystray import Icon, MenuItem, Menu
except Exception as e:
    log.critical('Tray dependency unavailable: %s', e, exc_info=True)
    Image = ImageDraw = None
    pystray = None
    Icon = MenuItem = Menu = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _resolve_output_dir() -> Path:
    """Return the user's Desktop folder in a way that works on Windows and OneDrive."""
    candidates = []
    for env_name in ('DESKTOP', 'USERPROFILE', 'HOME'):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value).expanduser())
    candidates.append(Path.home())

    for candidate in candidates:
        if candidate.name.lower() == 'desktop':
            path = candidate.expanduser()
        else:
            path = (candidate / 'Desktop').expanduser()
        if path.exists():
            return path.resolve()

    fallback = (Path.home() / 'Desktop').expanduser()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()


def _desktop() -> Path:
    """Return the user's Desktop folder."""
    return _resolve_output_dir()


def _appdata() -> Path:
    base = Path(os.environ.get('APPDATA') or Path.home())
    d = base / 'WorkSense'
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Workspace CLI helpers ──────────────────────────────────────────────────
def _run_workspace_cli(argv):
    manager = WorkspaceManager(root=Path.cwd())
    command = " ".join(argv).strip()

    if command in {"scan workspace", "scan"}:
        projects = manager.scan_workspace()
        print(f"Found {len(projects)} projects")
        print()
        print("Languages")
        stats = manager.get_stats()
        for language, count in sorted(stats["languages"].items()):
            print(f"{language}: {count}")
        print()
        print("Git repositories")
        print(stats["git_repositories"])
        print()
        print("README found")
        print(stats["readme_count"])
        print()
        print("Total size")
        print(f"{stats['total_size'] / (1024 * 1024):.1f} MB")
        return 0

    if command == "list projects":
        projects = manager.list_projects()
        for index, project_name in enumerate(projects, start=1):
            print(f"{index}. {project_name}")
        return 0

    if command == "workspace stats":
        stats = manager.get_stats()
        print(f"Projects: {stats['project_count']}")
        print(f"Git repositories: {stats['git_repositories']}")
        print(f"README files: {stats['readme_count']}")
        print(f"Total size: {stats['total_size'] / (1024 * 1024):.1f} MB")
        return 0

    if command.startswith("project info "):
        name = command[len("project info "):].strip()
        project = manager.get_project(name)
        if not project:
            print(f"Project '{name}' not found")
            return 1
        print(f"Name: {project.name}")
        print(f"Path: {project.path}")
        print(f"Languages: {', '.join(project.languages) if project.languages else 'Unknown'}")
        print(f"Frameworks: {', '.join(project.frameworks) if project.frameworks else 'None'}")
        if project.readme:
            print(f"Description: {project.readme.description or 'N/A'}")
        return 0

    if command.startswith("open project "):
        name = command[len("open project "):].strip()
        project = manager.get_project(name)
        if not project:
            print(f"Project '{name}' not found")
            return 1
        if hasattr(os, 'startfile'):
            os.startfile(project.path)
        else:
            subprocess.Popen(["xdg-open", project.path])
        print(f"Opened {project.name}")
        return 0

    if command == "refresh workspace":
        projects = manager.refresh()
        print(f"Refreshed workspace; discovered {len(projects)} projects")
        return 0

    print("Supported commands: scan workspace, list projects, project info <name>, open project <name>, workspace stats, refresh workspace")
    return 0


# ── Tray icon image ──────────────────────────────────────────────────────────
def _make_icon_image():
    if Image is None or ImageDraw is None:
        return None

    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Blue circle with W
    d.ellipse((2, 2, 62, 62), fill=(30, 144, 255))
    d.ellipse((6, 6, 58, 58), fill=(20, 100, 200))
    # Simple W shape
    pts = [
        (14, 18), (20, 46), (26, 30), (32, 46),
        (38, 30), (44, 46), (50, 18),
    ]
    d.line(pts, fill=(255, 255, 255), width=5)
    return img


# ── Notification helper ───────────────────────────────────────────────────────
_tray_icon_ref = None  # set after Icon is created


def _notify(title: str, msg: str):
    """Show a Windows balloon notification via the tray icon."""
    try:
        if _tray_icon_ref is not None:
            _tray_icon_ref.notify(msg, title)
    except Exception as e:
        log.warning('Notify failed: %s', e)


# ── Report generation ─────────────────────────────────────────────────────────
_report_lock = threading.Lock()
_last_report_path = None


def _do_generate_report(period: str = 'today', send_email: bool = False):
    global _last_report_path
    if not _report_lock.acquire(blocking=False):
        _notify('WorkSense', 'Report generation already in progress…')
        return

    _notify('WorkSense', 'Generating report, please wait…')
    try:
        from database.session import init_db
        from reports.generator import generate_pdf, generate_markdown, generate_docx, summarize_events

        init_db()

        now = datetime.now()
        if period == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # week
            start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

        out_dir = _resolve_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_folder = str(out_dir)
        log.info('Generating report: %s → %s into %s', start, now, out_folder)

        pdf_path = generate_pdf(start, now, out_folder)
        generate_markdown(start, now, out_folder)
        generate_docx(start, now, out_folder)

        _last_report_path = pdf_path
        _notify('WorkSense ✅', f'Report saved to Desktop!\n{os.path.basename(pdf_path)}')
        log.info('Report saved: %s', pdf_path)

        # Auto-open the PDF when possible
        if hasattr(os, 'startfile'):
            try:
                os.startfile(pdf_path)
            except Exception as e:
                log.warning('Could not open PDF: %s', e)
        else:
            log.info('startfile is unavailable; report saved to %s', pdf_path)

        # ── Optional email send ───────────────────────────────────────────────
        if send_email:
            _do_send_email(pdf_path, period, start, now)

    except Exception as e:
        log.exception('Report generation failed: %s', e)
        _notify('WorkSense ❌', f'Report failed: {e}')
    finally:
        _report_lock.release()


def _do_send_email(pdf_path: str, period: str, start, end):
    """Inner helper: send the report email and notify via tray."""
    try:
        from reports.emailer import send_report, EmailNotConfiguredError
        from reports.generator import summarize_events
        summary = summarize_events(start, end)
        send_report(pdf_path, period=period, extra_body=summary)
        _notify('WorkSense 📧', f'Report emailed successfully!')
        log.info('Report emailed: %s', pdf_path)
    except Exception as email_err:
        log.warning('Email send failed: %s', email_err)
        _notify('WorkSense ⚠️', f'Email failed: {email_err}')


def generate_report_today(_=None):
    t = threading.Thread(target=_do_generate_report, args=('today',), daemon=True)
    t.start()


def generate_report_week(_=None):
    t = threading.Thread(target=_do_generate_report, args=('week',), daemon=True)
    t.start()


def send_report_email_today(_=None):
    """Generate today's report and email it immediately."""
    t = threading.Thread(
        target=_do_generate_report,
        args=('today',),
        kwargs={'send_email': True},
        daemon=True,
    )
    t.start()


def open_email_config(_=None):
    """Open a simple text instructions file so the user knows how to set email env vars."""
    cfg_file = _appdata() / 'email_setup.txt'
    if not cfg_file.exists():
        cfg_file.write_text(
            "WorkSense AI — Email Configuration\n"
            "====================================\n\n"
            "To enable email reports, set these Windows Environment Variables:\n\n"
            "  WS_EMAIL_TO   = recipient@example.com\n"
            "  WS_EMAIL_USER = you@gmail.com\n"
            "  WS_EMAIL_PASS = xxxx xxxx xxxx xxxx   (Gmail App Password)\n"
            "  WS_EMAIL_SMTP = smtp.gmail.com         (default, optional)\n"
            "  WS_EMAIL_PORT = 587                    (default, optional)\n\n"
            "How to create a Gmail App Password:\n"
            "  1. Go to https://myaccount.google.com/apppasswords\n"
            "  2. Select App: Mail, Device: Windows Computer\n"
            "  3. Copy the 16-character password (no spaces)\n"
            "  4. Set it as WS_EMAIL_PASS environment variable\n\n"
            "How to set Environment Variables on Windows:\n"
            "  1. Press Win + R, type: sysdm.cpl, press Enter\n"
            "  2. Advanced tab → Environment Variables\n"
            "  3. Under 'User variables', click New and add each variable\n"
            "  4. Restart WorkSense after saving\n",
            encoding='utf-8',
        )
    try:
        os.startfile(str(cfg_file))
    except Exception as e:
        log.warning('Could not open email config: %s', e)


def open_last_report(_=None):
    global _last_report_path
    if _last_report_path and os.path.exists(_last_report_path):
        try:
            if hasattr(os, 'startfile'):
                os.startfile(_last_report_path)
        except Exception as e:
            log.warning('Could not open report: %s', e)
    else:
        # Find most recent report on Desktop
        desktop = _desktop()
        pdfs = sorted(desktop.glob('report_*.pdf'), reverse=True)
        if pdfs:
            try:
                if hasattr(os, 'startfile'):
                    os.startfile(str(pdfs[0]))
            except Exception as e:
                log.warning('Could not open report: %s', e)
        else:
            _notify('WorkSense', 'No report found yet. Generate one first!')


def open_log(_=None):
    try:
        os.startfile(LOG_FILE)
    except Exception as e:
        log.warning('Could not open log: %s', e)


import webbrowser


def open_dashboard(_=None):
    try:
        webbrowser.open('http://127.0.0.1:8000')
    except Exception as e:
        log.warning('Could not open browser dashboard: %s', e)


def on_exit(icon, _=None):
    log.info('User requested exit')
    try:
        from backend.app import tracker, git_watcher, screen_worker
        tracker.stop()
        git_watcher.stop()
        screen_worker.stop()
    except Exception as e:
        log.warning('Shutdown error: %s', e)
    # Destroy the floating widget
    try:
        from ui.status_widget import get_widget
        w = get_widget()
        if w:
            w.destroy()
    except Exception:
        pass
    icon.stop()


def pause_tracking(_=None):
    """Tray menu: pause tracking."""
    try:
        from ui.status_widget import get_widget
        w = get_widget()
        if w:
            w.stop_tracking()
            _notify('WorkSense ⏸', 'Tracking paused. Click Resume to continue.')
    except Exception as e:
        log.warning('Pause error: %s', e)


def resume_tracking(_=None):
    """Tray menu: resume tracking."""
    try:
        from ui.status_widget import get_widget
        w = get_widget()
        if w:
            w.resume_tracking()
            _notify('WorkSense ▶', 'Tracking resumed!')
    except Exception as e:
        log.warning('Resume error: %s', e)


# ── Web server thread ────────────────────────────────────────────────────────
def _start_web_server():
    try:
        import uvicorn
        from backend.app import app
        log.info('Starting Web Dashboard server on http://127.0.0.1:8000')
        # log_config=None prevents uvicorn from calling dictConfig which
        # crashes on Python 3.14 due to a changed logging formatter API.
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="error",
            log_config=None,
        )
    except Exception as e:
        log.exception('Web server error: %s', e)


def _start_daily_report_scheduler():
    def _loop():
        while True:
            try:
                now = datetime.now()
                # Run at 11:58 PM so report covers the full working day
                next_run = now.replace(hour=23, minute=58, second=0, microsecond=0)
                if next_run <= now:
                    next_run = next_run + timedelta(days=1)
                delay = max(1.0, (next_run - now).total_seconds())
                log.info('Daily report scheduler: next run at %s', next_run.strftime('%Y-%m-%d %H:%M'))
                time.sleep(delay)
                # Generate report AND auto-email it if credentials are configured
                t = threading.Thread(
                    target=_do_generate_report,
                    args=('today',),
                    kwargs={'send_email': True},
                    daemon=True,
                )
                t.start()
            except Exception as e:
                log.exception('Daily report scheduler error: %s', e)
                time.sleep(60)

    scheduler_thread = threading.Thread(target=_loop, daemon=True)
    scheduler_thread.start()


# ── Main ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    if args:
        if args[0] == "scan" and len(args) >= 2 and args[1] == "workspace":
            return _run_workspace_cli(["scan workspace"])
        if args[0] == "list" and len(args) >= 2 and args[1] == "projects":
            return _run_workspace_cli(["list projects"])
        if args[0] == "workspace" and len(args) >= 2 and args[1] == "stats":
            return _run_workspace_cli(["workspace stats"])
        if args[0] == "refresh" and len(args) >= 2 and args[1] == "workspace":
            return _run_workspace_cli(["refresh workspace"])
        if args[0] == "project" and len(args) >= 2 and args[1] == "info":
            return _run_workspace_cli(["project info", *args[2:]])
        if args[0] == "open" and len(args) >= 2 and args[1] == "project":
            return _run_workspace_cli(["open project", *args[2:]])

    global _tray_icon_ref

    if Icon is None or Menu is None or MenuItem is None:
        log.error('Tray dependencies are unavailable; workspace commands are still available from the CLI.')
        return 1

    # ── Show consent dialog FIRST (only on first launch) ────────────────────
    try:
        from ui.status_widget import show_consent_dialog
        show_consent_dialog()
    except Exception as e:
        log.warning('Consent dialog error: %s', e)

    # Start the web server (which initializes DB, activity tracker, git watcher, screen worker)
    web_thread = threading.Thread(target=_start_web_server, daemon=True)
    web_thread.start()
    _start_daily_report_scheduler()

    # ── Launch floating status widget ──────────────────────────────────────
    # Wait a moment for backend to initialize tracker + screen_worker
    time.sleep(1.5)
    try:
        from ui.status_widget import launch_widget
        from backend.app import tracker as _tracker, screen_worker as _sw
        launch_widget(tracker=_tracker, screen_worker=_sw)
        log.info('Status widget launched.')
    except Exception as e:
        log.warning('Could not launch status widget: %s', e)

    # Build tray menu
    menu = Menu(
        MenuItem('🌐 Open Dashboard',       open_dashboard),
        Menu.SEPARATOR,
        MenuItem('📊 Report: Today',         generate_report_today),
        MenuItem('📅 Report: This Week',     generate_report_week),
        MenuItem('📂 Open Last Report',      open_last_report),
        Menu.SEPARATOR,
        MenuItem('📧 Send Report by Email',  send_report_email_today),
        MenuItem('⚙️  Email Setup Guide',    open_email_config),
        Menu.SEPARATOR,
        MenuItem('⏸️  Pause Tracking',       pause_tracking),
        MenuItem('▶️  Resume Tracking',      resume_tracking),
        Menu.SEPARATOR,
        MenuItem('📋 Open Log File',         open_log),
        Menu.SEPARATOR,
        MenuItem('❌ Exit',                  on_exit),
    )

    icon = Icon(
        name='WorkSense',
        icon=_make_icon_image(),
        title='WorkSense AI — Tracking…',
        menu=menu,
    )
    _tray_icon_ref = icon

    log.info('Tray icon starting')
    try:
        icon.run()
    finally:
        try:
            from backend.app import tracker, git_watcher, screen_worker
            tracker.stop()
            git_watcher.stop()
            screen_worker.stop()
        except Exception:
            pass
        try:
            from ui.status_widget import get_widget
            w = get_widget()
            if w: w.destroy()
        except Exception:
            pass
        log.info('WorkSense exited cleanly')


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())

