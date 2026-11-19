"""Format-specific renderers for the shared report model."""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

from reports.model import ReportData, format_duration


# ── helpers ─────────────────────────────────────────────────────────────────

_UNICODE_REPLACEMENTS = {
    "\u2192": "->", "\u2014": "-", "\u2013": "-", "\u2022": "-",
    "\u25cf": "-", "\u2500": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
}


def _safe(text: Any) -> str:
    value = str(text or "")
    for source, target in _UNICODE_REPLACEMENTS.items():
        value = value.replace(source, target)
    return value.encode("latin-1", "replace").decode("latin-1")


def _clean(text: Any, limit: int = 0) -> str:
    value = str(text or "").replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    if limit and len(value) > limit:
        return value[: max(0, limit - 1)] + "…"
    return value


def _rows_table(rows: Iterable[Iterable[Any]]) -> List[List[Any]]:
    return [[_clean(cell) for cell in row] for row in rows]


_PDF_FONT_CACHE: Optional[Tuple[str, str]] = None


def _pdf_fonts() -> Tuple[str, str]:
    """Register a Unicode-capable TrueType font when one is available.

    ReportLab's built-in Helvetica only supports Latin-1.  Segoe UI/Arial/
    DejaVu cover accented Latin, Greek, Cyrillic and many other scripts.  If no
    suitable font exists we fall back to Helvetica with the old transliteration
    path so PDF generation still succeeds on minimal systems.
    """
    global _PDF_FONT_CACHE
    if _PDF_FONT_CACHE is not None:
        return _PDF_FONT_CACHE

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        _PDF_FONT_CACHE = ("Helvetica", "Helvetica-Bold")
        return _PDF_FONT_CACHE

    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        (os.path.join(windir, "Fonts", "arialuni.ttf"),
         os.path.join(windir, "Fonts", "arialuni.ttf")),
        (os.path.join(windir, "Fonts", "segoeui.ttf"),
         os.path.join(windir, "Fonts", "segoeuib.ttf")),
        (os.path.join(windir, "Fonts", "arial.ttf"),
         os.path.join(windir, "Fonts", "arialbd.ttf")),
        (os.path.join(windir, "Fonts", "calibri.ttf"),
         os.path.join(windir, "Fonts", "calibrib.ttf")),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/Library/Fonts/Arial Unicode.ttf",
         "/Library/Fonts/Arial Unicode.ttf"),
    ]

    for body_path, bold_path in candidates:
        if not os.path.exists(body_path):
            continue
        try:
            pdfmetrics.registerFont(TTFont("WorkSenseBody", body_path))
            if os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont("WorkSenseBody-Bold", bold_path))
                _PDF_FONT_CACHE = ("WorkSenseBody", "WorkSenseBody-Bold")
            else:
                _PDF_FONT_CACHE = ("WorkSenseBody", "WorkSenseBody")
            return _PDF_FONT_CACHE
        except Exception:
            continue

    _PDF_FONT_CACHE = ("Helvetica", "Helvetica-Bold")
    return _PDF_FONT_CACHE


# ── PDF script fonts and rich text ──────────────────────────────────────────

_PDF_SCRIPT_FONTS_CACHE: Optional[Dict[str, Optional[str]]] = None
_ARABIC_SUPPORT_CACHE: Optional[bool] = None


def _is_hangul(ch: str) -> bool:
    code = ord(ch)
    return (
        0xAC00 <= code <= 0xD7A3
        or 0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
    )


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x2E80 <= code <= 0x2EFF
        or 0x3000 <= code <= 0x303F
        or 0x3040 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF66 <= code <= 0xFF9F
    )


def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0x1F1E6 <= code <= 0x1F1FF
        or code in (0x200D, 0xFE0F)
    )


def _is_arabic(ch: str) -> bool:
    code = ord(ch)
    return (
        0x0600 <= code <= 0x06FF
        or 0x0750 <= code <= 0x077F
        or 0x08A0 <= code <= 0x08FF
        or 0xFB50 <= code <= 0xFDFF
        or 0xFE70 <= code <= 0xFEFF
    )


