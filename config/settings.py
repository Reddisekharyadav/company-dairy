import os
import sys
from pathlib import Path


def _app_data_dir() -> Path:
    """Return persistent WorkSense data directory (AppData on Windows)."""
    base = Path(os.environ.get('APPDATA') or Path.home())
    d = base / 'WorkSense'
    d.mkdir(parents=True, exist_ok=True)
    return d


# When frozen as EXE, __file__ lives in a temp _MEIPASS folder.
# Use AppData for any writable paths so data persists across runs.
_FROZEN = getattr(sys, 'frozen', False)
BASE_DIR = _app_data_dir() if _FROZEN else Path(__file__).resolve().parent.parent


import json
import uuid

CONFIG_FILE = _app_data_dir() / 'config.json'
SESSION_ID = uuid.uuid4().hex  # Unique ID for this specific run of the application

class Settings:
    def __init__(self):
        # Load from JSON if exists
        self.config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
            except Exception:
                pass

        def get_val(key, env_key, default):
            return self.config.get(key, os.environ.get(env_key, default))

        self.interval = float(get_val('interval', 'WS_INTERVAL', 5.0))
        self.ocr_enabled = str(get_val('ocr_enabled', 'WS_OCR', '0')) == '1'
        self.idle_timeout = int(get_val('idle_timeout', 'WS_IDLE', 300))
        self.export_folder = get_val('export_folder', 'WS_EXPORT', str(_app_data_dir() / 'exports'))
        # Workspace/report defaults.  Company is the safe default so a company
        # report can never silently contain development records.
        self.default_workspace = str(get_val('default_workspace', 'WS_DEFAULT_WORKSPACE', 'company'))
        self.retention_days = int(get_val('retention_days', 'WS_RETENTION_DAYS', 30))
        self.max_storage_mb = int(get_val('max_storage_mb', 'WS_MAX_STORAGE_MB', 2048))
        self.watcher_enabled = str(get_val('watcher_enabled', 'WS_WATCHER', '1')) == '1'
        self.watcher_debounce = float(get_val('watcher_debounce', 'WS_WATCHER_DEBOUNCE', 1.5))
        self.watcher_startup_grace = float(get_val('watcher_startup_grace', 'WS_WATCHER_GRACE', 1.0))
        self.watcher_tracked_extensions = get_val('watcher_tracked_extensions', 'WS_WATCHER_EXTS', None)
        self.watcher_ignored_dirs = get_val('watcher_ignored_dirs', 'WS_WATCHER_IGNORE', None)

        roots = get_val('project_roots', 'WS_PROJECT_ROOTS', None)
        if roots:
            if isinstance(roots, str):
                self.project_roots = [r.strip() for r in roots.split(',') if r.strip()]
            else:
                self.project_roots = [str(r) for r in roots if str(r).strip()]
        else:
            self.project_roots = [str(Path.home() / 'Documents'), str(Path.cwd())]
        # Keep the classifier's development roots aligned with the watcher.
        try:
            from config.workspace_rules import classifier
            classifier.update_rules({'development_roots': self.project_roots})
        except Exception:
            pass

        # ── Email / SMTP settings ─────────────────────────────────────────────
        self.email_to   = get_val('email_to', 'WS_EMAIL_TO', '')
        self.email_from = get_val('email_from', 'WS_EMAIL_FROM', '')
        self.email_smtp = get_val('email_smtp', 'WS_EMAIL_SMTP', 'smtp.gmail.com')
        self.email_port = int(get_val('email_port', 'WS_EMAIL_PORT', 587))
        self.email_user = get_val('email_user', 'WS_EMAIL_USER', '')
        self.email_pass = get_val('email_pass', 'WS_EMAIL_PASS', '')

        # ── AI Integration ────────────────────────────────────────────────────
        self.ai_api_key = get_val('ai_api_key', 'WS_AI_API_KEY', '')

    def save(self):
        self.config.update({
            'interval': self.interval,
            'ocr_enabled': '1' if self.ocr_enabled else '0',
            'idle_timeout': self.idle_timeout,
            'export_folder': self.export_folder,
            'project_roots': self.project_roots,
            'default_workspace': self.default_workspace,
            'retention_days': self.retention_days,
            'max_storage_mb': self.max_storage_mb,
            'watcher_enabled': '1' if self.watcher_enabled else '0',
            'watcher_debounce': self.watcher_debounce,
            'watcher_startup_grace': self.watcher_startup_grace,
            'watcher_tracked_extensions': self.watcher_tracked_extensions,
            'watcher_ignored_dirs': self.watcher_ignored_dirs,
            'email_to': self.email_to,
            'email_from': self.email_from,
            'email_smtp': self.email_smtp,
            'email_port': self.email_port,
            'email_user': self.email_user,
            'email_pass': self.email_pass,
            'ai_api_key': self.ai_api_key
        })
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass

    def set_project_roots(self, roots):
        cleaned = []
        for root in roots or []:
            root = str(root or '').strip()
            if root and root not in cleaned:
                cleaned.append(root)
        self.project_roots = cleaned
        try:
            from config.workspace_rules import classifier
            classifier.update_rules({'development_roots': cleaned})
        except Exception:
            pass
        self.save()

settings = Settings()

