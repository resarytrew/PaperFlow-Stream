"""Pluggable QR code reader.

Backends: OpenCV ``QRCodeDetector`` (always available), pyzbar and ZXing
(optional). The active backend chain is configurable; every backend is tried
in order until one succeeds.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

import cv2
import numpy as np

logger = logging.getLogger(__name__)

SHEET_UID_RE = re.compile(r"^[A-Za-z0-9._\-]{3,120}$")


@dataclass
class QrPayload:
    """Validated QR content."""

    version: int
    student_id: str
    class_id: str
    task_id: str
    sheet_id: str
    raw: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "studentId": self.student_id,
            "classId": self.class_id,
            "taskId": self.task_id,
            "sheetId": self.sheet_id,
            "raw": self.raw,
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass
class QrReadResult:
    success: bool = False
    payload: QrPayload | None = None
    raw_text: str = ""
    backend: str = ""
    points: list[list[float]] | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "payload": self.payload.to_dict() if self.payload else None,
            "rawText": self.raw_text,
            "backend": self.backend,
            "points": self.points,
            "error": self.error,
        }


class QrBackend(Protocol):
    name: str

    def decode(self, image: np.ndarray) -> tuple[str, list[list[float]] | None]:
        """Return (text, points). Empty text means "not found"."""


class OpenCvQrBackend:
    name = "opencv"

    def __init__(self) -> None:
        self._detector = cv2.QRCodeDetector()

    def decode(self, image: np.ndarray) -> tuple[str, list[list[float]] | None]:
        try:
            data, points, _ = self._detector.detectAndDecode(image)
        except cv2.error as exc:  # pragma: no cover - defensive
            logger.debug("opencv qr error: %s", exc)
            return "", None
        if data:
            pts = points.reshape(-1, 2).tolist() if points is not None else None
            return data, pts
        # multi-detection fallback (several codes / small code)
        try:
            ok, decoded, points, _ = self._detector.detectAndDecodeMulti(image)
        except cv2.error:  # pragma: no cover
            return "", None
        if ok and decoded:
            for i, text in enumerate(decoded):
                if text:
                    pts = points[i].reshape(-1, 2).tolist() if points is not None else None
                    return text, pts
        return "", None


class PyzbarQrBackend:
    name = "pyzbar"

    def __init__(self) -> None:
        self._available: bool | None = None

    def _check(self) -> bool:
        if self._available is None:
            try:
                from pyzbar import pyzbar  # noqa: F401

                self._available = True
            except Exception as exc:
                logger.info("pyzbar unavailable: %s", exc)
                self._available = False
        return self._available

    def decode(self, image: np.ndarray) -> tuple[str, list[list[float]] | None]:
        if not self._check():
            return "", None
        from pyzbar import pyzbar  # type: ignore[import-not-found]

        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        try:
            results = pyzbar.decode(gray)
        except Exception as exc:  # pragma: no cover
            logger.debug("pyzbar decode failed: %s", exc)
            return "", None
        for result in results:
            if result.data:
                pts = [[float(p.x), float(p.y)] for p in result.polygon] if result.polygon else None
                return result.data.decode("utf-8", errors="replace"), pts
        return "", None


class ZxingQrBackend:
    name = "zxing"

    def __init__(self) -> None:
        self._available: bool | None = None

    def _check(self) -> bool:
        if self._available is None:
            try:
                import zxingcpp  # noqa: F401

                self._available = True
            except Exception as exc:
                logger.info("zxing-cpp unavailable: %s", exc)
                self._available = False
        return self._available

    def decode(self, image: np.ndarray) -> tuple[str, list[list[float]] | None]:
        if not self._check():
            return "", None
        import zxingcpp  # type: ignore[import-not-found]

        try:
            results = zxingcpp.read_barcodes(image)
        except Exception as exc:  # pragma: no cover
            logger.debug("zxing decode failed: %s", exc)
            return "", None
        for result in results:
            if result.text:
                pos = getattr(result, "position", None)
                pts = None
                if pos is not None:
                    pts = [
                        [float(pos.top_left.x), float(pos.top_left.y)],
                        [float(pos.top_right.x), float(pos.top_right.y)],
                        [float(pos.bottom_right.x), float(pos.bottom_right.y)],
                        [float(pos.bottom_left.x), float(pos.bottom_left.y)],
                    ]
                return result.text, pts
        return "", None


_BACKENDS: dict[str, QrBackend] = {}


def get_backend(name: str) -> QrBackend:
    if name not in _BACKENDS:
        if name == "opencv":
            _BACKENDS[name] = OpenCvQrBackend()
        elif name == "pyzbar":
            _BACKENDS[name] = PyzbarQrBackend()
        elif name == "zxing":
            _BACKENDS[name] = ZxingQrBackend()
        else:
            raise ValueError(f"unknown QR backend: {name}")
    return _BACKENDS[name]


def parse_payload(text: str) -> QrPayload | None:
    """Parse JSON or the compact ``v1|student|class|task|sheet`` form."""
    text = (text or "").strip()
    if not text:
        return None

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        known = {"version", "studentId", "classId", "taskId", "sheetId"}
        student = str(data.get("studentId") or data.get("student_id") or "").strip()
        class_id = str(data.get("classId") or data.get("class_id") or "").strip()
        task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
        sheet_id = str(data.get("sheetId") or data.get("sheet_id") or "").strip()
        if not sheet_id and student and task_id:
            sheet_id = f"{student}-{task_id}"
        if not (student and sheet_id):
            return None
        try:
            version = int(data.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        extra = {k: v for k, v in data.items() if k not in known and not k.endswith("_id")}
        return QrPayload(
            version=version,
            student_id=student,
            class_id=class_id,
            task_id=task_id,
            sheet_id=sheet_id,
            raw=text,
            extra=extra,
        )

    # Compact form: v1|9B-17|9B|history-09-04|9B-17-history-09-04
    parts = [p.strip() for p in text.split("|")]
    if len(parts) >= 4 and parts[0].lower().startswith("v"):
        try:
            version = int(parts[0][1:] or 1)
        except ValueError:
            version = 1
        student, class_id, task_id = parts[1], parts[2], parts[3]
        sheet_id = parts[4] if len(parts) > 4 and parts[4] else f"{student}-{task_id}"
        if student and sheet_id:
            return QrPayload(
                version=version,
                student_id=student,
                class_id=class_id,
                task_id=task_id,
                sheet_id=sheet_id,
                raw=text,
            )
    return None


def validate_payload(payload: QrPayload | None) -> tuple[bool, str]:
    """Structural validation of a decoded payload."""
    if payload is None:
        return False, "payload_not_parsed"
    if payload.version < 1:
        return False, "unsupported_version"
    if not payload.student_id:
        return False, "missing_student_id"
    if not payload.sheet_id:
        return False, "missing_sheet_id"
    if not SHEET_UID_RE.match(payload.sheet_id):
        return False, "invalid_sheet_id_format"
    return True, ""


def _preprocess_variants(image: np.ndarray) -> list[np.ndarray]:
    """Progressively more aggressive versions to squeeze out a hard QR."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    variants = [image, gray]
    variants.append(cv2.convertScaleAbs(gray, alpha=1.6, beta=8))
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    variants.append(clahe.apply(gray))
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(otsu)
    variants.append(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8))
    h, w = gray.shape[:2]
    if max(h, w) < 900:
        variants.append(cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC))
    variants.append(cv2.GaussianBlur(gray, (3, 3), 0))
    return variants


