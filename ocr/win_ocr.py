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

            # winocr.recognize_pil_sync returns a dict: {'text': str, 'lines': list, ...}
            if isinstance(result, dict):
                if result.get('text'):
                    full_text.append(result['text'])
                raw_lines = result.get('lines', [])
                for line in raw_lines:
                    if isinstance(line, dict):
                        line_text = line.get('text', '')
                        words = []
                        for word in line.get('words', []):
                            rect = word.get('bounding_rect', {}) if isinstance(word, dict) else getattr(word, 'bounding_rect', {})
                            if isinstance(rect, dict):
                                bbox = [int(rect.get('x', 0)), int(rect.get('y', 0)), int(rect.get('width', 0)), int(rect.get('height', 0))]
                            elif hasattr(rect, 'x'):
                                bbox = [int(rect.x), int(rect.y), int(rect.width), int(rect.height)]
                            else:
                                bbox = [0, 0, 0, 0]
                            words.append({
                                "text": word.get('text', '') if isinstance(word, dict) else getattr(word, 'text', ''),
                                "bbox": bbox
                            })
                        lines.append({
                            "text": line_text,
                            "words": words
                        })
                    elif hasattr(line, 'text'):
                        words = []
                        if hasattr(line, 'words'):
                            for word in line.words:
                                bbox = [0, 0, 0, 0]
                                if hasattr(word, 'bounding_rect'):
                                    rect = word.bounding_rect
                                    bbox = [int(rect.x), int(rect.y), int(rect.width), int(rect.height)]
                                words.append({
                                    "text": getattr(word, 'text', ''),
                                    "bbox": bbox
                                })
                        lines.append({
                            "text": line.text,
                            "words": words
                        })
                if not full_text and lines:
                    full_text = [l['text'] for l in lines]
            elif hasattr(result, 'lines'):
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
