"""
Smart Summarizer — generates AI-like activity notes locally, no API keys needed.

Uses extractive summarization: keyword extraction from window titles + OCR text,
combined with per-tab dwell time tracking. Produces human-readable summaries like
"Reading about asyncio on docs.python.org (3m 20s)".

Now enriched with:
- OCR text integration for richer summaries
- Engagement detection (reading vs active_typing vs idle_on_tab)
- Short OCR-derived descriptions stored per insight

Runs as a background thread alongside the main activity tracker.
"""
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from threading import Thread, Event
from typing import Optional
import requests

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
    (r'(?i)antigravity', 'Coding in Antigravity IDE'),
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


def _generate_summary(proc_name: str, title: str, duration_sec: float,
                      ocr_text: str = '') -> str:
    """Generate a human-readable summary of what the user is doing.
    Now OCR-enriched: uses screen text to produce better descriptions.
    """
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


def _generate_ocr_summary(ocr_text: str, proc_name: str, title: str) -> Optional[str]:
    """Generate a short 1-2 line description from OCR screen text.
    
    This produces human-readable descriptions like:
    - "Reading Python docs about asyncio event loops and coroutine patterns"
    - "Viewing code with functions for database session management"
    - "Browsing Stack Overflow thread about React hooks"
    """
    if not ocr_text or len(ocr_text.strip()) < 30:
        return None
    
    text = ocr_text[:2000]  # limit for performance
    
    # Extract meaningful keywords from OCR
    keywords = _extract_keywords(text, max_keywords=8)
    if not keywords:
        return None
    
    # Detect content type from OCR
    code_indicators = [
        r'def\s+\w+\s*\(', r'class\s+\w+', r'import\s+\w+', r'from\s+\w+\s+import',
        r'function\s+\w+', r'const\s+\w+', r'return\s+', r'console\.log',
    ]
    has_code = sum(1 for p in code_indicators if re.search(p, text)) >= 2
    
    # Detect if reading documentation
    doc_indicators = ['documentation', 'docs', 'api reference', 'tutorial',
                      'getting started', 'guide', 'example', 'usage']
    has_docs = any(ind in text.lower() for ind in doc_indicators)
    
    # Build summary
    topic_words = ', '.join(keywords[:4])
    
    if has_code:
        return f"Viewing code related to: {topic_words}"
    elif has_docs:
        return f"Reading documentation about: {topic_words}"
    else:
        # Use title context
        topic = _extract_topic_from_title(title)
        if topic:
            return f"Engaged with \"{topic[:60]}\" — topics: {topic_words}"
        return f"Screen content about: {topic_words}"


def _detect_engagement(input_state: str, dwell_seconds: float,
                       ocr_text: str = '') -> str:
    """Determine engagement type from input state and dwell time.
    
    Returns one of: 'active_typing', 'reading', 'browsing', 'idle_on_tab'
    
    Logic:
    - active_typing: user is typing/clicking frequently
    - reading: user is on a page for 30s+ with little input (likely reading)
    - browsing: quick page visits (<30s) 
    - idle_on_tab: tab is open but no engagement signals
    """
    state = (input_state or '').lower()
    
    if 'typing' in state or 'active' in state:
        return 'active_typing'
    
    if dwell_seconds >= 30:
        # Long dwell + no typing = likely reading
        if ocr_text and len(ocr_text.strip()) > 50:
            return 'reading'
        return 'idle_on_tab'
    
    if dwell_seconds >= 5:
        return 'browsing'
    
    return 'idle_on_tab'


def _generate_ai_summary(api_key: str, proc: str, title: str, duration: float) -> str:
    """Use free AI API (Groq) to generate a summary."""
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        prompt = f"The user spent {duration} seconds on this application: '{proc}' with window title '{title}'. Generate a very brief 1-sentence summary of what they were doing. Start with an action verb (e.g. 'Reading...', 'Writing...', 'Browsing...'). Do not say 'The user was'."
        payload = {
            "model": "llama3-8b-8192",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 50,
            "temperature": 0.3
        }
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=5)
        if res.status_code == 200:
            return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("AI summary failed: %s", e)
    return ""


