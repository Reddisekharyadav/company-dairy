"""
Browser History Reader — reads actual browsing history from Chrome, Edge, and Firefox databases.

Runs periodically (every 5 minutes) to import new history entries.
Works on Windows, macOS, and Linux by using platform-specific paths.
"""
import logging
import os
import platform
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger('browser_history')

# ── Site name mapping (domain → human-readable name) ──────────────────────────
DOMAIN_SITE_MAP = {
    'github.com': 'GitHub',
    'stackoverflow.com': 'Stack Overflow',
    'google.com': 'Google',
    'youtube.com': 'YouTube',
    'reddit.com': 'Reddit',
    'medium.com': 'Medium',
    'twitter.com': 'Twitter',
    'x.com': 'X (Twitter)',
    'linkedin.com': 'LinkedIn',
    'facebook.com': 'Facebook',
    'instagram.com': 'Instagram',
    'whatsapp.com': 'WhatsApp',
    'web.whatsapp.com': 'WhatsApp',
    'chat.openai.com': 'ChatGPT',
    'chatgpt.com': 'ChatGPT',
    'gemini.google.com': 'Gemini AI',
    'claude.ai': 'Claude AI',
    'mail.google.com': 'Gmail',
    'docs.google.com': 'Google Docs',
    'sheets.google.com': 'Google Sheets',
    'drive.google.com': 'Google Drive',
    'meet.google.com': 'Google Meet',
    'calendar.google.com': 'Google Calendar',
    'outlook.live.com': 'Outlook',
    'outlook.office.com': 'Outlook',
    'notion.so': 'Notion',
    'trello.com': 'Trello',
    'slack.com': 'Slack',
    'discord.com': 'Discord',
    'zoom.us': 'Zoom',
    'teams.microsoft.com': 'Microsoft Teams',
    'gitlab.com': 'GitLab',
    'bitbucket.org': 'Bitbucket',
    'npmjs.com': 'npm',
    'pypi.org': 'PyPI',
    'dev.to': 'Dev.to',
    'wikipedia.org': 'Wikipedia',
    'en.wikipedia.org': 'Wikipedia',
    'amazon.com': 'Amazon',
    'amazon.in': 'Amazon India',
    'flipkart.com': 'Flipkart',
    'leetcode.com': 'LeetCode',
    'hackerrank.com': 'HackerRank',
    'udemy.com': 'Udemy',
    'coursera.org': 'Coursera',
    'netflix.com': 'Netflix',
    'spotify.com': 'Spotify',
    'figma.com': 'Figma',
    'canva.com': 'Canva',
    'vercel.com': 'Vercel',
    'netlify.com': 'Netlify',
    'aws.amazon.com': 'AWS',
    'console.cloud.google.com': 'Google Cloud',
    'portal.azure.com': 'Azure',
    'jira.atlassian.com': 'Jira',
    'localhost': 'Local Server',
    '127.0.0.1': 'Local Server',
    'perplexity.ai': 'Perplexity',
    'arxiv.org': 'arXiv',
    'news.ycombinator.com': 'Hacker News',
}

