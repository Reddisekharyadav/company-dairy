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


class Settings:
    def __init__(self):
        self.interval = float(os.environ.get('WS_INTERVAL', '5.0'))
        self.ocr_enabled = os.environ.get('WS_OCR', '0') == '1'
        self.idle_timeout = int(os.environ.get('WS_IDLE', '300'))
        self.export_folder = os.environ.get('WS_EXPORT', str(_app_data_dir() / 'exports'))
        # project_roots: comma-separated list of folders to scan for git repos
        roots = os.environ.get('WS_PROJECT_ROOTS')
        if roots:
            self.project_roots = [r.strip() for r in roots.split(',') if r.strip()]
        else:
            # default to user's Documents and current working directory
            self.project_roots = [str(Path.home() / 'Documents'), str(Path.cwd())]

        # ── Email / SMTP settings ─────────────────────────────────────────────
        self.email_to   = os.environ.get('WS_EMAIL_TO',   '')   # recipient
        self.email_from = os.environ.get('WS_EMAIL_FROM', '')   # sender (same as user for Gmail)
        self.email_smtp = os.environ.get('WS_EMAIL_SMTP', 'smtp.gmail.com')
        self.email_port = int(os.environ.get('WS_EMAIL_PORT', '587'))
        self.email_user = os.environ.get('WS_EMAIL_USER', '')   # SMTP login username
        self.email_pass = os.environ.get('WS_EMAIL_PASS', '')   # SMTP password / app-password


settings = Settings()

