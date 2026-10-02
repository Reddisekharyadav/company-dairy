"""
Screen capture worker v5.0: Microsoft Recall Clone Engine.
- Perceptual hash deduplication via dHash (64x36 grayscale)
- WebP compressed screenshots
- Privacy-safe exclusion rules (banking, passwords, etc.)
- Microsoft Florence-2 / OmniParser AI Vision parsing
- Native Windows OCR
- Vector embeddings and sqlite-vec
"""
import os
import time
import logging
import re
import json
from threading import Thread, Event
from datetime import datetime
from pathlib import Path
from PIL import Image

log = logging.getLogger('screen_capture')

def _appdata_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    d = base / "WorkSense"
    d.mkdir(parents=True, exist_ok=True)
    return d

CONSENT_FILE = _appdata_dir() / 'screen_consent.txt'
SCREENSHOT_DIR = _appdata_dir() / 'screenshots'

# Privacy Blacklists
SENSITIVE_PROCESSES = {'keepass', '1password', 'bitwarden', 'lastpass'}
SENSITIVE_TITLES_REGEX = re.compile(r'(?i)(password|bank|incognito|private window|inprivate)')

def is_consent_granted() -> bool:
    try:
        return CONSENT_FILE.exists() and CONSENT_FILE.read_text().strip() == 'granted'
    except Exception:
        return False

def grant_consent():
    CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONSENT_FILE.write_text('granted')
    log.info('Screen capture consent granted.')

def revoke_consent():
    if CONSENT_FILE.exists():
        CONSENT_FILE.write_text('revoked')
    log.info('Screen capture consent revoked.')

def compute_dhash(img: Image.Image) -> int:
    """Compute 64-bit dHash of an image for deduplication."""
    # Resize to 64x36 grayscale
    # Wait, dHash typically needs (W+1)xH to compute horizontal gradients
    # Let's use 9x8 for standard 64-bit hash
    resized = img.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())
    diff = []
    for row in range(8):
        for col in range(8):
            pixel_left = pixels[row * 9 + col]
            pixel_right = pixels[row * 9 + col + 1]
            diff.append(pixel_left > pixel_right)
    
    hash_val = 0
    for idx, bit in enumerate(diff):
        if bit:
            hash_val |= (1 << idx)
    return hash_val

