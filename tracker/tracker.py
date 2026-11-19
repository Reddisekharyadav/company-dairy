"""Background tracker that periodically samples active window and writes events to DB."""
from threading import Thread, Event as ThreadEvent
import time
from datetime import datetime, timedelta
from typing import Optional
from database.session import SessionLocal
from database.models import (
    ActivityInsight,
    BrowserHistory,
    DailyNote,
    Event,
    FileChangeEvent,
    FileEdit,
    GitActivity,
    Meeting,
    OCRText,
    Report,
    ResearchSession,
    ScreenFrame,
    SearchQuery,
    SessionSnapshot,
)
from tracker.active_window import get_active_window
from tracker.categorizer import categorize_activity, extract_website_name
from tracker.file_types import EXT_LANG_MAP, get_language
from tracker.input_tracker import input_tracker
import psutil
import logging
import os
import re
from pathlib import Path
from sqlalchemy.orm import Session
from config.settings import SESSION_ID, settings
from config.workspace_rules import classifier

log = logging.getLogger("tracker")


class ActivityTracker:
    def __init__(self, interval: float = 5.0, idle_timeout: int = 300):
        self.interval = interval
        self.idle_timeout = idle_timeout
        self._stop = ThreadEvent()
        self._thread = Thread(target=self._run, daemon=True)
        self._last_input = datetime.now()

    def start(self):
        self._stop.clear()
        input_tracker.start()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        input_tracker.stop()
        self._thread.join(timeout=2.0)

    def _is_idle(self) -> bool:
        # simple heuristic using psutil to detect system-wide idle via cpu_percent
        cpu = psutil.cpu_percent(interval=None)
        return cpu < 1.0

    def _guess_language_from_filename(self, filename: str) -> Optional[str]:
        return get_language(filename) if filename else None

    def _cleanup_old_data(self, session: Session, retention_days: int = 30,
                          max_storage_mb: int = 2048, screenshot_dirs=None):
        """Delete expired activity, screenshot files and enforce the storage cap."""
        cutoff_date = datetime.now() - timedelta(days=max(1, int(retention_days)))
        deleted = {}

        # Screenshot roots are computed first because database rows must never
        # be able to delete an arbitrary file outside these directories.
        if screenshot_dirs is None:
            root_paths = []
            appdata_scr = os.path.join(
                os.environ.get("APPDATA") or os.path.expanduser("~"),
                "WorkSense", "screenshots",
            )
            if os.path.exists(appdata_scr):
                root_paths.append(appdata_scr)
            local_scr = os.path.join(os.getcwd(), "screenshots")
            if os.path.exists(local_scr) and local_scr != appdata_scr:
                root_paths.append(local_scr)
        else:
            root_paths = [str(p) for p in screenshot_dirs]
        allowed_roots = []
        for root in root_paths:
            try:
                allowed_roots.append(Path(root).resolve())
            except OSError:
                continue

        def remove_screenshot(raw_path):
            if not raw_path:
                return False
            try:
                candidate = Path(raw_path).resolve(strict=True)
            except (OSError, ValueError):
                return False
            if not candidate.is_file():
                return False
            if not any(candidate.is_relative_to(root) for root in allowed_roots):
                return False
            try:
                candidate.unlink()
                return True
            except OSError:
                return False

        try:
            # 1. Delete old ScreenFrame records and their files.
            old_frames = session.query(ScreenFrame).filter(ScreenFrame.timestamp < cutoff_date).all()
            deleted_frames_count = 0
            for frame in old_frames:
                remove_screenshot(frame.screenshot_path)
                session.delete(frame)
                deleted_frames_count += 1
            if deleted_frames_count:
                deleted["screen_frames"] = deleted_frames_count

            # 2. Delete old OCR rows and their files.
            old_ocr = session.query(OCRText).filter(OCRText.timestamp < cutoff_date).all()
            for row in old_ocr:
                remove_screenshot(row.screenshot_path)
                session.delete(row)
            if old_ocr:
                deleted["ocr"] = len(old_ocr)

            # 3. Delete other old activity records.
            old_notes = session.query(DailyNote).filter(DailyNote.timestamp < cutoff_date).all()
            for note in old_notes:
                remove_screenshot(note.screenshot_path)
                session.delete(note)
            if old_notes:
                deleted["daily_notes"] = len(old_notes)

            specs = [
                (Event, Event.timestamp),
                (BrowserHistory, BrowserHistory.timestamp),
                (FileEdit, FileEdit.timestamp),
                (FileChangeEvent, FileChangeEvent.timestamp),
                (GitActivity, GitActivity.timestamp),
                (Meeting, Meeting.start_time),
                (SearchQuery, SearchQuery.timestamp),
                (ResearchSession, ResearchSession.start_time),
                (ActivityInsight, ActivityInsight.timestamp),
                (Report, Report.created_at),
                (SessionSnapshot, SessionSnapshot.created_at),
            ]
            for model, column in specs:
                count = (session.query(model).filter(column < cutoff_date)
                         .delete(synchronize_session=False))
                if count:
                    deleted[model.__tablename__] = count

            session.commit()

            total_deleted = sum(deleted.values())
            if total_deleted > 0:
                log.info("Cleanup: Deleted %d old DB records older than %s",
                         total_deleted, cutoff_date.date())

            # 4. Clean screenshot directories and enforce the storage cap.
            for sdir in root_paths:
                if not os.path.isdir(sdir):
                    continue
                all_files = []
                for filename in os.listdir(sdir):
                    filepath = os.path.join(sdir, filename)
                    try:
                        if os.path.isfile(filepath):
                            mtime = os.path.getmtime(filepath)
                            size = os.path.getsize(filepath)
                            all_files.append((filepath, mtime, size))
                    except OSError:
                        continue

                deleted_imgs = 0
                for filepath, mtime, _size in list(all_files):
                    if datetime.fromtimestamp(mtime) < cutoff_date:
                        try:
                            os.remove(filepath)
                            deleted_imgs += 1
                        except OSError:
                            pass
                if deleted_imgs > 0:
                    log.info("Cleanup: Deleted %d old screenshots from %s", deleted_imgs, sdir)

                try:
                    remaining_files = [(p, m, s) for p, m, s in all_files if os.path.exists(p)]
                    total_mb = sum(s for _, _, s in remaining_files) / (1024 * 1024)
                    if total_mb > max_storage_mb:
                        remaining_files.sort(key=lambda item: item[1])
                        freed_mb = 0
                        pruned_count = 0
                        for fpath, _, fsize in remaining_files:
                            if total_mb - freed_mb <= max_storage_mb:
                                break
                            try:
                                os.remove(fpath)
                                freed_mb += fsize / (1024 * 1024)
                                pruned_count += 1
                            except OSError:
                                pass
                        log.info("Storage cap: Pruned %d screenshots (freed %.1f MB) in %s",
                                 pruned_count, freed_mb, sdir)
                except Exception as cap_err:
                    log.debug("Storage cap check error: %s", cap_err)

            return deleted
        except Exception as e:
            log.error("Cleanup error: %s", e)
            session.rollback()
            return deleted

    def _run(self):
        session: Session = SessionLocal()
        last_cleanup = datetime.min
        
        current_window = None
        window_start_time = None
        last_tick = time.time()
        
        try:
            while not self._stop.is_set():
                now = datetime.now()
                now_ts = time.time()
                elapsed = now_ts - last_tick
                last_tick = now_ts
                
                # Run cleanup once a day
                if (now - last_cleanup).total_seconds() > 86400:
                    self._cleanup_old_data(
                        session,
                        retention_days=getattr(settings, "retention_days", 30),
                        max_storage_mb=getattr(settings, "max_storage_mb", 2048),
                    )
                    last_cleanup = now
                    
                proc_name, title = get_active_window()
                window_key = (proc_name, title)
                idle = self._is_idle()
                
                if not idle and proc_name:
                    if window_key != current_window:
                        # Save the previous window if we tracked it for at least a few seconds
                        if current_window and window_start_time:
                            duration = now_ts - window_start_time
                            if duration >= 1.0:
                                self._save_event(session, current_window[0], current_window[1], duration, idle)
                        
                        current_window = window_key
                        window_start_time = now_ts
                    else:
                        # Flush periodically if staying on the same window a long time (e.g. 60s)
                        if window_start_time and (now_ts - window_start_time) >= 60.0:
                            self._save_event(session, current_window[0], current_window[1], 60.0, idle)
                            window_start_time = now_ts
                elif idle and current_window:
                    # User went idle, flush current window
                    duration = now_ts - window_start_time
                    if duration >= 1.0:
                        self._save_event(session, current_window[0], current_window[1], duration, idle)
                    current_window = None
                    window_start_time = None
                    
                time.sleep(self.interval)
                
        except Exception as e:
            log.exception("Tracker error: %s", e)
        finally:
            if current_window and window_start_time:
                duration = time.time() - window_start_time
                if duration >= 1.0:
                    try:
                        self._save_event(session, current_window[0], current_window[1], duration, False)
                    except Exception:
                        pass
            session.close()

    def _save_event(self, session, proc_name, title, duration, idle):
        opened_file = None
        project = None
        language = None

        if title:
            nt = title.replace('\u2014', ' - ').replace('\u2013', ' - ')
            parts = [p.strip() for p in nt.split(' - ') if p.strip()]
            if 'Visual Studio Code' in nt or 'Code -' in nt or ' - Visual Studio Code' in nt:
                if len(parts) >= 2:
                    opened_file = parts[0]
                    project = parts[1]
            elif proc_name and any(k in proc_name.lower() for k in ('pycharm', 'intellij', 'idea')):
                if len(parts) >= 2:
                    opened_file = parts[0]
                    project = parts[1]
            else:
                if parts:
                    candidate = parts[0]
                    if re.search(r"\.[a-zA-Z0-9]{1,6}$", candidate):
                        opened_file = candidate

        if opened_file:
            language = self._guess_language_from_filename(opened_file)

        category = categorize_activity(proc_name or '', title or '')
        website = extract_website_name(title or '', proc_name or '')
        current_input_state = input_tracker.get_current_state()
        workspace = classifier.classify_application(
            process=proc_name or '',
            title=title or '',
            url=website,
            path=opened_file if opened_file and os.path.isabs(opened_file) else None,
        )

        ev = Event(
            timestamp=datetime.now(),
            session_id=SESSION_ID,
            duration=duration,
            application=proc_name or "",
            window_title=title or "",
            process_name=proc_name or "",
            project=project,
            opened_file=opened_file,
            language=language,
            cpu=psutil.cpu_percent(),
            idle=idle,
            category=category,
            website=website,
            input_state=current_input_state,
            workspace=workspace,
        )
        session.add(ev)
        session.commit()
        log.debug("Logged event: %s %s (%.1fs)", proc_name, title, duration)
