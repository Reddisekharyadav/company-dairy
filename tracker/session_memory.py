"""
Session Memory — the cross-session intelligence layer of WorkSense AI.

Responsibilities:
1. save_session_state()  — called on app shutdown / daily scheduler
2. load_last_session()   — called on startup, returns dict for Handoff Card
3. generate_ai_context() — exports context.md + session.json for AI tools

This is what makes WorkSense unique: AI coding tools (Claude Code, Cursor,
Antigravity) can pick up the context.md and immediately know what the user
was working on last session, which files need attention, and what research
topics were active.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger('session_memory')


def _appdata() -> Path:
    base = Path(os.environ.get('APPDATA') or Path.home())
    d = base / 'WorkSense'
    d.mkdir(parents=True, exist_ok=True)
    return d


SESSION_FILE   = _appdata() / 'last_session.json'
CONTEXT_MD     = _appdata() / 'context.md'
CONTEXT_JSON   = _appdata() / 'session.json'


# ── Save ──────────────────────────────────────────────────────────────────────

def save_session_state(period_hours: int = 24) -> dict:
    """
    Collect today's activity from DB, build a snapshot, persist it.
    Returns the snapshot dict.
    """
    try:
        from database.session import SessionLocal
        from database.models import (
            Event, FileEdit, SearchQuery, ResearchSession,
            GitActivity, SessionSnapshot
        )
        session = SessionLocal()
        now = datetime.now()
        since = now - timedelta(hours=period_hours)
        today = now.strftime('%Y-%m-%d')

        # ── Files edited ──────────────────────────────────────────────────────
        file_rows = (session.query(FileEdit)
                     .filter(FileEdit.session_date == today)
                     .order_by(FileEdit.duration_sec.desc())
                     .limit(20).all())
        files_data = [{
            'file': r.file_name,
            'project': r.project,
            'language': r.language,
            'duration_min': round((r.duration_sec or 0) / 60, 1),
            'last_seen': r.timestamp.strftime('%H:%M') if r.timestamp else '',
        } for r in file_rows]

        # ── Events summary ────────────────────────────────────────────────────
        events = (session.query(Event)
                  .filter(Event.timestamp >= since, Event.idle == False)
                  .all())
        cat_map: dict[str, float] = {}
        lang_map: dict[str, float] = {}
        total_active = 0.0
        for e in events:
            cat = e.category or 'Other'
            cat_map[cat] = cat_map.get(cat, 0) + (e.duration or 0)
            if e.language:
                lang_map[e.language] = lang_map.get(e.language, 0) + (e.duration or 0)
            total_active += e.duration or 0

        top_project = None
        top_lang = None
        proj_map: dict[str, float] = {}
        for e in events:
            if e.project:
                proj_map[e.project] = proj_map.get(e.project, 0) + (e.duration or 0)
        if proj_map:
            top_project = max(proj_map, key=lambda k: proj_map[k])
        if lang_map:
            top_lang = max(lang_map, key=lambda k: lang_map[k])

        idle_events = (session.query(Event)
                       .filter(Event.timestamp >= since, Event.idle == True).all())
        total_idle = sum(e.duration or 0 for e in idle_events)

        # ── Research sessions ─────────────────────────────────────────────────
        research_sessions = (session.query(ResearchSession)
                             .filter(ResearchSession.date == today).all())
        topics = []
        search_count = 0
        for rs in research_sessions:
            search_count += rs.query_count or 0
            if rs.topic_label:
                topics.append(rs.topic_label)
            else:
                # Use first query as topic
                sq = (session.query(SearchQuery)
                      .filter(SearchQuery.research_session_id == rs.id)
                      .first())
                if sq:
                    topics.append(sq.query[:40])

        # ── Git activity ──────────────────────────────────────────────────────
        git_rows = (session.query(GitActivity)
                    .filter(GitActivity.timestamp >= since)
                    .order_by(GitActivity.timestamp.desc())
                    .limit(5).all())
        last_commit_hash = git_rows[0].commit_hash if git_rows else None
        last_commit_msg  = git_rows[0].message.strip().split('\n')[0][:80] if git_rows else None
        repos_touched = list({r.repo for r in git_rows})

        # ── Build snapshot ────────────────────────────────────────────────────
        summary = _build_summary_text(
            today=today, files=files_data, categories=cat_map,
            topics=topics, search_count=search_count,
            total_active=total_active, top_project=top_project,
            last_commit=last_commit_msg,
        )
        ai_ctx = _build_ai_context_md(
            today=today, files=files_data, categories=cat_map,
            topics=topics, search_count=search_count,
            total_active=total_active, top_project=top_project,
            top_lang=top_lang, git_rows=git_rows,
        )

        snapshot = {
            'snapshot_date': today,
            'files_json': json.dumps(files_data),
            'top_project': top_project,
            'top_language': top_lang,
            'research_topics_json': json.dumps(topics),
            'search_count': search_count,
            'last_commit_hash': last_commit_hash,
            'last_commit_message': last_commit_msg,
            'repos_touched_json': json.dumps(repos_touched),
            'total_active_sec': total_active,
            'total_idle_sec': total_idle,
            'categories_json': json.dumps(cat_map),
            'summary_text': summary,
            'ai_context_md': ai_ctx,
        }

        # Upsert in DB
        existing = (session.query(SessionSnapshot)
                    .filter(SessionSnapshot.snapshot_date == today).first())
        if existing:
            for k, v in snapshot.items():
                setattr(existing, k, v)
        else:
            from database.models import SessionSnapshot
            ss = SessionSnapshot(**snapshot)
            session.add(ss)
        session.commit()
        session.close()

        # Write to files for AI tools
        SESSION_FILE.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
        CONTEXT_MD.write_text(ai_ctx, encoding='utf-8')
        CONTEXT_JSON.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
        log.info('Session state saved for %s', today)
        return snapshot

    except Exception as e:
        log.exception('save_session_state error: %s', e)
        return {}


# ── Load ──────────────────────────────────────────────────────────────────────

def load_last_session() -> Optional[dict]:
    """
    Load the most recent session snapshot for the dashboard Handoff Card.
    Returns None if no previous session exists.
    """
    try:
        # Try DB first
        from database.session import SessionLocal
        from database.models import SessionSnapshot
        session = SessionLocal()
        try:
            row = (session.query(SessionSnapshot)
                   .order_by(SessionSnapshot.created_at.desc())
                   .first())
            if row:
                files = json.loads(row.files_json or '[]')
                topics = json.loads(row.research_topics_json or '[]')
                repos = json.loads(row.repos_touched_json or '[]')
                cats = json.loads(row.categories_json or '{}')
                return {
                    'date': row.snapshot_date,
                    'files': files,
                    'top_project': row.top_project,
                    'top_language': row.top_language,
                    'topics': topics,
                    'search_count': row.search_count,
                    'last_commit': row.last_commit_message,
                    'repos': repos,
                    'total_active_sec': row.total_active_sec or 0,
                    'categories': cats,
                    'summary': row.summary_text,
                }
        finally:
            session.close()
    except Exception as e:
        log.warning('load_last_session DB error: %s', e)

    # Fallback to JSON file
    try:
        if SESSION_FILE.exists():
            data = json.loads(SESSION_FILE.read_text(encoding='utf-8'))
            return {
                'date': data.get('snapshot_date'),
                'files': json.loads(data.get('files_json') or '[]'),
                'top_project': data.get('top_project'),
                'top_language': data.get('top_language'),
                'topics': json.loads(data.get('research_topics_json') or '[]'),
                'search_count': data.get('search_count', 0),
                'last_commit': data.get('last_commit_message'),
                'repos': json.loads(data.get('repos_touched_json') or '[]'),
                'total_active_sec': data.get('total_active_sec', 0),
                'categories': json.loads(data.get('categories_json') or '{}'),
                'summary': data.get('summary_text'),
            }
    except Exception as e:
        log.warning('load_last_session file error: %s', e)

    return None


# ── AI Context Export ─────────────────────────────────────────────────────────

def generate_ai_context() -> tuple[str, str]:
    """
    Generate fresh AI context files from today's data.
    Returns (context_md_path, session_json_path).
    """
    snap = save_session_state()
    md_path   = str(CONTEXT_MD)
    json_path = str(CONTEXT_JSON)
    return md_path, json_path


def get_context_md_path() -> str:
    return str(CONTEXT_MD)


def get_context_json_path() -> str:
    return str(CONTEXT_JSON)


# ── Renderers ─────────────────────────────────────────────────────────────────

def _build_summary_text(today, files, categories, topics, search_count,
                        total_active, top_project, last_commit) -> str:
    """Plain-text session summary for the dashboard Handoff Card."""
    lines = [f"📅 Session: {today}"]
    h = total_active / 3600
    lines.append(f"⏱  Active time: {h:.1f}h")
    if top_project:
        lines.append(f"💻 Top project: {top_project}")
    if files:
        lines.append(f"📝 Files edited: {len(files)}")
        for f in files[:5]:
            lines.append(f"   • {f['file']} ({f['duration_min']} min)")
    if last_commit:
        lines.append(f"🔀 Last commit: {last_commit}")
    if topics:
        lines.append(f"🔬 Research topics: {', '.join(topics[:3])}")
    if search_count:
        lines.append(f"🔍 Searches: {search_count}")
    return '\n'.join(lines)


def _build_ai_context_md(today, files, categories, topics, search_count,
                          total_active, top_project, top_lang, git_rows) -> str:
    """Markdown context file readable by AI coding assistants."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    h = total_active / 3600

    lines = [
        f"# WorkSense AI — Session Context",
        f"",
        f"**Generated:** {now_str}  ",
        f"**Session date:** {today}  ",
        f"**Active coding time:** {h:.2f} hours",
        f"",
        f"## What I was working on",
        f"",
    ]

    if top_project:
        lines.append(f"- **Top project:** `{top_project}`")
    if top_lang:
        lines.append(f"- **Primary language:** {top_lang}")

    if files:
        lines += ["", "## Files edited (most time first)", ""]
        lines.append("| File | Language | Time (min) | Last seen |")
        lines.append("|------|----------|-----------|-----------|")
        for f in files[:15]:
            lang = f.get('language') or '—'
            proj = f.get('project') or ''
            proj_str = f" ({proj})" if proj else ''
            lines.append(f"| `{f['file']}`{proj_str} | {lang} | {f['duration_min']} | {f['last_seen']} |")

    if git_rows:
        lines += ["", "## Recent git commits", ""]
        for g in git_rows[:5]:
            msg = (g.message or '').strip().split('\n')[0][:70]
            ts  = g.timestamp.strftime('%Y-%m-%d %H:%M') if g.timestamp else ''
            lines.append(f"- `[{g.commit_hash[:7]}]` {msg} — {ts}")

    if categories:
        lines += ["", "## Time by category", ""]
        for cat, secs in sorted(categories.items(), key=lambda x: -x[1]):
            h2 = secs / 3600
            lines.append(f"- **{cat}:** {h2:.2f}h")

    if topics:
        lines += ["", "## Research topics explored", ""]
        for t in topics[:10]:
            lines.append(f"- {t}")
        if search_count:
            lines.append(f"- *(Total searches: {search_count})*")

    lines += [
        "",
        "---",
        "",
        "> *This file is auto-generated by WorkSense AI.*  ",
        "> *Load this as context when starting a new AI coding session to resume seamlessly.*",
    ]

    return '\n'.join(lines)
