"""Excel exporter driven by the shared report model.

Sheets: Summary, Timeline, File Changes, Meetings, Research, Git, Projects,
Languages.  All data is filtered by the requested workspace before it reaches
the workbook, so a company export can never include development records.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from config.workspace_rules import normalize_scope
from reports.model import build_report, format_duration
from reports.renderers import report_filename

log = logging.getLogger("excel_exporter")

HEADERS = {
    "summary": ["Metric", "Value", "Notes"],
    "timeline": ["Timestamp", "Application", "Window Title", "Category", "Project", "Website", "Duration (sec)", "Workspace"],
    "files": ["Timestamp", "Event", "File", "Relative Path", "Project", "Language", "Application", "Attribution", "Workspace"],
    "meetings": ["Start", "End", "Platform", "Title", "Duration (min)", "Workspace"],
    "research": ["Timestamp", "Query", "Source", "Bookmarked", "Workspace"],
    "git": ["Timestamp", "Hash", "Message", "Author", "Repository", "Branch", "Files", "Insertions", "Deletions", "Workspace"],
    "projects": ["Project", "Changes", "Files", "Hours", "Languages"],
    "languages": ["Language", "Hours", "Changes"],
}

# Excel interprets text beginning with these characters as a formula.  Report
# rows contain window titles, file names, commit messages and search queries
# from untrusted sources, so prefix them with an apostrophe to force text.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _excel_safe(value):
    if isinstance(value, str):
        value = value.replace("\x00", "")
        if value.startswith(_FORMULA_PREFIXES):
            return "'" + value
    return value



def generate_excel(start: datetime, end: datetime, out_folder: str,
                   session_id: str = None, workspace: str = "company",
                   scope: str = None) -> str:
    """Generate a multi-sheet workbook and return its path."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    scope = normalize_scope(scope or workspace)
    report = build_report(
        scope,
        start=start.isoformat() if isinstance(start, datetime) else str(start),
        end=end.isoformat() if isinstance(end, datetime) else str(end),
        include_personal=(scope == "all"),
    )

    wb = Workbook()
    header_font = Font(name="Calibri", bold=True, size=10, color="FFFFFF")
    header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
    alt_fill = PatternFill(start_color="F0F4F8", end_color="F0F4F8", fill_type="solid")
    title_font = Font(name="Calibri", bold=True, size=14, color="1E3A5F")
    subtitle_font = Font(name="Calibri", size=10, color="64748B")
    thin_border = Border(bottom=Side(style="thin", color="E2E8F0"))

    def style_header(ws, headers, row=1):
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def auto_width(ws, min_width=10, max_width=55):
        for column in ws.columns:
            letter = get_column_letter(column[0].column)
            longest = 0
            for cell in column:
                if cell.value is not None:
                    longest = max(longest, len(str(cell.value)))
            ws.column_dimensions[letter].width = max(min_width, min(longest + 2, max_width))

    def add_rows(ws, rows, start_row=2):
        for row_idx, row_data in enumerate(rows, start_row):
            for col_idx, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=_excel_safe(value))
                cell.border = thin_border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if (row_idx - start_row) % 2 == 1:
                    cell.fill = alt_fill

    def add_metadata(ws, title):
        ws.cell(row=1, column=1, value=title).font = title_font
        ws.cell(row=2, column=1, value=f"Workspace: {report.meta.workspace_label}").font = subtitle_font
        ws.cell(row=3, column=1, value=f"Period: {report.meta.period_label}").font = subtitle_font
        ws.cell(row=4, column=1, value=f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}").font = subtitle_font

    def finalize(ws, header_row=6, data_start=7):
        """Add usable filtering/freezing without making formula-like text executable."""
        if ws.max_row >= data_start:
            last_column = get_column_letter(ws.max_column)
            ws.auto_filter.ref = f"A{header_row}:{last_column}{ws.max_row}"
        ws.freeze_panes = f"A{data_start}"

    # ── Summary ──────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    add_metadata(ws, "WorkSense AI — Activity Report")
    style_header(ws, HEADERS["summary"], row=6)
    summary_rows = [[k["label"], k["value"], k["hint"]] for k in report.kpis()]
    add_rows(ws, summary_rows, start_row=7)
    row = 7 + len(summary_rows) + 1
    ws.cell(row=row, column=1, value="Limitations").font = Font(bold=True, size=11)
    for limitation in report.limitations:
        row += 1
        ws.cell(row=row, column=1, value=limitation)
    auto_width(ws)
    finalize(ws)

    # ── Timeline ─────────────────────────────────────────────────────────
    ws = wb.create_sheet("Timeline")
    add_metadata(ws, "Activity Timeline")
    style_header(ws, HEADERS["timeline"], row=6)
    add_rows(ws, [[
        (r["timestamp"] or "")[:19].replace("T", " "), r["application"], r["window_title"],
        r["category"], r["project"], r["website"], r["duration_sec"], r["workspace"],
    ] for r in report.timeline], start_row=7)
    auto_width(ws)
    finalize(ws)

    # ── File Changes ─────────────────────────────────────────────────────
    ws = wb.create_sheet("File Changes")
    add_metadata(ws, "Observed File Changes")
    style_header(ws, HEADERS["files"], row=6)
    add_rows(ws, [[
        (r["timestamp"] or "")[:19].replace("T", " "), r["event_type"], r["file_name"],
        r["relative_path"], r["project"], r["language"], r["application"],
        r["attribution"], r["workspace"],
    ] for r in report.file_changes], start_row=7)
    auto_width(ws)
    finalize(ws)

    # ── Meetings ─────────────────────────────────────────────────────────
    ws = wb.create_sheet("Meetings")
    add_metadata(ws, "Meetings")
    style_header(ws, HEADERS["meetings"], row=6)
    add_rows(ws, [[
        (r["start_time"] or "")[:19].replace("T", " "),
        (r["end_time"] or "")[:19].replace("T", " "),
        r["platform"], r["title"], round((r["duration_sec"] or 0) / 60, 1), r["workspace"],
    ] for r in report.meetings], start_row=7)
    auto_width(ws)
    finalize(ws)

    # ── Research ─────────────────────────────────────────────────────────
    ws = wb.create_sheet("Research")
    add_metadata(ws, "Research and Search")
    style_header(ws, HEADERS["research"], row=6)
    add_rows(ws, [[
        (r["timestamp"] or "")[:19].replace("T", " "), r["query"], r["source"],
        "Yes" if r["bookmarked"] else "", r["workspace"],
    ] for r in report.research_queries], start_row=7)
    if report.research_sessions:
        row = 9 + len(report.research_queries)
        ws.cell(row=row, column=1, value="Research Sessions").font = Font(bold=True, size=11)
        row += 1
        style_header(ws, ["Start", "End", "Topic", "Queries", "Duration (min)", "Workspace"], row=row)
        for session_row in report.research_sessions:
            row += 1
            ws.cell(row=row, column=1, value=_excel_safe((session_row["start_time"] or "")[:19].replace("T", " ")))
            ws.cell(row=row, column=2, value=_excel_safe((session_row["end_time"] or "")[:19].replace("T", " ")))
            ws.cell(row=row, column=3, value=_excel_safe(session_row["topic"]))
            ws.cell(row=row, column=4, value=session_row["query_count"])
            ws.cell(row=row, column=5, value=round((session_row["duration_sec"] or 0) / 60, 1))
            ws.cell(row=row, column=6, value=session_row["workspace"])
    auto_width(ws)
    finalize(ws)

    # ── Git ──────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Git")
    add_metadata(ws, "Git Commits")
    style_header(ws, HEADERS["git"], row=6)
    add_rows(ws, [[
        (r["timestamp"] or "")[:19].replace("T", " "), str(r["hash"])[:12], r["message"],
        r["author"], r["repo"], r["branch"], r["files_changed"], r["insertions"],
        r["deletions"], r["workspace"],
    ] for r in report.git_commits], start_row=7)
    auto_width(ws)
    finalize(ws)

    # ── Projects ─────────────────────────────────────────────────────────
    ws = wb.create_sheet("Projects")
    add_metadata(ws, "Projects")
    style_header(ws, HEADERS["projects"], row=6)
    add_rows(ws, [[
        r["project"], r["changes"], r["file_count"], r["hours"], ", ".join(r.get("languages") or []),
    ] for r in report.projects], start_row=7)
    auto_width(ws)
    finalize(ws)

    # ── Languages ────────────────────────────────────────────────────────
    ws = wb.create_sheet("Languages")
    add_metadata(ws, "Languages")
    style_header(ws, HEADERS["languages"], row=6)
    add_rows(ws, [[r["language"], r["hours"], r["changes"]] for r in report.languages], start_row=7)
    auto_width(ws)
    finalize(ws)

    os.makedirs(out_folder, exist_ok=True)
    filename = report_filename(report, "xlsx")
    path = os.path.join(out_folder, filename)
    wb.save(path)
    log.info("Excel report saved: %s", path)
    return path
