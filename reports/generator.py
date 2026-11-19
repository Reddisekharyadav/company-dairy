"""Report generation entry points.

The public functions keep their historical signatures while delegating to the
shared structured model (:mod:`reports.model`) and the format renderers
(:mod:`reports.renderers`).  ``workspace`` defaults to the safe company scope.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, Iterable, Optional

from config.workspace_rules import normalize_scope
from reports.model import build_report
from reports.renderers import (
    render_markdown,
    write_docx,
    write_markdown,
    write_pdf,
    write_text_summary,
)

__all__ = [
    "summarize_events",
    "generate_markdown",
    "generate_docx",
    "generate_pdf",
    "generate_report",
    "generate_recall_report",
]


def _build(start, end, workspace="company", session_id=None):
    return build_report(
        workspace,
        start=start.isoformat() if isinstance(start, datetime) else str(start),
        end=end.isoformat() if isinstance(end, datetime) else str(end),
        session=None,
        include_personal=(normalize_scope(workspace) == "all"),
    )


def summarize_events(start: datetime, end: datetime, session_id: str = None,
                     workspace: str = "company", scope: str = None) -> str:
    """Return a plain-text summary for the period.

    ``session_id`` is accepted for backwards compatibility; records are still
    filtered by the selected workspace scope.
    """
    report = _build(start, end, workspace=scope or workspace)
    return write_text_summary(report)


def generate_markdown(start: datetime, end: datetime, out_folder: str,
                      workspace: str = "company", session_id: str = None,
                      scope: str = None) -> str:
    report = _build(start, end, workspace=scope or workspace)
    os.makedirs(out_folder, exist_ok=True)
    return write_markdown(report, out_folder)


def generate_docx(start: datetime, end: datetime, out_folder: str,
                  workspace: str = "company", session_id: str = None,
                  scope: str = None) -> str:
    report = _build(start, end, workspace=scope or workspace)
    os.makedirs(out_folder, exist_ok=True)
    return write_docx(report, out_folder)


def generate_pdf(start: datetime, end: datetime, out_folder: str,
                 workspace: str = "company", session_id: str = None,
                 scope: str = None) -> str:
    report = _build(start, end, workspace=scope or workspace)
    os.makedirs(out_folder, exist_ok=True)
    return write_pdf(report, out_folder)


def generate_report(start: datetime, end: datetime, out_folder: str,
                    workspace: str = "company", formats: Iterable[str] = ("md", "pdf", "docx"),
                    session_id: str = None) -> Dict[str, str]:
    """Generate several formats from one report model and return their paths."""
    report = _build(start, end, workspace=workspace)
    os.makedirs(out_folder, exist_ok=True)
    output: Dict[str, str] = {}
    for fmt in formats:
        fmt = str(fmt).lower().lstrip(".")
        if fmt == "md":
            output["md"] = write_markdown(report, out_folder)
        elif fmt == "pdf":
            output["pdf"] = write_pdf(report, out_folder)
        elif fmt == "docx":
            output["docx"] = write_docx(report, out_folder)
    output["report"] = render_markdown(report)
    return output


def generate_recall_report(start: datetime, end: datetime, output_dir: str) -> str:
    """Generate a Microsoft Recall-style Markdown report (no code fences)."""
    from database.session import SessionLocal

    session = SessionLocal()
    try:
        from database.models import ActivityInsight, ScreenFrame

        frames = (
            session.query(ScreenFrame)
            .filter(
                ScreenFrame.timestamp >= start,
                ScreenFrame.timestamp <= end,
                ScreenFrame.is_duplicate == False,  # noqa: E712
            )
            .order_by(ScreenFrame.timestamp.asc())
            .all()
        )

        categories = {}
        hourly_activity = {hour: [] for hour in range(24)}
        topics = {}
        for frame in frames:
            category = frame.detected_category or "other"
            categories[category] = categories.get(category, 0) + 1
            if frame.timestamp:
                hourly_activity[frame.timestamp.hour].append({
                    "app": frame.process_name,
                    "summary": frame.analysis_summary or frame.window_title,
                    "category": category,
                })
            if frame.ocr_text and len(frame.ocr_text) > 50:
                for word in frame.ocr_text.lower().split():
                    if len(word) > 4 and word.isalpha():
                        topics[word] = topics.get(word, 0) + 1

        insights = (
            session.query(ActivityInsight)
            .filter(ActivityInsight.timestamp >= start, ActivityInsight.timestamp <= end)
            .all()
        )
        top_topics = sorted(topics.items(), key=lambda item: item[1], reverse=True)[:20]

        lines = [
            "# WorkSense Recall Report",
            f"## Period: {start.strftime('%Y-%m-%d %H:%M')} - {end.strftime('%Y-%m-%d %H:%M')}",
            "",
            "### Screen Analysis Summary",
            f"- **Total unique frames captured:** {len(frames)}",
            f"- **Activity categories detected:** {len(categories)}",
            "",
        ]
        if categories:
            lines.append("| Category | Frames | % |")
            lines.append("|:---------|-------:|--:|")
            total = sum(categories.values()) or 1
            for category, count in sorted(categories.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"| {category} | {count} | {round(count / total * 100, 1)}% |")
            lines.append("")

        lines.append("### Hourly Activity")
        lines.append("")
        for hour in range(24):
            activities = hourly_activity[hour]
            if activities:
                top_app = max(
                    set(activity["app"] for activity in activities),
                    key=lambda app: sum(1 for activity in activities if activity["app"] == app),
                )
                lines.append(f"  {hour:02d}:00  {'#' * min(len(activities), 20)} ({len(activities)} frames) - {top_app}")
        lines.append("")

        if top_topics:
            lines.append("### Top Topics Detected")
            for word, count in top_topics:
                lines.append(f"  - **{word}** ({count} mentions)")
            lines.append("")

        if insights:
            lines.append(f"### AI Activity Insights ({len(insights)} total)")
            for insight in insights[:15]:
                lines.append(f"  - {insight.summary or 'N/A'}")
            lines.append("")

        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"recall_report_{start.strftime('%Y%m%d')}.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        return path
    finally:
        session.close()