def _arabic_support() -> bool:
    """Return True when reshaping/bidi helpers are importable."""
    global _ARABIC_SUPPORT_CACHE
    if _ARABIC_SUPPORT_CACHE is None:
        try:
            import arabic_reshaper  # noqa: F401
            from bidi.algorithm import get_display  # noqa: F401

            _ARABIC_SUPPORT_CACHE = True
        except Exception:
            _ARABIC_SUPPORT_CACHE = False
    return _ARABIC_SUPPORT_CACHE


def _pdf_script_fonts() -> Dict[str, Optional[str]]:
    """Register CJK/Hangul fonts alongside the main body font.

    Segoe UI/Arial cover Latin, Greek, Cyrillic and Arabic but not CJK; Microsoft
    YaHei/SimSun cover Han and kana, and Malgun Gothic covers Hangul.  Each run is
    wrapped in the matching font so a single paragraph can mix scripts.
    """
    global _PDF_SCRIPT_FONTS_CACHE
    if _PDF_SCRIPT_FONTS_CACHE is not None:
        return _PDF_SCRIPT_FONTS_CACHE

    body_font, bold_font = _pdf_fonts()
    result: Dict[str, Optional[str]] = {
        "body": body_font,
        "bold": bold_font,
        "cjk": None,
        "hangul": None,
        "unicode": body_font != "Helvetica",
    }
    if not result["unicode"]:
        _PDF_SCRIPT_FONTS_CACHE = result
        return result

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        _PDF_SCRIPT_FONTS_CACHE = result
        return result

    windir = os.environ.get("WINDIR", r"C:\Windows")
    cjk_candidates = [
        (os.path.join(windir, "Fonts", "msyh.ttc"), 0),
        (os.path.join(windir, "Fonts", "msjh.ttc"), 0),
        (os.path.join(windir, "Fonts", "simsun.ttc"), 0),
        (os.path.join(windir, "Fonts", "arialuni.ttf"), 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/System/Library/Fonts/PingFang.ttc", 0),
    ]
    hangul_candidates = [
        (os.path.join(windir, "Fonts", "malgun.ttf"), 0),
        (os.path.join(windir, "Fonts", "malgunsl.ttf"), 0),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
    ]

    for path, index in cjk_candidates:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont("WorkSenseCJK", path, subfontIndex=index))
            result["cjk"] = "WorkSenseCJK"
            break
        except Exception:
            continue

    for path, index in hangul_candidates:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont("WorkSenseHangul", path, subfontIndex=index))
            result["hangul"] = "WorkSenseHangul"
            break
        except Exception:
            continue

    _PDF_SCRIPT_FONTS_CACHE = result
    return result


def _emoji_label(ch: str) -> str:
    try:
        return f"[{unicodedata.name(ch).lower()}]"
    except ValueError:
        return "[emoji]"


def _pdf_rich_text(value: Any, fonts: Dict[str, Optional[str]]) -> str:
    """Return ReportLab paragraph markup with per-script fonts.

    Arabic is reshaped and bidi-ordered when the optional helpers are installed.
    Non-BMP emoji cannot be embedded by ReportLab's TrueType subsetter, so they
    are rendered as a readable label such as ``[rocket]`` instead of a box.
    """
    cleaned = str(value or "").replace("\x00", "")
    if not fonts.get("unicode"):
        return escape(_safe(cleaned))

    if _arabic_support() and any(_is_arabic(ch) for ch in cleaned):
        import arabic_reshaper
        from bidi.algorithm import get_display

        cleaned = get_display(arabic_reshaper.reshape(cleaned))

    parts: List[str] = []
    run: List[str] = []
    run_kind = "body"

    def flush() -> None:
        if not run:
            return
        chunk = escape("".join(run))
        if run_kind == "body":
            parts.append(chunk)
        else:
            parts.append(f'<font name="{run_kind}">{chunk}</font>')
        run.clear()

    for ch in cleaned:
        if _is_emoji(ch):
            flush()
            parts.append(_emoji_label(ch))
            run_kind = "body"
            continue

        if _is_hangul(ch) and fonts.get("hangul"):
            kind = fonts["hangul"]
        elif _is_cjk(ch) and fonts.get("cjk"):
            kind = fonts["cjk"]
        else:
            kind = "body"

        if kind != run_kind:
            flush()
            run_kind = kind
        run.append(ch)
    flush()
    return "".join(parts)



