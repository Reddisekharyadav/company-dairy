"""
Search Query Extractor — detects and stores search queries from browser window titles.
Groups queries into Research Sessions (< 5 min gap = same session).
Powers the Researcher Mode tab on the dashboard.
"""
import re
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, Tuple

log = logging.getLogger('search_extractor')

# Source patterns: (source_name, [title_patterns])
SEARCH_SOURCES = [
    ('Google',     [r'^(.+?)\s*[-–—]\s*Google Search', r'^(.+?)\s*[-–—]\s*Google']),
    ('YouTube',    [r'^(.+?)\s*[-–—]\s*YouTube']),
    ('ChatGPT',    [r'^(.+?)\s*[-–—]\s*ChatGPT', r'^ChatGPT\s*[-–—]\s*(.+)']),
    ('Gemini',     [r'^(.+?)\s*[-–—]\s*Gemini', r'^Gemini\s*[-–—]\s*(.+)']),
    ('Claude',     [r'^(.+?)\s*[-–—]\s*Claude', r'^Claude\s*[-–—]\s*(.+)']),
    ('Bing',       [r'^(.+?)\s*[-–—]\s*Microsoft Bing', r'^(.+?)\s*[-–—]\s*Bing']),
    ('DuckDuckGo', [r'^(.+?)\s*[-–—]\s*DuckDuckGo']),
    ('Wikipedia',  [r'^(.+?)\s*[-–—]\s*Wikipedia']),
    ('Stack Overflow', [r'^(.+?)\s*[-–—]\s*Stack Overflow']),
    ('GitHub',     [r'^(.+?)\s*[-–—]\s*GitHub']),
    ('Medium',     [r'^(.+?)\s*[-–—]\s*Medium']),
    ('Reddit',     [r'^(.+?)\s*[-–—]\s*Reddit']),
    ('Dev.to',     [r'^(.+?)\s*[-–—]\s*DEV Community']),
    ('Udemy',      [r'^(.+?)\s*[-–—]\s*Udemy']),
    ('Coursera',   [r'^(.+?)\s*[-–—]\s*Coursera']),
    ('Perplexity', [r'^(.+?)\s*[-–—]\s*Perplexity']),
    ('arXiv',      [r'^(.+?)\s*[-–—]\s*arXiv']),
    ('Notion',     [r'^(.+?)\s*[-–—]\s*Notion']),
]

# Min length for a query to be meaningful
MIN_QUERY_LEN = 3
# Max gap between queries to be in the same research session (seconds)
SESSION_GAP_SEC = 5 * 60   # 5 minutes


def extract_search_query(window_title: str) -> Optional[Tuple[str, str]]:
    """
    Try to extract (query, source) from a browser window title.
    Returns None if no match.
    """
    if not window_title:
        return None

    for source, patterns in SEARCH_SOURCES:
        for pattern in patterns:
            m = re.match(pattern, window_title, re.IGNORECASE)
            if m:
                query = m.group(1).strip()
                if len(query) >= MIN_QUERY_LEN:
                    return (query, source)
    return None