# ── Category mapping for domains ──────────────────────────────────────────────
DOMAIN_CATEGORY_MAP = {
    'github.com': 'Coding/Research', 'gitlab.com': 'Coding/Research',
    'bitbucket.org': 'Coding/Research', 'stackoverflow.com': 'Coding/Research',
    'npmjs.com': 'Coding/Research', 'pypi.org': 'Coding/Research',
    'dev.to': 'Coding/Research', 'leetcode.com': 'Coding/Research',
    'hackerrank.com': 'Coding/Research', 'vercel.com': 'Coding/Research',
    'netlify.com': 'Coding/Research', 'localhost': 'Coding/Research',
    '127.0.0.1': 'Coding/Research',
    'youtube.com': 'Entertainment/Social', 'netflix.com': 'Entertainment/Social',
    'spotify.com': 'Entertainment/Social', 'reddit.com': 'Entertainment/Social',
    'instagram.com': 'Entertainment/Social', 'facebook.com': 'Entertainment/Social',
    'twitter.com': 'Entertainment/Social', 'x.com': 'Entertainment/Social',
    'mail.google.com': 'Communication', 'outlook.live.com': 'Communication',
    'outlook.office.com': 'Communication', 'slack.com': 'Communication',
    'discord.com': 'Communication', 'teams.microsoft.com': 'Communication',
    'web.whatsapp.com': 'Communication', 'zoom.us': 'Communication',
    'meet.google.com': 'Communication',
    'udemy.com': 'Learning/Research', 'coursera.org': 'Learning/Research',
    'medium.com': 'Learning/Research', 'wikipedia.org': 'Learning/Research',
    'en.wikipedia.org': 'Learning/Research', 'arxiv.org': 'Learning/Research',
    'chat.openai.com': 'Learning/Research', 'chatgpt.com': 'Learning/Research',
    'gemini.google.com': 'Learning/Research', 'claude.ai': 'Learning/Research',
    'perplexity.ai': 'Learning/Research',
    'docs.google.com': 'Productivity', 'sheets.google.com': 'Productivity',
    'drive.google.com': 'Productivity', 'notion.so': 'Productivity',
    'trello.com': 'Productivity', 'figma.com': 'Productivity',
    'canva.com': 'Productivity', 'calendar.google.com': 'Productivity',
    'amazon.com': 'Shopping', 'amazon.in': 'Shopping', 'flipkart.com': 'Shopping',
}


def _get_site_name(domain: str) -> str:
    """Map a domain to a clean human-readable site name."""
    if domain in DOMAIN_SITE_MAP:
        return DOMAIN_SITE_MAP[domain]
    # Try without subdomain (e.g., en.wikipedia.org → wikipedia.org)
    parts = domain.split('.')
    if len(parts) > 2:
        parent = '.'.join(parts[-2:])
        if parent in DOMAIN_SITE_MAP:
            return DOMAIN_SITE_MAP[parent]
    # Fallback: capitalize the domain
    return domain.split('.')[0].capitalize() if domain else 'Unknown'


def _get_category(domain: str) -> str:
    """Get category for a domain."""
    if domain in DOMAIN_CATEGORY_MAP:
        return DOMAIN_CATEGORY_MAP[domain]
    parts = domain.split('.')
    if len(parts) > 2:
        parent = '.'.join(parts[-2:])
        if parent in DOMAIN_CATEGORY_MAP:
            return DOMAIN_CATEGORY_MAP[parent]
    return 'Browsing'


