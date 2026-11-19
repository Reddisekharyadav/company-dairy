"""
Workspace classification rules.

WorkSense is a local-first tracker.  A record may belong to exactly one of the
four canonical workspaces below.  Classification must be conservative: when the
available evidence is not strong enough the record is marked ``unclassified``
rather than guessed.

Rules are persisted in ``%APPDATA%/WorkSense/workspace_rules.json`` so users can
edit application/domain/path rules without touching code.  Nothing here ever
stores document contents, message bodies, or credentials.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("workspace_rules")

COMPANY = "company"
DEVELOPMENT = "development"
PERSONAL = "personal"
UNCLASSIFIED = "unclassified"

WORKSPACES = (COMPANY, DEVELOPMENT, PERSONAL, UNCLASSIFIED)
# ``combined`` is a *scope*, not a stored workspace.  It only ever contains
# company + development and must be requested explicitly.
COMBINED = "combined"
VALID_SCOPES = WORKSPACES + (COMBINED, "all")


def normalize_workspace(value: Any, default: str = UNCLASSIFIED) -> str:
    """Return a canonical workspace value."""
    text = str(value or "").strip().lower()
    if text in WORKSPACES:
        return text
    return default


def normalize_scope(value: Any, default: str = COMPANY) -> str:
    """Validate a user-supplied workspace scope."""
    text = str(value or "").strip().lower()
    if text in VALID_SCOPES:
        return text
    return default


def scope_workspaces(scope: str) -> List[str]:
    """Expand a scope into concrete stored workspace values.

    ``combined`` intentionally excludes personal and unclassified records so a
    combined report cannot leak unrelated activity.
    """
    scope = normalize_scope(scope)
    if scope == COMBINED:
        return [COMPANY, DEVELOPMENT]
    if scope == "all":
        return list(WORKSPACES)
    return [scope]


def _app_data_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "WorkSense"


DEFAULT_RULES: Dict[str, Any] = {
    "company_apps": [
        "outlook", "outlook.exe", "ms-teams", "ms-teams.exe", "teams",
        "teams.exe", "msteams", "msteams.exe", "winword", "winword.exe",
        "excel", "excel.exe", "powerpnt", "powerpnt.exe", "onenote",
        "onenote.exe", "msaccess", "msaccess.exe", "visio", "visio.exe",
        "webex", "webex.exe", "ciscowebex", "ciscowebex.exe", "zoom",
        "zoom.exe", "slack", "slack.exe", "acroRd32", "acrord32.exe",
        "sharepoint", "sharepoint.exe",
    ],
    "development_apps": [
        "code", "code.exe", "code - insiders", "code-insiders",
        "cursor", "cursor.exe", "antigravity", "antigravity.exe",
        "pycharm", "pycharm.exe", "pycharm64.exe", "idea", "idea.exe",
        "idea64.exe", "webstorm", "webstorm.exe", "webstorm64.exe",
        "rider", "rider.exe", "rider64.exe", "clion", "clion.exe",
        "clion64.exe", "goland", "goland.exe", "goland64.exe",
        "rubymine", "rubymine.exe", "rubymine64.exe", "phpstorm",
        "phpstorm.exe", "phpstorm64.exe", "sublime_text",
        "sublime_text.exe", "atom", "atom.exe", "notepad++",
        "notepad++.exe", "vim", "nvim", "nvim.exe", "emacs",
        "emacs.exe", "zed", "zed.exe", "fleet", "fleet.exe",
    ],
    "personal_apps": [
        "spotify", "spotify.exe", "netflix", "netflix.exe", "discord",
        "discord.exe", "telegram", "telegram.exe", "whatsapp",
        "whatsapp.exe", "steam", "steam.exe", "epicgameslauncher",
        "epicgameslauncher.exe", "vlc", "vlc.exe", "tiktok", "tiktok.exe",
    ],
    "company_domains": [
        "outlook.office.com", "outlook.live.com", "teams.microsoft.com",
        "sharepoint.com", "office.com", "microsoft365.com",
        "jira.atlassian.com", "confluence.atlassian.com", "salesforce.com",
        "workday.com", "sap.com", "servicenow.com", "zoom.us",
        "meet.google.com", "calendar.google.com", "docs.google.com",
        "sheets.google.com", "drive.google.com", "slack.com",
    ],
    "development_domains": [
        "github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com",
        "stackexchange.com", "npmjs.com", "pypi.org", "dev.to",
        "leetcode.com", "hackerrank.com", "codepen.io", "replit.com",
        "codesandbox.io", "sourcegraph.com", "developer.mozilla.org",
        "docs.python.org", "docs.docker.com", "kubernetes.io",
        "rust-lang.org", "go.dev", "nodejs.org", "react.dev",
        "vuejs.org", "angular.io", "nextjs.org", "vercel.com",
        "netlify.com", "jetbrains.com", "cursor.com", "localhost",
        "127.0.0.1",
    ],
    "personal_domains": [
        "youtube.com", "netflix.com", "spotify.com", "reddit.com",
        "instagram.com", "facebook.com", "twitter.com", "x.com",
        "whatsapp.com", "web.whatsapp.com", "amazon.com", "amazon.in",
        "flipkart.com", "twitch.tv", "tiktok.com", "hotstar.com",
    ],
    "company_title_keywords": [
        "outlook", "microsoft teams", "teams meeting", "standup",
        "stand-up", "client call", "performance review", "payroll",
        "invoice", "purchase order", "sharepoint", "confluence",
        "salesforce", "workday",
    ],
    "development_title_keywords": [
        "visual studio code", "vs code", "cursor", "antigravity",
        "pycharm", "intellij", "webstorm", "pull request", "merge request",
        "stack overflow", "github", "gitlab", "debugger", "terminal",
    ],
    "personal_title_keywords": [
        "netflix", "youtube", "spotify", "instagram", "facebook",
        "whatsapp", "reddit", "tiktok", "twitch",
    ],
    # Project roots that should be treated as development work.  These are
    # kept in sync with Settings.project_roots by the settings API.
    "development_roots": [],
    "company_roots": [],
    "personal_roots": [],
    # Manual corrections take precedence over every rule below.
    "overrides": {
        "processes": {},
        "domains": {},
        "paths": {},
    },
}

_BROWSER_PROCESSES = (
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "safari",
)


def _normalize_process(process: str) -> str:
    value = str(process or "").strip().lower()
    return value


def _match_process(process: str, values: Iterable[str]) -> bool:
    proc = _normalize_process(process)
    if not proc:
        return False
    for raw in values:
        value = str(raw or "").strip().lower()
        if not value:
            continue
        if proc == value:
            return True
        # Process names from psutil may include or omit .exe.
        if proc == f"{value}.exe" or f"{proc}.exe" == value:
            return True
        # Match a configured vendor prefix (e.g. pycharm64 vs pycharm).
        if len(value) >= 5 and proc.startswith(value):
            return True
    return False


def _is_browser(process: str) -> bool:
    proc = _normalize_process(process)
    return any(name in proc for name in _BROWSER_PROCESSES)


def _domain_from_url(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


def _domain_in_text(text: str, domains: Iterable[str]) -> Optional[str]:
    lowered = (text or "").lower()
    for domain in domains:
        domain = str(domain or "").lower()
        if domain and domain in lowered:
            return domain
    return None


class WorkspaceClassifier:
    """Thread-safe workspace classifier backed by a local JSON rule file."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else _app_data_dir() / "workspace_rules.json"
        self._lock = threading.RLock()
        self.rules: Dict[str, Any] = json.loads(json.dumps(DEFAULT_RULES))
        self._load()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.config_path.exists():
            self._save()
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in self.rules and isinstance(value, (list, dict)):
                        self.rules[key] = value
        except Exception as exc:
            log.warning("Could not read workspace rules; using defaults: %s", exc)

    def _save(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(self.rules, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("Could not persist workspace rules: %s", exc)

    def reload(self) -> None:
        with self._lock:
            self.rules = json.loads(json.dumps(DEFAULT_RULES))
            self._load()

    def get_rules(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.rules))

    def update_rules(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Replace list rules / merge override dictionaries, then persist."""
        with self._lock:
            for key, value in (updates or {}).items():
                if key == "overrides" and isinstance(value, dict):
                    for section in ("processes", "domains", "paths"):
                        if isinstance(value.get(section), dict):
                            self.rules["overrides"].setdefault(section, {}).update(value[section])
                elif key in self.rules and isinstance(value, list):
                    self.rules[key] = [str(v) for v in value if str(v).strip()]
            self._save()
            return self.get_rules()

    def set_path_override(self, path: str, workspace: str) -> None:
        workspace = normalize_workspace(workspace)
        with self._lock:
            self.rules.setdefault("overrides", {}).setdefault("paths", {})[str(path)] = workspace
            self._save()

    def remove_path_override(self, path: str) -> None:
        with self._lock:
            self.rules.setdefault("overrides", {}).setdefault("paths", {}).pop(str(path), None)
            self._save()

    # ── classification ───────────────────────────────────────────────────
    def _override(self, section: str, key: str) -> Optional[str]:
        overrides = self.rules.get("overrides", {}).get(section, {})
        if not isinstance(overrides, dict):
            return None
        value = overrides.get(str(key))
        if value:
            return normalize_workspace(value)
        return None

    def _domain_workspace(self, domain: str) -> Optional[str]:
        domain = (domain or "").lower().lstrip("www.")
        if not domain:
            return None
        override = self._override("domains", domain)
        if override:
            return override
        for part in (domain, ".".join(domain.split(".")[-2:])):
            if not part:
                continue
            if part in {d.lower() for d in self.rules["company_domains"]}:
                return COMPANY
            if part in {d.lower() for d in self.rules["development_domains"]}:
                return DEVELOPMENT
            if part in {d.lower() for d in self.rules["personal_domains"]}:
                return PERSONAL
        return None

    def classify_path(self, path: str) -> str:
        """Classify a filesystem path using overrides then configured roots."""
        if not path:
            return UNCLASSIFIED
        try:
            candidate = os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            candidate = os.path.normcase(str(path))

        with self._lock:
            paths = self.rules.get("overrides", {}).get("paths", {})
            if isinstance(paths, dict):
                best = None
                for prefix, workspace in paths.items():
                    try:
                        norm_prefix = os.path.normcase(os.path.abspath(prefix))
                    except Exception:
                        norm_prefix = os.path.normcase(prefix)
                    if candidate == norm_prefix or candidate.startswith(norm_prefix + os.sep):
                        if best is None or len(norm_prefix) > len(best[0]):
                            best = (norm_prefix, workspace)
                if best:
                    return normalize_workspace(best[1])

            checks = (
                ("development_roots", DEVELOPMENT),
                ("company_roots", COMPANY),
                ("personal_roots", PERSONAL),
            )
            for key, workspace in checks:
                for root in self.rules.get(key, []):
                    if not root:
                        continue
                    try:
                        norm_root = os.path.normcase(os.path.abspath(str(root)))
                    except Exception:
                        continue
                    if candidate == norm_root or candidate.startswith(norm_root + os.sep):
                        return workspace
        return UNCLASSIFIED

    def classify_application(
        self,
        process: str = "",
        title: str = "",
        url: Optional[str] = None,
        path: Optional[str] = None,
    ) -> str:
        """Classify an activity event.

        Precedence: path evidence -> explicit process override -> known app ->
        browser domain -> strong title keywords -> unclassified.
        """
        # A path inside a configured root is reliable evidence.
        if path:
            by_path = self.classify_path(path)
            if by_path != UNCLASSIFIED:
                return by_path

        proc = _normalize_process(process)
        override = self._override("processes", proc) or self._override("processes", proc.replace(".exe", ""))
        if override:
            return override

        with self._lock:
            if _match_process(proc, self.rules["development_apps"]):
                return DEVELOPMENT
            if _match_process(proc, self.rules["company_apps"]):
                return COMPANY
            if _match_process(proc, self.rules["personal_apps"]):
                return PERSONAL

            if _is_browser(proc):
                domain = _domain_from_url(url)
                if domain:
                    by_domain = self._domain_workspace(domain)
                    if by_domain:
                        return by_domain
                for list_name in ("company_domains", "development_domains", "personal_domains"):
                    found = _domain_in_text(title, self.rules[list_name])
                    if found:
                        return self._domain_workspace(found) or UNCLASSIFIED
                # A browser title alone is not enough to classify a whole
                # browser session.  Fall through to conservative title hints.
                title_lower = (title or "").lower()
                if any(k in title_lower for k in self.rules["development_title_keywords"]):
                    return DEVELOPMENT
                if any(k in title_lower for k in self.rules["company_title_keywords"]):
                    return COMPANY
                if any(k in title_lower for k in self.rules["personal_title_keywords"]):
                    return PERSONAL
                return UNCLASSIFIED

            title_lower = (title or "").lower()
            if any(k in title_lower for k in self.rules["development_title_keywords"]):
                return DEVELOPMENT
            if any(k in title_lower for k in self.rules["company_title_keywords"]):
                return COMPANY
            if any(k in title_lower for k in self.rules["personal_title_keywords"]):
                return PERSONAL
        return UNCLASSIFIED

    def classify_search(self, source: str, query: str, raw_title: str = "") -> str:
        """Classify a search query without treating a browser as one workspace."""
        text = f"{source or ''} {query or ''} {raw_title or ''}".lower()
        if any(domain in text for domain in ("github", "stack overflow", "stackoverflow", "gitlab", "npm", "pypi", "localhost", "127.0.0.1", "developer.mozilla", "docs.python")):
            return DEVELOPMENT
        if any(domain in text for domain in ("teams.microsoft", "outlook.office", "sharepoint", "jira", "confluence", "salesforce", "workday", "meet.google")):
            return COMPANY
        if any(domain in text for domain in ("youtube", "netflix", "reddit", "instagram", "facebook", "tiktok", "spotify")):
            return PERSONAL
        dev_terms = ("python", "javascript", "typescript", "api", "code", "debug", "docker", "sql", "react", "django", "fastapi", "git ")
        if any(term in text for term in dev_terms):
            return DEVELOPMENT
        return UNCLASSIFIED


classifier = WorkspaceClassifier()

__all__ = [
    "COMPANY",
    "DEVELOPMENT",
    "PERSONAL",
    "UNCLASSIFIED",
    "COMBINED",
    "WORKSPACES",
    "VALID_SCOPES",
    "WorkspaceClassifier",
    "classifier",
    "normalize_workspace",
    "normalize_scope",
    "scope_workspaces",
]