def hamming_distance(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count('1')

class ScreenCaptureWorker:
    def __init__(self, interval: int = 30):
        self.interval = interval
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self.last_hash = None

    def start(self):
        if not is_consent_granted():
            log.info('Screen capture not started — consent not granted.')
            return
        self._stop.clear()
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()
        log.info('Microsoft Recall capture worker started (interval=%ds)', self.interval)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3.0)

    def _save_webp(self, img: Image.Image, timestamp: datetime) -> str:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        # WebP compressed format as per OpenRecall
        fname = f"screen_{timestamp.strftime('%Y%m%d_%H%M%S')}.webp"
        fpath = SCREENSHOT_DIR / fname
        img.save(str(fpath), 'WEBP', quality=75)
        return str(fpath)

    def _run(self):
        from database.session import SessionLocal
        from database.models import ScreenFrame
        from config.settings import SESSION_ID
        from tracker.active_window import get_active_window
        from sqlalchemy import text as sa_text
        import mss

        # Optional imports — gracefully degrade if not available
        try:
            from ocr.win_ocr import extract_text as win_ocr_extract
        except Exception:
            win_ocr_extract = None

        try:
            from ocr.vision_ai import analyze_ui_frame
        except Exception:
            analyze_ui_frame = None

        try:
            from ocr.search_engine import search_engine
        except Exception:
            search_engine = None

        # Screen content analyzer — generates human-readable activity summaries
        try:
            from ocr.screen_analyzer import analyze_screen, detect_category
        except Exception:
            analyze_screen = None
            detect_category = None

        # Fallback OCR using pytesseract
        try:
            from ocr.ocr import extract_text as tesseract_extract
        except Exception:
            tesseract_extract = None

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        session = SessionLocal()

        try:
            while not self._stop.is_set():
                if not is_consent_granted():
                    break

                proc, title = get_active_window()
                proc = proc or ''
                title = title or ''
                
                # ── Privacy Blacklist Check ──
                if any(sp in proc.lower() for sp in SENSITIVE_PROCESSES) or SENSITIVE_TITLES_REGEX.search(title):
                    log.debug("Privacy rule triggered for %s / %s. Skipping.", proc, title)
                    self._stop.wait(self.interval)
                    continue

                ts = datetime.now()
                
                try:
                    with mss.mss() as sct:
                        monitor = sct.monitors[1]
                        sct_img = sct.grab(monitor)
                        img = Image.frombytes('RGB', sct_img.size, sct_img.bgra, 'raw', 'BGRX')

                        # ── Perceptual Hashing (dHash) ──
                        current_hash = compute_dhash(img)
                        is_duplicate = False
                        
                        if self.last_hash is not None:
                            distance = hamming_distance(self.last_hash, current_hash)
                            if distance <= 5:
                                is_duplicate = True
                        
                        if is_duplicate:
                            log.debug("Dedup: Frame identical to previous, skipping.")
                            self._stop.wait(self.interval)
                            continue

                        self.last_hash = current_hash
                        phash_hex = format(current_hash, '016x')

                        # ── Save WebP ──
                        webp_path = self._save_webp(img, ts)
                        file_size = os.path.getsize(webp_path)

                        # ── OCR (Windows Native → Tesseract fallback) ──
                        ocr_text = ""
                        try:
                            if win_ocr_extract:
                                ocr_data = win_ocr_extract(webp_path)
                                ocr_text = ocr_data.get("text", "")
                            elif tesseract_extract:
                                ocr_text = tesseract_extract(webp_path) or ""
                        except Exception as ocr_e:
                            log.debug("OCR failed: %s", ocr_e)

                        # ── OmniParser & Florence-2 UI Understanding (optional) ──
                        omniparser_json_str = None
                        try:
                            if analyze_ui_frame:
                                omniparser_json_str = analyze_ui_frame(img, ocr_data=ocr_data if 'ocr_data' in locals() else None)
                        except Exception as vision_e:
                            log.debug("Vision AI failed: %s", vision_e)

                        # ── Activity Analysis (screen_analyzer) ──
                        analysis_summary = None
                        detected_category = None
                        try:
                            if analyze_screen:
                                analysis_summary = analyze_screen(ocr_text, proc, title)
                            if detect_category:
                                detected_category = detect_category(proc, title, analysis_summary)
                        except Exception as ana_e:
                            log.debug("Screen analysis failed: %s", ana_e)

                        # ── Embedding Generation (optional) ──
                        embedding_json_str = None
                        try:
                            if search_engine and ocr_text:
                                embedding_vector = search_engine.encode(ocr_text[:2000])
                                if embedding_vector:
                                    embedding_json_str = json.dumps(embedding_vector)
                        except Exception as emb_e:
                            log.debug("Embedding failed: %s", emb_e)

                        # ── Save to Database ──
                        frame = ScreenFrame(
                            timestamp=ts,
                            session_id=SESSION_ID,
                            session_date=ts.strftime('%Y-%m-%d'),
                            screenshot_path=webp_path,
                            file_size_bytes=file_size,
                            process_name=proc[:256],
                            window_title=title[:1024],
                            phash=phash_hex,
                            is_duplicate=is_duplicate,
                            ocr_text=ocr_text,
                            ocr_text_length=len(ocr_text),
                            analysis_summary=analysis_summary,
                            detected_category=detected_category,
                            omniparser_json=omniparser_json_str,
                            embedding_json=embedding_json_str,
                        )
                        session.add(frame)
                        session.commit()
                        
                        # ── Index into FTS5 ──
                        try:
                            session.execute(
                                sa_text("INSERT INTO screen_frames_fts(rowid, ocr_text, window_title, analysis_summary) VALUES (:id, :txt, :title, :summary)"),
                                {"id": frame.id, "txt": ocr_text, "title": title, "summary": analysis_summary or ""}
                            )
                            session.commit()
                        except Exception as fts_e:
                            session.rollback()
                            log.debug("FTS5 indexing failed: %s", fts_e)

                        log.info("Recall frame #%s saved (%s KB, OCR %d chars, hash %s)", 
                                 frame.id, round(file_size/1024, 1), len(ocr_text), phash_hex[:8])

                except Exception as e:
                    log.error('Screen capture failed: %s', e)

                self._stop.wait(self.interval)
        finally:
            session.close()
