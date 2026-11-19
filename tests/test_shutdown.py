"""Shutdown-safety tests for the Tk status widget and tray Exit path."""
from __future__ import annotations

import threading

from ui.status_widget import StatusWidget


class _FakeRoot:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


def test_destroy_from_foreign_thread_defers_tk_call():
    """Tray Exit runs on the pystray thread; it must not call Tk directly."""
    widget = StatusWidget()
    root = _FakeRoot()
    widget._root = root
    widget._tk_thread_id = -1  # pretend the Tk thread is someone else

    widget.destroy()

    assert widget._quit_requested.is_set()
    assert root.destroyed is False

    # Simulate the Tk _tick callback observing the request.
    widget._destroy_from_tk()

    assert root.destroyed is True
    assert widget._root is None
    assert widget._quit_requested.is_set() is False


def test_destroy_from_tk_thread_is_immediate():
    widget = StatusWidget()
    root = _FakeRoot()
    widget._root = root
    widget._tk_thread_id = threading.get_ident()

    widget.destroy()

    assert root.destroyed is True
    assert widget._root is None
