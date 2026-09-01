"""
Meeting Tracker — detects and records video/voice meetings.

Detects when the user is in a Microsoft Teams, Zoom, or Google Meet session
by checking the active window process and title. Records meeting start/end
times, duration, platform, and extracted meeting title.
"""
import logging
import re
import time
from datetime import datetime
from threading import Thread, Event

log = logging.getLogger('meeting_tracker')

# Platform detection rules: (process_keywords, title_keywords, platform_name)
MEETING_RULES = [
    # Microsoft Teams (desktop app)
    (['teams.exe', 'ms-teams.exe'], ['meeting', 'call', 'join'], 'Teams'),
    # Microsoft Teams (browser)
    (['chrome.exe', 'msedge.exe', 'firefox.exe'], ['teams.microsoft.com', 'microsoft teams'], 'Teams'),
    # Zoom (desktop app)
    (['zoom.exe'], ['zoom meeting', 'zoom'], 'Zoom'),
    # Zoom (browser)
    (['chrome.exe', 'msedge.exe', 'firefox.exe'], ['zoom.us'], 'Zoom'),
    # Google Meet (browser only)
    (['chrome.exe', 'msedge.exe', 'firefox.exe'], ['meet.google.com', 'google meet'], 'Meet'),
    # Webex
    (['webex.exe', 'ciscowebex.exe'], [], 'Webex'),
    (['chrome.exe', 'msedge.exe', 'firefox.exe'], ['webex.com'], 'Webex'),
]

# Minimum seconds in a meeting window to count as a real meeting
MIN_MEETING_DURATION = 120  # 2 minutes


def _detect_meeting(proc_name: str, title: str):
    """Return platform name if the current window is a meeting, else None."""
    proc = (proc_name or '').lower()
    t = (title or '').lower()

    for proc_keywords, title_keywords, platform in MEETING_RULES:
        proc_match = any(k in proc for k in proc_keywords)
        if proc_match:
            if not title_keywords:
                # Desktop app match (e.g., zoom.exe) — always a meeting
                return platform
            if any(k in t for k in title_keywords):
                return platform
    return None


def _extract_meeting_title(title: str, platform: str) -> str:
    """Extract a clean meeting title from the window title."""
    if not title:
        return f'{platform} Meeting'

    # Remove browser suffixes
    for suffix in ['- Google Chrome', '- Microsoft Edge', '- Firefox',
                   '| Microsoft Teams', '- Zoom Meeting', '- Zoom']:
        title = title.replace(suffix, '').strip()

    # Remove common prefixes
    for prefix in ['Meeting |', 'Zoom Meeting -']:
        if title.startswith(prefix):
            title = title[len(prefix):].strip()

    # Truncate overly long titles
    if len(title) > 200:
        title = title[:200] + '…'

    return title or f'{platform} Meeting'


class MeetingTracker:
    """Background thread that detects meetings and logs them to the database."""

    def __init__(self, interval: float = 10.0):
        self.interval = interval
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._current_meeting = None  # {'platform', 'title', 'start_time'}

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()
        log.info('MeetingTracker started (interval=%ds)', self.interval)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3.0)

    def _run(self):
        from database.session import SessionLocal
        from database.models import Meeting
        from tracker.active_window import get_active_window

        session = SessionLocal()
        try:
            while not self._stop.is_set():
                try:
                    proc, title = get_active_window()
                    platform = _detect_meeting(proc, title)

                    if platform and self._current_meeting is None:
                        # Meeting started
                        self._current_meeting = {
                            'platform': platform,
                            'title': _extract_meeting_title(title, platform),
                            'start_time': datetime.now(),
                        }
                        log.info('Meeting started: %s — %s',
                                 platform, self._current_meeting['title'])

                    elif platform and self._current_meeting:
                        # Still in meeting — update title if it changed
                        new_title = _extract_meeting_title(title, platform)
                        if new_title != self._current_meeting['title']:
                            self._current_meeting['title'] = new_title

                    elif not platform and self._current_meeting:
                        # Meeting ended — save to DB
                        end_time = datetime.now()
                        duration = (end_time - self._current_meeting['start_time']).total_seconds()

                        if duration >= MIN_MEETING_DURATION:
                            m = Meeting(
                                start_time=self._current_meeting['start_time'],
                                end_time=end_time,
                                duration_sec=duration,
                                platform=self._current_meeting['platform'],
                                title=self._current_meeting['title'],
                                session_date=self._current_meeting['start_time'].strftime('%Y-%m-%d'),
                            )
                            session.add(m)
                            session.commit()
                            log.info('Meeting saved: %s — %.0f min — %s',
                                     m.platform, duration / 60, m.title)
                        else:
                            log.debug('Meeting too short (%.0fs), skipping.', duration)

                        self._current_meeting = None

                except Exception as e:
                    log.debug('MeetingTracker tick error: %s', e)

                self._stop.wait(self.interval)

        except Exception as e:
            log.exception('MeetingTracker fatal error: %s', e)
        finally:
            # If app exits while in a meeting, save what we have
            if self._current_meeting:
                try:
                    end_time = datetime.now()
                    duration = (end_time - self._current_meeting['start_time']).total_seconds()
                    if duration >= MIN_MEETING_DURATION:
                        m = Meeting(
                            start_time=self._current_meeting['start_time'],
                            end_time=end_time,
                            duration_sec=duration,
                            platform=self._current_meeting['platform'],
                            title=self._current_meeting['title'],
                            session_date=self._current_meeting['start_time'].strftime('%Y-%m-%d'),
                        )
                        session.add(m)
                        session.commit()
                except Exception:
                    pass
            session.close()
