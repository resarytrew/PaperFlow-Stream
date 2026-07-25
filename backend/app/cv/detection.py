"""Paper sheet detection.

The primary pipeline is pure OpenCV (background subtraction → threshold →
contours → quadrilateral). It works without any ML model. YOLO is an optional
*assist* used for hard cases and is never required.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.config import DetectionConfig
from app.cv.geometry import (
    Quad,
    order_corners,
    perspective_score,
    polygon_to_mask,
    quad_aspect_ratio,
)


@dataclass
class DetectionResult:
    found: bool = False
    quad: Quad | None = None
    area_ratio: float = 0.0
    aspect_ratio: float = 0.0
    perspective: float = 0.0
    diff_ratio: float = 0.0
    method: str = "none"
    candidates_found: int = 0
    touches_border: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "quad": self.quad.as_list() if self.quad else None,
            "areaRatio": round(self.area_ratio, 4),
            "aspectRatio": round(self.aspect_ratio, 4),
            "perspective": round(self.perspective, 4),
            "diffRatio": round(self.diff_ratio, 4),
            "method": self.method,
            "candidatesFound": self.candidates_found,
            "touchesBorder": self.touches_border,
            "warnings": self.warnings,
        }


def _prepare_gray(image: np.ndarray) -> np.ndarray:
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def foreground_diff_ratio(
    gray: np.ndarray,
    background: np.ndarray | None,
    mask: np.ndarray | None = None,
    threshold: int = 28,
) -> tuple[float, np.ndarray | None]:
    """Ratio of pixels differing from the empty-desk reference."""
    if background is None:
        return 0.0, None
    bg = background
    if bg.shape != gray.shape:
        bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]))
    diff = cv2.absdiff(gray, bg)
    _, binary = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    if mask is not None:
        if mask.shape != binary.shape:
            mask = cv2.resize(mask, (binary.shape[1], binary.shape[0]), interpolation=cv2.INTER_NEAREST)
        binary = cv2.bitwise_and(binary, mask)
        denominator = max(int(np.count_nonzero(mask)), 1)
    else:
        denominator = binary.size
    return float(np.count_nonzero(binary) / denominator), binary


def _candidate_masks(gray: np.ndarray, config: DetectionConfig, foreground: np.ndarray | None) -> list[np.ndarray]:
    """Several binarisations – paper on desk is not always high contrast."""
    masks: list[np.ndarray] = []
    if foreground is not None and np.count_nonzero(foreground) > 0:
        closed = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        masks.append(closed)

    # Otsu on a slightly blurred image – bright paper against darker desk.
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8)))

    # Edge based – robust when the desk is also light coloured.
    edges = cv2.Canny(gray, config.canny_low, config.canny_high)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    masks.append(edges)

    # Adaptive – handles uneven lighting.
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 51, -8)
    masks.append(cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8)))
    return masks


def _quad_from_contour(contour: np.ndarray, epsilon_ratio: float) -> np.ndarray | None:
    peri = cv2.arcLength(contour, True)
    if peri <= 1e-6:
        return None
    for factor in (epsilon_ratio, epsilon_ratio * 1.6, epsilon_ratio * 2.6, epsilon_ratio * 0.6):
        approx = cv2.approxPolyDP(contour, factor * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype(np.float32)
    # Fall back to the minimum area rectangle for rounded / occluded corners.
    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect).astype(np.float32)
    hull_area = cv2.contourArea(contour)
    box_area = cv2.contourArea(box)
    if box_area > 0 and hull_area / box_area > 0.72:
        return box
    return None


def _plausibility(
    gray: np.ndarray,
    quad: Quad,
    foreground: np.ndarray | None,
) -> tuple[float, float]:
    """How likely is this quad an actual sheet of paper?

    Returns ``(foreground_support, brightness_contrast)``.

    * ``foreground_support`` – share of the quad covered by background-diff pixels.
    * ``brightness_contrast`` – how much brighter the quad is than its surroundings,
      normalised to 0..1. Paper is virtually always lighter than the desk.
    """
    h, w = gray.shape[:2]
    inside = np.zeros((h, w), np.uint8)
    cv2.fillPoly(inside, [quad.points.astype(np.int32)], 255)
    inside_count = int(np.count_nonzero(inside))
    if inside_count == 0:
        return 0.0, 0.0

    support = 0.0
    if foreground is not None:
        fg = foreground
        if fg.shape != inside.shape:
            fg = cv2.resize(fg, (w, h), interpolation=cv2.INTER_NEAREST)
        support = float(np.count_nonzero(cv2.bitwise_and(fg, inside)) / inside_count)

    # Compare the quad against a ring around it.
    dilated = cv2.dilate(inside, np.ones((41, 41), np.uint8))
    ring = cv2.subtract(dilated, inside)
    if int(np.count_nonzero(ring)) < 50:
        return support, 0.0
    inner_med = float(np.median(gray[inside > 0]))
    outer_med = float(np.median(gray[ring > 0]))
    contrast = float(np.clip((inner_med - outer_med) / 45.0, 0.0, 1.0))
    return support, contrast


def _touches_border(quad: Quad, width: int, height: int, margin: int = 3) -> bool:
    pts = quad.points
    return bool(
        np.any(pts[:, 0] <= margin)
        or np.any(pts[:, 1] <= margin)
        or np.any(pts[:, 0] >= width - 1 - margin)
        or np.any(pts[:, 1] >= height - 1 - margin)
    )


def detect_paper(
    image: np.ndarray,
    config: DetectionConfig,
    *,
    background: np.ndarray | None = None,
    work_area: list[list[float]] | None = None,
) -> DetectionResult:
    """Detect a sheet of paper. Returns the best scoring quadrilateral."""
    if image is None or image.size == 0:
        return DetectionResult(warnings=["empty_frame"])

    height, width = image.shape[:2]
    gray = _prepare_gray(image)

    work_mask: np.ndarray | None = None
    if work_area and len(work_area) >= 3:
        work_mask = polygon_to_mask(work_area, (height, width))

    diff_ratio, foreground = foreground_diff_ratio(gray, background, work_mask)

    frame_area = float(width * height)
    if work_mask is not None:
        frame_area = float(max(int(np.count_nonzero(work_mask)), 1))

    best: tuple[float, Quad, str] | None = None
    total_candidates = 0
    large_centroids: list[np.ndarray] = []

    for index, mask in enumerate(_candidate_masks(gray, config, foreground)):
        if work_mask is not None:
            mask = cv2.bitwise_and(mask, work_mask)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:6]
        for contour in contours:
            area = float(cv2.contourArea(contour))
            ratio = area / frame_area
            if ratio < config.min_area_ratio or ratio > config.max_area_ratio:
                continue
            total_candidates += 1
            corners = _quad_from_contour(contour, config.approx_epsilon)
            if corners is None:
                continue
            quad = Quad(order_corners(corners))
            aspect = quad_aspect_ratio(quad)
            normalised_aspect = aspect if aspect >= 1.0 else (1.0 / aspect if aspect > 1e-6 else 0.0)
            if not (config.min_aspect_ratio <= aspect <= config.max_aspect_ratio) and not (
                config.min_aspect_ratio <= normalised_aspect <= config.max_aspect_ratio
            ):
                continue
            quad_ratio = quad.area / frame_area
            if quad_ratio < config.min_area_ratio or quad_ratio > config.max_area_ratio:
                continue
            if quad_ratio > 0.16:
                # Count *distinct* large quads only: the same sheet is normally
                # rediscovered by every mask strategy.
                centroid = quad.points.mean(axis=0)
                min_separation = 0.12 * float(np.hypot(width, height))
                if all(float(np.linalg.norm(centroid - c)) > min_separation for c in large_centroids):
                    large_centroids.append(centroid)
            support, contrast = _plausibility(gray, quad, foreground)
            # A sheet must either stand out from the empty-desk reference or be
            # visibly brighter than its surroundings. This rejects the phantom
            # "whole frame" quad that thresholding yields on an empty desk.
            if background is not None:
                if support < 0.45 and contrast < 0.25:
                    continue
            elif contrast < 0.30:
                # Without a background reference we can only rely on the sheet
                # being clearly brighter than the desk around it.
                continue

            persp = perspective_score(quad)
            # Prefer big, rectangular, well-filled shapes; slight bias to earlier
            # (more reliable) mask strategies.
            fill = area / max(quad.area, 1e-6)
            score = (
                persp * 0.38
                + min(quad_ratio / 0.55, 1.0) * 0.22
                + min(fill, 1.0) * 0.15
                + support * 0.15
                + contrast * 0.10
                - index * 0.01
            )
            if best is None or score > best[0]:
                best = (score, quad, f"opencv:{['bgdiff', 'otsu', 'canny', 'adaptive'][min(index, 3)]}")

    result = DetectionResult(diff_ratio=diff_ratio, candidates_found=total_candidates)
    if best is None:
        result.method = "opencv:none"
        return result

    _, quad, method = best
    result.found = True
    result.quad = quad
    result.method = method
    result.area_ratio = quad.area / frame_area
    result.aspect_ratio = quad_aspect_ratio(quad)
    result.perspective = perspective_score(quad)
    result.touches_border = _touches_border(quad, width, height)
    if result.touches_border:
        result.warnings.append("sheet_out_of_bounds")
    if len(large_centroids) > 1:
        result.warnings.append("multiple_sheets_suspected")
    if result.perspective < 0.55:
        result.warnings.append("high_perspective_angle")
    return result


def refine_with_yolo(
    image: np.ndarray,
    base: DetectionResult,
    yolo_boxes: list[dict] | None,
    config: DetectionConfig,
) -> DetectionResult:
    """Merge YOLO observations into an OpenCV result (assist, not a replacement)."""
    if not yolo_boxes:
        return base
    height, width = image.shape[:2]
    frame_area = float(width * height)

    papers = [b for b in yolo_boxes if b.get("label") in {"paper", "sheet", "document", "book"}]
    hands = [b for b in yolo_boxes if b.get("label") in {"hand", "person"}]
    others = [b for b in yolo_boxes if b not in papers and b not in hands]

    if len(papers) > 1:
        base.warnings.append("multiple_sheets_detected")
    if others:
        base.warnings.append("foreign_object_detected")
    if hands:
        base.warnings.append("hand_in_frame")

    if not base.found and papers:
        best_box = max(papers, key=lambda b: float(b.get("confidence", 0.0)))
        x1, y1, x2, y2 = (float(v) for v in best_box["bbox"])
        quad = Quad(np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32))
        area_ratio = quad.area / frame_area
        if config.min_area_ratio <= area_ratio <= config.max_area_ratio:
            base.found = True
            base.quad = quad
            base.method = "yolo:bbox"
            base.area_ratio = area_ratio
            base.aspect_ratio = quad_aspect_ratio(quad)
            base.perspective = perspective_score(quad)
            base.warnings.append("yolo_fallback_bbox")
    return base
