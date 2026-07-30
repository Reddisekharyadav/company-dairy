"""
WorkSense — Floating status widget + startup consent dialog.

Collapsed view (always visible):
  ● Recording  00:42:15   [☰]

Click ☰ to expand a panel below with:
  [⏸ Pause | ❌ Kill]
  Period: [Today ▾]   [⬇ Download]  [📧 Email]

Uses only tkinter (stdlib) — no extra dependencies.
"""
import tkinter as tk
from tkinter import messagebox, ttk
import threading
import time
import logging
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

log = logging.getLogger('worksense.widget')

# ── Consent file (same as screen_capture uses) ───────────────────────────────
def _appdata() -> Path:
    base = Path(os.environ.get('APPDATA') or Path.home())
    d = base / 'WorkSense'
    d.mkdir(parents=True, exist_ok=True)
    return d


CONSENT_FILE        = _appdata() / 'screen_consent.txt'
TRACKING_STATE_FILE = _appdata() / 'tracking_paused.txt'

# Port the web server is running on (set from main.py if changed)
_SERVER_PORT: int = 8000


def set_server_port(port: int):
    """Call this from main.py if using a non-default port."""
    global _SERVER_PORT
    _SERVER_PORT = port


def get_server_port() -> int:
    return _SERVER_PORT


def is_screen_consent_granted() -> bool:
    return CONSENT_FILE.exists() and CONSENT_FILE.read_text().strip() == 'granted'


def show_consent_dialog() -> bool:
    """
    Show a one-time popup asking the user to allow screen recording.
    Returns True if granted, False if denied.
    Skipped if consent was already given.
    """
    if is_screen_consent_granted():
        return True

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    result = messagebox.askyesno(
        title='WorkSense — Screen Recording Permission',
        message=(
            'WorkSense wants to take periodic screenshots\n'
            'to help you track what you worked on.\n\n'
            'Screenshots are stored ONLY on your computer\n'
            'and never uploaded anywhere.\n\n'
            'Allow screen recording?'
        ),
        icon='question',
        parent=root,
    )
    root.destroy()

    if result:
        CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONSENT_FILE.write_text('granted')
        log.info('Screen capture consent granted via dialog.')
    else:
        CONSENT_FILE.write_text('revoked')
        log.info('Screen capture consent denied via dialog.')

    return result


# ── Floating status widget ────────────────────────────────────────────────────

