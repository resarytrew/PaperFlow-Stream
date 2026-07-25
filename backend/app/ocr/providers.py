"""Concrete :class:`HandwritingRecognitionProvider` implementations.

* :class:`LocalHandwritingProvider` – real local inference (RapidOCR / ONNX).
* :class:`MockHandwritingProvider`  – deterministic, for automated tests only.
* :class:`CloudHandwritingProvider` – opt-in placeholder; never active by default.

IMPORTANT (honesty about accuracy): the model bundled with RapidOCR is trained
mainly on printed Latin/Chinese text. It reads printed Russian only roughly and
cursive Russian handwriting poorly. The confidence returned by the model is
propagated unchanged so that weak results are routed to manual review instead of
being presented as reliable. A better Russian HTR model can be supplied via
``PAPERFLOW_OCR_REC_MODEL`` / ``PAPERFLOW_OCR_REC_KEYS`` without any code change.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from app.ocr.preprocess import PreprocessResult, build_variants, is_blank
from app.ocr.provider import (
    HandwritingRecognitionProvider,
    RecognitionOutput,
    RecognizedLine,
    TokenConfidence,
    _load_image,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- local


class LocalHandwritingProvider(HandwritingRecognitionProvider):
    """Local ONNX inference through RapidOCR.

    Runs fully offline on CPU. The engine is loaded lazily on first use and
    reused afterwards; inference is executed in a worker thread so the event
    loop (and therefore live scanning) is never blocked.
    """

    name = "local"

    def __init__(self, model_name: str | None = None, min_line_height: int = 12) -> None:
        self.model_name = model_name or os.getenv("PAPERFLOW_OCR_MODEL", "rapidocr-onnx-ppocrv4")
        self.min_line_height = min_line_height
        self._engine = None
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._attempted = False

    # ------------------------------------------------------------- engine

    def _ensure_engine(self):
        if self._attempted:
            return self._engine
        with self._lock:
            if self._attempted:
                return self._engine
            self._attempted = True
            try:
                from rapidocr_onnxruntime import RapidOCR

                kwargs: dict = {}
                # Allow swapping in a Russian recognition model without code changes.
                rec_model = os.getenv("PAPERFLOW_OCR_REC_MODEL")
                rec_keys = os.getenv("PAPERFLOW_OCR_REC_KEYS")
                if rec_model and Path(rec_model).exists():
                    kwargs["Rec.model_path"] = rec_model
                    logger.info("using custom recognition model: %s", rec_model)
                if rec_keys and Path(rec_keys).exists():
                    kwargs["Rec.keys_path"] = rec_keys

                self._engine = RapidOCR(**kwargs) if kwargs else RapidOCR()
                logger.info("local OCR engine ready (%s)", self.model_name)
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._engine = None
                logger.error("local OCR engine unavailable: %s", self._load_error)
            return self._engine

    @property
    def available(self) -> bool:
        return self._ensure_engine() is not None

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "model": self.model_name,
            "available": self.available,
            "error": self._load_error,
            "notes": (
                "Локальный CPU-inference. Встроенная модель обучена преимущественно на "
                "печатном тексте — рукописный русский распознаётся ограниченно."
            ),
        }

    # ------------------------------------------------------------ inference

    def _run_engine(self, image: np.ndarray) -> list[tuple[list, str, float]]:
        engine = self._ensure_engine()
        if engine is None:
            raise RuntimeError(f"local OCR engine not available: {self._load_error}")
        result, _ = engine(image)
        return list(result or [])

    @staticmethod
    def _bbox_from_points(points: list, offset: tuple[int, int] = (0, 0)) -> dict:
        array = np.asarray(points, dtype=np.float32).reshape(-1, 2)
        x1, y1 = array.min(axis=0)
        x2, y2 = array.max(axis=0)
        return {
            "x": int(round(float(x1))) + offset[0],
            "y": int(round(float(y1))) + offset[1],
            "w": max(1, int(round(float(x2 - x1)))),
            "h": max(1, int(round(float(y2 - y1)))),
        }

    def _recognize_variant(self, variant: PreprocessResult) -> tuple[list[RecognizedLine], float]:
        """Run the engine on one preprocessing variant."""
        image = variant.image
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image

        # Upscale small crops – the detector needs a minimum stroke thickness.
        scale = 1.0
        if bgr.shape[0] < 220:
            scale = 220.0 / max(bgr.shape[0], 1)
            bgr = cv2.resize(bgr, (int(bgr.shape[1] * scale), int(bgr.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)

        raw = self._run_engine(bgr)
        lines: list[RecognizedLine] = []
        for index, item in enumerate(raw):
            try:
                points, text, confidence = item[0], str(item[1]), float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            if not text.strip():
                continue
            bbox = self._bbox_from_points(points)
            if scale != 1.0:
                bbox = {k: max(1, int(round(v / scale))) if k in ("w", "h") else int(round(v / scale))
                        for k, v in bbox.items()}
            words = [w for w in text.split() if w]
            # The engine reports one confidence per detected line; distribute it
            # across tokens rather than inventing per-token values.
            tokens = [TokenConfidence(text=w, confidence=confidence) for w in words]
            lines.append(
                RecognizedLine(
                    index=index,
                    text=text.strip(),
                    confidence=confidence,
                    bounding_box=bbox,
                    token_confidences=tokens,
                )
            )

        lines.sort(key=lambda line: (line.bounding_box["y"], line.bounding_box["x"]))
        for position, line in enumerate(lines):
            line.index = position

        if not lines:
            return [], 0.0
        # Length-weighted average: long confident lines should dominate.
        weights = [max(len(line.text), 1) for line in lines]
        overall = float(np.average([line.confidence for line in lines], weights=weights))
        return lines, overall

    async def recognize(self, image_path: str, language: str = "ru") -> RecognitionOutput:
        started = time.perf_counter()
        output = RecognitionOutput(provider=self.name, model_name=self.model_name)

        try:
            image = await asyncio.to_thread(_load_image, image_path)
        except Exception as exc:
            output.warnings.append(f"не удалось открыть изображение: {exc}")
            output.processing_time_ms = int((time.perf_counter() - started) * 1000)
            raise

        blank, ratio = await asyncio.to_thread(is_blank, image)
        if blank:
            output.is_blank = True
            output.warnings.append(f"Пустой ответ (доля чернил {ratio:.4f})")
            output.processing_time_ms = int((time.perf_counter() - started) * 1000)
            return output

        variants = await asyncio.to_thread(build_variants, image, self.min_line_height)
        if not variants:
            output.warnings.append("не удалось подготовить изображение")
            output.processing_time_ms = int((time.perf_counter() - started) * 1000)
            return output

        best_lines: list[RecognizedLine] = []
        best_confidence = -1.0
        best_variant = ""
        errors: list[str] = []

        # Try each preprocessing variant, keep the most confident result.
        for variant in variants:
            try:
                lines, confidence = await asyncio.to_thread(self._recognize_variant, variant)
            except Exception as exc:
                errors.append(f"{variant.name}: {exc}")
                logger.warning("OCR variant %s failed: %s", variant.name, exc)
                continue
            if confidence > best_confidence and lines:
                best_confidence = confidence
                best_lines = lines
                best_variant = variant.name

        if not best_lines:
            output.warnings.extend(errors or ["модель не нашла текст в области ответа"])
            output.overall_confidence = 0.0
            if errors and len(errors) == len(variants):
                output.processing_time_ms = int((time.perf_counter() - started) * 1000)
                raise RuntimeError("; ".join(errors))
            output.processing_time_ms = int((time.perf_counter() - started) * 1000)
            return output

        output.lines = best_lines
        output.text = "\n".join(line.text for line in best_lines)
        output.overall_confidence = max(0.0, min(best_confidence, 1.0))
        output.preprocess_variant = best_variant
        output.processing_time_ms = int((time.perf_counter() - started) * 1000)
        if errors:
            output.warnings.append(f"часть вариантов обработки не удалась: {len(errors)}")
        output.warnings.append(
            "Модель оптимизирована под печатный текст; рукописный русский требует проверки учителем."
        )
        return output

    async def warmup(self) -> None:
        await asyncio.to_thread(self._ensure_engine)


# ---------------------------------------------------------------------- mock


class MockHandwritingProvider(HandwritingRecognitionProvider):
    """Deterministic provider used by the automated test-suite only.

    Produces stable pseudo-results derived from the image content so tests can
    assert on queue routing, confidence bucketing and export contents without
    depending on a real model.
    """

    name = "mock"

    def __init__(self, model_name: str = "mock-htr-v1", fail: bool = False, fixed_confidence: float | None = None) -> None:
        self.model_name = model_name
        self.fail = fail
        self.fixed_confidence = fixed_confidence

    async def recognize(self, image_path: str, language: str = "ru") -> RecognitionOutput:
        started = time.perf_counter()
        if self.fail:
            raise RuntimeError("mock provider failure (intentional)")

        image = _load_image(image_path)
        blank, ratio = is_blank(image)
        output = RecognitionOutput(provider=self.name, model_name=self.model_name)
        if blank:
            output.is_blank = True
            output.warnings.append(f"Пустой ответ (доля чернил {ratio:.4f})")
            output.processing_time_ms = int((time.perf_counter() - started) * 1000)
            return output

        digest = hashlib.sha256(image.tobytes()[:20000]).hexdigest()
        seed = int(digest[:8], 16)
        rng = np.random.default_rng(seed)

        variants = build_variants(image)
        segments = variants[1].lines if len(variants) > 1 else []
        if not segments:
            segments = variants[0].lines if variants else []

        phrases = [
            "отмена крепостного права",
            "реформа Александра II",
            "экономическое отставание России",
            "поражение в Крымской войне",
        ]

        lines: list[RecognizedLine] = []
        for index, segment in enumerate(segments[:6]):
            text = phrases[index % len(phrases)]
            confidence = (
                self.fixed_confidence
                if self.fixed_confidence is not None
                else float(np.clip(0.55 + rng.random() * 0.42, 0.0, 1.0))
            )
            lines.append(
                RecognizedLine(
                    index=index,
                    text=text,
                    confidence=confidence,
                    bounding_box=segment.bbox_dict(),
                    token_confidences=[
                        TokenConfidence(text=word, confidence=float(np.clip(confidence + rng.normal(0, 0.05), 0, 1)))
                        for word in text.split()
                    ],
                )
            )

        output.lines = lines
        output.text = "\n".join(line.text for line in lines)
        output.overall_confidence = float(np.mean([line.confidence for line in lines])) if lines else 0.0
        output.preprocess_variant = "clean"
        output.processing_time_ms = int((time.perf_counter() - started) * 1000)
        output.warnings.append("MOCK-провайдер: результат сгенерирован для тестов, не является распознаванием.")
        return output


# --------------------------------------------------------------------- cloud


class CloudHandwritingProvider(HandwritingRecognitionProvider):
    """Opt-in placeholder for an external service.

    Deliberately refuses to run unless the teacher explicitly enables cloud
    providers in the privacy settings *and* supplies an endpoint. No student
    data ever leaves the machine by default (section 12).
    """

    name = "cloud"

    def __init__(self, endpoint: str = "", api_key: str = "", enabled: bool = False) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.enabled = enabled
        self.model_name = "external"

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "model": self.model_name,
            "available": bool(self.enabled and self.endpoint),
            "notes": "Отключён по умолчанию. Требует явного согласия в настройках приватности.",
        }

    async def recognize(self, image_path: str, language: str = "ru") -> RecognitionOutput:
        if not self.enabled:
            raise PermissionError(
                "Облачный провайдер отключён. Включите его явно в настройках приватности."
            )
        if not self.endpoint:
            raise ValueError("Не задан endpoint облачного провайдера.")
        raise NotImplementedError(
            "CloudHandwritingProvider — точка расширения. Реализуйте вызов вашего сервиса здесь."
        )


# ------------------------------------------------------------------ registry

_PROVIDERS: dict[str, HandwritingRecognitionProvider] = {}


def get_provider(name: str = "local", **kwargs) -> HandwritingRecognitionProvider:
    """Return a cached provider instance by name."""
    key = f"{name}:{sorted(kwargs.items())}"
    if key in _PROVIDERS:
        return _PROVIDERS[key]

    if name == "local":
        provider: HandwritingRecognitionProvider = LocalHandwritingProvider(**kwargs)
    elif name == "mock":
        provider = MockHandwritingProvider(**kwargs)
    elif name == "cloud":
        provider = CloudHandwritingProvider(**kwargs)
    else:
        raise ValueError(f"unknown handwriting provider: {name}")

    _PROVIDERS[key] = provider
    return provider


def available_providers() -> list[dict]:
    return [
        LocalHandwritingProvider().describe(),
        MockHandwritingProvider().describe(),
        CloudHandwritingProvider().describe(),
    ]


def clear_provider_cache() -> None:
    _PROVIDERS.clear()
