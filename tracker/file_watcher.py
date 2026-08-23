"""
File Edit Watcher â€” tracks which files are being modified across project roots.
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

# Process name â†’ IDE display name
IDE_PROCESS_MAP = {
    'code': 'VS Code',
    'code.exe': 'VS Code',
    'code - insiders': 'VS Code Insiders',
    'cursor': 'Cursor',
    'cursor.exe': 'Cursor',
    'pycharm': 'PyCharm',
    'pycharm64.exe': 'PyCharm',
    'pycharm.exe': 'PyCharm',
    'idea': 'IntelliJ IDEA',
    'idea64.exe': 'IntelliJ IDEA',
    'idea.exe': 'IntelliJ IDEA',
    'webstorm': 'WebStorm',
    'webstorm64.exe': 'WebStorm',
    'rider': 'Rider',
    'rider64.exe': 'Rider',
    'clion': 'CLion',
    'clion64.exe': 'CLion',
    'goland': 'GoLand',
    'goland64.exe': 'GoLand',
    'rubymine': 'RubyMine',
    'rubymine64.exe': 'RubyMine',
    'phpstorm': 'PhpStorm',
    'phpstorm64.exe': 'PhpStorm',
    'sublime_text': 'Sublime Text',
    'sublime_text.exe': 'Sublime Text',
    'atom': 'Atom',
    'atom.exe': 'Atom',
    'notepad++': 'Notepad++',
    'notepad++.exe': 'Notepad++',
    'vim': 'Vim',
    'nvim': 'Neovim',
    'nvim.exe': 'Neovim',
    'emacs': 'Emacs',
    'notepad': 'Notepad',
    'notepad.exe': 'Notepad',
    'antigravity': 'Antigravity IDE',
    'antigravity.exe': 'Antigravity IDE',
    'fleet': 'Fleet',
    'fleet.exe': 'Fleet',
    'zed': 'Zed',
    'zed.exe': 'Zed',
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


def _detect_ide(proc_name: str) -> Optional[str]:
    """Map a process name to a human-readable IDE name."""
    if not proc_name:
        return None
    proc_lower = proc_name.lower().strip()
    # Direct match
    if proc_lower in IDE_PROCESS_MAP:
        return IDE_PROCESS_MAP[proc_lower]
    # Partial match for common patterns
    for key, name in IDE_PROCESS_MAP.items():
        if key in proc_lower:
            return name
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
        self._recent_files: dict[str, float] = {}  # path â†’ last seen timestamp

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
                        'editor': r.editor,
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
        _file_durations: dict[str, float] = {}  # path â†’ accumulated seconds
        _file_editors: dict[str, str] = {}  # path â†’ IDE name

        try:
            while not self._stop.is_set():
                try:
                    proc, title = get_active_window()
                    if title:
                        file_info = self._extract_file_info(proc or '', title)
                        if file_info:
                            file_path, project, editor = file_info
                            _file_durations[file_path] = _file_durations.get(file_path, 0) + self.interval
                            if editor:
                                _file_editors[file_path] = editor
                            # Write to DB every 30 seconds per file to reduce churn
                            if _file_durations[file_path] % 30 < self.interval + 1:
                                self._record_edit(
                                    session, file_path, _file_durations[file_path],
                                    project=project,
                                    editor=_file_editors.get(file_path),
                                )
                except Exception as e:
                    log.debug('FileEditWatcher tick error: %s', e)
                time.sleep(self.interval)
        except Exception as e:
            log.exception('FileEditWatcher error: %s', e)
        finally:
            session.close()

    def _extract_file_info(self, proc: str, title: str) -> Optional[tuple[str, Optional[str], Optional[str]]]:
        """
        Extract (file_path, project_name, ide_name) from IDE window title.
        Returns None if not an IDE or no file detected.
        """
        import re
        proc_lower = proc.lower()
        if not title:
            return None

        # Check if this is an IDE process
        ide_procs = ('code', 'pycharm', 'cursor', 'idea', 'webstorm', 'rider',
                     'vim', 'nvim', 'sublime', 'notepad', 'atom', 'clion',
                     'goland', 'rubymine', 'phpstorm', 'fleet', 'zed',
                     'antigravity', 'emacs')
        if not any(k in proc_lower for k in ide_procs):
            return None

        # Detect IDE name
        editor = _detect_ide(proc)

        # Normalize dashes
        nt = title.replace('\u2014', ' - ').replace('\u2013', ' - ')
        parts = [p.strip() for p in nt.split(' - ') if p.strip()]
        if not parts:
            return None

        # First part is usually the filename
        candidate = parts[0].strip().lstrip('â— ')  # remove "â—" dirty indicator in VS Code
        if not candidate:
            return None

        # Must have a tracked extension
        ext = Path(candidate).suffix.lower()
        if ext not in TRACKED_EXTENSIONS:
            return None

        file_name = candidate

        # Try to extract project from title parts
        # VS Code: "filename.py â€” project_folder â€” Visual Studio Code"
        # PyCharm: "filename.py [project] â€” PyCharm"
        project = None
        if len(parts) >= 2:
            # Second part is usually the project/folder name
            project_candidate = parts[1].strip()
            # Skip if it's the IDE name itself
            ide_names = ['Visual Studio Code', 'VS Code', 'PyCharm', 'Cursor',
                         'IntelliJ IDEA', 'WebStorm', 'Sublime Text', 'Notepad++',
                         'Antigravity']
            if project_candidate not in ide_names:
                project = project_candidate

        # Try to build a full file path if project folder is available
        # If the title gives us the full path, use it
        file_path = file_name
        if project and len(parts) >= 3:
            # VS Code format: "file â€” folder â€” IDE"
            # The folder might be a full path on some systems
            folder = parts[1].strip()
            possible_path = os.path.join(folder, file_name)
            if os.path.isfile(possible_path):
                file_path = os.path.abspath(possible_path)

        return (file_path, project, editor)

    def _record_edit(self, session, file_name: str, duration: float,
                     project: Optional[str] = None, editor: Optional[str] = None):
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
                if project and not existing.project:
                    existing.project = project
                if editor and not existing.editor:
                    existing.editor = editor
            else:
                lang = _get_language(file_name)
                fe = FileEdit(
                    file_path=file_name,
                    file_name=file_name,
                    project=project,
                    language=lang,
                    duration_sec=duration,
                    event_type='modified',
                    session_date=today,
                    editor=editor,
                )
                session.add(fe)
            session.commit()
        except Exception as e:
            log.debug('FileEdit record error: %s', e)
            try:
                session.rollback()
            except Exception:
                pass

