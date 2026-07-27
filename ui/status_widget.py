"""
WorkSense — Floating status widget + startup consent dialog.

Shows a small always-on-top pill in the top-right corner of the screen:
  🟢 Recording  00:42:15   [Stop]
  🔴 Paused               [Resume]

Also provides show_consent_dialog() which pops up once on first launch.
Uses only tkinter (stdlib) — no extra dependencies.
"""
import tkinter as tk
from tkinter import messagebox
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
import os

log = logging.getLogger('worksense.widget')

# ── Consent file (same as screen_capture uses) ───────────────────────────────
def _appdata() -> Path:
    base = Path(os.environ.get('APPDATA') or Path.home())
    d = base / 'WorkSense'
    d.mkdir(parents=True, exist_ok=True)
    return d

CONSENT_FILE   = _appdata() / 'screen_consent.txt'
TRACKING_STATE_FILE = _appdata() / 'tracking_paused.txt'


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
    Small always-on-top floating window.
    Call start() from the main thread (or a dedicated thread).
    Call stop_tracking() / resume_tracking() from any thread.
    """

    # Colours
    BG_ACTIVE  = '#1a1a2e'   # dark navy
    BG_PAUSED  = '#2a1a1a'   # dark red-ish
    FG_ACTIVE  = '#00e676'   # green
    FG_PAUSED  = '#ff5252'   # red
    FG_TIME    = '#e0e0e0'
    FG_MUTED   = '#888888'
    BTN_STOP   = '#e53935'
    BTN_RESUME = '#43a047'

    def __init__(self, tracker=None, screen_worker=None):
        self._tracker      = tracker        # ActivityTracker instance
        self._screen_worker = screen_worker # ScreenCaptureWorker instance
        self._tracking     = True
        self._start_time   = datetime.now()
        self._paused_at    = None
        self._elapsed_paused = 0.0         # total seconds spent paused
        self._root         = None
        self._running      = False
        self._lock         = threading.Lock()

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
        total = (datetime.now() - self._start_time).total_seconds()
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
            self._build_ui()
            self._tick()
            self._root.mainloop()
        except Exception as e:
            log.exception('StatusWidget error: %s', e)
        finally:
            self._running = False

    def destroy(self):
        if self._root:
            try: self._root.destroy()
            except Exception: pass

    def _build_ui(self):
        r = self._root
        r.title('WorkSense')
        r.overrideredirect(True)          # no title bar
        r.attributes('-topmost', True)    # always on top
        r.attributes('-alpha', 0.92)      # slight transparency
        r.configure(bg=self.BG_ACTIVE)
        r.resizable(False, False)

        # Position: top-right corner with small margin
        sw = r.winfo_screenwidth()
        r.geometry(f'260x56+{sw - 276}+12')

        # ── drag support ─────────────────────────────────────────────────────
        self._drag_x = 0
        self._drag_y = 0
        r.bind('<Button-1>',   self._on_drag_start)
        r.bind('<B1-Motion>',  self._on_drag_move)

        # ── Layout: left status pill + right buttons ──────────────────────────
        outer = tk.Frame(r, bg=self.BG_ACTIVE, padx=8, pady=6)
        outer.pack(fill='both', expand=True)

        # Status dot + label + timer
        left = tk.Frame(outer, bg=self.BG_ACTIVE)
        left.pack(side='left', fill='y')

        self._dot_lbl = tk.Label(left, text='●', font=('Segoe UI', 11),
                                 fg=self.FG_ACTIVE, bg=self.BG_ACTIVE)
        self._dot_lbl.pack(side='left')

        info = tk.Frame(left, bg=self.BG_ACTIVE)
        info.pack(side='left', padx=4)

        self._status_lbl = tk.Label(info, text='Recording', font=('Segoe UI', 8, 'bold'),
                                    fg=self.FG_ACTIVE, bg=self.BG_ACTIVE)
        self._status_lbl.pack(anchor='w')

        self._timer_lbl = tk.Label(info, text='00:00:00', font=('Consolas', 10, 'bold'),
                                   fg=self.FG_TIME, bg=self.BG_ACTIVE)
        self._timer_lbl.pack(anchor='w')

        # Buttons on right
        right = tk.Frame(outer, bg=self.BG_ACTIVE)
        right.pack(side='right', fill='y')

        self._toggle_btn = tk.Button(
            right, text='⏸ Stop', font=('Segoe UI', 8, 'bold'),
            fg='white', bg=self.BTN_STOP, relief='flat', cursor='hand2',
            padx=8, pady=2, bd=0,
            command=self._on_toggle,
        )
        self._toggle_btn.pack(pady=2)

    def _on_toggle(self):
        if self.is_tracking():
            self.stop_tracking()
        else:
            self.resume_tracking()
        self._refresh_ui()

    def _refresh_ui(self):
        if not self._root:
            return
        active = self.is_tracking()
        bg  = self.BG_ACTIVE  if active else self.BG_PAUSED
        fg  = self.FG_ACTIVE  if active else self.FG_PAUSED
        dot = '●'
        txt = 'Recording' if active else 'Paused'
        btn_txt = '⏸ Stop'  if active else '▶ Resume'
        btn_bg  = self.BTN_STOP if active else self.BTN_RESUME

        self._root.configure(bg=bg)
        for w in (self._dot_lbl, self._status_lbl, self._timer_lbl):
            w.configure(bg=bg)
        for frame in self._root.winfo_children():
            self._set_bg_recursive(frame, bg)

        self._dot_lbl.configure(fg=fg, text=dot)
        self._status_lbl.configure(fg=fg, text=txt)
        self._toggle_btn.configure(text=btn_txt, bg=btn_bg)

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
            secs = int(self.get_elapsed())
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


# ── Singleton reference ───────────────────────────────────────────────────────
_widget: StatusWidget | None = None


def get_widget() -> StatusWidget | None:
    return _widget


def launch_widget(tracker=None, screen_worker=None) -> StatusWidget:
    """
    Create and start the StatusWidget in a background daemon thread.
    Returns the widget instance so callers can call stop/resume on it.
    """
    global _widget
    _widget = StatusWidget(tracker=tracker, screen_worker=screen_worker)

    t = threading.Thread(target=_widget.start, daemon=True, name='StatusWidget')
    t.start()
    return _widget
