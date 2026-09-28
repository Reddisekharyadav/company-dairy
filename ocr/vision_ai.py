"""
Vision AI module for WorkSense AI (Microsoft Recall Clone)
Integrates OpenRouter API and OCR fallback for deep UI understanding.
"""
import logging
import json
import base64
import requests
from io import BytesIO
from typing import Optional, Dict, Any
from config.settings import settings

log = logging.getLogger('vision_ai')

def analyze_ui_frame(image, ocr_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    try:
        # 1. Base elements from OCR (fallback OmniParser format)
        elements = []
        element_id = 0
        if ocr_data and 'lines' in ocr_data:
            for line in ocr_data['lines']:
                if 'words' in line:
                    for word in line['words']:
                        bbox = word.get('bbox', [0,0,0,0])
                        # Map win_ocr bbox [x, y, w, h] to omniparser [x, y, x2, y2]
                        # Actually the frontend parses [x,y,w,h] if len is 4, or x,y,x+w,y+h
                        # Frontend logic: x=bbox[0], y=bbox[1], w=bbox[2]-bbox[0], h=bbox[3]-bbox[1]
                        # So we need to provide [x, y, x+w, y+h]
                        elements.append({
                            "id": element_id,
                            "type": "text",
                            "interactivity": False,
                            "label": word.get("text", ""),
                            "bbox": [bbox[0], bbox[1], bbox[0]+bbox[2], bbox[1]+bbox[3]]
                        })
                        element_id += 1

        result = {
            "version": "omniparser_v1",
            "image_size": image.size,
            "elements": elements
        }

        # 2. Try OpenRouter for dense caption / summary if key exists
        if getattr(settings, 'ai_api_key', None):
            try:
                buffered = BytesIO()
                image.save(buffered, format="JPEG", quality=60)
                img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
                
                headers = {
                    "Authorization": f"Bearer {settings.ai_api_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "model": "google/gemini-pro-vision", # Using a generic vision model available on OpenRouter
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Briefly describe what the user is doing on this screen in one sentence."},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_str}"}}
                            ]
                        }
                    ],
                    "max_tokens": 50
                }
                
                # Non-blocking request ideally, but we are in a worker thread anyway
                response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=5)
                if response.status_code == 200:
                    ai_text = response.json()['choices'][0]['message']['content']
                    result["analysis_summary"] = ai_text.strip()
            except Exception as api_e:
                log.warning(f"OpenRouter API failed: {api_e}")

        return json.dumps(result)
    except Exception as e:
        log.error("Failed to analyze UI frame: %s", e)
        return None
