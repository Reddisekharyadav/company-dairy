import logging
import time

log = logging.getLogger('worksense.input')

# pynput needs a desktop session (X11/Wayland on Linux).  Importing it at
# module import time used to abort the whole application on headless servers,
# containers and SSH sessions, which also took the web dashboard down.  Treat
# it as optional and degrade to "no keyboard/mouse signal" instead.
try:  # pragma: no cover - the failure path depends on the host display
    from pynput import keyboard, mouse
    _PYNPUT_ERROR = None
except Exception as exc:  # ImportError, Xlib.error.DisplayNameError, ...
    keyboard = None
    mouse = None
    _PYNPUT_ERROR = exc


class InputTracker:
    def __init__(self, idle_threshold=300):
        self.last_key_time = 0
        self.last_scroll_time = 0
        self.last_move_time = 0
        self.idle_threshold = idle_threshold

        self.mouse_listener = None
        self.keyboard_listener = None
        self._running = False
        self._warned = False

    @property
    def available(self) -> bool:
        """True when a global keyboard/mouse listener can be created."""
        return mouse is not None and keyboard is not None

    def start(self):
        if self._running:
            return
        if not self.available:
            if not self._warned:
                log.warning(
                    'Global input tracking unavailable (%s). '
                    'Idle detection falls back to active-window sampling.',
                    _PYNPUT_ERROR,
                )
                self._warned = True
            return

        try:
            self.mouse_listener = mouse.Listener(
                on_move=self._on_move,
                on_scroll=self._on_scroll,
                on_click=self._on_click,
            )
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_press
            )

            self.mouse_listener.start()
            self.keyboard_listener.start()
        except Exception as exc:
            log.warning('Could not start input listeners: %s', exc)
            self._stop_listeners()
            return

        self._running = True

    def stop(self):
        self._running = False
        self._stop_listeners()

    def _stop_listeners(self):
        for listener in (self.mouse_listener, self.keyboard_listener):
            if listener is None:
                continue
            try:
                listener.stop()
            except Exception:
                pass
        self.mouse_listener = None
        self.keyboard_listener = None

    def _on_move(self, x, y):
        self.last_move_time = time.time()

    def _on_scroll(self, x, y, dx, dy):
        self.last_scroll_time = time.time()

    def _on_click(self, x, y, button, pressed):
        if pressed:
            self.last_move_time = time.time()

    def _on_press(self, key):
        self.last_key_time = time.time()

    def get_current_state(self):
        """
        Determine the user's current attention state.
        - Writing/Coding: If typing within the last 15 seconds.
        - Reading: If scrolling within the last 15 seconds (and not typing).
        - Looking/Watching: If moving the mouse (but not scrolling/typing) or no input for a short period.
        - Idle: No input for longer than `idle_threshold`.

        Without input listeners (headless Linux) there is no signal to read,
        so report Idle instead of inventing activity.
        """
        if not self._running:
            return "Idle"

        now = time.time()

        time_since_key = now - self.last_key_time
        time_since_scroll = now - self.last_scroll_time
        time_since_move = now - self.last_move_time

        min_time_since_input = min(time_since_key, time_since_scroll, time_since_move)

        if min_time_since_input > self.idle_threshold:
            return "Idle"

        if time_since_key < 15:
            return "Writing/Coding"

        if time_since_scroll < 15:
            return "Reading"

        # If there is mouse movement or recent activity that isn't heavy typing or scrolling
        return "Looking/Watching"


input_tracker = InputTracker()
