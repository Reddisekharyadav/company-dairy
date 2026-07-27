from datetime import datetime
from database.session import SessionLocal
from database.models import Event, GitActivity, Report
import os
from jinja2 import Template
from docx import Document
from docx.shared import Pt, RGBColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors


def summarize_events(start: datetime, end: datetime) -> str:
    session = SessionLocal()
    events = session.query(Event).filter(
        Event.timestamp >= start, Event.timestamp <= end
    ).order_by(Event.timestamp).all()

    git_activity = session.query(GitActivity).filter(
        GitActivity.timestamp >= start, GitActivity.timestamp <= end
    ).all()
    session.close()

    lines = []
    lines.append(f"Report: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 55)

    # --- Category breakdown ---
    cat_map = {}
    for e in events:
        if e.idle:
            continue
        cat = e.category or 'Other'
        cat_map.setdefault(cat, 0)
        cat_map[cat] += e.duration or 0

    total_secs = sum(cat_map.values()) or 1
    lines.append("\n🗂  ACTIVITY CATEGORIES")
    lines.append("-" * 30)
    for cat, secs in sorted(cat_map.items(), key=lambda x: -x[1]):
        pct = secs / total_secs * 100
        hours = secs / 3600.0
        bar = '█' * int(pct / 5)
        lines.append(f"  {cat:<22} {hours:5.2f}h  ({pct:4.1f}%)  {bar}")

    # --- Website breakdown ---
    web_map = {}
    for e in events:
        if e.idle or not e.website:
            continue
        web_map.setdefault(e.website, 0)
        web_map[e.website] += e.duration or 0

    if web_map:
        lines.append("\n🌐  WEBSITES & APPS VISITED")
        lines.append("-" * 30)
        for site, secs in sorted(web_map.items(), key=lambda x: -x[1])[:15]:
            mins = secs / 60.0
            lines.append(f"  {site:<30} {mins:5.1f} min")

    # --- Files & projects ---
    file_map = {}
    for e in events:
        if e.opened_file:
            file_map.setdefault(e.opened_file, 0)
            file_map[e.opened_file] += e.duration or 0

    if file_map:
        lines.append("\n💻  FILES WORKED ON")
        lines.append("-" * 30)
        for fname, secs in sorted(file_map.items(), key=lambda x: -x[1])[:10]:
            mins = secs / 60.0
            lines.append(f"  {fname:<40} {mins:4.1f} min")

    # --- Git commits ---
    if git_activity:
        lines.append("\n🔀  GIT COMMITS")
        lines.append("-" * 30)
        for ga in git_activity[:10]:
            msg = (ga.message or '').strip().split('\n')[0][:60]
            lines.append(f"  [{ga.commit_hash[:7]}] {msg}")
            lines.append(f"           by {ga.author}  @ {ga.timestamp.strftime('%H:%M')}")

    lines.append("\n" + "=" * 55)
    lines.append(f"Total tracked time: {total_secs/3600:.2f} hours")

    return "\n".join(lines)


def generate_markdown(start: datetime, end: datetime, out_folder: str):
    summary = summarize_events(start, end)
    fname = os.path.join(out_folder, f"report_{start.date()}_{end.date()}.md")
    with open(fname, "w", encoding="utf-8") as f:
        f.write("# WorkSense AI — Daily Activity Report\n\n")
        f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n")
        f.write("```\n")
        f.write(summary)
        f.write("\n```\n")
    return fname


def generate_docx(start: datetime, end: datetime, out_folder: str):
    summary = summarize_events(start, end)
    doc = Document()

    # Title
    title = doc.add_heading('WorkSense AI — Activity Report', level=1)
    title.runs[0].font.color.rgb = RGBColor(0x1E, 0x90, 0xFF)

    doc.add_paragraph(f"Period: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}")
    doc.add_paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    doc.add_paragraph()

    for line in summary.split('\n'):
        p = doc.add_paragraph(line)
        if line.startswith('🗂') or line.startswith('🌐') or line.startswith('💻') or line.startswith('🔀'):
            p.runs[0].bold = True if p.runs else False

    fname = os.path.join(out_folder, f"report_{start.date()}_{end.date()}.docx")
    doc.save(fname)
    return fname


def generate_pdf(start: datetime, end: datetime, out_folder: str):
    summary = summarize_events(start, end)
    fname = os.path.join(out_folder, f"report_{start.date()}_{end.date()}.pdf")
    c = canvas.Canvas(fname, pagesize=letter)

    # Header
    c.setFillColor(colors.HexColor('#1E90FF'))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 770, "WorkSense AI — Activity Report")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawString(50, 752, f"Period: {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")
    c.drawString(50, 740, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    y = 720
    c.setFont("Courier", 8)
    for line in summary.splitlines():
        if y < 50:
            c.showPage()
            y = 770
            c.setFont("Courier", 8)
        # Highlight section headers
        if line.startswith('🗂') or line.startswith('🌐') or line.startswith('💻') or line.startswith('🔀'):
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.HexColor('#1E90FF'))
        else:
            c.setFont("Courier", 8)
            c.setFillColor(colors.black)
        c.drawString(50, y, line.encode('ascii', 'replace').decode())
        y -= 12

    c.save()
    return fname
