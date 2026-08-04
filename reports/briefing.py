"""
Morning Briefing Generator — powers the Company Worker mode.
Generates a "Good morning" card from yesterday's session snapshot.
"""
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger('briefing')


def generate_morning_briefing() -> dict:
    """
    Build today's morning briefing from yesterday's session data.
    Returns a dict ready to be serialized to JSON for the API.
    """
    try:
        from database.session import SessionLocal
        from database.models import SessionSnapshot, Event, FileEdit, GitActivity

        session = SessionLocal()
        now = datetime.now()
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        today = now.strftime('%Y-%m-%d')

        # Load yesterday's snapshot
        snap = (session.query(SessionSnapshot)
                .filter(SessionSnapshot.snapshot_date == yesterday)
                .order_by(SessionSnapshot.created_at.desc())
                .first())

        briefing = {
            'greeting': _greeting(now),
            'date': today,
            'yesterday_date': yesterday,
            'has_data': snap is not None,
        }

        if snap:
            files = json.loads(snap.files_json or '[]')
            topics = json.loads(snap.research_topics_json or '[]')
            repos = json.loads(snap.repos_touched_json or '[]')
            cats = json.loads(snap.categories_json or '{}')

            hours = (snap.total_active_sec or 0) / 3600
            top_files = files[:3]

            briefing.update({
                'top_project': snap.top_project,
                'top_language': snap.top_language,
                'hours_worked': round(hours, 1),
                'files_edited_count': len(files),
                'top_files': top_files,
                'last_commit': snap.last_commit_message,
                'repos': repos,
                'research_topics': topics[:5],
                'search_count': snap.search_count or 0,
                'categories': cats,
                'resume_message': _resume_message(snap, top_files),
            })
        else:
            # Try raw events from yesterday
            start = now.replace(hour=0, minute=0, second=0) - timedelta(days=1)
            end = start + timedelta(days=1)
            events = (session.query(Event)
                      .filter(Event.timestamp >= start,
                              Event.timestamp <= end,
                              Event.idle == False)
                      .all())
            total_sec = sum(e.duration or 0 for e in events)
            briefing.update({
                'hours_worked': round(total_sec / 3600, 1),
                'files_edited_count': 0,
                'top_files': [],
                'last_commit': None,
                'repos': [],
                'research_topics': [],
                'search_count': 0,
                'categories': {},
                'resume_message': 'Start fresh today! Your tracking history begins now.',
            })

        session.close()
        return briefing

    except Exception as e:
        log.exception('generate_morning_briefing error: %s', e)
        return {
            'greeting': _greeting(datetime.now()),
            'has_data': False,
            'error': str(e),
        }


def _greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return f"Good morning! 🌅 Ready to pick up where you left off?"
    elif hour < 17:
        return f"Good afternoon! ☀️ Here's your session summary."
    else:
        return f"Good evening! 🌙 Here's what you accomplished today."


def _resume_message(snap, top_files: list) -> str:
    parts = []
    if snap.top_project:
        parts.append(f"You were working on **{snap.top_project}**")
    if top_files:
        fnames = ', '.join(f"`{f['file']}`" for f in top_files[:2])
        parts.append(f"editing {fnames}")
    if snap.last_commit_message:
        msg = snap.last_commit_message[:50]
        parts.append(f"Last commit: *{msg}*")
    if not parts:
        return "Welcome back! Your activity history is ready."
    return ' — '.join(parts) + '.'
