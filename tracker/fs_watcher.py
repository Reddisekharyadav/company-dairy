"""Reliable filesystem change detection using watchdog.

Why this module exists
----------------------
The previous watcher inferred file changes from the *focused window title*.
That cannot see background coding agents, terminal tools, CI scripts, or files
changed while another window is focused.  This watcher observes real
filesystem events for every configured development root.

Important semantics
-------------------
* A filesystem event proves that a file changed.  It does **not** prove that a
  human typed the change.  ``FileChangeEvent`` is therefore separate from the
  estimated focused-window duration stored in ``FileEdit``.
* The responsible application is only recorded when there is reliable
  evidence: a known editor window is focused and its title contains the exact
  file name.  Otherwise the application is ``None``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from config.settings import SESSION_ID, settings
from config.workspace_rules import UNCLASSIFIED, classifier, normalize_workspace, scope_workspaces
from tracker.file_types import (
    DEFAULT_IGNORED_DIRS,
    DEFAULT_IGNORED_PREFIXES,
    DEFAULT_IGNORED_SUFFIXES,
    detect_ide,
    get_language,
    is_tracked_file,
)

log = logging.getLogger("fs_watcher")

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver

    WATCHDOG_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when dependency missing
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    PollingObserver = None  # type: ignore
    WATCHDOG_AVAILABLE = False


@dataclass
class RootConfig:
    path: str
    workspace: str = "development"

    def normalized(self) -> "RootConfig":
        return RootConfig(os.path.abspath(os.path.expanduser(self.path)), normalize_workspace(self.workspace, "development"))


@dataclass
class _PendingEvent:
    file_path: str
    event_type: str
    old_path: Optional[str] = None
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    size: Optional[int] = None


class _ChangeHandler(FileSystemEventHandler):
    """Translate watchdog events into debounced FileChangeEvent rows."""

    def __init__(self, watcher: "FileSystemWatcher"):
        self.watcher = watcher

    def on_created(self, event):
        self.watcher._handle_event(event, "created")

    def on_modified(self, event):
        self.watcher._handle_event(event, "modified")

    def on_deleted(self, event):
        self.watcher._handle_event(event, "deleted")

    def on_moved(self, event):
        self.watcher._handle_event(event, "moved")


class FileSystemWatcher:
    """Recursive, multi-root, debounced filesystem watcher."""

    def __init__(
        self,
        roots: Optional[Iterable[Any]] = None,
        *,
        debounce_seconds: float = 1.5,
        startup_grace: float = 1.0,
        tracked_extensions: Optional[Iterable[str]] = None,
        ignored_dirs: Optional[Iterable[str]] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        active_window_provider: Optional[Callable[[], tuple]] = None,
        workspace: str = "development",
    ):
        self._lock = threading.RLock()
        self._debounce = max(0.1, float(debounce_seconds))
        self._startup_grace = max(0.0, float(startup_grace))
        self._extra_extensions = {str(e).lower() for e in (tracked_extensions or [])}
        self._ignored_dirs = {str(d).lower() for d in (ignored_dirs or DEFAULT_IGNORED_DIRS)}
        self._session_factory = session_factory
        self._active_window_provider = active_window_provider
        self._default_workspace = normalize_workspace(workspace, "development")

        raw_roots = roots if roots is not None else settings.project_roots
        self._roots: List[RootConfig] = []
        self._observer = None
        self._handler = _ChangeHandler(self)
        self._running = False
        self._stop = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None
        self._pending: Dict[str, _PendingEvent] = {}
        self._recent_emits: Dict[str, float] = {}
        self._started_at = 0.0

        # Health/status counters.
        self._events_seen = 0
        self._events_recorded = 0
        self._events_ignored = 0
        self._events_deduplicated = 0
        self._write_errors = 0
        self._last_event_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._recent_errors: deque = deque(maxlen=20)

        self.set_roots(raw_roots or [])

    # ── root management ──────────────────────────────────────────────────
    def _coerce_root(self, item: Any) -> RootConfig:
        if isinstance(item, dict):
            return RootConfig(str(item.get("path", "")), normalize_workspace(item.get("workspace"), self._default_workspace))
        return RootConfig(str(item), self._default_workspace)

    def set_roots(self, roots: Iterable[Any]) -> None:
        """Replace the root list, removing duplicates/nested overlap.

        Overlapping roots are collapsed to the parent directory so the same
        file cannot be recorded twice by two observers.
        """
        configs: List[RootConfig] = []
        seen = set()
        for item in roots or []:
            cfg = self._coerce_root(item).normalized()
            if not cfg.path or cfg.path in seen:
                continue
            seen.add(cfg.path)
            configs.append(cfg)

        # Shortest first, then keep roots that are not inside an already kept
        # root.  Processing parents first ensures nested roots are collapsed
        # onto the parent instead of keeping both.
        configs.sort(key=lambda c: len(c.path))
        kept: List[RootConfig] = []
        for cfg in configs:
            nested = False
            for parent in kept:
                try:
                    if os.path.commonpath([cfg.path, parent.path]) == os.path.normpath(parent.path):
                        log.info("Watcher root %s is inside %s; using the parent only.", cfg.path, parent.path)
                        nested = True
                        break
                except Exception:
                    continue
            if not nested:
                kept.append(cfg)

        with self._lock:
            self._roots = sorted(kept, key=lambda c: c.path.lower())
            # Ensure path classification treats these as development roots.
            try:
                classifier.update_rules({"development_roots": [c.path for c in self._roots]})
            except Exception:
                pass

        if self._running:
            self._restart_observer()

    def add_root(self, path: str, workspace: str = "development") -> None:
        roots = [{"path": c.path, "workspace": c.workspace} for c in self._roots]
        roots.append({"path": path, "workspace": workspace})
        self.set_roots(roots)

    def remove_root(self, path: str) -> None:
        target = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
        roots = [
            {"path": c.path, "workspace": c.workspace}
            for c in self._roots
            if os.path.normcase(c.path) != target
        ]
        self.set_roots(roots)

    def get_roots(self) -> List[Dict[str, Any]]:
        with self._lock:
            result = []
            for cfg in self._roots:
                exists = os.path.isdir(cfg.path)
                result.append({
                    "path": cfg.path,
                    "workspace": cfg.workspace,
                    "exists": exists,
                    "accessible": exists and os.access(cfg.path, os.R_OK),
                })
            return result

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Idempotent start.  Returns True when an observer is running."""
        with self._lock:
            if self._running:
                return True
            if not WATCHDOG_AVAILABLE:
                self._record_error("watchdog is not installed; filesystem tracking is disabled")
                return False
            if not self._roots:
                self._record_error("No development roots configured")
                return False

            observer_cls = Observer
            # watchdog 7's Windows emitter batches events until its queue is
            # quiet for ``timeout`` seconds.  The default is 1s; under a
            # continuous build/agent write stream that can delay events
            # indefinitely.  A short drain window keeps latency bounded while
            # still coalescing bursts before they reach our debounce queue.
            try:
                self._observer = observer_cls(timeout=0.1)
            except TypeError:  # pragma: no cover - older watchdog fallback
                self._observer = observer_cls()
            scheduled = 0
            for cfg in self._roots:
                if not os.path.isdir(cfg.path):
                    self._record_error(f"Root does not exist: {cfg.path}")
                    continue
                if not os.access(cfg.path, os.R_OK):
                    self._record_error(f"Root is not readable: {cfg.path}")
                    continue
                try:
                    self._observer.schedule(self._handler, cfg.path, recursive=True)
                    scheduled += 1
                except Exception as exc:
                    self._record_error(f"Could not watch {cfg.path}: {exc}")

            if scheduled == 0:
                self._record_error("No accessible roots could be watched")
                self._observer = None
                return False

            self._stop.clear()
            try:
                self._observer.start()
            except Exception as exc:
                self._record_error(f"Could not start filesystem observer: {exc}")
                self._observer = None
                return False

            self._running = True
            self._started_at = time.monotonic()
            self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True, name="FileSystemWatcherFlush")
            self._flush_thread.start()
            log.info("FileSystemWatcher started with %d root(s).", scheduled)
            return True

    def stop(self) -> None:
        """Idempotent stop with a final best-effort flush."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop.set()
            observer = self._observer
            self._observer = None

        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5.0)
            except Exception as exc:
                self._record_error(f"Observer stop error: {exc}")
        flush_thread = self._flush_thread
        if flush_thread is not None and flush_thread.is_alive():
            flush_thread.join(timeout=3.0)
        self._flush_pending(force=True)
        log.info("FileSystemWatcher stopped.")

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def _restart_observer(self) -> None:
        was_running = self._running
        if was_running:
            self.stop()
            self.start()

    # ── event handling ───────────────────────────────────────────────────
    def _handle_event(self, event, event_type: str) -> None:
        try:
            if getattr(event, "is_directory", False):
                self._events_ignored += 1
                return
            self._events_seen += 1
            path = str(getattr(event, "dest_path", "") or getattr(event, "src_path", "") or "")
            old_path = str(getattr(event, "src_path", "") or "") if event_type == "moved" else None
            if not path or not self._is_relevant(path):
                self._events_ignored += 1
                return

            now = time.monotonic()
            key = os.path.normcase(os.path.abspath(path))
            with self._lock:
                pending = self._pending.get(key)
                if pending is None:
                    self._pending[key] = _PendingEvent(
                        file_path=os.path.abspath(path),
                        event_type=event_type,
                        old_path=os.path.abspath(old_path) if old_path else None,
                        size=self._safe_size(path),
                    )
                else:
                    # Coalesce create+modify into create, and modify+delete into delete.
                    if pending.event_type == "created" and event_type == "modified":
                        pending.event_type = "created"
                    elif event_type == "deleted":
                        pending.event_type = "deleted"
                    elif pending.event_type == "deleted" and event_type in ("created", "modified"):
                        pending.event_type = "modified"
                    elif event_type == "moved":
                        pending.event_type = "moved"
                        pending.old_path = os.path.abspath(old_path) if old_path else pending.old_path
                    pending.last_seen = now
                    pending.size = self._safe_size(path)
        except Exception as exc:
            self._record_error(f"Filesystem event handling failed: {exc}")

    def _is_relevant(self, path: str) -> bool:
        path = os.path.abspath(path)
        name = os.path.basename(path).lower()
        if not name:
            return False
        if any(name.startswith(prefix) for prefix in DEFAULT_IGNORED_PREFIXES):
            return False
        if any(name.endswith(suffix) for suffix in DEFAULT_IGNORED_SUFFIXES):
            return False
        # Only ignore directories *inside* a configured root.  A legitimate
        # project may itself live under a parent folder named "temp" or "build";
        # the absolute path's ancestors must not disqualify every event.
        root = self._root_for(path)
        try:
            relative = os.path.relpath(path, root.path) if root else path
        except ValueError:
            relative = path
        parts = {part.lower() for part in Path(relative).parts[:-1]}
        if parts.intersection(self._ignored_dirs):
            return False
        if not is_tracked_file(path, self._extra_extensions):
            return False
        # Never record the application's own SQLite/WAL files if a root happens
        # to contain them.
        if name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")):
            return False
        return True

    @staticmethod
    def _safe_size(path: str) -> Optional[int]:
        try:
            return os.path.getsize(path)
        except Exception:
            return None

    def _flush_loop(self) -> None:
        while not self._stop.wait(0.2):
            self._flush_pending(force=False)

    def _flush_pending(self, force: bool = False) -> None:
        now = time.monotonic()
        ready: List[_PendingEvent] = []
        with self._lock:
            for key, pending in list(self._pending.items()):
                if force or (now - pending.last_seen) >= self._debounce:
                    ready.append(self._pending.pop(key))
        for pending in ready:
            self._persist(pending)

    def _persist(self, pending: _PendingEvent) -> None:
        try:
            from database.models import FileChangeEvent

            root_cfg = self._root_for(pending.file_path)
            workspace = root_cfg.workspace if root_cfg else self._default_workspace
            if workspace == UNCLASSIFIED:
                workspace = classifier.classify_path(pending.file_path)
            relative_path = None
            project = None
            project_root = root_cfg.path if root_cfg else None
            if root_cfg:
                try:
                    relative_path = os.path.relpath(pending.file_path, root_cfg.path)
                except Exception:
                    relative_path = None
                project = self._project_name(pending.file_path, root_cfg.path)

            application, attribution = self._attribute_application(pending.file_path)
            language = get_language(pending.file_path)
            extension = Path(pending.file_path).suffix.lower() or None
            now = datetime.now()

            # Avoid identical duplicate rows produced by overlapping events.
            dedupe_key = f"{pending.event_type}:{os.path.normcase(pending.file_path)}"
            last = self._recent_emits.get(dedupe_key)
            if last is not None and (time.monotonic() - last) < self._debounce:
                self._events_deduplicated += 1
                return
            self._recent_emits[dedupe_key] = time.monotonic()
            # Bound dedupe memory on long-running installs.  Entries are only
            # consulted for one debounce window, so retaining a small multiple
            # of that window is sufficient even during a very large burst.
            if len(self._recent_emits) > 1000:
                cutoff = time.monotonic() - max(2.0, self._debounce * 2)
                for old_key, seen_at in list(self._recent_emits.items()):
                    if seen_at < cutoff:
                        self._recent_emits.pop(old_key, None)

            session_factory = self._session_factory
            if session_factory is None:
                from database.session import SessionLocal
                session_factory = SessionLocal
            session = session_factory()
            try:
                row = FileChangeEvent(
                    timestamp=now,
                    session_id=SESSION_ID,
                    workspace=workspace,
                    event_type=pending.event_type,
                    source="filesystem",
                    attribution=attribution,
                    application=application,
                    project_root=project_root,
                    project=project,
                    file_path=pending.file_path,
                    relative_path=relative_path,
                    file_name=os.path.basename(pending.file_path),
                    extension=extension,
                    language=language,
                    file_size=pending.size,
                    old_path=pending.old_path,
                    session_date=now.strftime("%Y-%m-%d"),
                )
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            self._events_recorded += 1
            self._last_event_at = now
            log.debug(
                "File change: %s %s project=%s workspace=%s",
                pending.event_type,
                relative_path or os.path.basename(pending.file_path),
                project,
                workspace,
            )
        except Exception as exc:
            self._write_errors += 1
            self._record_error(f"Could not persist file change: {exc}")
            log.warning("Could not persist filesystem event: %s", exc)

    def _root_for(self, file_path: str) -> Optional[RootConfig]:
        candidate = os.path.normcase(os.path.abspath(file_path))
        best = None
        for cfg in self._roots:
            root = os.path.normcase(os.path.abspath(cfg.path))
            if candidate == root or candidate.startswith(root + os.sep):
                if best is None or len(root) > len(best.path):
                    best = cfg
        return best

    def _project_name(self, file_path: str, root: str) -> str:
        """Find the nearest repository/project marker, else use the root name."""
        try:
            current = Path(file_path).parent
            root_path = Path(root)
            markers = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle")
            for _ in range(8):
                if any((current / marker).exists() for marker in markers):
                    return current.name
                if current == root_path or current.parent == current:
                    break
                current = current.parent
        except Exception:
            pass
        return Path(root).name or root

    def _attribute_application(self, file_path: str) -> tuple[Optional[str], Optional[str]]:
        """Record an application only with reliable focused-window evidence."""
        if self._active_window_provider is None:
            return None, None
        try:
            proc, title = self._active_window_provider()
            editor = detect_ide(proc or "")
            if not editor or not title:
                return None, None
            basename = os.path.basename(file_path)
            if basename and basename.lower() in (title or "").lower():
                return editor, "focused_window"
        except Exception:
            return None, None
        return None, None

    def _record_error(self, message: str) -> None:
        self._last_error = message
        self._recent_errors.appendleft({"time": datetime.now().isoformat(), "message": message})
        log.warning("FileSystemWatcher: %s", message)

    # ── status ───────────────────────────────────────────────────────────
    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "available": WATCHDOG_AVAILABLE,
                "running": self._running,
                "roots": self.get_roots(),
                "last_event_at": self._last_event_at.isoformat() if self._last_event_at else None,
                "events_seen": self._events_seen,
                "events_recorded": self._events_recorded,
                "events_ignored": self._events_ignored,
                "events_deduplicated": self._events_deduplicated,
                "write_errors": self._write_errors,
                "last_error": self._last_error,
                "recent_errors": list(self._recent_errors),
                "debounce_seconds": self._debounce,
            }


watcher = FileSystemWatcher()

__all__ = ["FileSystemWatcher", "watcher", "WATCHDOG_AVAILABLE"]
