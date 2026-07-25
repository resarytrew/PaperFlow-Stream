"""Optional Ultralytics YOLO adapter.

The application must run without YOLO. This module lazily imports ultralytics
and degrades to a no-op detector when the package or weights are missing.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_PAPER_ALIASES = {"paper", "sheet", "document", "book", "laptop", "cell phone", "tv"}
_HAND_ALIASES = {"hand", "person"}


@dataclass
class YoloDetection:
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "bbox": [round(float(v), 2) for v in self.bbox],
        }


class YoloAdapter:
    """Thin wrapper around ultralytics YOLO with safe fallbacks."""

    def __init__(self, model_path: str = "", confidence: float = 0.35) -> None:
        self.model_path = model_path
        self.confidence = confidence
        self._model: Any | None = None
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._attempted = False

    @property
    def available(self) -> bool:
        self._ensure_model()
        return self._model is not None

    @property
    def status(self) -> dict:
        self._ensure_model()
        return {
            "available": self._model is not None,
            "modelPath": self.model_path,
            "error": self._load_error,
        }

    def _ensure_model(self) -> None:
        if self._attempted:
            return
        with self._lock:
            if self._attempted:
                return
            self._attempted = True
            if not self.model_path:
                self._load_error = "no model configured"
                return
            if not Path(self.model_path).exists():
                self._load_error = f"model file not found: {self.model_path}"
                logger.warning("YOLO disabled: %s", self._load_error)
                return
            try:
                from ultralytics import YOLO  # type: ignore[import-not-found]

                self._model = YOLO(self.model_path)
                logger.info("YOLO model loaded from %s", self.model_path)
            except Exception as exc:  # pragma: no cover - depends on optional dep
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._model = None
                logger.warning("YOLO unavailable (%s) – continuing with OpenCV only", self._load_error)

    def detect(self, image: np.ndarray) -> list[YoloDetection]:
        """Run inference. Returns [] when the model is unavailable."""
        self._ensure_model()
        if self._model is None or image is None or image.size == 0:
            return []
        try:
            results = self._model.predict(image, conf=self.confidence, verbose=False)
        except Exception as exc:  # pragma: no cover - runtime robustness
            logger.warning("YOLO inference failed: %s", exc)
            return []

        detections: list[YoloDetection] = []
        for result in results:
            names = getattr(result, "names", {}) or {}
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                except Exception:  # pragma: no cover
                    continue
                raw = str(names.get(cls_id, cls_id)).lower()
                label = "paper" if raw in _PAPER_ALIASES else ("hand" if raw in _HAND_ALIASES else raw)
                detections.append(YoloDetection(label=label, confidence=conf, bbox=(x1, y1, x2, y2)))
        return detections


_adapter: YoloAdapter | None = None
_adapter_key: tuple[str, float] | None = None


def get_yolo_adapter(model_path: str = "", confidence: float = 0.35) -> YoloAdapter:
    """Process-wide cached adapter (reloaded when configuration changes)."""
    global _adapter, _adapter_key
    key = (model_path, confidence)
    if _adapter is None or _adapter_key != key:
        _adapter = YoloAdapter(model_path, confidence)
        _adapter_key = key
    return _adapter
