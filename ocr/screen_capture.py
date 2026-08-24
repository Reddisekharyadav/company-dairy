"""
Screen capture worker: takes screenshots every N seconds, saves thumbnail,
runs OCR, and stores extracted text + screenshot path in the database.
Only runs if user has granted consent (stored in a local consent file).
"""
import os
import time
import logging
from threading import Thread, Event
from datetime import datetime
from pathlib import Path

log = logging.getLogger('screen_capture')

def _appdata_dir() -> Path:
    """Return the WorkSense AppData directory, creating it if needed."""
    base = Path(os.environ.get("APPDATA") or Path.home())
    d = base / "WorkSense"
    d.mkdir(parents=True, exist_ok=True)
    return d

CONSENT_FILE = _appdata_dir() / 'screen_consent.txt'
SCREENSHOT_DIR = _appdata_dir() / 'screenshots'


def is_consent_granted() -> bool:
    """Check if user has granted screen capture consent."""
    try:
        return CONSENT_FILE.exists() and CONSENT_FILE.read_text().strip() == 'granted'
    except Exception:
        return False


def grant_consent():
    """Save screen capture consent."""
    CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONSENT_FILE.write_text('granted')
    log.info('Screen capture consent granted.')


def revoke_consent():
    """Revoke screen capture consent."""
    if CONSENT_FILE.exists():
        CONSENT_FILE.write_text('revoked')
    log.info('Screen capture consent revoked.')


class ScreenCaptureWorker:
    def __init__(self, interval: int = 30):
        self.interval = interval
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self):
        if not is_consent_granted():
            log.info('Screen capture not started — consent not granted.')
            return
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()
        log.info('Screen capture worker started (interval=%ds)', self.interval)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3.0)

    def _run(self):
        from database.session import SessionLocal
        from database.models import OCRText
        from tracker.active_window import get_active_window

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

        session = SessionLocal()
        try:
            while not self._stop.is_set():
                if not is_consent_granted():
                    log.info('Consent revoked — stopping screen capture.')
                    break

                proc, title = get_active_window()
                ts = datetime.now()
                screenshot_path = None
                ocr_text = ''

                try:
                    import mss
                    import mss.tools
                    from PIL import Image
                    import io

                    with mss.mss() as sct:
                        # Capture primary monitor
                        monitor = sct.monitors[1]  # 1 = first real monitor
                        sct_img = sct.grab(monitor)
                        img = Image.frombytes('RGB', sct_img.size, sct_img.bgra, 'raw', 'BGRX')

                        # Save thumbnail (small, for privacy)
                        thumb = img.copy()
                        thumb.thumbnail((640, 360))
                        fname = f"screen_{ts.strftime('%Y%m%d_%H%M%S')}.jpg"
                        fpath = SCREENSHOT_DIR / fname
                        thumb.save(str(fpath), 'JPEG', quality=60)
                        screenshot_path = str(fpath)

                    # Run OCR on the captured image
                    try:
                        import pytesseract
                        ocr_text = pytesseract.image_to_string(img)
                    except Exception as e:
                        log.debug('OCR failed (Tesseract not installed?): %s', e)
                        ocr_text = f'[OCR unavailable: {e}]'

                except Exception as e:
                    log.debug('Screen capture failed: %s', e)
                    # Create a blank placeholder image so the UI shows active tracking
                    try:
                        from PIL import Image, ImageDraw
                        thumb = Image.new('RGB', (640, 360), color=(30, 45, 69))
                        d = ImageDraw.Draw(thumb)
                        d.text((10, 10), "Screen Locked / Access Denied", fill=(255, 255, 255))
                        fname = f"screen_{ts.strftime('%Y%m%d_%H%M%S')}.jpg"
                        fpath = SCREENSHOT_DIR / fname
                        thumb.save(str(fpath), 'JPEG', quality=60)
                        screenshot_path = str(fpath)
                        ocr_text = f"[Screen capture failed: {e}]"
                    except Exception as ex:
                        log.error("Failed to create placeholder image: %s", ex)

                if ocr_text or screenshot_path:
                    try:
                        o = OCRText(
                            timestamp=ts,
                            source=title or proc or 'unknown',
                            text=(ocr_text or '')[:4000],  # limit to 4KB
                            screenshot_path=screenshot_path,
                        )
                        session.add(o)
                        session.commit()
                    except Exception as e:
                        session.rollback()
                        log.error('Database error in screen capture: %s', e)

                time.sleep(self.interval)
        except Exception as e:
            log.exception('ScreenCaptureWorker error: %s', e)
        finally:
            session.close()