# ── Markdown ────────────────────────────────────────────────────────────────

def render_markdown(report: ReportData) -> str:
    meta = report.meta
    lines: List[str] = []
    lines.append(f"# {meta.title}")
    lines.append("")
    lines.append(f"**Workspace:** {meta.workspace_label}  ")
    lines.append(f"**Period:** {meta.period_label}  ")
    lines.append(f"**Generated:** {_clean(meta.generated_at, 19).replace('T', ' ')}")
    lines.append("")

    lines.append("## Key metrics")
    lines.append("")
    lines.append("| Metric | Value | Notes |")
    lines.append("|:--|--:|:--|")
    for kpi in report.kpis():
        lines.append(f"| {kpi['label']} | {kpi['value']} | {kpi['hint']} |")
    lines.append("")

    if report.categories:
        lines.append("## Activity categories")
        lines.append("")
        lines.append("| Category | Time | Hours |")
        lines.append("|:--|--:|--:|")
        for row in report.categories:
            lines.append(f"| {_clean(row['category'])} | {format_duration(row['seconds'])} | {row['hours']} |")
        lines.append("")

    if report.projects:
        lines.append("## Projects")
        lines.append("")
        lines.append("| Project | Changes | Files | Hours | Languages |")
        lines.append("|:--|--:|--:|--:|:--|")
        for row in report.projects:
            languages = ", ".join(row.get("languages") or [])
            lines.append(
                f"| {_clean(row['project'])} | {row['changes']} | {row['file_count']} | {row['hours']} | {_clean(languages)} |"
            )
        lines.append("")

    if report.file_changes:
        lines.append("## File changes (observed filesystem events)")
        lines.append("")
        lines.append("| Time | Event | File | Project | Language | Workspace |")
        lines.append("|:--|:--|:--|:--|:--|:--|")
        for row in report.file_changes[:100]:
            lines.append(
                "| {time} | {event} | {file} | {project} | {language} | {workspace} |".format(
                    time=_clean(row["timestamp"], 19).replace("T", " "),
                    event=_clean(row["event_type"]),
                    file=_clean(row["relative_path"] or row["file_name"]),
                    project=_clean(row["project"]),
                    language=_clean(row["language"]),
                    workspace=_clean(row["workspace"]),
                )
            )
        lines.append("")

    if report.file_durations:
        lines.append("## Estimated focused file time")
        lines.append("")
        lines.append("> This is inferred from the focused editor window, not from keystrokes.")
        lines.append("")
        lines.append("| File | Editor | Project | Time | Workspace |")
        lines.append("|:--|:--|:--|--:|:--|")
        for row in report.file_durations[:100]:
            lines.append(
                "| {file} | {editor} | {project} | {duration} | {workspace} |".format(
                    file=_clean(row["file_name"]),
                    editor=_clean(row["editor"]),
                    project=_clean(row["project"]),
                    duration=format_duration(row["duration_sec"]),
                    workspace=_clean(row["workspace"]),
                )
            )
        lines.append("")

    if report.meetings:
        lines.append("## Meetings")
        lines.append("")
        lines.append("| Start | Platform | Title | Duration |")
        lines.append("|:--|:--|:--|--:|")
        for row in report.meetings:
            lines.append(
                "| {start} | {platform} | {title} | {duration} |".format(
                    start=_clean(row["start_time"], 19).replace("T", " "),
                    platform=_clean(row["platform"]),
                    title=_clean(row["title"], 80),
                    duration=format_duration(row["duration_sec"]),
                )
            )
        lines.append("")

    if report.research_queries:
        lines.append("## Research and search")
        lines.append("")
        lines.append("| Time | Query | Source | Workspace |")
        lines.append("|:--|:--|:--|:--|")
        for row in report.research_queries[:100]:
            lines.append(
                "| {time} | {query} | {source} | {workspace} |".format(
                    time=_clean(row["timestamp"], 19).replace("T", " "),
                    query=_clean(row["query"], 120).replace("|", "/"),
                    source=_clean(row["source"]),
                    workspace=_clean(row["workspace"]),
                )
            )
        lines.append("")

    if report.git_commits:
        lines.append("## Git commits")
        lines.append("")
        lines.append("| Time | Hash | Message | Author | Repo |")
        lines.append("|:--|:--|:--|:--|:--|")
        for row in report.git_commits[:100]:
            lines.append(
                "| {time} | {hash} | {message} | {author} | {repo} |".format(
                    time=_clean(row["timestamp"], 19).replace("T", " "),
                    hash=_clean(row["hash"])[:10],
                    message=_clean(row["message"], 100).replace("|", "/"),
                    author=_clean(row["author"]),
                    repo=_clean(row["repo"], 80),
                )
            )
        lines.append("")

    lines.append("## Notes and limitations")
    lines.append("")
    for limitation in report.limitations:
        lines.append(f"- {limitation}")
    lines.append("")
    lines.append("*WorkSense AI is local-first. Report contents stay on this device.*")
    lines.append("")
    return "\n".join(lines)


