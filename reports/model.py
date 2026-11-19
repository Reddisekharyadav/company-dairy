"""Shared structured report model.

Every renderer (PDF, DOCX, Markdown, XLSX, plain text) consumes this model so
the numbers can never diverge between formats.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.workspace_rules import COMBINED, normalize_scope
from services.activity_service import DateRange, ActivityService, resolve_date_range


WORKSPACE_LABELS = {
    "company": "Company Work",
    "development": "Development Work",
    "personal": "Personal",
    "unclassified": "Unclassified",
    "combined": "Company + Development",
    "all": "All Workspaces",
}


def workspace_label(scope: str) -> str:
    return WORKSPACE_LABELS.get(normalize_scope(scope), normalize_scope(scope).title())


def format_duration(seconds: Any) -> str:
    try:
        total = max(0, int(round(float(seconds or 0))))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass
class ReportMeta:
    title: str
    workspace: str
    workspace_label: str
    period: str
    start: str
    end: str
    generated_at: str

    @property
    def period_label(self) -> str:
        try:
            start_dt = datetime.fromisoformat(self.start)
            end_dt = datetime.fromisoformat(self.end)
            return f"{start_dt:%Y-%m-%d %H:%M} → {end_dt:%Y-%m-%d %H:%M}"
        except Exception:
            return f"{self.start} → {self.end}"


@dataclass
class ReportData:
    """Serializable report payload shared by all renderers."""

    meta: ReportMeta
    summary: Dict[str, Any] = field(default_factory=dict)
    categories: List[Dict[str, Any]] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    file_changes: List[Dict[str, Any]] = field(default_factory=list)
    file_durations: List[Dict[str, Any]] = field(default_factory=list)
    projects: List[Dict[str, Any]] = field(default_factory=list)
    languages: List[Dict[str, Any]] = field(default_factory=list)
    meetings: List[Dict[str, Any]] = field(default_factory=list)
    research_queries: List[Dict[str, Any]] = field(default_factory=list)
    research_sessions: List[Dict[str, Any]] = field(default_factory=list)
    git_commits: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[Dict[str, Any]] = field(default_factory=list)
    insights: List[Dict[str, Any]] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def kpis(self) -> List[Dict[str, str]]:
        s = self.summary
        rows = [
            {"label": "Active tracked time", "value": format_duration(s.get("active_seconds")), "hint": "focused windows, idle excluded"},
            {"label": "Meetings", "value": str(s.get("meetings", 0)), "hint": format_duration(s.get("meeting_seconds"))},
            {"label": "File changes", "value": str(s.get("file_changes", 0)), "hint": "observed filesystem events"},
            {"label": "Files touched", "value": str(s.get("files_touched", 0)), "hint": "focused editor windows"},
            {"label": "Git commits", "value": str(s.get("git_commits", 0)), "hint": "committed work"},
            {"label": "Research queries", "value": str(s.get("research_queries", 0)), "hint": "detected searches"},
            {"label": "Projects", "value": str(s.get("projects", 0)), "hint": "active in range"},
            {"label": "Sites", "value": str(s.get("sites", 0)), "hint": "distinct domains"},
        ]
        return rows


# ── row coercion helpers ─────────────────────────────────────────────────────

def _iso(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _event_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": _iso(r.timestamp),
            "application": r.application or "",
            "window_title": (r.window_title or "")[:200],
            "category": r.category or "Other",
            "project": r.project or "",
            "website": r.website or "",
            "duration_sec": round(float(r.duration or 0), 1),
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def _file_change_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": _iso(r.timestamp),
            "event_type": r.event_type,
            "file_name": r.file_name or "",
            "file_path": r.file_path or "",
            "relative_path": r.relative_path or "",
            "project": r.project or "",
            "language": r.language or "",
            "workspace": r.workspace or "unclassified",
            "application": r.application or "",
            "attribution": r.attribution or "",
            "file_size": r.file_size,
        }
        for r in rows
    ]


def _file_duration_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "file_name": r.file_name or "",
            "file_path": r.file_path or "",
            "project": r.project or "",
            "language": r.language or "",
            "editor": r.editor or "",
            "duration_sec": round(float(r.duration_sec or 0), 1),
            "workspace": r.workspace or "unclassified",
            "source": r.source or "active_window_inference",
            "timestamp": _iso(r.timestamp),
        }
        for r in rows
    ]


def _meeting_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "start_time": _iso(r.start_time),
            "end_time": _iso(r.end_time),
            "platform": r.platform or "",
            "title": r.title or "",
            "duration_sec": round(float(r.duration_sec or 0), 1),
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def _query_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": _iso(r.timestamp),
            "query": r.query or "",
            "source": r.source or "",
            "bookmarked": bool(r.bookmarked),
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def _session_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "start_time": _iso(r.start_time),
            "end_time": _iso(r.end_time),
            "topic": r.topic_label or "",
            "query_count": r.query_count or 0,
            "duration_sec": round(float(r.total_duration_sec or 0), 1),
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def _git_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": _iso(r.timestamp),
            "repo": r.repo or "",
            "hash": r.commit_hash or "",
            "message": (r.message or "").split("\n")[0][:300],
            "author": r.author or "",
            "branch": r.branch or "",
            "files_changed": r.files_changed,
            "insertions": r.insertions,
            "deletions": r.deletions,
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def _note_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": _iso(r.timestamp),
            "content": (r.content or "")[:500],
            "source": r.source or "",
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def _insight_rows(rows) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": _iso(r.timestamp),
            "app": r.app or "",
            "summary": (r.summary or "")[:500],
            "engagement_type": r.engagement_type or "",
            "duration_sec": round(float(r.duration_on_tab or 0), 1),
            "workspace": r.workspace or "unclassified",
        }
        for r in rows
    ]


def build_report(
    scope: str = "company",
    *,
    period: str = "daily",
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
    session=None,
    include_personal: bool = False,
) -> ReportData:
    """Build a complete, workspace-isolated report payload."""
    scope = normalize_scope(scope)
    if scope == "all" and not include_personal:
        # ``all`` must be requested explicitly; keep an audit note.
        pass
    date_range: DateRange = resolve_date_range(period=period, start=start, end=end, days=days, now=now)
    service = ActivityService(session=session)

    summary = service.summary(scope, date_range)
    meta = ReportMeta(
        title=f"WorkSense AI — {workspace_label(scope)} Report",
        workspace=scope,
        workspace_label=workspace_label(scope),
        period=period,
        start=date_range.start.isoformat(),
        end=date_range.end.isoformat(),
        generated_at=datetime.now().isoformat(),
    )

    limitations = [
        "Focused-window file time is an estimate, not proof of typing.",
        "Filesystem events prove a file changed, not who changed it.",
    ]
    if scope == COMBINED:
        limitations.append("Combined scope contains company and development records only; personal and unclassified are excluded.")
    if scope == "all":
        limitations.append("All-workspace scope was explicitly requested and includes personal/unclassified records.")

    return ReportData(
        meta=meta,
        summary=summary,
        categories=service.category_totals(scope, date_range),
        timeline=_event_rows(service.events(scope, date_range, limit=500)),
        file_changes=_file_change_rows(service.file_changes(scope, date_range, limit=500)),
        file_durations=_file_duration_rows(service.file_durations(scope, date_range, limit=200)),
        projects=service.project_totals(scope, date_range),
        languages=service.language_totals(scope, date_range),
        meetings=_meeting_rows(service.meetings(scope, date_range, limit=200)),
        research_queries=_query_rows(service.search_queries(scope, date_range, limit=300)),
        research_sessions=_session_rows(service.research_sessions(scope, date_range, limit=100)),
        git_commits=_git_rows(service.git_commits(scope, date_range, limit=200)),
        notes=_note_rows(service.notes(scope, date_range, limit=100)),
        insights=_insight_rows(service.insights(scope, date_range, limit=100)),
        limitations=limitations,
    )


__all__ = [
    "ReportMeta",
    "ReportData",
    "build_report",
    "format_duration",
    "workspace_label",
]
