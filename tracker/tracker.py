"""Background tracker that periodically samples active window and writes events to DB."""
from threading import Thread, Event as ThreadEvent
import time
from datetime import datetime, timedelta
from typing import Optional
from database.session import SessionLocal
from database.models import Event, BrowserHistory, FileEdit, GitActivity, Report
from tracker.active_window import get_active_window
from tracker.categorizer import categorize_activity, extract_website_name
import psutil
import logging
import os
import re
from sqlalchemy.orm import Session

log = logging.getLogger("tracker")

EXT_LANG_MAP = {
    '.py': 'Python',
    '.js': 'JavaScript',
    '.ts': 'TypeScript',
    '.java': 'Java',
    '.c': 'C',
    '.cpp': 'C++',
    '.cs': 'C#',
    '.go': 'Go',
    '.rb': 'Ruby',
    '.php': 'PHP',
    '.rs': 'Rust',
    '.md': 'Markdown',
}


class ActivityTracker:
    def __init__(self, interval: float = 5.0, idle_timeout: int = 300):
        self.interval = interval
        self.idle_timeout = idle_timeout
        self._stop = ThreadEvent()
        self._thread = Thread(target=self._run, daemon=True)
        self._last_input = datetime.now()

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _is_idle(self) -> bool:
        # simple heuristic using psutil to detect system-wide idle via cpu_percent
        cpu = psutil.cpu_percent(interval=None)
        return cpu < 1.0

    def _guess_language_from_filename(self, filename: str) -> Optional[str]:
        if not filename:
            return None
        _, ext = os.path.splitext(filename)
        return EXT_LANG_MAP.get(ext.lower())

    def _cleanup_old_data(self, session: Session, retention_days: int = 30):
        """Deletes data older than retention_days from database and disk."""
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        try:
            # 1. Delete old DB records
            deleted_events = session.query(Event).filter(Event.timestamp < cutoff_date).delete()
            deleted_bh = session.query(BrowserHistory).filter(BrowserHistory.timestamp < cutoff_date).delete()
            deleted_fe = session.query(FileEdit).filter(FileEdit.timestamp < cutoff_date).delete()
            deleted_ga = session.query(GitActivity).filter(GitActivity.timestamp < cutoff_date).delete()
            deleted_reports = session.query(Report).filter(Report.created_at < cutoff_date).delete()
            session.commit()
            
            total_deleted = deleted_events + deleted_bh + deleted_fe + deleted_ga + deleted_reports
            if total_deleted > 0:
                log.info("Cleanup: Deleted %d old DB records older than %s", total_deleted, cutoff_date.date())

            # 2. Delete old screenshots
            screenshots_dir = os.path.join(os.getcwd(), 'screenshots')
            if os.path.exists(screenshots_dir):
                deleted_imgs = 0
                for filename in os.listdir(screenshots_dir):
                    filepath = os.path.join(screenshots_dir, filename)
                    if os.path.isfile(filepath):
                        # Use file modification time
                        if datetime.fromtimestamp(os.path.getmtime(filepath)) < cutoff_date:
                            try:
                                os.remove(filepath)
                                deleted_imgs += 1
                            except OSError:
                                pass
                if deleted_imgs > 0:
                    log.info("Cleanup: Deleted %d old screenshots", deleted_imgs)
        except Exception as e:
            log.error("Cleanup error: %s", e)
            session.rollback()

    def _run(self):
        session: Session = SessionLocal()
        last_cleanup = datetime.min
        try:
            while not self._stop.is_set():
                now = datetime.now()
                
                # Run cleanup once a day
                if (now - last_cleanup).total_seconds() > 86400:
                    self._cleanup_old_data(session, retention_days=30)
                    last_cleanup = now
                    
                proc_name, title = get_active_window()
                now = datetime.now()
                idle = self._is_idle()
                opened_file = None
                project = None
                language = None

                if title:
                    # normalize em dash to simple dash
                    nt = title.replace('\u2014', ' - ').replace('\u2013', ' - ')
                    # common patterns:
                    # "filename - folder - Visual Studio Code"
                    # "filename — project — PyCharm"
                    # "Tab Title - Site - Google Chrome"
                    parts = [p.strip() for p in nt.split(' - ') if p.strip()]
                    # heuristics for VS Code / JetBrains: filename is first part
                    if 'Visual Studio Code' in nt or 'Code -' in nt or ' - Visual Studio Code' in nt:
                        if len(parts) >= 2:
                            opened_file = parts[0]
                            project = parts[1]
                    elif proc_name and any(k in proc_name.lower() for k in ('pycharm', 'intellij', 'idea')):
                        if len(parts) >= 2:
                            opened_file = parts[0]
                            project = parts[1]
                    else:
                        # browser or other apps: try to detect file-like strings
                        # if the first part contains a file extension, treat as filename
                        if parts:
                            candidate = parts[0]
                            if re.search(r"\.[a-zA-Z0-9]{1,6}$", candidate):
                                opened_file = candidate
                            else:
                                # sometimes VS Code shows "file - Workspace - Visual Studio Code"
                                opened_file = None

                # try to detect language from opened_file
                if opened_file:
                    language = self._guess_language_from_filename(opened_file)

                category = categorize_activity(proc_name or '', title or '')
                website = extract_website_name(title or '', proc_name or '')

                ev = Event(
                    timestamp=now,
                    duration=self.interval,
                    application=proc_name or "",
                    window_title=title or "",
                    process_name=proc_name or "",
                    project=project,
                    opened_file=opened_file,
                    language=language,
                    cpu=psutil.cpu_percent(interval=None),
                    idle=idle,
                    category=category,
                    website=website,
                )
                session.add(ev)
                session.commit()
                log.debug("Logged event: %s %s", proc_name, title)
                time.sleep(self.interval)
        except Exception as e:
            log.exception("Tracker error: %s", e)
        finally:
            session.close()
