"""
Smart Summarizer — generates AI-like activity notes locally, no API keys needed.

Uses extractive summarization: keyword extraction from window titles + OCR text,
combined with per-tab dwell time tracking. Produces human-readable summaries like
"Reading about asyncio on docs.python.org (3m 20s)".

Runs as a background thread alongside the main activity tracker.
"""
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from threading import Thread, Event
from typing import Optional

log = logging.getLogger('smart_summarizer')

# Stop words to filter out from keyword extraction
STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'both', 'either',
    'neither', 'each', 'every', 'all', 'any', 'few', 'more', 'most',
    'other', 'some', 'such', 'no', 'only', 'own', 'same', 'than',
    'too', 'very', 'just', 'because', 'if', 'when', 'while', 'that',
    'this', 'these', 'those', 'it', 'its', 'my', 'your', 'his', 'her',
    'our', 'their', 'what', 'which', 'who', 'whom', 'whose', 'where',
    'how', 'new', 'tab', 'page', 'google', 'chrome', 'edge', 'firefox',
    'microsoft', 'mozilla', 'untitled', 'about', 'blank', 'search',
})

# Action verb patterns for summary generation
ACTION_PATTERNS = [
    (r'(?i)(stack\s?overflow|stackoverflow)', 'Researching a coding issue on Stack Overflow'),
    (r'(?i)(github\.com|gitlab)', 'Reviewing code on {site}'),
    (r'(?i)(chatgpt|claude|gemini|perplexity)', 'Using AI assistant ({site})'),
    (r'(?i)(youtube)', 'Watching a video on YouTube'),
    (r'(?i)(docs\.|documentation|reference|api)', 'Reading documentation'),
    (r'(?i)(udemy|coursera|learn|tutorial|course)', 'Taking an online course'),
    (r'(?i)(mail|gmail|outlook|inbox)', 'Checking email'),
    (r'(?i)(slack|discord|teams|whatsapp)', 'In a chat/messaging app'),
    (r'(?i)(meet\.google|zoom|teams.*meeting)', 'In a video meeting'),
    (r'(?i)(jira|trello|asana|notion)', 'Managing tasks/projects'),
    (r'(?i)(figma|canva)', 'Working on a design'),
    (r'(?i)(linkedin)', 'Browsing LinkedIn'),
    (r'(?i)(reddit)', 'Browsing Reddit'),
    (r'(?i)(wikipedia)', 'Reading a Wikipedia article'),
    (r'(?i)(amazon|flipkart|shopping)', 'Shopping online'),
    (r'(?i)(news|bbc|cnn|times)', 'Reading news'),
]

# IDE/editor patterns
IDE_PATTERNS = [
    (r'(?i)visual studio code|vscode|code\.exe', 'Coding in VS Code'),
    (r'(?i)pycharm|intellij|webstorm', 'Coding in JetBrains IDE'),
    (r'(?i)cursor', 'Coding in Cursor'),
    (r'(?i)sublime', 'Coding in Sublime Text'),
    (r'(?i)notepad\+\+', 'Editing text in Notepad++'),
]


def _extract_keywords(text: str, max_keywords: int = 5) -> list:
    """Extract the most relevant keywords from text using simple TF scoring."""
    if not text:
        return []

    # Tokenize: extract words 3+ chars, lowercase
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    # Filter stop words
    words = [w for w in words if w not in STOP_WORDS]

    if not words:
        return []

    # Count frequencies
    counter = Counter(words)
    # Return top keywords
    return [word for word, _ in counter.most_common(max_keywords)]


def _extract_topic_from_title(title: str) -> Optional[str]:
    """Extract the main topic/page title from a browser window title."""
    if not title:
        return None

    # Strip browser name from title
    for suffix in ['- Google Chrome', '- Microsoft Edge', '- Firefox',
                   '- Opera', '- Brave', '- Google Search',
                   '| Microsoft Teams', '| Slack']:
        title = title.replace(suffix, '').strip()

    # Strip common separators and take the first meaningful part
    parts = re.split(r'\s[-–—|]\s', title)
    if parts:
        topic = parts[0].strip()
        if len(topic) > 3 and topic.lower() not in STOP_WORDS:
            return topic[:150]

    return title[:150] if len(title) > 3 else None


