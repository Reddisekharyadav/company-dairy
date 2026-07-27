"""Optional OCR module that captures active window every 60s and extracts text."""
from PIL import ImageGrab
import pytesseract
import time
from threading import Thread, Event
from tracker.active_window import get_active_window
from database.session import SessionLocal
from database.models import OCRText
from datetime import datetime
import logging

log = logging.getLogger('ocr')


class OCRWorker:
    def __init__(self, interval: int = 60):
        self.interval = interval
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self):
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self):
        session = SessionLocal()
        try:
            while not self._stop.is_set():
                proc, title = get_active_window()
                try:
                    hwnd = ImageGrab.grab()
                    text = pytesseract.image_to_string(hwnd)
                    if text and text.strip():
                        o = OCRText(timestamp=datetime.now(), source=title or proc or 'unknown', text=text)
                        session.add(o)
                        session.commit()
                except Exception as e:
                    log.debug('OCR capture failed: %s', e)
                time.sleep(self.interval)
        finally:
            session.close()