class SmartSummarizer:
    """Background thread that tracks per-tab dwell time and generates summaries.
    
    Now enriched with:
    - OCR text integration for richer descriptions
    - Engagement type detection (reading/typing/idle)
    - Per-tab OCR summaries stored in ActivityInsight
    """

    def __init__(self, interval: float = 30.0, summary_interval: float = 300.0):
        self.interval = interval            # How often to sample active window
        self.summary_interval = summary_interval  # How often to flush summaries to DB (5 min)
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

        # Per-tab dwell tracking: {(proc, title_hash): {'duration', 'proc', 'title', 'first_seen', 'input_states'}}
        self._tab_dwell = defaultdict(lambda: {
            'duration': 0.0, 'proc': '', 'title': '', 'first_seen': None,
            'input_states': []
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
        from database.models import Event, OCRText, ActivityInsight
        from config.settings import SESSION_ID
        from tracker.active_window import get_active_window
        from tracker.input_tracker import input_tracker

        session = SessionLocal()
        last_flush = time.time()

        try:
            while not self._stop.is_set():
                now = time.time()
                proc, title = get_active_window()
                window_key = (proc or '', (title or '')[:100])

                # Get current input state for engagement detection
                try:
                    current_input_state = input_tracker.get_current_state()
                except Exception:
                    current_input_state = 'unknown'

                # Update dwell time for previous window
                if self._last_window and self._last_sample_time:
                    elapsed = now - self._last_sample_time
                    lw = self._last_window
                    entry = self._tab_dwell[lw]
                    entry['duration'] += elapsed
                    entry['input_states'].append(current_input_state)
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

    def _determine_engagement(self, data: dict) -> str:
        """Determine engagement type from accumulated input states and dwell time."""
        states = data.get('input_states', [])
        duration = data.get('duration', 0)
        
        if not states:
            return _detect_engagement('unknown', duration)
        
        # Count input states
        typing_count = sum(1 for s in states if 'typing' in (s or '').lower() or 'active' in (s or '').lower())
        total = len(states)
        
        if total > 0 and typing_count / total > 0.3:
            return 'active_typing'
        
        if duration >= 30:
            return 'reading'
        elif duration >= 5:
            return 'browsing'
        
        return 'idle_on_tab'

    def _get_recent_ocr(self, session, timestamp, window_minutes=5):
        """Get the most recent OCR text from around the given timestamp."""
        try:
            from database.models import OCRText
            start = timestamp - timedelta(minutes=window_minutes)
            end = timestamp + timedelta(minutes=1)
            ocr = (session.query(OCRText)
                   .filter(OCRText.timestamp >= start, OCRText.timestamp <= end)
                   .order_by(OCRText.timestamp.desc())
                   .first())
            if ocr and ocr.text and not ocr.text.startswith('['):
                return ocr.text
        except Exception as e:
            log.debug('OCR fetch error: %s', e)
        return ''

    def _flush_insights(self, session):
        """Generate and save activity insights from accumulated dwell data."""
        from database.models import ActivityInsight
        from config.settings import SESSION_ID

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
            first_seen = data.get('first_seen') or datetime.now()
            
            # Determine engagement type
            engagement = self._determine_engagement(data)
            
            # Get OCR text for richer summary
            ocr_text = self._get_recent_ocr(session, first_seen)
            ocr_summary = _generate_ocr_summary(ocr_text, proc, title) if ocr_text else None
            
            # Generate summary (AI or local)
            from config.settings import settings
            summary = ""
            if hasattr(settings, 'ai_api_key') and settings.ai_api_key:
                summary = _generate_ai_summary(settings.ai_api_key, proc, title, duration)
                
            if not summary:
                summary = _generate_summary(proc, title, duration, ocr_text)
                
            keywords = _extract_keywords(f'{title} {proc} {ocr_text[:500] if ocr_text else ""}')

            try:
                insight = ActivityInsight(
                    timestamp=first_seen,
                    session_id=SESSION_ID,
                    app=proc[:256] if proc else None,
                    window_title=title[:1024] if title else None,
                    summary=summary,
                    topic_keywords=', '.join(keywords) if keywords else None,
                    duration_on_tab=duration,
                    session_date=today,
                    engagement_type=engagement,
                    ocr_summary=ocr_summary[:1024] if ocr_summary else None,
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
