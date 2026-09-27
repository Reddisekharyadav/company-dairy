"""
Native Windows OCR Engine for WorkSense AI
Uses the `winocr` package (wrapper for Windows.Media.Ocr).
Provides high-speed text extraction with spatial bounding boxes.
"""
import logging
from typing import Dict, Any
from PIL import Image

log = logging.getLogger('win_ocr')

class WinOCREngine:
    def __init__(self):
        self._is_available = False
        try:
            import winocr
            self._winocr = winocr
            self._is_available = True
            log.info("Windows Native OCR (winocr) initialized successfully.")
        except ImportError:
            self._winocr = None
            log.warning("winocr package not found. Native Windows OCR unavailable.")

    def is_available(self) -> bool:
        return self._is_available

    def recognize(self, image_path: str) -> Dict[str, Any]:
        """Synchronous OCR using winocr's recognize_pil_sync."""
        if not self._is_available:
            return {"text": "", "lines": []}
            
        try:
            img = Image.open(image_path)
            result = self._winocr.recognize_pil_sync(img, lang="en")
            
            lines = []
            full_text = []
            
            if hasattr(result, 'lines'):
                for line in result.lines:
                    full_text.append(line.text)
                    words = []
                    if hasattr(line, 'words'):
                        for word in line.words:
                            bbox = [0, 0, 0, 0]
                            if hasattr(word, 'bounding_rect'):
                                rect = word.bounding_rect
                                bbox = [int(rect.x), int(rect.y), int(rect.width), int(rect.height)]
                            words.append({
                                "text": word.text,
                                "bbox": bbox
                            })
                    lines.append({
                        "text": line.text,
                        "words": words
                    })
            elif hasattr(result, 'text'):
                # Simpler result format
                full_text.append(result.text)
                
            return {
                "text": "\n".join(full_text),
                "lines": lines
            }
        except Exception as e:
            log.warning("WinOCR failed for %s: %s", image_path, e)
            return {"text": "", "lines": []}

win_ocr_engine = WinOCREngine()

def extract_text(image_path: str) -> Dict[str, Any]:
    return win_ocr_engine.recognize(image_path)
