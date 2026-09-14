"""
Excel Exporter — generates a multi-sheet Excel workbook with all tracked data.

Uses openpyxl to create professionally formatted spreadsheets with:
  - Summary sheet with key metrics
  - Activity Timeline
  - Websites & Apps
  - Files Edited
  - Meetings
  - Research Queries
  - AI Screen Notes (auto-generated)
  - Git Commits
"""
import os
import logging
from datetime import datetime, timedelta

log = logging.getLogger('excel_exporter')


def generate_excel(start: datetime, end: datetime, out_folder: str,
                   session_id: str = None) -> str:
    """Generate a multi-sheet Excel workbook and return the file path."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from database.session import SessionLocal

    wb = Workbook()
    session = SessionLocal()

    # ── Styling ───────────────────────────────────────────────────────────────
    header_font = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
    header_fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
    alt_fill = PatternFill(start_color='F0F4F8', end_color='F0F4F8', fill_type='solid')
    title_font = Font(name='Calibri', bold=True, size=14, color='1E3A5F')
    subtitle_font = Font(name='Calibri', size=10, color='64748B')
    thin_border = Border(
        bottom=Side(style='thin', color='E2E8F0'),
    )

    def _style_header(ws, headers, row=1):
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

    def _auto_width(ws, min_width=10, max_width=50):
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = max(min_width, min(max_len + 2, max_width))

    def _add_data_rows(ws, data, start_row=2):
        for row_idx, row_data in enumerate(data, start_row):
            for col_idx, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = thin_border
                cell.alignment = Alignment(vertical='center', wrap_text=True)
                if (row_idx - start_row) % 2 == 1:
                    cell.fill = alt_fill

    try:
        # ════════════════════════════════════════════════════════════════════
        # SHEET 1: Summary
        # ════════════════════════════════════════════════════════════════════
        ws_summary = wb.active
        ws_summary.title = 'Summary'
        ws_summary.cell(row=1, column=1, value='WorkSense AI — Activity Report').font = title_font
        ws_summary.cell(row=2, column=1,
                        value=f"Period: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}").font = subtitle_font
        ws_summary.cell(row=3, column=1,
                        value=f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}").font = subtitle_font

        # Summary metrics
        from database.models import Event, ActivityInsight, FileEdit, Meeting, GitActivity, BrowserHistory, DailyNote

        query_filter = []
        if session_id:
            events = session.query(Event).filter(Event.session_id == session_id, Event.idle == False).all()
        else:
            events = session.query(Event).filter(Event.timestamp >= start, Event.timestamp <= end,
                                                  Event.idle == False).all()

        total_active_sec = sum(e.duration or 0 for e in events)
        unique_apps = len(set(e.application for e in events if e.application))
        unique_sites = len(set(e.website for e in events if e.website))

        summary_data = [
            ('Total Active Time', f"{total_active_sec / 3600:.2f} hours"),
            ('Unique Applications', str(unique_apps)),
            ('Unique Websites', str(unique_sites)),
        ]

        # Top categories
        cat_map = {}
        for e in events:
            cat = e.category or 'Other'
            cat_map[cat] = cat_map.get(cat, 0) + (e.duration or 0)

        row = 5
        _style_header(ws_summary, ['Metric', 'Value'], row)
        row += 1
        for label, val in summary_data:
            ws_summary.cell(row=row, column=1, value=label)
            ws_summary.cell(row=row, column=2, value=val)
            row += 1

        row += 1
        ws_summary.cell(row=row, column=1, value='Activity Categories').font = Font(bold=True, size=12)
        row += 1
        _style_header(ws_summary, ['Category', 'Hours', 'Percentage'], row)
        row += 1
        total_secs = sum(cat_map.values()) or 1
        for cat, secs in sorted(cat_map.items(), key=lambda x: -x[1]):
            ws_summary.cell(row=row, column=1, value=cat)
            ws_summary.cell(row=row, column=2, value=round(secs / 3600, 2))
            ws_summary.cell(row=row, column=3, value=f"{secs / total_secs * 100:.1f}%")
            row += 1

        _auto_width(ws_summary)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 2: Activity Timeline
        # ════════════════════════════════════════════════════════════════════
        ws_timeline = wb.create_sheet('Activity Timeline')
        headers = ['Timestamp', 'Application', 'Window Title', 'Category', 'Duration (sec)']
        _style_header(ws_timeline, headers)

        timeline_data = []
        for e in sorted(events, key=lambda x: x.timestamp or datetime.min, reverse=True)[:500]:
            timeline_data.append([
                e.timestamp.strftime('%Y-%m-%d %H:%M:%S') if e.timestamp else '',
                e.application or '',
                (e.window_title or '')[:100],
                e.category or '',
                round(e.duration or 0, 1),
            ])
        _add_data_rows(ws_timeline, timeline_data)
        _auto_width(ws_timeline)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 3: Study & Research Overview
        # ════════════════════════════════════════════════════════════════════
        ws_sites = wb.create_sheet('Study Overview')
        headers = ['Topic', 'Minutes Spent', 'Key Notes & Summaries']
        _style_header(ws_sites, headers)

        insights = session.query(ActivityInsight).filter(
            ActivityInsight.timestamp >= start, ActivityInsight.timestamp <= end
        ).all()
        
        topic_map = {}
        for i in insights:
            t = i.topic_keywords or "General Activity"
            if t not in topic_map:
                topic_map[t] = {'duration': 0, 'summaries': set()}
            topic_map[t]['duration'] += i.duration_on_tab or 0
            if i.summary:
                topic_map[t]['summaries'].add(i.summary)

        site_data = []
        for topic, info in sorted(topic_map.items(), key=lambda x: -x[1]['duration']):
            mins = info['duration'] / 60.0
            if mins < 1.0: continue
            summaries = " • " + "\n • ".join(list(info['summaries'])[:3])
            site_data.append([
                topic.upper(),
                round(mins, 1),
                summaries,
            ])
        _add_data_rows(ws_sites, site_data)
        _auto_width(ws_sites)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 4: Files Edited
        # ════════════════════════════════════════════════════════════════════
        ws_files = wb.create_sheet('Files Edited')
        headers = ['File', 'Project', 'Language', 'IDE', 'Duration (min)', 'Last Edited']
        _style_header(ws_files, headers)

        if session_id:
            files = session.query(FileEdit).filter(FileEdit.session_id == session_id).order_by(
                FileEdit.duration_sec.desc()).limit(100).all()
        else:
            files = session.query(FileEdit).filter(FileEdit.timestamp >= start, FileEdit.timestamp <= end).order_by(
                FileEdit.duration_sec.desc()).limit(100).all()

        file_data = []
        for f in files:
            file_data.append([
                f.file_name or f.file_path or '',
                f.project or '',
                f.language or '',
                f.editor or '',
                round((f.duration_sec or 0) / 60, 1),
                f.timestamp.strftime('%Y-%m-%d %H:%M') if f.timestamp else '',
            ])
        _add_data_rows(ws_files, file_data)
        _auto_width(ws_files)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 5: Meetings
        # ════════════════════════════════════════════════════════════════════
        ws_meetings = wb.create_sheet('Meetings')
        headers = ['Platform', 'Title', 'Start Time', 'End Time', 'Duration (min)']
        _style_header(ws_meetings, headers)

        if session_id:
            meetings = session.query(Meeting).filter(Meeting.session_id == session_id).all()
        else:
            meetings = session.query(Meeting).filter(Meeting.start_time >= start,
                                                      Meeting.start_time <= end).all()

        meeting_data = []
        for m in meetings:
            meeting_data.append([
                m.platform or '',
                m.title or '',
                m.start_time.strftime('%Y-%m-%d %H:%M') if m.start_time else '',
                m.end_time.strftime('%Y-%m-%d %H:%M') if m.end_time else '',
                round((m.duration_sec or 0) / 60, 1),
            ])
        _add_data_rows(ws_meetings, meeting_data)
        _auto_width(ws_meetings)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 6: Research Queries
        # ════════════════════════════════════════════════════════════════════
        ws_research = wb.create_sheet('Research Queries')
        headers = ['Timestamp', 'Query', 'Source', 'Bookmarked']
        _style_header(ws_research, headers)

        from database.models import SearchQuery
        queries = session.query(SearchQuery).filter(
            SearchQuery.timestamp >= start, SearchQuery.timestamp <= end
        ).order_by(SearchQuery.timestamp.desc()).limit(200).all()

        query_data = []
        for q in queries:
            query_data.append([
                q.timestamp.strftime('%Y-%m-%d %H:%M') if q.timestamp else '',
                q.query or '',
                q.source or '',
                'Yes' if q.bookmarked else '',
            ])
        _add_data_rows(ws_research, query_data)
        _auto_width(ws_research)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 7: AI Screen Notes
        # ════════════════════════════════════════════════════════════════════
        ws_notes = wb.create_sheet('AI Screen Notes')
        headers = ['Timestamp', 'Note', 'Source', 'App Context']
        _style_header(ws_notes, headers)

        notes = session.query(DailyNote).filter(
            DailyNote.timestamp >= start, DailyNote.timestamp <= end
        ).order_by(DailyNote.timestamp.desc()).limit(200).all()

        note_data = []
        for n in notes:
            import json
            ctx = ''
            if n.context_data:
                try:
                    ctx_obj = json.loads(n.context_data)
                    ctx = f"{ctx_obj.get('app', '')} — {ctx_obj.get('window', '')}"[:80]
                except Exception:
                    pass
            note_data.append([
                n.timestamp.strftime('%Y-%m-%d %H:%M') if n.timestamp else '',
                n.content or '',
                n.source or '',
                ctx,
            ])
        _add_data_rows(ws_notes, note_data)
        _auto_width(ws_notes)

        # ════════════════════════════════════════════════════════════════════
        # SHEET 8: Git Commits
        # ════════════════════════════════════════════════════════════════════
        ws_git = wb.create_sheet('Git Commits')
        headers = ['Timestamp', 'Hash', 'Message', 'Author', 'Repository']
        _style_header(ws_git, headers)

        if session_id:
            commits = session.query(GitActivity).filter(GitActivity.session_id == session_id).all()
        else:
            commits = session.query(GitActivity).filter(
                GitActivity.timestamp >= start, GitActivity.timestamp <= end).all()

        git_data = []
        for c in commits:
            git_data.append([
                c.timestamp.strftime('%Y-%m-%d %H:%M') if c.timestamp else '',
                c.commit_hash[:10] if c.commit_hash else '',
                (c.message or '').split('\n')[0][:100],
                c.author or '',
                c.repo or '',
            ])
        _add_data_rows(ws_git, git_data)
        _auto_width(ws_git)

    finally:
        session.close()

    # Save workbook
    os.makedirs(out_folder, exist_ok=True)
    fname = os.path.join(out_folder,
                          f"worksense_report_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.xlsx")
    wb.save(fname)
    log.info('Excel report saved: %s', fname)
    return fname
