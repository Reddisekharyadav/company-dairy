"""Utilities to get active window and process information on Windows."""
from typing import Optional, Tuple
import win32gui
import win32process
import psutil


def get_active_window() -> Tuple[Optional[str], Optional[str]]:
    try:
        hwnd = win32gui.GetForegroundWindow()
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        proc = psutil.Process(pid)
        title = win32gui.GetWindowText(hwnd)
        return proc.name(), title
    except Exception:
        return None, None
