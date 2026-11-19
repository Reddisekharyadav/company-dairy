"""Scan project roots for git repositories and record commits.

A commit is recorded once per repository+hash.  When several commits happened
between polls (for example while an agent worked in a repository) all of them
are stored, not only ``HEAD``.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from threading import Event, Thread

from config.settings import SESSION_ID, settings
from config.workspace_rules import classifier

try:
    from git import Repo, InvalidGitRepositoryError, NoSuchPathError
    GIT_AVAILABLE = True
except Exception:  # pragma: no cover
    GIT_AVAILABLE = False

log = logging.getLogger("git_watcher")

MAX_COMMITS_PER_POLL = 50
_IGNORED_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    "target", ".idea", ".vscode", "Library", "Temp", "tmp",
}


class GitWatcher:
    def __init__(self, interval: int = 30, roots=None):
        self.interval = max(5, int(interval))
        self.roots = [str(r) for r in (roots or settings.project_roots) if str(r).strip()]
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True, name="GitWatcher")
        self._running = False
        self._last_error = None

    def start(self) -> bool:
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True, name="GitWatcher")
            self._thread.start()
        self._running = True
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._running = False

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "roots": list(self.roots),
            "interval_seconds": self.interval,
            "git_available": GIT_AVAILABLE,
            "last_error": self._last_error,
        }

    def _discover_repos(self) -> list[str]:
        repos: list[str] = []
        seen = set()
        for root in self.roots:
            if not root or not os.path.isdir(root):
                continue
            for dirpath, dirnames, _filenames in os.walk(root):
                dirnames[:] = [
                    name for name in dirnames
                    if name.lower() not in _IGNORED_DIRS and not name.startswith(".")
                ]
                entries = _safe_listdir(dirpath) or []
                if ".git" in entries:
                    normalised = os.path.normcase(os.path.abspath(dirpath))
                    if normalised not in seen:
                        seen.add(normalised)
                        repos.append(dirpath)
                    dirnames[:] = []
                if len(repos) >= 200:
                    return repos
        return repos

    def _run(self) -> None:
        if not GIT_AVAILABLE:
            self._last_error = "GitPython is not installed; git tracking is disabled."
            log.warning(self._last_error)
            return

        from database.session import SessionLocal

        session = SessionLocal()
        try:
            while not self._stop.is_set():
                try:
                    for repo_path in self._discover_repos():
                        try:
                            self._record_new_commits(session, repo_path)
                        except (InvalidGitRepositoryError, NoSuchPathError):
                            continue
                        except Exception as exc:  # pragma: no cover - defensive
                            self._last_error = str(exc)
                            log.exception("GitWatcher error for %s: %s", repo_path, exc)
                finally:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                self._stop.wait(self.interval)
        finally:
            session.close()

    def _record_new_commits(self, session, repo_path: str) -> int:
        from database.models import GitActivity

        repo = Repo(repo_path)
        if repo.head.is_detached:
            branch = "detached"
        else:
            branch = repo.active_branch.name

        known_hashes = {
            row[0]
            for row in session.query(GitActivity.commit_hash)
            .filter(GitActivity.repo == repo_path, GitActivity.commit_hash.isnot(None))
            .all()
        }

        recorded = 0
        for commit in repo.iter_commits(max_count=MAX_COMMITS_PER_POLL):
            if commit.hexsha in known_hashes:
                break
            try:
                stats = commit.stats.total
            except Exception:
                stats = {}
            row = GitActivity(
                session_id=SESSION_ID,
                repo=repo_path,
                commit_hash=commit.hexsha,
                message=commit.message,
                author=str(commit.author),
                timestamp=datetime.fromtimestamp(commit.committed_date),
                workspace=classifier.classify_path(repo_path),
                branch=branch,
                files_changed=stats.get("files"),
                insertions=stats.get("insertions"),
                deletions=stats.get("deletions"),
                source="git_commit",
            )
            session.add(row)
            known_hashes.add(commit.hexsha)
            recorded += 1
            log.info("Recorded git commit %s in %s", commit.hexsha[:8], repo_path)

        if recorded:
            session.commit()
        return recorded

    def get_recent_commits(self, limit: int = 20, workspace: str | None = None) -> list[dict]:
        try:
            from database.models import GitActivity
            from database.session import SessionLocal

            session = SessionLocal()
            try:
                query = session.query(GitActivity)
                if workspace:
                    query = query.filter(GitActivity.workspace == workspace)
                rows = query.order_by(GitActivity.timestamp.desc()).limit(limit).all()
                return [
                    {
                        "repo": r.repo,
                        "hash": r.commit_hash,
                        "message": (r.message or "").split("\n")[0],
                        "author": r.author,
                        "branch": r.branch,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "files_changed": r.files_changed,
                        "insertions": r.insertions,
                        "deletions": r.deletions,
                        "workspace": r.workspace,
                    }
                    for r in rows
                ]
            finally:
                session.close()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("get_recent_commits error: %s", exc)
            return []


def _safe_listdir(path: str):
    try:
        return os.listdir(path)
    except Exception:
        return None