def read_qr(
    image: np.ndarray,
    backends: tuple[str, ...] = ("opencv", "pyzbar", "zxing"),
    *,
    enhance: bool = True,
) -> QrReadResult:
    """Try every backend over several preprocessing variants."""
    if image is None or image.size == 0:
        return QrReadResult(error="empty_image")

    variants = _preprocess_variants(image) if enhance else [image]
    last_error = "not_found"

    for backend_name in backends:
        try:
            backend = get_backend(backend_name)
        except ValueError:
            continue
        for variant in variants:
            try:
                text, points = backend.decode(variant)
            except Exception as exc:  # pragma: no cover - backend robustness
                logger.debug("QR backend %s raised %s", backend_name, exc)
                continue
            if not text:
                continue
            payload = parse_payload(text)
            valid, error = validate_payload(payload)
            if valid:
                return QrReadResult(
                    success=True, payload=payload, raw_text=text, backend=backend_name, points=points
                )
            last_error = error
            # Text decoded but structurally wrong – report it (do not keep trying).
            return QrReadResult(
                success=False, payload=payload, raw_text=text, backend=backend_name, points=points, error=error
            )
    return QrReadResult(error=last_error)


def qr_readability_score(image: np.ndarray, region: dict | None = None) -> float:
    """Cheap proxy for "can we read the QR here": 1.0 if decoded, else texture based."""
    if image is None or image.size == 0:
        return 0.0
    roi = image
    if region:
        from app.cv.geometry import crop_normalized

        roi = crop_normalized(image, region)
        if roi.size == 0:
            return 0.0
    result = read_qr(roi, backends=("opencv",), enhance=False)
    if result.success:
        return 1.0
    gray = roi if roi.ndim == 2 else cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Local contrast is a decent proxy for a resolvable finder pattern.
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(np.clip(variance / 400.0, 0.0, 0.75))
