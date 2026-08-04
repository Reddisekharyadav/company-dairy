"""
File Edit Watcher — tracks which files are being modified across project roots.
Uses watchdog for filesystem events + active-window polling to measure time-per-file.
Stores results in FileEdit table for the Dev Files tab and AI context export.
"""
import os
import time
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger('file_watcher')

# File extensions we care about (developer files)
TRACKED_EXTENSIONS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.c', '.cpp', '.cs',
    '.go', '.rb', '.php', '.rs', '.swift', '.kt', '.vue', '.svelte',
    '.html', '.css', '.scss', '.sass', '.less',
    '.json', '.yaml', '.yml', '.toml', '.xml',
    '.md', '.mdx', '.rst', '.txt',
    '.sql', '.sh', '.bash', '.ps1', '.bat',
    '.dockerfile', '.tf', '.hcl',
}

EXT_LANG_MAP = {
    '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
    '.tsx': 'TypeScript/React', '.jsx': 'JavaScript/React',
    '.java': 'Java', '.c': 'C', '.cpp': 'C++', '.cs': 'C#',
    '.go': 'Go', '.rb': 'Ruby', '.php': 'PHP', '.rs': 'Rust',
    '.swift': 'Swift', '.kt': 'Kotlin', '.vue': 'Vue',
    '.svelte': 'Svelte', '.html': 'HTML', '.css': 'CSS',
    '.scss': 'SCSS', '.sass': 'Sass', '.less': 'Less',
    '.sql': 'SQL', '.sh': 'Shell', '.ps1': 'PowerShell',
    '.md': 'Markdown', '.json': 'JSON', '.yaml': 'YAML',
    '.yml': 'YAML', '.toml': 'TOML', '.tf': 'Terraform',
}


def _get_language(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return EXT_LANG_MAP.get(ext)


def _get_project_name(file_path: str) -> Optional[str]:
    """Walk up the directory tree to find the project root (contains .git or pyproject.toml)."""
    p = Path(file_path).parent
    for _ in range(6):  # max 6 levels up
        markers = ['.git', 'pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod']
        for marker in markers:
            if (p / marker).exists():
                return p.name
        if p.parent == p:
            break
        p = p.parent
    return None


class FileEditWatcher:
    """
    Watches project root directories for file modification events.
    Records each edit to the FileEdit table.
    Also tracks time-in-file by watching the active window title.
    """

    def __init__(self, roots=None, interval: float = 5.0):
        from config.settings import settings
        self.roots = roots or settings.project_roots
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name='FileEditWatcher')
        self._recent_files: dict[str, float] = {}  # path → last seen timestamp

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name='FileEditWatcher')
            self._thread.start()
        log.info('FileEditWatcher started, watching: %s', self.roots)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3.0)

    def get_recent_files(self, limit: int = 20) -> list[dict]:
        """Return recently edited files from DB (for API)."""
        try:
            from database.session import SessionLocal
            from database.models import FileEdit
            session = SessionLocal()
            try:
                rows = (session.query(FileEdit)
                        .order_by(FileEdit.timestamp.desc())
                        .limit(limit).all())
                result = []
                seen = set()
                for r in rows:
                    key = r.file_path
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append({
                        'file': r.file_name,
                        'path': r.file_path,
                        'project': r.project,
                        'language': r.language,
                        'duration_sec': r.duration_sec,
                        'timestamp': r.timestamp.isoformat() if r.timestamp else None,
                        'date': r.session_date,
                    })
                return result
            finally:
                session.close()
        except Exception as e:
            log.warning('get_recent_files error: %s', e)
            return []

    def _run(self):
        """Poll active window every N seconds and record file edits."""
        from database.session import SessionLocal
        from database.models import FileEdit
        from tracker.active_window import get_active_window
        import re

        session = SessionLocal()
        _file_durations: dict[str, float] = {}  # path → accumulated seconds

        try:
            while not self._stop.is_set():
                try:
                    proc, title = get_active_window()
                    if title:
                        file_path = self._extract_file_from_title(proc or '', title)
                        if file_path:
                            _file_durations[file_path] = _file_durations.get(file_path, 0) + self.interval
                            # Write to DB every 30 seconds per file to reduce churn
                            if _file_durations[file_path] % 30 < self.interval + 1:
                                self._record_edit(session, file_path, _file_durations[file_path])
                except Exception as e:
                    log.debug('FileEditWatcher tick error: %s', e)
                time.sleep(self.interval)
        except Exception as e:
            log.exception('FileEditWatcher error: %s', e)
        finally:
            session.close()

    def _extract_file_from_title(self, proc: str, title: str) -> Optional[str]:
        """Extract file path from IDE window title."""
        import re
        proc_lower = proc.lower()
        if not title:
            return None

        # VS Code: "filename.py — project — Visual Studio Code"
        # Cursor: "filename.py — project — Cursor"
        # PyCharm: "filename.py [project] — PyCharm"
        # Notepad++: "filename.py - Notepad++"

        ide_procs = ('code', 'pycharm', 'cursor', 'idea', 'webstorm', 'rider',
                     'vim', 'nvim', 'sublime', 'notepad', 'atom')
        if not any(k in proc_lower for k in ide_procs):
            return None

        # Normalize dashes
        nt = title.replace('\u2014', ' - ').replace('\u2013', ' - ')
        parts = [p.strip() for p in nt.split(' - ') if p.strip()]
        if not parts:
            return None

        # First part is usually the filename
        candidate = parts[0].strip().lstrip('● ')  # remove "●" dirty indicator in VS Code
        if not candidate:
            return None

        # Must have a tracked extension
        ext = Path(candidate).suffix.lower()
        if ext not in TRACKED_EXTENSIONS:
            return None

        return candidate  # just the filename — we store it as-is

    def _record_edit(self, session, file_name: str, duration: float):
        """Upsert a FileEdit record."""
        try:
            from database.models import FileEdit
            today = datetime.now().strftime('%Y-%m-%d')
            # Find existing record for same file+date
            existing = (session.query(FileEdit)
                        .filter(FileEdit.file_name == file_name,
                                FileEdit.session_date == today)
                        .first())
            if existing:
                existing.duration_sec = duration
                existing.timestamp = datetime.now()
            else:
                lang = _get_language(file_name)
                fe = FileEdit(
                    file_path=file_name,
                    file_name=file_name,
                    project=None,
                    language=lang,
                    duration_sec=duration,
                    event_type='modified',
                    session_date=today,
                )
                session.add(fe)
            session.commit()
        except Exception as e:
            log.debug('FileEdit record error: %s', e)
            try:
                session.rollback()
            except Exception:
                pass