class SearchExtractor:
    """
    Runs alongside the activity tracker.
    Polls the active window title and extracts search queries.
    Groups consecutive queries into ResearchSession records.
    """

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name='SearchExtractor')
        self._last_query: Optional[str] = None
        self._current_session_id: Optional[int] = None
        self._last_query_time: Optional[datetime] = None

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name='SearchExtractor')
            self._thread.start()
        log.info('SearchExtractor started.')

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3.0)

    def _run(self):
        from database.session import SessionLocal
        from database.models import SearchQuery, ResearchSession
        from tracker.active_window import get_active_window

        session = SessionLocal()
        try:
            while not self._stop.is_set():
                try:
                    proc, title = get_active_window()
                    if title:
                        result = extract_search_query(title)
                        if result:
                            query, source = result
                            # Deduplicate consecutive identical queries
                            if query != self._last_query:
                                self._last_query = query
                                now = datetime.now()
                                session_id = self._get_or_create_session(
                                    session, now, source
                                )
                                sq = SearchQuery(
                                    query=query,
                                    source=source,
                                    raw_title=title[:1024],
                                    research_session_id=session_id,
                                    bookmarked=False,
                                )
                                session.add(sq)
                                # Update session query count
                                rs = session.query(ResearchSession).filter(
                                    ResearchSession.id == session_id
                                ).first()
                                if rs:
                                    rs.query_count = (rs.query_count or 0) + 1
                                    rs.end_time = now
                                    rs.total_duration_sec = (rs.total_duration_sec or 0) + self.interval
                                session.commit()
                                self._last_query_time = now
                                log.debug('SearchQuery: [%s] %s', source, query[:60])
                except Exception as e:
                    log.debug('SearchExtractor tick error: %s', e)
                    try:
                        session.rollback()
                    except Exception:
                        pass
                time.sleep(self.interval)
        except Exception as e:
            log.exception('SearchExtractor error: %s', e)
        finally:
            session.close()

    def _get_or_create_session(self, session, now: datetime, source: str) -> int:
        """Return current research session id, or create a new one if gap exceeded."""
        from database.models import ResearchSession
        import json

        # If last query was recent, reuse current session
        if (self._current_session_id is not None and
                self._last_query_time is not None and
                (now - self._last_query_time).total_seconds() < SESSION_GAP_SEC):
            return self._current_session_id

        # Start new session
        today = now.strftime('%Y-%m-%d')
        rs = ResearchSession(
            start_time=now,
            end_time=now,
            date=today,
            query_count=0,
            total_duration_sec=0.0,
            sources=json.dumps([source]),
        )
        session.add(rs)
        session.flush()  # get the ID
        self._current_session_id = rs.id
        log.info('New research session started: id=%d', rs.id)
        return rs.id

    # ── Public API helpers ────────────────────────────────────────────────────

    def get_today_queries(self, limit: int = 50) -> list[dict]:
        """Return today's search queries for the Research tab."""
        try:
            from database.session import SessionLocal
            from database.models import SearchQuery
            today = datetime.now().strftime('%Y-%m-%d')
            session = SessionLocal()
            try:
                rows = (session.query(SearchQuery)
                        .filter(SearchQuery.timestamp >= datetime.strptime(today, '%Y-%m-%d'))
                        .order_by(SearchQuery.timestamp.desc())
                        .limit(limit).all())
                return [{
                    'id': r.id,
                    'query': r.query,
                    'source': r.source,
                    'timestamp': r.timestamp.isoformat() if r.timestamp else None,
                    'session_id': r.research_session_id,
                    'bookmarked': r.bookmarked,
                } for r in rows]
            finally:
                session.close()
        except Exception as e:
            log.warning('get_today_queries error: %s', e)
            return []

    def get_research_sessions(self, days: int = 7) -> list[dict]:
        """Return research sessions for the last N days."""
        try:
            from database.session import SessionLocal
            from database.models import ResearchSession, SearchQuery
            import json
            since = datetime.now() - timedelta(days=days)
            session = SessionLocal()
            try:
                rows = (session.query(ResearchSession)
                        .filter(ResearchSession.start_time >= since)
                        .order_by(ResearchSession.start_time.desc())
                        .limit(50).all())
                result = []
                for r in rows:
                    # Get sample queries for this session
                    samples = (session.query(SearchQuery)
                               .filter(SearchQuery.research_session_id == r.id)
                               .order_by(SearchQuery.timestamp)
                               .limit(5).all())
                    topic = r.topic_label or (samples[0].query[:40] if samples else 'Unknown')
                    result.append({
                        'id': r.id,
                        'topic': topic,
                        'date': r.date,
                        'start': r.start_time.isoformat() if r.start_time else None,
                        'end': r.end_time.isoformat() if r.end_time else None,
                        'query_count': r.query_count,
                        'duration_min': round((r.total_duration_sec or 0) / 60, 1),
                        'sources': json.loads(r.sources) if r.sources else [],
                        'sample_queries': [s.query for s in samples],
                    })
                return result
            finally:
                session.close()
        except Exception as e:
            log.warning('get_research_sessions error: %s', e)
            return []
