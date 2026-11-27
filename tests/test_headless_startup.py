"""Headless-host regression tests.

The Linux release used to exit with status 1 before the dashboard started
whenever the desktop session was missing (no X display, no tkinter, no tray).
These tests pin the graceful-degradation behaviour.
"""
import pytest

from tracker import input_tracker as input_tracker_module
from tracker.input_tracker import InputTracker


def test_input_tracker_without_pynput_is_inert(monkeypatch):
    """No pynput backend => no crash, no fake activity."""
    monkeypatch.setattr(input_tracker_module, "mouse", None)
    monkeypatch.setattr(input_tracker_module, "keyboard", None)

    tracker = InputTracker()
    assert tracker.available is False

    tracker.start()          # must not raise
    tracker.start()          # idempotent
    assert tracker.get_current_state() == "Idle"

    tracker.stop()           # must not raise
    assert tracker.mouse_listener is None
    assert tracker.keyboard_listener is None


def test_input_tracker_listener_failure_is_contained(monkeypatch):
    """A backend that imports but cannot connect must not escape start()."""

    class BrokenListener:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no display")

    class FakeBackend:
        Listener = BrokenListener

    monkeypatch.setattr(input_tracker_module, "mouse", FakeBackend)
    monkeypatch.setattr(input_tracker_module, "keyboard", FakeBackend)

    tracker = InputTracker()
    tracker.start()

    assert tracker.mouse_listener is None
    assert tracker.keyboard_listener is None
    assert tracker.get_current_state() == "Idle"


def test_consent_dialog_without_tkinter_returns_false(monkeypatch):
    """A missing/broken tkinter must not break the caller."""
    from ui import status_widget

    monkeypatch.setattr(status_widget, "tk", None)
    monkeypatch.setattr(status_widget, "messagebox", None)
    monkeypatch.setattr(status_widget, "is_screen_consent_granted", lambda: False)
    monkeypatch.setattr("platform.system", lambda: "Linux")

    assert status_widget.show_consent_dialog() is False


def test_start_component_swallows_component_errors():
    """One failing tracker must not prevent the FastAPI app from starting."""
    from backend import app as backend_app

    called = []

    def broken():
        raise RuntimeError("xdotool missing")

    backend_app._start_component("Broken tracker", broken)
    backend_app._start_component("Working tracker", lambda: called.append(True))

    assert called == [True]


def test_main_keeps_running_without_tray(monkeypatch):
    """main() must start the web server instead of returning 1."""
    import main as entrypoint

    started = []

    monkeypatch.setattr(entrypoint, "Icon", None)
    monkeypatch.setattr(entrypoint, "Menu", None)
    monkeypatch.setattr(entrypoint, "MenuItem", None)
    monkeypatch.setenv("WS_HEADLESS", "1")
    monkeypatch.setattr(entrypoint, "_start_web_server", lambda port: started.append(port))

    def stop_wait(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(entrypoint.time, "sleep", stop_wait)

    assert entrypoint.main([]) == 0
    assert started, "web server thread was never started"