def write_markdown(report: ReportData, out_folder: str, filename: str | None = None) -> str:
    os.makedirs(out_folder, exist_ok=True)
    path = os.path.join(out_folder, filename or report_filename(report, "md"))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    return path


# ── DOCX ────────────────────────────────────────────────────────────────────

def write_docx(report: ReportData, out_folder: str, filename: str | None = None) -> str:
    from docx import Document
    from docx.shared import Pt, RGBColor

    os.makedirs(out_folder, exist_ok=True)
    path = os.path.join(out_folder, filename or report_filename(report, "docx"))
    doc = Document()

    styles = doc.styles
    styles["Normal"].font.name = "Calibri"
    styles["Normal"].font.size = Pt(10)

    title = doc.add_heading(report.meta.title, level=0)
    if title.runs:
        title.runs[0].font.color.rgb = RGBColor(0x1E, 0x3A, 0x8A)

    doc.add_paragraph(f"Workspace: {report.meta.workspace_label}")
    doc.add_paragraph(f"Period: {report.meta.period_label}")
    doc.add_paragraph(f"Generated: {report.meta.generated_at.replace('T', ' ')[:19]}")
    doc.add_paragraph()

    doc.add_heading("Key metrics", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    header[0].text, header[1].text, header[2].text = "Metric", "Value", "Notes"
    for kpi in report.kpis():
        cells = table.add_row().cells
        cells[0].text = kpi["label"]
        cells[1].text = kpi["value"]
        cells[2].text = kpi["hint"]

    if report.categories:
        doc.add_heading("Activity categories", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("Category", "Time", "Hours")):
            cell.text = text
        for row in report.categories:
            cells = table.add_row().cells
            cells[0].text = str(row["category"])
            cells[1].text = format_duration(row["seconds"])
            cells[2].text = str(row["hours"])

    if report.projects:
        doc.add_heading("Projects", level=1)
        table = doc.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("Project", "Changes", "Files", "Hours", "Languages")):
            cell.text = text
        for row in report.projects:
            cells = table.add_row().cells
            cells[0].text = str(row["project"])
            cells[1].text = str(row["changes"])
            cells[2].text = str(row["file_count"])
            cells[3].text = str(row["hours"])
            cells[4].text = ", ".join(row.get("languages") or [])

    if report.file_changes:
        doc.add_heading("File changes", level=1)
        doc.add_paragraph("Observed filesystem events; this proves a file changed, not who changed it.")
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("Time", "Event", "File", "Project")):
            cell.text = text
        for row in report.file_changes[:100]:
            cells = table.add_row().cells
            cells[0].text = str(row["timestamp"] or "")[:19].replace("T", " ")
            cells[1].text = str(row["event_type"])
            cells[2].text = str(row["relative_path"] or row["file_name"])[:120]
            cells[3].text = str(row["project"])

    if report.file_durations:
        doc.add_heading("Estimated focused file time", level=1)
        doc.add_paragraph("Inferred from focused editor windows, not from keystrokes.")
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("File", "Editor", "Project", "Time")):
            cell.text = text
        for row in report.file_durations[:100]:
            cells = table.add_row().cells
            cells[0].text = str(row["file_name"])[:120]
            cells[1].text = str(row["editor"])
            cells[2].text = str(row["project"])
            cells[3].text = format_duration(row["duration_sec"])

    if report.meetings:
        doc.add_heading("Meetings", level=1)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("Start", "Platform", "Title", "Duration")):
            cell.text = text
        for row in report.meetings:
            cells = table.add_row().cells
            cells[0].text = str(row["start_time"] or "")[:19].replace("T", " ")
            cells[1].text = str(row["platform"])
            cells[2].text = str(row["title"])[:120]
            cells[3].text = format_duration(row["duration_sec"])

    if report.research_queries:
        doc.add_heading("Research and search", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("Time", "Query", "Source")):
            cell.text = text
        for row in report.research_queries[:100]:
            cells = table.add_row().cells
            cells[0].text = str(row["timestamp"] or "")[:19].replace("T", " ")
            cells[1].text = str(row["query"])[:200]
            cells[2].text = str(row["source"])

    if report.git_commits:
        doc.add_heading("Git commits", level=1)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        for cell, text in zip(table.rows[0].cells, ("Time", "Hash", "Message", "Author")):
            cell.text = text
        for row in report.git_commits[:100]:
            cells = table.add_row().cells
            cells[0].text = str(row["timestamp"] or "")[:19].replace("T", " ")
            cells[1].text = str(row["hash"])[:10]
            cells[2].text = str(row["message"])[:200]
            cells[3].text = str(row["author"])

    doc.add_heading("Notes and limitations", level=1)
    for limitation in report.limitations:
        doc.add_paragraph(limitation, style="List Bullet")
    doc.add_paragraph("WorkSense AI is local-first. Report contents stay on this device.")

    doc.save(path)
    return path


