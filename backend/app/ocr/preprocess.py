"""Handwriting preprocessing and line segmentation (section 7.2).

Produces several preprocessing variants; the recogniser picks whichever gives
the most confident result. Hard binarisation is offered but never forced.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LineImage:
    """One segmented text line."""

    index: int
    image: np.ndarray
    bbox: tuple[int, int, int, int]  # x, y, w, h in the crop's coordinates
    ink_ratio: float = 0.0

    def bbox_dict(self) -> dict:
        x, y, w, h = self.bbox
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


@dataclass
class PreprocessResult:
    name: str
    image: np.ndarray
    lines: list[LineImage] = field(default_factory=list)
    ink_ratio: float = 0.0
    deskew_angle: float = 0.0
    warnings: list[str] = field(default_factory=list)


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def normalize_illumination(gray: np.ndarray, kernel: int = 35) -> np.ndarray:
    """Flatten uneven lighting while keeping stroke greyscale intact."""
    kernel = max(3, kernel | 1)
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((5, 5), np.uint8)), kernel)
    background = np.where(background == 0, 1, background).astype(np.float32)
    flat = np.clip(gray.astype(np.float32) / background * 205.0, 0, 255)
    return flat.astype(np.uint8)


def remove_form_lines(gray: np.ndarray, min_length_ratio: float = 0.45) -> np.ndarray:
    """Erase the printed ruling lines of the form without cutting the writing.

    Detects long horizontal/vertical runs and inpaints them from surrounding
    pixels, so descenders crossing a line are not destroyed.
    """
    height, width = gray.shape[:2]
    inverted = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 12
    )

    h_len = max(int(width * min_length_ratio), 20)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    h_lines = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, h_kernel, iterations=1)

    v_len = max(int(height * 0.7), 20)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
    v_lines = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, v_kernel, iterations=1)

    mask = cv2.bitwise_or(h_lines, v_lines)
    if int(np.count_nonzero(mask)) == 0:
        return gray
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    return cv2.inpaint(gray, mask, 3, cv2.INPAINT_TELEA)


def enhance_ink(gray: np.ndarray, clip: float = 2.6) -> np.ndarray:
    """Boost pen strokes against the paper."""
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.normalize(enhanced, None, 0, 255, cv2.NORM_MINMAX)


def estimate_skew(gray: np.ndarray, max_angle: float = 12.0) -> float:
    """Estimate the text baseline angle in degrees."""
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    coords = cv2.findNonZero(binary)
    if coords is None or len(coords) < 60:
        return 0.0

    lines = cv2.HoughLinesP(binary, 1, np.pi / 360, threshold=90, minLineLength=int(gray.shape[1] * 0.25), maxLineGap=22)
    angles: list[float] = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            if x2 == x1:
                continue
            angle = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
            if abs(angle) <= max_angle:
                angles.append(angle)
    if angles:
        return float(np.median(angles))

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle += 90
    return float(angle) if abs(angle) <= max_angle else 0.0


def deskew(gray: np.ndarray, angle: float | None = None) -> tuple[np.ndarray, float]:
    resolved = estimate_skew(gray) if angle is None else angle
    if abs(resolved) < 0.25:
        return gray, 0.0
    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), resolved, 1.0)
    rotated = cv2.warpAffine(
        gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated, resolved


def remove_speckles(gray: np.ndarray, min_area: int = 6) -> np.ndarray:
    """Drop tiny isolated blobs (dust, JPEG noise) but keep dots of 'i'/'ё'."""
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 10)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num <= 1:
        return gray
    result = gray.copy()
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            result[labels == i] = 255
    return result


def ink_ratio(gray: np.ndarray) -> float:
    """Fraction of pixels that look like pen ink."""
    if gray.size == 0:
        return 0.0
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 14)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return float(np.count_nonzero(binary) / binary.size)


def segment_lines(gray: np.ndarray, min_line_height: int = 12) -> list[LineImage]:
    """Split the answer area into text lines using a horizontal projection."""
    if gray.size == 0:
        return []
    height, width = gray.shape[:2]
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 14)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)))

    projection = binary.sum(axis=1).astype(np.float32) / 255.0
    if projection.max() <= 0:
        return []
    threshold = max(projection.max() * 0.08, width * 0.008)

    lines: list[LineImage] = []
    start: int | None = None
    for y in range(height):
        active = projection[y] > threshold
        if active and start is None:
            start = y
        elif not active and start is not None:
            if y - start >= min_line_height:
                lines.append((start, y))
            start = None
    if start is not None and height - start >= min_line_height:
        lines.append((start, height))

    results: list[LineImage] = []
    for index, (y1, y2) in enumerate(lines):  # type: ignore[misc]
        pad = 4
        top = max(0, y1 - pad)
        bottom = min(height, y2 + pad)
        strip = binary[top:bottom]
        columns = np.where(strip.sum(axis=0) > 0)[0]
        if columns.size == 0:
            continue
        x1 = max(0, int(columns[0]) - pad)
        x2 = min(width, int(columns[-1]) + pad)
        crop = gray[top:bottom, x1:x2]
        if crop.size == 0:
            continue
        results.append(
            LineImage(
                index=index,
                image=crop,
                bbox=(x1, top, x2 - x1, bottom - top),
                ink_ratio=ink_ratio(crop),
            )
        )
    return results


def build_variants(image: np.ndarray, min_line_height: int = 12) -> list[PreprocessResult]:
    """Produce the preprocessing variants tried by the recogniser."""
    gray = to_gray(image)
    if gray.size == 0:
        return []

    variants: list[PreprocessResult] = []

    # 1. Gentle: illumination + contrast only, no line removal, no binarisation.
    gentle = enhance_ink(normalize_illumination(gray))
    gentle, angle = deskew(gentle)
    variants.append(PreprocessResult(name="gentle", image=gentle, deskew_angle=angle))

    # 2. Clean: form lines removed + speckle removal (default for most sheets).
    clean = remove_form_lines(normalize_illumination(gray))
    clean = remove_speckles(enhance_ink(clean))
    clean, angle = deskew(clean)
    variants.append(PreprocessResult(name="clean", image=clean, deskew_angle=angle))

    # 3. High contrast: soft (not hard) thresholding for faint pencil.
    strong = normalize_illumination(gray, 25)
    strong = cv2.addWeighted(strong, 1.8, cv2.GaussianBlur(strong, (0, 0), 3), -0.8, 0)
    strong = remove_form_lines(strong)
    strong, angle = deskew(np.clip(strong, 0, 255).astype(np.uint8))
    variants.append(PreprocessResult(name="high_contrast", image=strong, deskew_angle=angle))

    for variant in variants:
        variant.ink_ratio = ink_ratio(variant.image)
        try:
            variant.lines = segment_lines(variant.image, min_line_height)
        except Exception as exc:  # pragma: no cover
            logger.warning("line segmentation failed for %s: %s", variant.name, exc)
            variant.warnings.append(f"line segmentation failed: {exc}")
    return variants


def is_blank(image: np.ndarray, threshold: float = 0.004) -> tuple[bool, float]:
    """Cheap blank-answer test performed *before* invoking the OCR model."""
    gray = to_gray(image)
    if gray.size == 0:
        return True, 0.0
    cleaned = remove_form_lines(normalize_illumination(gray))
    cleaned = remove_speckles(cleaned, min_area=8)
    ratio = ink_ratio(cleaned)
    return ratio < threshold, ratio
