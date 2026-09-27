"""
Vision AI module for WorkSense AI (Microsoft Recall Clone)
Integrates Microsoft Florence-2-base and OmniParser pipelines for deep UI understanding.
Models are loaded lazily to minimize startup overhead and prevent blocking the main thread.
"""
import logging
import json
import threading
from typing import Optional, Dict, Any

log = logging.getLogger('vision_ai')

class VisionAIEngine:
    def __init__(self):
        self._florence_model = None
        self._florence_processor = None
        self._lock = threading.Lock()
        self._is_loaded = False
        
    def _lazy_load(self):
        if self._is_loaded:
            return
            
        with self._lock:
            if self._is_loaded:
                return
                
            try:
                log.info("Loading Vision AI models (Florence-2 & OmniParser)...")
                from transformers import AutoProcessor, AutoModelForCausalLM
                import torch
                
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                florence_model_id = "microsoft/Florence-2-base"
                self._florence_processor = AutoProcessor.from_pretrained(florence_model_id, trust_remote_code=True)
                self._florence_model = AutoModelForCausalLM.from_pretrained(
                    florence_model_id, 
                    trust_remote_code=True
                ).to(self.device).eval()
                
                self._is_loaded = True
                log.info("Vision AI models loaded successfully on %s.", self.device)
            except Exception as e:
                log.error("Failed to load Vision AI models: %s", e)
                raise
                
    def run_florence_task(self, image, task_prompt: str) -> Any:
        self._lazy_load()
        if not self._florence_model:
            return None
            
        try:
            inputs = self._florence_processor(text=task_prompt, images=image, return_tensors="pt").to(self.device)
            generated_ids = self._florence_model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3
            )
            generated_text = self._florence_processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
            parsed_answer = self._florence_processor.post_process_generation(generated_text, task=task_prompt, image_size=image.size)
            return parsed_answer.get(task_prompt, parsed_answer)
        except Exception as e:
            log.warning("Florence-2 task '%s' failed: %s", task_prompt, e)
            return None

    def get_ui_understanding(self, image) -> Dict[str, Any]:
        self._lazy_load()
        
        ocr_regions = self.run_florence_task(image, "<OCR_WITH_REGION>")
        dense_captions = self.run_florence_task(image, "<DENSE_REGION_CAPTION>")
        
        elements = []
        element_id = 0
        
        if isinstance(ocr_regions, dict) and "quad_boxes" in ocr_regions:
            labels = ocr_regions.get("labels", [])
            boxes = ocr_regions.get("quad_boxes", [])
            for idx, box in enumerate(boxes):
                elements.append({
                    "id": element_id,
                    "type": "text",
                    "interactivity": False,
                    "label": labels[idx] if idx < len(labels) else "",
                    "bbox": box
                })
                element_id += 1
                
        if isinstance(dense_captions, dict) and "bboxes" in dense_captions:
            labels = dense_captions.get("labels", [])
            boxes = dense_captions.get("bboxes", [])
            for idx, box in enumerate(boxes):
                label = labels[idx] if idx < len(labels) else ""
                is_interactive = any(kw in label.lower() for kw in ["button", "icon", "link", "input", "menu", "logo"])
                elements.append({
                    "id": element_id,
                    "type": "ui_element",
                    "interactivity": is_interactive,
                    "label": label,
                    "bbox": box
                })
                element_id += 1
                
        return {
            "version": "omniparser_v1",
            "image_size": image.size,
            "elements": elements
        }

vision_engine = VisionAIEngine()

def analyze_ui_frame(image) -> Optional[str]:
    try:
        data = vision_engine.get_ui_understanding(image)
        return json.dumps(data)
    except Exception as e:
        log.error("Failed to analyze UI frame: %s", e)
        return None
