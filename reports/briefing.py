"""
Morning Briefing Generator — powers the Company Worker mode.
Generates a "Good morning" card from yesterday's session data.
Now enriched with live activity insights (OCR-derived descriptions).
"""
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger('briefing')


def generate_morning_briefing() -> dict:
    """
    Build today's morning briefing from yesterday's session data.
    Returns a dict ready to be serialized to JSON for the API.
    Now includes live activity insights with OCR summaries.
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

        # --- Always fetch the latest top files and research topics directly ---
        recent_edits = (session.query(FileEdit)
                        .order_by(FileEdit.timestamp.desc())
                        .limit(50).all())
        seen = {}
        for r in recent_edits:
            key = r.file_name or r.file_path
            if key not in seen:
                seen[key] = {
                    'file': r.file_name,
                    'duration_min': round((r.duration_sec or 0) / 60, 1)
                }
        top_files_live = list(seen.values())[:3]
        
        from database.models import SearchQuery
        recent_queries = (session.query(SearchQuery)
                          .order_by(SearchQuery.timestamp.desc())
                          .limit(5).all())
        research_topics_live = [q.query for q in recent_queries]

        # --- Fetch live activity insights (OCR-enriched) ---
        from database.models import ActivityInsight
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        recent_insights = (session.query(ActivityInsight)
                           .filter(ActivityInsight.timestamp >= today_start)
                           .order_by(ActivityInsight.timestamp.desc())
                           .limit(20).all())
        
        activity_timeline = []
        engagement_summary = {'reading': 0, 'active_typing': 0, 'browsing': 0, 'idle_on_tab': 0}
        
        for ins in recent_insights:
            activity_timeline.append({
                'time': ins.timestamp.strftime('%H:%M') if ins.timestamp else '',
                'app': ins.app or '',
                'summary': ins.summary or '',
                'ocr_summary': ins.ocr_summary if hasattr(ins, 'ocr_summary') else None,
                'duration_min': round((ins.duration_on_tab or 0) / 60, 1),
                'engagement': getattr(ins, 'engagement_type', None) or 'unknown',
                'keywords': ins.topic_keywords or '',
            })
            eng = getattr(ins, 'engagement_type', None) or 'idle_on_tab'
            if eng in engagement_summary:
                engagement_summary[eng] += (ins.duration_on_tab or 0)

        # Convert engagement seconds to minutes
        engagement_minutes = {k: round(v / 60, 1) for k, v in engagement_summary.items()}

        if snap:
            hours = (snap.total_active_sec or 0) / 3600

            briefing.update({
                'top_project': snap.top_project,
                'top_language': snap.top_language,
                'hours_worked': round(hours, 1),
                'files_edited_count': len(seen),
                'top_files': top_files_live,
                'last_commit': snap.last_commit_message,
                'repos': json.loads(snap.repos_touched_json or '[]'),
                'research_topics': research_topics_live,
                'search_count': snap.search_count or 0,
                'categories': json.loads(snap.categories_json or '{}'),
                'resume_message': _resume_message(snap, top_files_live),
                'activity_timeline': activity_timeline,
                'engagement': engagement_minutes,
            })
        else:
            # Try raw events from today/yesterday
            start = now.replace(hour=0, minute=0, second=0) - timedelta(days=1)
            end = start + timedelta(days=2)
            events = (session.query(Event)
                      .filter(Event.timestamp >= start,
                              Event.timestamp <= end,
                              Event.idle == False)
                      .all())
            total_sec = sum(e.duration or 0 for e in events)
            briefing.update({
                'hours_worked': round(total_sec / 3600, 1),
                'files_edited_count': len(seen),
                'top_files': top_files_live,
                'last_commit': None,
                'repos': [],
                'research_topics': research_topics_live,
                'search_count': len(research_topics_live),
                'categories': {},
                'resume_message': 'Start fresh today! Your tracking history begins now.',
                'activity_timeline': activity_timeline,
                'engagement': engagement_minutes,
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