# ── PDF ─────────────────────────────────────────────────────────────────────

def write_pdf(report: ReportData, out_folder: str, filename: str | None = None) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    os.makedirs(out_folder, exist_ok=True)
    path = os.path.join(out_folder, filename or report_filename(report, "pdf"))

    body_font, bold_font = _pdf_fonts()
    script_fonts = _pdf_script_fonts()

    def pdf_text(value: Any) -> str:
        return _pdf_rich_text(value, script_fonts)

    def pdf_cell(value: Any) -> str:
        return _pdf_rich_text(_clean(value), script_fonts)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="WSBody", parent=styles["BodyText"], fontName=body_font,
        fontSize=9, leading=13, textColor=colors.HexColor("#1f2937"),
    ))
    styles.add(ParagraphStyle(
        name="WSSmall", parent=styles["BodyText"], fontName=body_font, fontSize=8, leading=11,
        textColor=colors.HexColor("#4b5563"),
    ))
    styles.add(ParagraphStyle(
        name="WSTitle", parent=styles["Title"], fontName=bold_font, fontSize=20, leading=24,
        textColor=colors.HexColor("#1e3a8a"), spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="WSH2", parent=styles["Heading2"], fontName=bold_font, fontSize=12, leading=15,
        textColor=colors.HexColor("#1e3a8a"), spaceBefore=10, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="WSCell", parent=styles["BodyText"], fontName=body_font, fontSize=7.5, leading=9,
        textColor=colors.HexColor("#1f2937"),
    ))
    styles.add(ParagraphStyle(
        name="WSCellHeader", parent=styles["WSCell"], fontName=bold_font,
        textColor=colors.white,
    ))

    def p(text: Any, style: str = "WSBody") -> Paragraph:
        return Paragraph(pdf_text(text), styles[style])

    def table(data: List[List[Any]], widths: List[float] | None = None) -> Table:
        rows: List[List[Any]] = []
        for index, row in enumerate(data):
            cell_style = styles["WSCellHeader"] if index == 0 else styles["WSCell"]
            rows.append([
                Paragraph(pdf_cell(cell) or " ", cell_style) for cell in row
            ])
        instance = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        instance.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), body_font),
            ("FONTNAME", (0, 0), (-1, 0), bold_font),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("LEADING", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return instance

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=_clean(report.meta.title), author="WorkSense AI",
    )
    story: List[Any] = [
        p(report.meta.title, "WSTitle"),
        p(f"Workspace: {report.meta.workspace_label}", "WSSmall"),
        p(f"Period: {report.meta.period_label}", "WSSmall"),
        p(f"Generated: {report.meta.generated_at.replace('T', ' ')[:19]}", "WSSmall"),
        Spacer(1, 6),
    ]

    story.append(p("Key metrics", "WSH2"))
    story.append(table([["Metric", "Value", "Notes"]] + [
        [k["label"], k["value"], k["hint"]] for k in report.kpis()
    ], widths=[58 * mm, 30 * mm, 88 * mm]))

    if report.categories:
        story.append(p("Activity categories", "WSH2"))
        story.append(table([["Category", "Time", "Hours"]] + [
            [r["category"], format_duration(r["seconds"]), str(r["hours"])]
            for r in report.categories
        ], widths=[80 * mm, 40 * mm, 40 * mm]))

    if report.projects:
        story.append(p("Projects", "WSH2"))
        story.append(table([["Project", "Changes", "Files", "Hours", "Languages"]] + [
            [r["project"], r["changes"], r["file_count"], r["hours"], ", ".join(r.get("languages") or [])]
            for r in report.projects
        ], widths=[50 * mm, 18 * mm, 16 * mm, 16 * mm, 66 * mm]))

    if report.file_changes:
        story.append(PageBreak())
        story.append(p("File changes (observed filesystem events)", "WSH2"))
        story.append(p("A filesystem event proves a file changed; it does not prove who changed it.", "WSSmall"))
        story.append(table([["Time", "Event", "File", "Project", "Workspace"]] + [
            [str(r["timestamp"] or "")[:19].replace("T", " "), r["event_type"],
             r["relative_path"] or r["file_name"], r["project"], r["workspace"]]
            for r in report.file_changes[:150]
        ], widths=[30 * mm, 16 * mm, 66 * mm, 28 * mm, 22 * mm]))

    if report.file_durations:
        story.append(p("Estimated focused file time", "WSH2"))
        story.append(p("Inferred from the focused editor window, not from keystrokes.", "WSSmall"))
        story.append(table([["File", "Editor", "Project", "Time"]] + [
            [r["file_name"], r["editor"], r["project"], format_duration(r["duration_sec"])]
            for r in report.file_durations[:150]
        ], widths=[62 * mm, 28 * mm, 46 * mm, 26 * mm]))

    if report.meetings:
        story.append(p("Meetings", "WSH2"))
        story.append(table([["Start", "Platform", "Title", "Duration"]] + [
            [str(r["start_time"] or "")[:19].replace("T", " "), r["platform"], r["title"],
             format_duration(r["duration_sec"])]
            for r in report.meetings[:100]
        ], widths=[34 * mm, 20 * mm, 74 * mm, 34 * mm]))

    if report.research_queries:
        story.append(PageBreak())
        story.append(p("Research and search", "WSH2"))
        story.append(table([["Time", "Query", "Source", "Workspace"]] + [
            [str(r["timestamp"] or "")[:19].replace("T", " "), r["query"], r["source"], r["workspace"]]
            for r in report.research_queries[:150]
        ], widths=[30 * mm, 88 * mm, 28 * mm, 24 * mm]))

    if report.git_commits:
        story.append(p("Git commits", "WSH2"))
        story.append(table([["Time", "Hash", "Message", "Author"]] + [
            [str(r["timestamp"] or "")[:19].replace("T", " "), str(r["hash"])[:10],
             r["message"], r["author"]]
            for r in report.git_commits[:150]
        ], widths=[30 * mm, 20 * mm, 82 * mm, 34 * mm]))

    story.append(p("Notes and limitations", "WSH2"))
    for limitation in report.limitations:
        story.append(p(f"- {limitation}", "WSBody"))
    story.append(Spacer(1, 4))
    story.append(p("WorkSense AI is local-first. Report contents stay on this device.", "WSSmall"))

    doc.build(story)
    return path


