"""Utilities to get active window and process information.
Cross-platform: Windows, macOS, Linux.
"""
import platform
import logging
from typing import Optional, Tuple

log = logging.getLogger('active_window')

_system = platform.system()


def get_active_window() -> Tuple[Optional[str], Optional[str]]:
    """
    Return (process_name, window_title) for the currently focused window.
    Works on Windows, macOS, and Linux.
    """
    if _system == 'Windows':
        return _get_active_window_windows()
    elif _system == 'Darwin':
        return _get_active_window_macos()
    elif _system == 'Linux':
        return _get_active_window_linux()
    else:
        return None, None


# ── Windows ───────────────────────────────────────────────────────────────────

def _get_active_window_windows() -> Tuple[Optional[str], Optional[str]]:
    try:
        import win32gui
        import win32process
        import psutil
        hwnd = win32gui.GetForegroundWindow()
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        proc = psutil.Process(pid)
        title = win32gui.GetWindowText(hwnd)
        return proc.name(), title
    except Exception:
        return None, None


# ── macOS ─────────────────────────────────────────────────────────────────────

def _get_active_window_macos() -> Tuple[Optional[str], Optional[str]]:
    try:
        import subprocess
        # Get frontmost application name
        script_app = 'tell application "System Events" to get name of first application process whose frontmost is true'
        proc_result = subprocess.run(
            ['osascript', '-e', script_app],
            capture_output=True, text=True, timeout=3,
        )
        proc_name = proc_result.stdout.strip() if proc_result.returncode == 0 else None

        # Get window title
        if proc_name:
            script_title = (
                f'tell application "System Events" to tell process "{proc_name}" '
                f'to get name of front window'
            )
            title_result = subprocess.run(
                ['osascript', '-e', script_title],
                capture_output=True, text=True, timeout=3,
            )
            title = title_result.stdout.strip() if title_result.returncode == 0 else None
        else:
            title = None

        return proc_name, title
    except Exception:
        return None, None


# ── Linux ─────────────────────────────────────────────────────────────────────

def _get_active_window_linux() -> Tuple[Optional[str], Optional[str]]:
    try:
        import subprocess

        # Try xdotool first (most common)
        try:
            wid_result = subprocess.run(
                ['xdotool', 'getactivewindow'],
                capture_output=True, text=True, timeout=3,
            )
            if wid_result.returncode == 0:
                wid = wid_result.stdout.strip()

                # Get window title
                title_result = subprocess.run(
                    ['xdotool', 'getactivewindow', 'getwindowname'],
                    capture_output=True, text=True, timeout=3,
                )
                title = title_result.stdout.strip() if title_result.returncode == 0 else None

                # Get PID and process name
                pid_result = subprocess.run(
                    ['xdotool', 'getactivewindow', 'getwindowpid'],
                    capture_output=True, text=True, timeout=3,
                )
                proc_name = None
                if pid_result.returncode == 0:
                    pid = pid_result.stdout.strip()
                    try:
                        import psutil
                        proc_name = psutil.Process(int(pid)).name()
                    except Exception:
                        # Fallback: read from /proc
                        try:
                            with open(f'/proc/{pid}/comm', 'r') as f:
                                proc_name = f.read().strip()
                        except Exception:
                            pass

                return proc_name, title
        except FileNotFoundError:
            pass  # xdotool not installed

        # Fallback: try xprop
        try:
            result = subprocess.run(
                ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and '0x' in result.stdout:
                import re
                match = re.search(r'0x[0-9a-fA-F]+', result.stdout)
                if match:
                    wid = match.group(0)
                    name_result = subprocess.run(
                        ['xprop', '-id', wid, 'WM_NAME'],
                        capture_output=True, text=True, timeout=3,
                    )
                    title = None
                    if name_result.returncode == 0:
                        m = re.search(r'"(.+)"', name_result.stdout)
                        title = m.group(1) if m else None
                    return None, title
        except FileNotFoundError:
            pass

        return None, None
    except Exception:
        return None, None