class StatusWidget:
    """
    Small always-on-top floating pill in the top-right corner.

    Collapsed (default):
        ● Recording  00:42:15   [☰]

    Expanded (click ☰):
        ─────────────────────────────────
        [⏸ Pause]          [❌ Kill]
        Period: [Today ▾]
        [⬇ Download Report]  [📧 Email]
        ─────────────────────────────────
    """

    # ── Colours ──────────────────────────────────────────────────────────────
    BG_ACTIVE   = '#0d1117'
    BG_PAUSED   = '#1a0a0a'
    FG_ACTIVE   = '#3fb950'   # green
    FG_PAUSED   = '#f85149'   # red
    FG_TIME     = '#e6edf3'
    FG_MUTED    = '#8b949e'
    FG_PORT     = '#58a6ff'   # blue
    ACCENT      = '#1e90ff'

    BTN_MENU    = '#21262d'   # dark grey menu button
    BTN_PAUSE   = '#b91c1c'   # deep red for pause
    BTN_RESUME  = '#166534'   # deep green for resume
    BTN_KILL    = '#4c1d95'   # deep purple
    BTN_DL      = '#0d3d6e'   # dark blue download
    BTN_MAIL    = '#0d4d2d'   # dark green email
    PANEL_BG    = '#161b22'
    PANEL_BORDER= '#30363d'

    PILL_W = 280
    PILL_H = 48
    PANEL_W = 280
    PANEL_H = 120   # height of expanded panel
    MARGIN  = 12

    def __init__(self, tracker=None, screen_worker=None, on_exit_callback=None):
        self._tracker           = tracker
        self._screen_worker     = screen_worker
        self._on_exit_callback  = on_exit_callback
        self._tracking          = True
        self._start_time        = datetime.now()
        self._paused_at         = None
        self._elapsed_paused    = 0.0
        self._root              = None
        self._panel_win         = None   # secondary Toplevel for the expanded panel
        self._panel_open        = False
        self._running           = False
        self._lock              = threading.Lock()

        # report period state
        self._period_var        = None   # tk.StringVar

    # ── Public API (thread-safe) ──────────────────────────────────────────────

    def stop_tracking(self):
        with self._lock:
            if self._tracking:
                self._tracking = False
                self._paused_at = time.monotonic()
                if self._tracker:
                    try: self._tracker.stop()
                    except Exception: pass
                if self._screen_worker:
                    try: self._screen_worker.stop()
                    except Exception: pass
                log.info('Tracking paused by user.')

    def resume_tracking(self):
        with self._lock:
            if not self._tracking:
                self._tracking = True
                if self._paused_at is not None:
                    self._elapsed_paused += time.monotonic() - self._paused_at
                    self._paused_at = None
                if self._tracker:
                    try: self._tracker.start()
                    except Exception: pass
                if self._screen_worker:
                    try: self._screen_worker.start()
                    except Exception: pass
                log.info('Tracking resumed by user.')

    def is_tracking(self) -> bool:
        with self._lock:
            return self._tracking

    def get_elapsed(self) -> float:
        """Return elapsed *active* tracking seconds (excludes paused time)."""
        total  = (datetime.now() - self._start_time).total_seconds()
        paused = self._elapsed_paused
        if self._paused_at is not None:
            paused += time.monotonic() - self._paused_at
        return max(0.0, total - paused)

    # ── GUI ──────────────────────────────────────────────────────────────────

    def start(self):
        """Build and run the widget (blocking — run in its own thread)."""
        self._running = True
        try:
            self._root = tk.Tk()
            self._build_pill()
            self._tick()
            self._root.mainloop()
        except Exception as e:
            log.exception('StatusWidget error: %s', e)
        finally:
            self._running = False

    def destroy(self):
        if self._panel_win:
            try: self._panel_win.destroy()
            except Exception: pass
        if self._root:
            try: self._root.destroy()
            except Exception: pass

    # ── PILL ─────────────────────────────────────────────────────────────────

    def _build_pill(self):
        r = self._root
        r.title('WorkSense')
        r.overrideredirect(True)
        r.attributes('-topmost', True)
        r.attributes('-alpha', 0.97)
        r.configure(bg=self.BG_ACTIVE)
        r.resizable(False, False)

        sw = r.winfo_screenwidth()
        x  = sw - self.PILL_W - self.MARGIN
        r.geometry(f'{self.PILL_W}x{self.PILL_H}+{x}+{self.MARGIN}')

        # ── drag ──────────────────────────────────────────────────────────────
        self._drag_x = 0
        self._drag_y = 0

        # outer frame
        outer = tk.Frame(r, bg=self.BG_ACTIVE, padx=10, pady=6)
        outer.pack(fill='both', expand=True)
        outer.bind('<Button-1>',  self._on_drag_start)
        outer.bind('<B1-Motion>', self._on_drag_move)

        # LEFT: dot + status text + timer
        left = tk.Frame(outer, bg=self.BG_ACTIVE)
        left.pack(side='left', fill='y')
        left.bind('<Button-1>',  self._on_drag_start)
        left.bind('<B1-Motion>', self._on_drag_move)

        self._dot_lbl = tk.Label(left, text='●', font=('Segoe UI', 10),
                                 fg=self.FG_ACTIVE, bg=self.BG_ACTIVE)
        self._dot_lbl.pack(side='left')

        info = tk.Frame(left, bg=self.BG_ACTIVE)
        info.pack(side='left', padx=(4, 0))

        self._status_lbl = tk.Label(info, text='Recording',
                                    font=('Segoe UI', 7, 'bold'),
                                    fg=self.FG_ACTIVE, bg=self.BG_ACTIVE)
        self._status_lbl.pack(anchor='w')

        self._timer_lbl = tk.Label(info, text='00:00:00',
                                   font=('Consolas', 10, 'bold'),
                                   fg=self.FG_TIME, bg=self.BG_ACTIVE)
        self._timer_lbl.pack(anchor='w')

        # RIGHT: single ☰ menu button
        self._menu_btn = tk.Button(
            outer,
            text='☰',
            font=('Segoe UI', 11, 'bold'),
            fg='#c9d1d9',
            bg=self.BTN_MENU,
            relief='flat',
            cursor='hand2',
            padx=8, pady=2, bd=0,
            activebackground='#30363d',
            activeforeground='#e6edf3',
            command=self._toggle_panel,
        )
        self._menu_btn.pack(side='right')

    # ── PANEL ────────────────────────────────────────────────────────────────

    def _toggle_panel(self):
        if self._panel_open:
            self._close_panel()
        else:
            self._open_panel()

    def _open_panel(self):
        if self._panel_open:
            return
        self._panel_open = True
        self._menu_btn.configure(text='✕', fg='#f85149')

        rx = self._root.winfo_x()
        ry = self._root.winfo_y()

        pw = tk.Toplevel(self._root)
        self._panel_win = pw
        pw.overrideredirect(True)
        pw.attributes('-topmost', True)
        pw.attributes('-alpha', 0.97)
        pw.configure(bg=self.PANEL_BG)
        pw.resizable(False, False)

        # Position panel directly below the pill
        pw.geometry(f'{self.PANEL_W}x{self.PANEL_H}+{rx}+{ry + self.PILL_H + 2}')

        # Panel border frame
        border = tk.Frame(pw, bg=self.PANEL_BORDER, padx=1, pady=1)
        border.pack(fill='both', expand=True)

        inner = tk.Frame(border, bg=self.PANEL_BG, padx=12, pady=10)
        inner.pack(fill='both', expand=True)

        # ── Row 1: Pause/Resume + Kill ────────────────────────────────────────
        row1 = tk.Frame(inner, bg=self.PANEL_BG)
        row1.pack(fill='x', pady=(0, 8))

        self._toggle_btn = tk.Button(
            row1,
            text='⏸  Pause',
            font=('Segoe UI', 8, 'bold'),
            fg='white', bg=self.BTN_PAUSE, relief='flat', cursor='hand2',
            padx=10, pady=4, bd=0,
            activebackground='#991b1b',
            command=self._on_toggle,
        )
        self._toggle_btn.pack(side='left', fill='x', expand=True, padx=(0, 4))

        self._kill_btn = tk.Button(
            row1,
            text='❌  Kill',
            font=('Segoe UI', 8, 'bold'),
            fg='white', bg=self.BTN_KILL, relief='flat', cursor='hand2',
            padx=10, pady=4, bd=0,
            activebackground='#3b0764',
            command=self._on_kill,
        )
        self._kill_btn.pack(side='right', fill='x', expand=True, padx=(4, 0))

        # ── Row 2: Period dropdown ────────────────────────────────────────────
        row2 = tk.Frame(inner, bg=self.PANEL_BG)
        row2.pack(fill='x', pady=(0, 8))

        tk.Label(
            row2, text='Report Period:',
            font=('Segoe UI', 7), fg=self.FG_MUTED, bg=self.PANEL_BG,
        ).pack(side='left', padx=(0, 6))

        self._period_var = tk.StringVar(value='daily')
        period_menu = ttk.Combobox(
            row2,
            textvariable=self._period_var,
            values=['daily', 'weekly'],
            state='readonly',
            width=8,
            font=('Segoe UI', 7),
        )
        period_menu.pack(side='left')

        # style the combobox a bit
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TCombobox',
                        fieldbackground='#21262d',
                        background='#21262d',
                        foreground='#e6edf3',
                        selectbackground='#1e90ff',
                        selectforeground='#fff',
                        arrowcolor='#8b949e')

        # ── Row 3: Download + Email ───────────────────────────────────────────
        row3 = tk.Frame(inner, bg=self.PANEL_BG)
        row3.pack(fill='x')

        tk.Button(
            row3,
            text='⬇  Download Report',
            font=('Segoe UI', 8, 'bold'),
            fg='#58a6ff', bg=self.BTN_DL, relief='flat', cursor='hand2',
            padx=8, pady=4, bd=0,
            activebackground='#0c2a50',
            command=self._on_download,
        ).pack(side='left', fill='x', expand=True, padx=(0, 4))

        tk.Button(
            row3,
            text='📧  Email Report',
            font=('Segoe UI', 8, 'bold'),
            fg='#3fb950', bg=self.BTN_MAIL, relief='flat', cursor='hand2',
            padx=8, pady=4, bd=0,
            activebackground='#0a3520',
            command=self._on_email,
        ).pack(side='right', fill='x', expand=True, padx=(4, 0))

        # Click outside to close
        pw.bind('<FocusOut>', self._on_panel_focus_out)

        # Update toggle button state
        self._refresh_toggle_btn()

    def _close_panel(self):
        self._panel_open = False
        self._menu_btn.configure(text='☰', fg='#c9d1d9')
        if self._panel_win:
            try:
                self._panel_win.destroy()
            except Exception:
                pass
            self._panel_win = None

    def _on_panel_focus_out(self, event=None):
        """Close panel when user clicks somewhere else."""
        if self._panel_win:
            try:
                # Small delay to let button clicks register first
                self._panel_win.after(200, self._check_focus_lost)
            except Exception:
                pass

    def _check_focus_lost(self):
        try:
            if self._panel_win and not self._panel_win.focus_displayof():
                self._close_panel()
        except Exception:
            pass

    # ── Sync panel position when pill is dragged ──────────────────────────────

    def _sync_panel_pos(self):
        if self._panel_open and self._panel_win:
            try:
                rx = self._root.winfo_x()
                ry = self._root.winfo_y()
                self._panel_win.geometry(
                    f'{self.PANEL_W}x{self.PANEL_H}+{rx}+{ry + self.PILL_H + 2}'
                )
            except Exception:
                pass

    # ── Button handlers ───────────────────────────────────────────────────────

    def _on_toggle(self):
        if self.is_tracking():
            self.stop_tracking()
        else:
            self.resume_tracking()
        self._refresh_ui()
        self._refresh_toggle_btn()

    def _refresh_toggle_btn(self):
        if not self._toggle_btn:
            return
        active = self.is_tracking()
        if active:
            self._toggle_btn.configure(text='⏸  Pause', bg=self.BTN_PAUSE,
                                       activebackground='#991b1b')
        else:
            self._toggle_btn.configure(text='▶  Resume', bg=self.BTN_RESUME,
                                       activebackground='#14532d')

    def _on_kill(self):
        """Fully exit the WorkSense application."""
        log.info('User clicked Kill — shutting down WorkSense.')
        try:
            if self._tracker:
                self._tracker.stop()
        except Exception:
            pass
        try:
            if self._screen_worker:
                self._screen_worker.stop()
        except Exception:
            pass
        if self._on_exit_callback:
            try:
                self._on_exit_callback()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    def _on_download(self):
        """Generate report via API and open the export folder."""
        period = self._period_var.get() if self._period_var else 'daily'
        self._close_panel()

        def _do():
            try:
                import urllib.request, json
                url = f'http://127.0.0.1:{_SERVER_PORT}/api/generate_report?period={period}'
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read())
                # Open the export folder (or PDF directly)
                path = data.get('pdf') or data.get('md') or ''
                if path and os.path.exists(path):
                    folder = os.path.dirname(path)
                    if hasattr(os, 'startfile'):
                        os.startfile(folder)
                    log.info('Download: opened folder %s', folder)
                else:
                    log.warning('Download: no valid path in response: %s', data)
            except Exception as e:
                log.warning('Download report error: %s', e)

        threading.Thread(target=_do, daemon=True).start()

    def _on_email(self):
        """Send report by email via API."""
        period = self._period_var.get() if self._period_var else 'daily'
        self._close_panel()

        def _do():
            try:
                import urllib.request, json
                url = f'http://127.0.0.1:{_SERVER_PORT}/api/send_email?period={period}'
                req = urllib.request.Request(url, method='POST', data=b'')
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                if data.get('status') == 'sent':
                    log.info('Email report sent successfully.')
                else:
                    log.warning('Email report not sent: %s', data)
            except Exception as e:
                log.warning('Email report error: %s', e)

        threading.Thread(target=_do, daemon=True).start()

    def _on_open_dashboard(self, _event=None):
        """Open the dashboard in the default browser."""
        try:
            webbrowser.open(f'http://127.0.0.1:{_SERVER_PORT}')
        except Exception as e:
            log.warning('Could not open dashboard: %s', e)

    # ── UI state refresh ──────────────────────────────────────────────────────

    def _refresh_ui(self):
        if not self._root:
            return
        active = self.is_tracking()
        bg     = self.BG_ACTIVE if active else self.BG_PAUSED
        fg     = self.FG_ACTIVE if active else self.FG_PAUSED
        txt    = 'Recording'   if active else 'Paused'

        self._root.configure(bg=bg)
        self._set_bg_recursive(self._root, bg)
        self._dot_lbl.configure(fg=fg)
        self._status_lbl.configure(fg=fg, text=txt)

    def _set_bg_recursive(self, widget, bg):
        try:
            widget.configure(bg=bg)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._set_bg_recursive(child, bg)

    def _tick(self):
        """Update timer label every second."""
        try:
            secs   = int(self.get_elapsed())
            h, rem = divmod(secs, 3600)
            m, s   = divmod(rem, 60)
            self._timer_lbl.configure(text=f'{h:02d}:{m:02d}:{s:02d}')
        except Exception:
            pass
        if self._root:
            self._root.after(1000, self._tick)

    # ── Drag ─────────────────────────────────────────────────────────────────

    def _on_drag_start(self, e):
        self._drag_x = e.x
        self._drag_y = e.y

    def _on_drag_move(self, e):
        x = self._root.winfo_x() + (e.x - self._drag_x)
        y = self._root.winfo_y() + (e.y - self._drag_y)
        self._root.geometry(f'+{x}+{y}')
        self._sync_panel_pos()


# ── Singleton reference ───────────────────────────────────────────────────────
_widget: StatusWidget | None = None


def get_widget() -> StatusWidget | None:
    return _widget


def launch_widget(tracker=None, screen_worker=None,
                  on_exit_callback=None) -> StatusWidget:
    """
    Create and start the StatusWidget in a background daemon thread.
    Returns the widget instance so callers can call stop/resume on it.
    """
    global _widget
    _widget = StatusWidget(
        tracker=tracker,
        screen_worker=screen_worker,
        on_exit_callback=on_exit_callback,
    )

    t = threading.Thread(target=_widget.start, daemon=True, name='StatusWidget')
    t.start()
    return _widget
