"""Scan project roots for git repositories and record new commits to DB."""
from threading import Thread, Event
import time
import os
from database.session import SessionLocal
from database.models import GitActivity
from datetime import datetime
import logging
from config.settings import settings

try:
    from git import Repo, InvalidGitRepositoryError, NoSuchPathError
    GIT_AVAILABLE = True
except Exception:
    GIT_AVAILABLE = False

log = logging.getLogger('git_watcher')


class GitWatcher:
    def __init__(self, interval: int = 30, roots=None):
        self.interval = interval
        self.roots = roots or settings.project_roots
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _discover_repos(self):
        repos = set()
        for root in self.roots:
            if not os.path.exists(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                if '.git' in dirnames:
                    repos.add(dirpath)
                    # don't recurse into nested deeper repos
                    dirnames[:] = []
        return list(repos)

    def _run(self):
        if not GIT_AVAILABLE:
            log.warning('Git executable not found. Git activity tracking will be disabled.')
            return

        session = SessionLocal()
        try:
            while not self._stop.is_set():
                repos = self._discover_repos()
                for repo_path in repos:
                    try:
                        repo = Repo(repo_path)
                        head = repo.head.commit
                        commit_hash = head.hexsha
                        # check last stored commit for this repo
                        last = session.query(GitActivity).filter(GitActivity.repo == repo_path).order_by(GitActivity.timestamp.desc()).first()
                        if not last or last.commit_hash != commit_hash:
                            ga = GitActivity(repo=repo_path, commit_hash=commit_hash, message=head.message, author=str(head.author), timestamp=datetime.fromtimestamp(head.committed_date))
                            session.add(ga)
                            session.commit()
                            log.info('Recorded new git commit %s in %s', commit_hash[:8], repo_path)
                    except (InvalidGitRepositoryError, NoSuchPathError) as e:
                        continue
                    except Exception as e:
                        log.exception('GitWatcher error: %s', e)
                time.sleep(self.interval)
        finally:
            session.close()