def _extract_domain(url: str) -> str:
    """Extract clean domain from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ''
        # Remove www. prefix
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain
    except Exception:
        return ''


# ── Browser DB paths per platform ─────────────────────────────────────────────

def _get_chrome_history_paths() -> list[Path]:
    """Return possible Chrome history DB paths for current OS."""
    system = platform.system()
    paths = []
    if system == 'Windows':
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            paths.append(Path(local) / 'Google' / 'Chrome' / 'User Data' / 'Default' / 'History')
            # Check for profiles
            chrome_dir = Path(local) / 'Google' / 'Chrome' / 'User Data'
            if chrome_dir.exists():
                for profile in chrome_dir.iterdir():
                    if profile.name.startswith('Profile '):
                        paths.append(profile / 'History')
    elif system == 'Darwin':
        home = Path.home()
        paths.append(home / 'Library' / 'Application Support' / 'Google' / 'Chrome' / 'Default' / 'History')
    elif system == 'Linux':
        home = Path.home()
        paths.append(home / '.config' / 'google-chrome' / 'Default' / 'History')
        paths.append(home / '.config' / 'chromium' / 'Default' / 'History')
    return paths


def _get_edge_history_paths() -> list[Path]:
    """Return possible Edge history DB paths."""
    system = platform.system()
    paths = []
    if system == 'Windows':
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            paths.append(Path(local) / 'Microsoft' / 'Edge' / 'User Data' / 'Default' / 'History')
    elif system == 'Darwin':
        home = Path.home()
        paths.append(home / 'Library' / 'Application Support' / 'Microsoft Edge' / 'Default' / 'History')
    elif system == 'Linux':
        home = Path.home()
        paths.append(home / '.config' / 'microsoft-edge' / 'Default' / 'History')
    return paths


def _get_brave_history_paths() -> list[Path]:
    """Return possible Brave history DB paths."""
    system = platform.system()
    paths = []
    if system == 'Windows':
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            paths.append(Path(local) / 'BraveSoftware' / 'Brave-Browser' / 'User Data' / 'Default' / 'History')
    elif system == 'Darwin':
        home = Path.home()
        paths.append(home / 'Library' / 'Application Support' / 'BraveSoftware' / 'Brave-Browser' / 'Default' / 'History')
    elif system == 'Linux':
        home = Path.home()
        paths.append(home / '.config' / 'BraveSoftware' / 'Brave-Browser' / 'Default' / 'History')
    return paths


def _get_firefox_history_paths() -> list[Path]:
    """Return possible Firefox history DB paths."""
    system = platform.system()
    paths = []
    if system == 'Windows':
        appdata = os.environ.get('APPDATA', '')
        if appdata:
            profiles_dir = Path(appdata) / 'Mozilla' / 'Firefox' / 'Profiles'
            if profiles_dir.exists():
                for profile in profiles_dir.iterdir():
                    if profile.is_dir():
                        db = profile / 'places.sqlite'
                        if db.exists():
                            paths.append(db)
    elif system == 'Darwin':
        profiles_dir = Path.home() / 'Library' / 'Application Support' / 'Firefox' / 'Profiles'
        if profiles_dir.exists():
            for profile in profiles_dir.iterdir():
                if profile.is_dir():
                    db = profile / 'places.sqlite'
                    if db.exists():
                        paths.append(db)
    elif system == 'Linux':
        profiles_dir = Path.home() / '.mozilla' / 'firefox'
        if profiles_dir.exists():
            for profile in profiles_dir.iterdir():
                if profile.is_dir():
                    db = profile / 'places.sqlite'
                    if db.exists():
                        paths.append(db)
    return paths


def _read_chromium_history(db_path: Path, since_timestamp: float) -> list[dict]:
    """
    Read history from a Chromium-based browser (Chrome/Edge).
    Chrome stores timestamps as microseconds since 1601-01-01 (Windows FILETIME).
    """
    entries = []
    if not db_path.exists():
        return entries

    # Copy the DB to a temp file (browser locks it)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db')
    os.close(tmp_fd)
    try:
        shutil.copy2(str(db_path), tmp_path)
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row

        # Chrome epoch: 1601-01-01. Difference from Unix epoch in microseconds.
        chrome_epoch_offset = 11644473600 * 1000000

        # Convert our since_timestamp (unix) to Chrome timestamp
        chrome_since = int(since_timestamp * 1000000) + chrome_epoch_offset

        cursor = conn.execute("""
            SELECT u.url, u.title, u.visit_count, v.visit_time
            FROM urls u
            JOIN visits v ON u.id = v.url
            WHERE v.visit_time > ?
            ORDER BY v.visit_time DESC
            LIMIT 500
        """, (chrome_since,))

        for row in cursor:
            # Convert Chrome timestamp to Python datetime
            unix_us = row['visit_time'] - chrome_epoch_offset
            visit_dt = datetime.fromtimestamp(unix_us / 1000000)
            entries.append({
                'url': row['url'],
                'title': row['title'] or '',
                'visit_count': row['visit_count'] or 1,
                'timestamp': visit_dt,
            })

        conn.close()
    except Exception as e:
        log.debug('Error reading Chromium history from %s: %s', db_path, e)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return entries


def _read_firefox_history(db_path: Path, since_timestamp: float) -> list[dict]:
    """
    Read history from Firefox.
    Firefox stores timestamps as microseconds since Unix epoch.
    """
    entries = []
    if not db_path.exists():
        return entries

    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db')
    os.close(tmp_fd)
    try:
        shutil.copy2(str(db_path), tmp_path)
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row

        # Firefox uses microseconds since Unix epoch
        firefox_since = int(since_timestamp * 1000000)

        cursor = conn.execute("""
            SELECT p.url, p.title, p.visit_count, h.visit_date
            FROM moz_places p
            JOIN moz_historyvisits h ON p.id = h.place_id
            WHERE h.visit_date > ?
            ORDER BY h.visit_date DESC
            LIMIT 500
        """, (firefox_since,))

        for row in cursor:
            visit_dt = datetime.fromtimestamp(row['visit_date'] / 1000000)
            entries.append({
                'url': row['url'],
                'title': row['title'] or '',
                'visit_count': row['visit_count'] or 1,
                'timestamp': visit_dt,
            })

        conn.close()
    except Exception as e:
        log.debug('Error reading Firefox history from %s: %s', db_path, e)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return entries


class BrowserHistoryTracker:
    """
    Periodically reads browser history from Chrome, Edge, and Firefox databases.
    Stores entries in the BrowserHistory table.
    """

    def __init__(self, interval: float = 300.0):
        """interval: seconds between history reads (default 5 minutes)."""
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name='BrowserHistoryTracker')
        self._last_read_time: float = 0.0   # Unix timestamp of last successful read

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name='BrowserHistoryTracker')
            self._thread.start()
        log.info('BrowserHistoryTracker started (interval=%ds).', self.interval)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5.0)

    def _run(self):
        from database.session import SessionLocal
        from database.models import BrowserHistory

        session = SessionLocal()
        try:
            # On first run, only track history from when the app started
            if self._last_read_time == 0:
                self._last_read_time = datetime.now().timestamp()

            while not self._stop.is_set():
                try:
                    self._import_history(session)
                except Exception as e:
                    log.debug('BrowserHistoryTracker tick error: %s', e)
                    try:
                        session.rollback()
                    except Exception:
                        pass
                # Wait for next interval
                self._stop.wait(self.interval)
        except Exception as e:
            log.exception('BrowserHistoryTracker fatal error: %s', e)
        finally:
            session.close()

    def _import_history(self, session):
        """Read from all detected browsers and import to DB."""
        from database.models import BrowserHistory

        since = self._last_read_time
        now_ts = datetime.now().timestamp()
        today = datetime.now().strftime('%Y-%m-%d')
        new_count = 0

        # ── Chrome ────────────────────────────────────────────────────────────
        for db_path in _get_chrome_history_paths():
            entries = _read_chromium_history(db_path, since)
            for entry in entries:
                if self._is_duplicate(session, entry['url'], entry['timestamp']):
                    continue
                domain = _extract_domain(entry['url'])
                if not domain:
                    continue
                bh = BrowserHistory(
                    timestamp=entry['timestamp'],
                    url=entry['url'][:2048],
                    title=entry['title'][:1024] if entry['title'] else None,
                    site_name=_get_site_name(domain),
                    domain=domain,
                    visit_count=entry['visit_count'],
                    duration_sec=0.0,
                    browser='Chrome',
                    category=_get_category(domain),
                    session_date=entry['timestamp'].strftime('%Y-%m-%d'),
                )
                session.add(bh)
                new_count += 1

        # ── Edge ──────────────────────────────────────────────────────────────
        for db_path in _get_edge_history_paths():
            entries = _read_chromium_history(db_path, since)
            for entry in entries:
                if self._is_duplicate(session, entry['url'], entry['timestamp']):
                    continue
                domain = _extract_domain(entry['url'])
                if not domain:
                    continue
                bh = BrowserHistory(
                    timestamp=entry['timestamp'],
                    url=entry['url'][:2048],
                    title=entry['title'][:1024] if entry['title'] else None,
                    site_name=_get_site_name(domain),
                    domain=domain,
                    visit_count=entry['visit_count'],
                    duration_sec=0.0,
                    browser='Edge',
                    category=_get_category(domain),
                    session_date=entry['timestamp'].strftime('%Y-%m-%d'),
                )
                session.add(bh)
                new_count += 1

        # ── Brave ─────────────────────────────────────────────────────────────
        for db_path in _get_brave_history_paths():
            entries = _read_chromium_history(db_path, since)
            for entry in entries:
                if self._is_duplicate(session, entry['url'], entry['timestamp']):
                    continue
                domain = _extract_domain(entry['url'])
                if not domain:
                    continue
                bh = BrowserHistory(
                    timestamp=entry['timestamp'],
                    url=entry['url'][:2048],
                    title=entry['title'][:1024] if entry['title'] else None,
                    site_name=_get_site_name(domain),
                    domain=domain,
                    visit_count=entry['visit_count'],
                    duration_sec=0.0,
                    browser='Brave',
                    category=_get_category(domain),
                    session_date=entry['timestamp'].strftime('%Y-%m-%d'),
                )
                session.add(bh)
                new_count += 1

        # ── Firefox ───────────────────────────────────────────────────────────
        for db_path in _get_firefox_history_paths():
            entries = _read_firefox_history(db_path, since)
            for entry in entries:
                if self._is_duplicate(session, entry['url'], entry['timestamp']):
                    continue
                domain = _extract_domain(entry['url'])
                if not domain:
                    continue
                bh = BrowserHistory(
                    timestamp=entry['timestamp'],
                    url=entry['url'][:2048],
                    title=entry['title'][:1024] if entry['title'] else None,
                    site_name=_get_site_name(domain),
                    domain=domain,
                    visit_count=entry['visit_count'],
                    duration_sec=0.0,
                    browser='Firefox',
                    category=_get_category(domain),
                    session_date=entry['timestamp'].strftime('%Y-%m-%d'),
                )
                session.add(bh)
                new_count += 1

        if new_count > 0:
            session.commit()
            log.info('Imported %d new browser history entries.', new_count)

        self._last_read_time = now_ts

    def _is_duplicate(self, session, url: str, timestamp: datetime) -> bool:
        """Check if this exact URL+timestamp combination already exists."""
        from database.models import BrowserHistory
        try:
            existing = (session.query(BrowserHistory)
                        .filter(BrowserHistory.url == url[:2048],
                                BrowserHistory.timestamp == timestamp)
                        .first())
            return existing is not None
        except Exception:
            return False

    # ── Public API ────────────────────────────────────────────────────────────

    def get_history(self, days: int = 1, limit: int = 100) -> list[dict]:
        """Return browser history entries for the API."""
        try:
            from database.session import SessionLocal
            from database.models import BrowserHistory
            since = datetime.now() - timedelta(days=days)
            session = SessionLocal()
            try:
                rows = (session.query(BrowserHistory)
                        .filter(BrowserHistory.timestamp >= since)
                        .order_by(BrowserHistory.timestamp.desc())
                        .limit(limit).all())
                return [{
                    'id': r.id,
                    'url': r.url,
                    'title': r.title,
                    'site_name': r.site_name,
                    'domain': r.domain,
                    'browser': r.browser,
                    'category': r.category,
                    'visit_count': r.visit_count,
                    'timestamp': r.timestamp.isoformat() if r.timestamp else None,
                } for r in rows]
            finally:
                session.close()
        except Exception as e:
            log.warning('get_history error: %s', e)
            return []

    def get_top_sites(self, days: int = 7, limit: int = 20) -> list[dict]:
        """Return most visited sites with total visit count."""
        try:
            from database.session import SessionLocal
            from database.models import BrowserHistory
            from sqlalchemy import func
            since = datetime.now() - timedelta(days=days)
            session = SessionLocal()
            try:
                rows = (session.query(
                            BrowserHistory.site_name,
                            BrowserHistory.domain,
                            BrowserHistory.category,
                            func.count(BrowserHistory.id).label('visits'),
                            func.sum(BrowserHistory.duration_sec).label('total_sec'),
                        )
                        .filter(BrowserHistory.timestamp >= since)
                        .group_by(BrowserHistory.domain)
                        .order_by(func.count(BrowserHistory.id).desc())
                        .limit(limit).all())
                return [{
                    'site_name': r.site_name,
                    'domain': r.domain,
                    'category': r.category,
                    'visits': r.visits,
                    'total_minutes': round((r.total_sec or 0) / 60, 1),
                } for r in rows]
            finally:
                session.close()
        except Exception as e:
            log.warning('get_top_sites error: %s', e)
            return []