# ── naming ──────────────────────────────────────────────────────────────────

def report_filename(report: ReportData, extension: str) -> str:
    """Return a collision-safe filename for this report instance.

    The generation timestamp plus a content hash means two reports for the
    same workspace/date range never overwrite each other, while rendering the
    same report model is deterministic (important for tests and retries).
    """
    start = report.meta.start[:10] or datetime.now().strftime("%Y-%m-%d")
    end = report.meta.end[:10] or start
    try:
        stamp = datetime.fromisoformat(report.meta.generated_at).strftime("%Y%m%d-%H%M%S-%f")
    except Exception:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        fingerprint = json.dumps(report.to_dict(), sort_keys=True, default=str)
    except Exception:
        fingerprint = repr(report)
    digest = hashlib.sha256(fingerprint.encode("utf-8", "replace")).hexdigest()[:8]
    ext = str(extension).lstrip(".")
    return f"worksense_{report.meta.workspace}_{start}_{end}_{stamp}_{digest}.{ext}"


def write_text_summary(report: ReportData) -> str:
    """Plain text fallback kept for the legacy API clients."""
    lines = [
        report.meta.title,
        f"Workspace: {report.meta.workspace_label}",
        f"Period: {report.meta.period_label.replace('→', 'to')}",
        "=" * 60,
        "",
        "KEY METRICS",
        "-" * 40,
    ]
    for kpi in report.kpis():
        lines.append(f"  {kpi['label']:<24} {kpi['value']}")
    if report.categories:
        lines.append("")
        lines.append("ACTIVITY CATEGORIES")
        lines.append("-" * 40)
        for row in report.categories:
            lines.append(f"  {row['category']:<24} {format_duration(row['seconds'])}")
    if report.projects:
        lines.append("")
        lines.append("PROJECTS")
        lines.append("-" * 40)
        for row in report.projects:
            lines.append(f"  {row['project']:<24} {row['hours']}h  {row['changes']} changes")
    if report.file_changes:
        lines.append("")
        lines.append("FILE CHANGES (observed filesystem events)")
        lines.append("-" * 40)
        for row in report.file_changes[:25]:
            lines.append(f"  [{row['event_type']}] {row['relative_path'] or row['file_name']}")
    if report.meetings:
        lines.append("")
        lines.append("MEETINGS")
        lines.append("-" * 40)
        for row in report.meetings:
            lines.append(f"  [{row['platform']}] {row['title']} ({format_duration(row['duration_sec'])})")
    if report.git_commits:
        lines.append("")
        lines.append("GIT COMMITS")
        lines.append("-" * 40)
        for row in report.git_commits[:25]:
            lines.append(f"  [{str(row['hash'])[:8]}] {row['message']}")
    lines.append("")
    for limitation in report.limitations:
        lines.append(f"Note: {limitation}")
    return "\n".join(lines)


__all__ = [
    "render_markdown",
    "write_markdown",
    "write_docx",
    "write_pdf",
    "write_text_summary",
    "report_filename",
]