def _generate_summary(proc_name: str, title: str, duration_sec: float) -> str:
    """Generate a human-readable summary of what the user is doing."""
    proc = (proc_name or '').lower()
    full_text = f'{proc} {title or ""}'
    duration_str = _format_duration(duration_sec)

    # Check IDE patterns first (highest priority)
    for pattern, action in IDE_PATTERNS:
        if re.search(pattern, full_text):
            topic = _extract_topic_from_title(title)
            if topic:
                return f'{action}: {topic} ({duration_str})'
            return f'{action} ({duration_str})'

    # Check action patterns
    for pattern, action_template in ACTION_PATTERNS:
        match = re.search(pattern, full_text)
        if match:
            site = match.group(1)
            action = action_template.format(site=site)
            topic = _extract_topic_from_title(title)
            if topic and topic.lower() != site.lower():
                return f'{action}: "{topic}" ({duration_str})'
            return f'{action} ({duration_str})'

    # Fallback: use title as-is
    topic = _extract_topic_from_title(title)
    if topic:
        app_name = proc_name or 'Unknown App'
        return f'Using {app_name}: "{topic}" ({duration_str})'

    return f'Active in {proc_name or "unknown"} ({duration_str})'


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f'{int(seconds)}s'
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f'{minutes}m {secs}s' if secs else f'{minutes}m'
    hours = minutes // 60
    mins = minutes % 60
    return f'{hours}h {mins}m'


class SmartSummarizer:
    """Background thread that tracks per-tab dwell time and generates summaries."""

    def __init__(self, interval: float = 30.0, summary_interval: float = 300.0):
        self.interval = interval            # How often to sample active window
        self.summary_interval = summary_interval  # How often to flush summaries to DB (5 min)
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

        # Per-tab dwell tracking: {(proc, title_hash): {'duration', 'proc', 'title', 'first_seen'}}
        self._tab_dwell = defaultdict(lambda: {
            'duration': 0.0, 'proc': '', 'title': '', 'first_seen': None
        })
        self._last_window = None
        self._last_sample_time = None

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()
        log.info('SmartSummarizer started (sample=%ds, flush=%ds)',
                 self.interval, self.summary_interval)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3.0)

    def _run(self):
        from database.session import SessionLocal
        from database.models import ActivityInsight
        from tracker.active_window import get_active_window

        session = SessionLocal()
        last_flush = time.time()

        try:
            while not self._stop.is_set():
                now = time.time()
                proc, title = get_active_window()
                window_key = (proc or '', (title or '')[:100])

                # Update dwell time for previous window
                if self._last_window and self._last_sample_time:
                    elapsed = now - self._last_sample_time
                    lw = self._last_window
                    entry = self._tab_dwell[lw]
                    entry['duration'] += elapsed
                    if not entry['proc']:
                        entry['proc'] = lw[0]
                        entry['title'] = lw[1]
                        entry['first_seen'] = datetime.now()

                self._last_window = window_key
                self._last_sample_time = now

                # Flush summaries to DB periodically
                if now - last_flush >= self.summary_interval:
                    self._flush_insights(session)
                    last_flush = now

                self._stop.wait(self.interval)

        except Exception as e:
            log.exception('SmartSummarizer fatal error: %s', e)
        finally:
            # Final flush
            try:
                self._flush_insights(session)
            except Exception:
                pass
            session.close()

    def _flush_insights(self, session):
        """Generate and save activity insights from accumulated dwell data."""
        from database.models import ActivityInsight

        if not self._tab_dwell:
            return

        today = datetime.now().strftime('%Y-%m-%d')
        saved = 0

        for key, data in list(self._tab_dwell.items()):
            duration = data['duration']
            if duration < 30:  # Skip tabs with < 30s dwell time
                continue

            proc = data['proc']
            title = data['title']
            summary = _generate_summary(proc, title, duration)
            keywords = _extract_keywords(f'{title} {proc}')

            try:
                insight = ActivityInsight(
                    timestamp=data.get('first_seen') or datetime.now(),
                    app=proc[:256] if proc else None,
                    window_title=title[:1024] if title else None,
                    summary=summary,
                    topic_keywords=', '.join(keywords) if keywords else None,
                    duration_on_tab=duration,
                    session_date=today,
                )
                session.add(insight)
                saved += 1
            except Exception as e:
                log.debug('Failed to save insight: %s', e)

        if saved:
            try:
                session.commit()
                log.info('Saved %d activity insights.', saved)
            except Exception as e:
                session.rollback()
                log.error('Failed to commit insights: %s', e)

        # Clear accumulated data
        self._tab_dwell.clear()
