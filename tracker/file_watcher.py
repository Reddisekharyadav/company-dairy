"""Estimated focused-window file time.

This module complements :mod:`tracker.fs_watcher`.

* ``fs_watcher`` observes **real filesystem changes** (including background
  agents, terminals and CI tools) and writes :class:`database.models.FileChangeEvent`.
* this module estimates how long an editor window with a file open stayed
  focused and writes :class:`database.models.FileEdit` with
  ``source='active_window_inference'``.

A focused editor window is **not** proof that a human typed.  The duration is an
estimate; callers must label it as such.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import SESSION_ID, settings
from config.workspace_rules import classifier, normalize_workspace
from tracker.file_types import (
    EXT_LANG_MAP,
    IDE_PROCESS_MAP,
    TRACKED_EXTENSIONS,
    detect_ide,
    get_language,
)

log = logging.getLogger("file_watcher")

# Backwards-compatible aliases (older integrations imported these names).
__all__ = [
    "TRACKED_EXTENSIONS",
    "EXT_LANG_MAP",
    "IDE_PROCESS_MAP",
    "FileEditWatcher",
]


def _get_language(file_path: str) -> Optional[str]:
    return get_language(file_path)


def _get_project_root(file_path: str) -> Optional[str]:
    """Return the nearest project root containing a known marker."""
    try:
        current = Path(file_path).resolve().parent
    except Exception:
        current = Path(file_path).parent
    markers = (
        ".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
        "pom.xml", "build.gradle", "requirements.txt", "setup.py",
    )
    for _ in range(10):
        try:
            if any((current / marker).exists() for marker in markers):
                return str(current)
        except OSError:
            pass
        if current.parent == current:
            break
        current = current.parent
    return None


def _get_project_name(file_path: str) -> Optional[str]:
    root = _get_project_root(file_path)
    if root:
        return Path(root).name
    return None


def _detect_ide(proc_name: str) -> Optional[str]:
    return detect_ide(proc_name)


class FileEditWatcher:
    """Poll the active window and estimate focused time per open file."""

    def __init__(self, roots=None, interval: float = 5.0, workspace: Optional[str] = None):
        self.roots = [str(r) for r in (roots or settings.project_roots) if str(r).strip()]
        self.interval = max(1.0, float(interval))
        self.workspace = normalize_workspace(workspace, "development") if workspace else None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="FileEditWatcher")
        self._file_durations: dict[str, float] = {}
        self._file_editors: dict[str, str] = {}
        self._last_error: Optional[str] = None
        self._running = False

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> bool:
        self._stop.clear()
        with self._lock:
            if self._thread.is_alive():
                self._running = True
                return True
            self._thread = threading.Thread(target=self._run, daemon=True, name="FileEditWatcher")
            self._thread.start()
            self._running = True
        log.info("FileEditWatcher started, watching: %s", self.roots)
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread.is_alive():
            thread.join(timeout=3.0)
        self._running = False

    def set_roots(self, roots) -> None:
        self.roots = [str(r) for r in (roots or []) if str(r).strip()]

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "roots": list(self.roots),
            "interval_seconds": self.interval,
            "last_error": self._last_error,
            "source": "active_window_inference",
        }

    # ── queries ──────────────────────────────────────────────────────────
    def get_recent_files(self, limit: int = 20, workspace: Optional[str] = None) -> list[dict]:
        """Return recent inferred file durations from the database."""
        try:
            from database.models import FileEdit
            from database.session import SessionLocal

            session = SessionLocal()
            try:
                query = session.query(FileEdit)
                if workspace:
                    query = query.filter(FileEdit.workspace == normalize_workspace(workspace))
                rows = query.order_by(FileEdit.timestamp.desc()).limit(max(1, min(limit, 500))).all()
                result = []
                seen = set()
                for row in rows:
                    key = row.file_path or row.file_name
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append({
                        "file": row.file_name,
                        "path": row.file_path,
                        "project": row.project,
                        "language": row.language,
                        "duration_sec": row.duration_sec,
                        "editor": row.editor,
                        "workspace": row.workspace or "unclassified",
                        "source": row.source or "active_window_inference",
                        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                        "date": row.session_date,
                    })
                return result
            finally:
                session.close()
        except Exception as exc:
            log.warning("get_recent_files error: %s", exc)
            return []

    # ── worker ───────────────────────────────────────────────────────────
    def _run(self) -> None:
        from database.models import FileEdit
        from database.session import SessionLocal
        from tracker.active_window import get_active_window

        session = SessionLocal()
        last_flush: dict[str, float] = {}
        try:
            while not self._stop.is_set():
                try:
                    proc, title = get_active_window()
                    if title:
                        info = self._extract_file_info(proc or "", title)
                        if info:
                            file_path, project, editor = info
                            with self._lock:
                                self._file_durations[file_path] = (
                                    self._file_durations.get(file_path, 0.0) + self.interval
                                )
                                duration = self._file_durations[file_path]
                                if editor:
                                    self._file_editors[file_path] = editor
                            # Flush every 30 seconds of accumulated focused time.
                            if duration - last_flush.get(file_path, 0.0) >= 30.0:
                                last_flush[file_path] = duration
                                self._record_edit(
                                    session,
                                    file_path,
                                    duration,
                                    project=project,
                                    editor=self._file_editors.get(file_path),
                                    proc=proc or "",
                                    title=title,
                                )
                except Exception as exc:  # pragma: no cover - defensive
                    self._last_error = str(exc)
                    log.debug("FileEditWatcher tick error: %s", exc)
                self._stop.wait(self.interval)
        except Exception as exc:  # pragma: no cover - defensive
            self._last_error = str(exc)
            log.exception("FileEditWatcher error: %s", exc)
        finally:
            session.close()

    def _resolve_project_file(
        self, candidate: str, project_hint: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """Resolve an editor title's file name against configured project roots.

        Editor titles usually contain only a base name (``app.py``), so the
        absolute path and the real project folder must be recovered from the
        watched roots.  Without this, FileEdit rows are stored as ``app.py``
        with the editor name as the project.

        If the same base name exists in more than one watched root and the title
        does not identify the project, the path is intentionally left
        unresolved instead of guessing the first root.  A wrong project is worse
        than an explicitly ambiguous row.
        """
        if os.path.isabs(candidate):
            try:
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate), _get_project_name(candidate)
            except OSError:
                pass
            return candidate, None

        matches: list[tuple[str, str]] = []
        for root in self.roots:
            try:
                root_path = Path(root).resolve()
                possible = (root_path / candidate).resolve()
                if possible.is_file() and possible.is_relative_to(root_path):
                    matches.append((str(possible), root_path.name))
            except (OSError, ValueError):
                continue

        if not matches:
            return candidate, None

        if project_hint:
            hint = project_hint.strip().lower()
            preferred = [
                match for match in matches
                if match[1].lower() == hint
                or Path(match[0]).parent.name.lower() == hint
            ]
            if len(preferred) == 1:
                return preferred[0]

        if len(matches) == 1:
            return matches[0]
        return candidate, None

    def _project_root_for_path(self, file_path: str) -> Optional[str]:
        if not os.path.isabs(file_path):
            return None
        try:
            resolved = Path(file_path).resolve()
        except OSError:
            return None
        for root in self.roots:
            try:
                root_path = Path(root).resolve()
                if resolved.is_relative_to(root_path):
                    return str(root_path)
            except (OSError, ValueError):
                continue
        return _get_project_root(file_path)

    def _extract_file_info(self, proc: str, title: str) -> Optional[tuple[str, Optional[str], Optional[str]]]:
        """Extract ``(file_path, project, ide)`` from an IDE window title."""
        if not title:
            return None
        title_lower = title.lower()

        editor = _detect_ide(proc)
        if not editor:
            for key, name in IDE_PROCESS_MAP.items():
                if len(key) >= 5 and key in title_lower:
                    editor = name
                    break
        if not editor:
            return None

        normalised = title.replace("\u2014", " - ").replace("\u2013", " - ")
        parts = [part.strip() for part in normalised.split(" - ") if part.strip()]
        if not parts:
            return None

        # Editors differ in title order:
        #   VS Code / Cursor:  app.py - project - Editor
        #   Antigravity:       Antigravity IDE - app.py
        # Pick the first part with a tracked file extension rather than always
        # assuming parts[0] is the file.
        candidate = None
        for part in parts:
            cleaned = part.lstrip("\u25cf ").strip().strip("*").strip()
            if cleaned and Path(cleaned).suffix.lower() in TRACKED_EXTENSIONS:
                candidate = cleaned
                break
        if not candidate:
            return None

        ide_names = {name.lower() for name in IDE_PROCESS_MAP.values()}
        ide_names.update(key.lower() for key in IDE_PROCESS_MAP)
        project: Optional[str] = None
        for part in parts:
            if part == candidate:
                continue
            if part.lower() in ide_names:
                continue
            project = part
            break

        file_path, resolved_project = self._resolve_project_file(candidate, project)
        if resolved_project:
            project = resolved_project
        elif os.path.isabs(file_path):
            project = _get_project_name(file_path) or project
        return file_path, project, editor

    def _record_edit(
        self,
        session,
        file_path: str,
        duration: float,
        project: Optional[str] = None,
        editor: Optional[str] = None,
        proc: str = "",
        title: str = "",
    ) -> None:
        """Upsert the inferred duration for ``file_path`` for today."""
        from database.models import FileEdit

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            workspace = self.workspace or classifier.classify_application(
                process=proc, title=title, path=file_path
            )
            if workspace == "unclassified" and project:
                # A resolved project root is reliable development evidence.
                workspace = "development"

            project_root = self._project_root_for_path(file_path)
            relative_path = None
            if project_root:
                try:
                    relative_path = os.path.relpath(file_path, project_root)
                except Exception:
                    relative_path = None

            existing = (
                session.query(FileEdit)
                .filter(
                    FileEdit.session_date == today,
                    FileEdit.file_path == file_path,
                )
                .first()
            )
            if existing:
                existing.duration_sec = duration
                existing.timestamp = datetime.now()
                existing.workspace = workspace
                if project:
                    existing.project = project
                if editor:
                    existing.editor = editor
                if project_root:
                    existing.project_root = project_root
                if relative_path:
                    existing.relative_path = relative_path
            else:
                session.add(FileEdit(
                    session_id=SESSION_ID,
                    file_path=file_path,
                    file_name=os.path.basename(file_path),
                    project=project,
                    language=get_language(file_path),
                    duration_sec=duration,
                    event_type="modified",
                    session_date=today,
                    editor=editor,
                    workspace=workspace,
                    source="active_window_inference",
                    project_root=project_root,
                    relative_path=relative_path,
                    extension=Path(file_path).suffix.lower() or None,
                ))
            session.commit()
        except Exception as exc:
            self._last_error = str(exc)
            log.debug("FileEdit record error: %s", exc)
            try:
                session.rollback()
            except Exception:
                pass
