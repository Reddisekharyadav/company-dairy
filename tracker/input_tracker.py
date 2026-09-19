import time
import threading
from pynput import mouse, keyboard

class InputTracker:
    def __init__(self, idle_threshold=300):
        self.last_key_time = 0
        self.last_scroll_time = 0
        self.last_move_time = 0
        self.idle_threshold = idle_threshold
        
        self.mouse_listener = None
        self.keyboard_listener = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        
        # Non-blocking listeners
        self.mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_scroll=self._on_scroll,
            on_click=self._on_click
        )
        self.keyboard_listener = keyboard.Listener(
            on_press=self._on_press
        )
        
        self.mouse_listener.start()
        self.keyboard_listener.start()

    def stop(self):
        self._running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.keyboard_listener:
            self.keyboard_listener.stop()

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
        """
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

# Singleton instance for easy import
input_tracker = InputTracker()
