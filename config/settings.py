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

CONFIG_FILE = _app_data_dir() / 'config.json'

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
        
        roots = get_val('project_roots', 'WS_PROJECT_ROOTS', None)
        if roots:
            if isinstance(roots, str):
                self.project_roots = [r.strip() for r in roots.split(',') if r.strip()]
            else:
                self.project_roots = roots
        else:
            self.project_roots = [str(Path.home() / 'Documents'), str(Path.cwd())]

        # ── Email / SMTP settings ─────────────────────────────────────────────
        self.email_to   = get_val('email_to', 'WS_EMAIL_TO', '')
        self.email_from = get_val('email_from', 'WS_EMAIL_FROM', '')
        self.email_smtp = get_val('email_smtp', 'WS_EMAIL_SMTP', 'smtp.gmail.com')
        self.email_port = int(get_val('email_port', 'WS_EMAIL_PORT', 587))
        self.email_user = get_val('email_user', 'WS_EMAIL_USER', '')
        self.email_pass = get_val('email_pass', 'WS_EMAIL_PASS', '')

    def save(self):
        self.config.update({
            'interval': self.interval,
            'ocr_enabled': '1' if self.ocr_enabled else '0',
            'idle_timeout': self.idle_timeout,
            'export_folder': self.export_folder,
            'project_roots': self.project_roots,
            'email_to': self.email_to,
            'email_from': self.email_from,
            'email_smtp': self.email_smtp,
            'email_port': self.email_port,
            'email_user': self.email_user,
            'email_pass': self.email_pass
        })
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4)

settings = Settings()

