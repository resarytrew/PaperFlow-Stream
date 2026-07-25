"""HandwritingRecognitionProvider abstraction (section 7.3).

The business logic depends only on this interface, never on a concrete model,
so a different local model — or later a cloud provider — can be plugged in
without touching the queue, the review screen or the exports.
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TokenConfidence:
    text: str
    confidence: float

    def to_dict(self) -> dict:
        return {"text": self.text, "confidence": round(self.confidence, 4)}


@dataclass
class RecognizedLine:
    """One recognised text line with its position on the source image."""

    index: int
    text: str
    confidence: float
    bounding_box: dict  # {x, y, w, h} in pixels of the answer crop
    token_confidences: list[TokenConfidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "boundingBox": self.bounding_box,
            "tokenConfidences": [t.to_dict() for t in self.token_confidences],
        }


@dataclass
class RecognitionOutput:
    """Return value of :meth:`HandwritingRecognitionProvider.recognize`."""

    text: str = ""
    overall_confidence: float = 0.0
    lines: list[RecognizedLine] = field(default_factory=list)
    processing_time_ms: int = 0
    provider: str = ""
    model_name: str = ""
    warnings: list[str] = field(default_factory=list)
    preprocess_variant: str = ""
    is_blank: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "overallConfidence": round(self.overall_confidence, 4),
            "lines": [line.to_dict() for line in self.lines],
            "processingTimeMs": self.processing_time_ms,
            "provider": self.provider,
            "modelName": self.model_name,
            "warnings": self.warnings,
            "preprocessVariant": self.preprocess_variant,
            "isBlank": self.is_blank,
        }


class HandwritingRecognitionProvider(abc.ABC):
    """Interface every recogniser must implement."""

    name: str = "base"
    model_name: str = "unknown"

    @abc.abstractmethod
    async def recognize(self, image_path: str, language: str = "ru") -> RecognitionOutput:
        """Recognise the handwriting on ``image_path``."""

    async def warmup(self) -> None:
        """Optional: load weights ahead of the first request."""
        return None

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model_name, "available": True}


def _load_image(image_path: str) -> np.ndarray:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError(f"could not read image: {image_path}")
    return image
