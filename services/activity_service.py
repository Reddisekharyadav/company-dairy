"""Workspace- and date-filtered database access used by APIs and reports.

Every public method applies the workspace scope in the SQL query itself.  The
frontend never gets to decide whether isolation is applied; it only selects a
validated scope.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from config.workspace_rules import (
    COMBINED,
    WORKSPACES,
    normalize_scope,
    scope_workspaces,
)
from database.models import (
    ActivityInsight,
    BrowserHistory,
    DailyNote,
    Event,
    FileChangeEvent,
    FileEdit,
    GitActivity,
    Meeting,
    ResearchSession,
    ScreenFrame,
    SearchQuery,
)
from database.session import SessionLocal

log = logging.getLogger("activity_service")


def _to_local_datetime(value: Any, *, end_of_day: bool = False) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.max if end_of_day else time.min)
    text_value = str(value).strip()
    try:
        # Accept both date-only and ISO datetime strings.
        if len(text_value) == 10:
            parsed = datetime.strptime(text_value, "%Y-%m-%d")
            return parsed.replace(hour=23, minute=59, second=59, microsecond=999999) if end_of_day else parsed
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return None


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime

    def as_dict(self) -> Dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def resolve_date_range(
    period: str = "daily",
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> DateRange:
    """Resolve a validated, local, naive datetime range."""
    now = now or datetime.now()
    if start or end:
        parsed_start = _to_local_datetime(start) or now.replace(hour=0, minute=0, second=0, microsecond=0)
        parsed_end = _to_local_datetime(end, end_of_day=True) or now
        if parsed_end < parsed_start:
            parsed_start, parsed_end = parsed_end, parsed_start
        return DateRange(parsed_start, parsed_end)

    if days is not None:
        count = max(1, min(int(days), 3660))
        start_dt = (now - timedelta(days=count)).replace(hour=0, minute=0, second=0, microsecond=0)
        return DateRange(start_dt, now)

    period = (period or "daily").strip().lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period in ("today", "daily", "day"):
        return DateRange(today, now)
    if period in ("yesterday",):
        return DateRange(today - timedelta(days=1), today - timedelta(microseconds=1))
    if period in ("weekly", "week", "7d", "last7"):
        return DateRange(today - timedelta(days=6), now)
    if period in ("monthly", "month", "30d", "last30"):
        return DateRange(today - timedelta(days=29), now)
    if period in ("90d", "quarter"):
        return DateRange(today - timedelta(days=89), now)
    if period in ("all", "all_time", "any", "everything"):
        return DateRange(datetime(1970, 1, 1), now)
    # Unknown period: be conservative and use today.
    return DateRange(today, now)


def workspace_values(scope: str) -> List[str]:
    return scope_workspaces(scope)


def apply_workspace(query, model, scope: str):
    values = workspace_values(scope)
    if len(values) == 1:
        return query.filter(model.workspace == values[0])
    return query.filter(model.workspace.in_(values))


def apply_range(query, model, date_range: DateRange, column=None):
    col = column if column is not None else model.timestamp
    return query.filter(col >= date_range.start, col <= date_range.end)


def _session(session: Optional[Session]) -> Tuple[Session, bool]:
    if session is not None:
        return session, False
    return SessionLocal(), True


class ActivityService:
    """Read-only aggregation helpers.  Never writes records."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    # ── generic helpers ──────────────────────────────────────────────────
    def _query(self, model, scope, date_range, column=None, session=None):
        db, owned = _session(session or self._session)
        q = db.query(model)
        q = apply_workspace(q, model, scope)
        if date_range is not None:
            q = apply_range(q, model, date_range, column=column)
        return db, owned, q

    # ── activity events ──────────────────────────────────────────────────
    def events(self, scope="company", date_range=None, limit=100, offset=0, include_idle=False):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(Event), Event, scope)
            if date_range:
                q = apply_range(q, Event, date_range)
            if not include_idle:
                q = q.filter(Event.idle == False)  # noqa: E712
            return q.order_by(Event.timestamp.desc()).offset(offset).limit(max(1, min(limit, 2000))).all()
        finally:
            if owned:
                db.close()

    def category_totals(self, scope, date_range) -> List[Dict[str, Any]]:
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(Event), Event, scope)
            q = apply_range(q, Event, date_range).filter(Event.idle == False)  # noqa: E712
            rows = q.all()
            totals: Dict[str, float] = {}
            for row in rows:
                key = row.category or "Other"
                totals[key] = totals.get(key, 0.0) + float(row.duration or 0)
            return [
                {"category": k, "seconds": round(v, 1), "hours": round(v / 3600, 3)}
                for k, v in sorted(totals.items(), key=lambda item: -item[1])
            ]
        finally:
            if owned:
                db.close()

    def website_totals(self, scope, date_range) -> List[Dict[str, Any]]:
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(Event), Event, scope)
            q = apply_range(q, Event, date_range).filter(Event.idle == False, Event.website.isnot(None))  # noqa: E712
            totals: Dict[str, Dict[str, Any]] = {}
            for row in q.all():
                if not row.website:
                    continue
                entry = totals.setdefault(row.website, {"seconds": 0.0, "category": row.category or "Other"})
                entry["seconds"] += float(row.duration or 0)
            return [
                {"site": k, "minutes": round(v["seconds"] / 60, 1), "category": v["category"]}
                for k, v in sorted(totals.items(), key=lambda item: -item[1]["seconds"])
            ]
        finally:
            if owned:
                db.close()

    def hour_buckets(self, scope, date_range) -> Dict[str, Dict[str, float]]:
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(Event), Event, scope)
            q = apply_range(q, Event, date_range)
            buckets: Dict[str, Dict[str, float]] = {}
            for row in q.all():
                if not row.timestamp:
                    continue
                hour = str(row.timestamp.hour)
                cat = row.category or "Other"
                buckets.setdefault(hour, {})
                buckets[hour][cat] = buckets[hour].get(cat, 0.0) + float(row.duration or 0)
            return buckets
        finally:
            if owned:
                db.close()

    def daily_totals(self, scope, date_range) -> List[Dict[str, Any]]:
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(Event), Event, scope)
            q = apply_range(q, Event, date_range).filter(Event.idle == False)  # noqa: E712
            totals: Dict[str, float] = {}
            for row in q.all():
                if row.timestamp:
                    key = row.timestamp.strftime("%Y-%m-%d")
                    totals[key] = totals.get(key, 0.0) + float(row.duration or 0)
            return [{"date": k, "seconds": round(v, 1), "hours": round(v / 3600, 2)} for k, v in sorted(totals.items())]
        finally:
            if owned:
                db.close()

    # ── files ────────────────────────────────────────────────────────────
    def file_changes(
        self,
        scope="development",
        date_range=None,
        limit=200,
        offset=0,
        project=None,
        event_type=None,
        search=None,
        order_by="timestamp",
    ) -> List[FileChangeEvent]:
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(FileChangeEvent), FileChangeEvent, scope)
            if date_range:
                q = apply_range(q, FileChangeEvent, date_range)
            if project:
                q = q.filter(FileChangeEvent.project == project)
            if event_type:
                q = q.filter(FileChangeEvent.event_type == event_type)
            if search:
                like = f"%{search.strip()}%"
                q = q.filter(or_(
                    FileChangeEvent.file_path.ilike(like),
                    FileChangeEvent.file_name.ilike(like),
                    FileChangeEvent.project.ilike(like),
                ))
            if order_by == "file":
                q = q.order_by(FileChangeEvent.file_name.asc())
            elif order_by == "project":
                q = q.order_by(FileChangeEvent.project.asc())
            else:
                q = q.order_by(FileChangeEvent.timestamp.desc())
            return q.offset(max(0, offset)).limit(max(1, min(limit, 5000))).all()
        finally:
            if owned:
                db.close()

    def file_durations(self, scope="development", date_range=None, limit=200, search=None):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(FileEdit), FileEdit, scope)
            if date_range:
                q = apply_range(q, FileEdit, date_range)
            if search:
                like = f"%{search.strip()}%"
                q = q.filter(or_(FileEdit.file_path.ilike(like), FileEdit.file_name.ilike(like), FileEdit.project.ilike(like)))
            return q.order_by(FileEdit.duration_sec.desc()).limit(max(1, min(limit, 2000))).all()
        finally:
            if owned:
                db.close()

    def project_totals(self, scope="development", date_range=None) -> List[Dict[str, Any]]:
        db, owned = _session(self._session)
        try:
            projects: Dict[str, Dict[str, Any]] = {}

            q_changes = apply_workspace(db.query(FileChangeEvent), FileChangeEvent, scope)
            if date_range:
                q_changes = apply_range(q_changes, FileChangeEvent, date_range)
            for row in q_changes.all():
                name = row.project or row.project_root or "Unknown project"
                entry = projects.setdefault(name, {"project": name, "changes": 0, "duration_sec": 0.0, "files": set(), "languages": set()})
                entry["changes"] += 1
                if row.file_path:
                    entry["files"].add(row.file_path)
                if row.language:
                    entry["languages"].add(row.language)

            q_edits = apply_workspace(db.query(FileEdit), FileEdit, scope)
            if date_range:
                q_edits = apply_range(q_edits, FileEdit, date_range)
            for row in q_edits.all():
                name = row.project or row.project_root or "Unknown project"
                entry = projects.setdefault(name, {"project": name, "changes": 0, "duration_sec": 0.0, "files": set(), "languages": set()})
                entry["duration_sec"] += float(row.duration_sec or 0)
                if row.file_path:
                    entry["files"].add(row.file_path)
                if row.language:
                    entry["languages"].add(row.language)

            q_events = apply_workspace(db.query(Event), Event, scope).filter(Event.idle == False, Event.project.isnot(None))  # noqa: E712
            if date_range:
                q_events = apply_range(q_events, Event, date_range)
            for row in q_events.all():
                name = row.project or "Unknown project"
                entry = projects.setdefault(name, {"project": name, "changes": 0, "duration_sec": 0.0, "files": set(), "languages": set()})
                entry["duration_sec"] += float(row.duration or 0)
                if row.language:
                    entry["languages"].add(row.language)

            result = []
            for entry in projects.values():
                result.append({
                    "project": entry["project"],
                    "changes": entry["changes"],
                    "hours": round(entry["duration_sec"] / 3600, 2),
                    "duration_sec": round(entry["duration_sec"], 1),
                    "languages": sorted(entry["languages"]),
                    "file_count": len(entry["files"]),
                })
            return sorted(result, key=lambda item: (-item["duration_sec"], -item["changes"], item["project"].lower()))
        finally:
            if owned:
                db.close()

    def language_totals(self, scope="development", date_range=None) -> List[Dict[str, Any]]:
        db, owned = _session(self._session)
        try:
            totals: Dict[str, Dict[str, float]] = {}
            for model, value_attr in ((FileEdit, "duration_sec"), (FileChangeEvent, None)):
                q = apply_workspace(db.query(model), model, scope)
                if date_range:
                    q = apply_range(q, model, date_range)
                for row in q.all():
                    lang = row.language or "Unknown"
                    entry = totals.setdefault(lang, {"seconds": 0.0, "changes": 0})
                    if value_attr:
                        entry["seconds"] += float(getattr(row, value_attr) or 0)
                    else:
                        entry["changes"] += 1
            return [
                {"language": k, "seconds": round(v["seconds"], 1), "hours": round(v["seconds"] / 3600, 2), "changes": int(v["changes"])}
                for k, v in sorted(totals.items(), key=lambda item: -item[1]["seconds"] - item[1]["changes"])
            ]
        finally:
            if owned:
                db.close()

    # ── git ──────────────────────────────────────────────────────────────
    def git_commits(self, scope="development", date_range=None, limit=200, repo=None):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(GitActivity), GitActivity, scope)
            if date_range:
                q = apply_range(q, GitActivity, date_range)
            if repo:
                q = q.filter(GitActivity.repo == repo)
            return q.order_by(GitActivity.timestamp.desc()).limit(max(1, min(limit, 1000))).all()
        finally:
            if owned:
                db.close()

    # ── meetings ─────────────────────────────────────────────────────────
    def meetings(self, scope="company", date_range=None, limit=200):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(Meeting), Meeting, scope)
            if date_range:
                q = q.filter(
                    or_(
                        Meeting.start_time.between(date_range.start, date_range.end),
                        Meeting.end_time.between(date_range.start, date_range.end),
                        (Meeting.start_time <= date_range.start) & ((Meeting.end_time >= date_range.start) | (Meeting.end_time.is_(None))),
                    )
                )
            return q.order_by(Meeting.start_time.desc()).limit(max(1, min(limit, 1000))).all()
        finally:
            if owned:
                db.close()

    # ── research/browser ─────────────────────────────────────────────────
    def search_queries(self, scope, date_range=None, limit=200, bookmarked_only=False):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(SearchQuery), SearchQuery, scope)
            if date_range:
                q = apply_range(q, SearchQuery, date_range)
            if bookmarked_only:
                q = q.filter(SearchQuery.bookmarked == True)  # noqa: E712
            return q.order_by(SearchQuery.timestamp.desc()).limit(max(1, min(limit, 2000))).all()
        finally:
            if owned:
                db.close()

    def research_sessions(self, scope, date_range=None, limit=100):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(ResearchSession), ResearchSession, scope)
            if date_range:
                q = apply_range(q, ResearchSession, date_range, column=ResearchSession.start_time)
            return q.order_by(ResearchSession.start_time.desc()).limit(max(1, min(limit, 500))).all()
        finally:
            if owned:
                db.close()

    def browser_history(self, scope, date_range=None, limit=200):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(BrowserHistory), BrowserHistory, scope)
            if date_range:
                q = apply_range(q, BrowserHistory, date_range)
            return q.order_by(BrowserHistory.timestamp.desc()).limit(max(1, min(limit, 2000))).all()
        finally:
            if owned:
                db.close()

    def insights(self, scope, date_range=None, limit=100):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(ActivityInsight), ActivityInsight, scope)
            if date_range:
                q = apply_range(q, ActivityInsight, date_range)
            return q.order_by(ActivityInsight.timestamp.desc()).limit(max(1, min(limit, 1000))).all()
        finally:
            if owned:
                db.close()

    def notes(self, scope, date_range=None, limit=100):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(DailyNote), DailyNote, scope)
            if date_range:
                q = apply_range(q, DailyNote, date_range)
            return q.order_by(DailyNote.timestamp.desc()).limit(max(1, min(limit, 1000))).all()
        finally:
            if owned:
                db.close()

    def screen_frames(self, scope, date_range=None, limit=100, offset=0, date_str=None):
        db, owned = _session(self._session)
        try:
            q = apply_workspace(db.query(ScreenFrame), ScreenFrame, scope)
            if date_range:
                q = apply_range(q, ScreenFrame, date_range)
            if date_str:
                q = q.filter(ScreenFrame.session_date == date_str)
            return q.order_by(ScreenFrame.timestamp.desc()).offset(max(0, offset)).limit(max(1, min(limit, 500))).all()
        finally:
            if owned:
                db.close()

    # ── summary / KPI ────────────────────────────────────────────────────
    def summary(self, scope="company", date_range=None) -> Dict[str, Any]:
        scope = normalize_scope(scope)
        date_range = date_range or resolve_date_range("daily")
        db, owned = _session(self._session)
        try:
            events_q = apply_workspace(db.query(Event), Event, scope).filter(Event.idle == False)  # noqa: E712
            events_q = apply_range(events_q, Event, date_range)
            events = events_q.all()
            total_sec = sum(float(e.duration or 0) for e in events)
            unique_apps = {e.application for e in events if e.application}

            meetings = self.meetings(scope, date_range, limit=1000)
            meeting_sec = sum(float(m.duration_sec or 0) for m in meetings)

            changes_q = apply_workspace(db.query(FileChangeEvent), FileChangeEvent, scope)
            changes_q = apply_range(changes_q, FileChangeEvent, date_range)
            change_count = changes_q.count()

            edits_q = apply_workspace(db.query(FileEdit), FileEdit, scope)
            edits_q = apply_range(edits_q, FileEdit, date_range)
            file_count = edits_q.count()
            edit_sec = sum(float(row.duration_sec or 0) for row in edits_q.all())

            commits_q = apply_workspace(db.query(GitActivity), GitActivity, scope)
            commits_q = apply_range(commits_q, GitActivity, date_range)
            commit_count = commits_q.count()

            research_q = apply_workspace(db.query(SearchQuery), SearchQuery, scope)
            research_q = apply_range(research_q, SearchQuery, date_range)
            query_count = research_q.count()

            projects = self.project_totals(scope, date_range)
            browser_q = apply_workspace(db.query(BrowserHistory), BrowserHistory, scope)
            browser_q = apply_range(browser_q, BrowserHistory, date_range)
            site_count = db.query(func.count(func.distinct(BrowserHistory.domain))).select_from(BrowserHistory)
            site_count = apply_workspace(site_count, BrowserHistory, scope)
            site_count = apply_range(site_count, BrowserHistory, date_range).scalar() or 0

            return {
                "workspace": scope,
                "workspaces": workspace_values(scope),
                "period": date_range.as_dict(),
                "active_seconds": round(total_sec, 1),
                "active_hours": round(total_sec / 3600, 2),
                "meetings": len(meetings),
                "meeting_seconds": round(meeting_sec, 1),
                "meeting_minutes": round(meeting_sec / 60, 1),
                "file_changes": change_count,
                "files_touched": file_count,
                "estimated_edit_seconds": round(edit_sec, 1),
                "estimated_edit_minutes": round(edit_sec / 60, 1),
                "projects": len([p for p in projects if p.get("project") != "Unknown project"]),
                "git_commits": commit_count,
                "research_queries": query_count,
                "sites": int(site_count),
                "unique_apps": len(unique_apps),
                "top_categories": self.category_totals(scope, date_range)[:8],
                "notes": [
                    {"id": n.id, "content": n.content, "timestamp": n.timestamp.isoformat() if n.timestamp else None}
                    for n in self.notes(scope, date_range, limit=50)
                ],
            }
        finally:
            if owned:
                db.close()


service = ActivityService()

__all__ = [
    "ActivityService",
    "DateRange",
    "resolve_date_range",
    "apply_workspace",
    "apply_range",
    "workspace_values",
    "service",
]
