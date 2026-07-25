"""Corner ordering, perspective transform and geometry scoring.

Pure functions – fully unit testable without a camera.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

Point = tuple[float, float]


@dataclass(frozen=True)
class Quad:
    """A quadrilateral in *top-left, top-right, bottom-right, bottom-left* order."""

    points: np.ndarray  # shape (4, 2) float32

    def __post_init__(self) -> None:
        if self.points.shape != (4, 2):
            raise ValueError(f"Quad requires a (4, 2) array, got {self.points.shape}")

    @property
    def area(self) -> float:
        return float(abs(cv2.contourArea(self.points.astype(np.float32))))

    def as_list(self) -> list[list[float]]:
        return [[float(x), float(y)] for x, y in self.points]

    def scaled(self, factor: float) -> "Quad":
        return Quad(self.points.astype(np.float32) * float(factor))


def order_corners(points: np.ndarray) -> np.ndarray:
    """Return the 4 points ordered TL, TR, BR, BL.

    Uses the classic sum/difference trick, then falls back to angular sorting
    when the shape is degenerate (which happens on very skewed quads).
    """
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        raise ValueError(f"expected 4 points, got {pts.shape[0]}")

    ordered = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    ordered[0] = pts[int(np.argmin(s))]  # TL: smallest x+y
    ordered[2] = pts[int(np.argmax(s))]  # BR: largest x+y
    ordered[1] = pts[int(np.argmin(d))]  # TR: smallest y-x
    ordered[3] = pts[int(np.argmax(d))]  # BL: largest y-x

    # Degenerate case: the same point got assigned twice -> angular sort.
    if len({tuple(np.round(p, 3)) for p in ordered}) != 4:
        centre = pts.mean(axis=0)
        angles = np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0])
        # start from the point closest to -135deg (top-left direction)
        order = np.argsort(angles)
        rotated = pts[order]
        start = int(np.argmin(rotated.sum(axis=1)))
        ordered = np.roll(rotated, -start, axis=0).astype(np.float32)
    return ordered


def quad_from_points(points: np.ndarray) -> Quad:
    return Quad(order_corners(points))


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)))


def quad_side_lengths(quad: Quad) -> tuple[float, float, float, float]:
    tl, tr, br, bl = quad.points
    return _dist(tl, tr), _dist(tr, br), _dist(br, bl), _dist(bl, tl)


def quad_aspect_ratio(quad: Quad) -> float:
    """Width / height estimated from the mean of opposite sides."""
    top, right, bottom, left = quad_side_lengths(quad)
    width = (top + bottom) / 2.0
    height = (left + right) / 2.0
    if height <= 1e-6:
        return 0.0
    return width / height


def quad_angles(quad: Quad) -> list[float]:
    """Interior angles in degrees."""
    pts = quad.points
    angles: list[float] = []
    for i in range(4):
        prev_p = pts[(i - 1) % 4]
        cur = pts[i]
        next_p = pts[(i + 1) % 4]
        v1 = prev_p - cur
        v2 = next_p - cur
        n1 = float(np.linalg.norm(v1))
        n2 = float(np.linalg.norm(v2))
        if n1 < 1e-6 or n2 < 1e-6:
            angles.append(0.0)
            continue
        cosang = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        angles.append(math.degrees(math.acos(cosang)))
    return angles


def perspective_score(quad: Quad) -> float:
    """1.0 for a perfect rectangle facing the camera, →0 for heavy skew.

    Combines corner-angle deviation from 90° with opposite-side length ratio.
    """
    angles = quad_angles(quad)
    if not angles or min(angles) <= 1e-6:
        return 0.0
    angle_error = sum(abs(a - 90.0) for a in angles) / 4.0
    angle_term = max(0.0, 1.0 - angle_error / 35.0)

    top, right, bottom, left = quad_side_lengths(quad)
    if min(top, right, bottom, left) <= 1e-6:
        return 0.0
    horiz_ratio = min(top, bottom) / max(top, bottom)
    vert_ratio = min(left, right) / max(left, right)
    side_term = (horiz_ratio + vert_ratio) / 2.0

    return float(np.clip(0.5 * angle_term + 0.5 * side_term, 0.0, 1.0))


def target_size_for_quad(quad: Quad, fixed: tuple[int, int] | None = None) -> tuple[int, int]:
    if fixed is not None:
        return fixed
    top, right, bottom, left = quad_side_lengths(quad)
    width = int(round(max(top, bottom)))
    height = int(round(max(left, right)))
    return max(width, 16), max(height, 16)


def warp_quad(
    image: np.ndarray,
    quad: Quad,
    size: tuple[int, int] | None = None,
    keep_aspect: bool = False,
) -> np.ndarray:
    """Rectify the region bounded by ``quad`` into an axis-aligned image."""
    if image is None or image.size == 0:
        raise ValueError("empty image passed to warp_quad")

    if keep_aspect and size is not None:
        ratio = quad_aspect_ratio(quad)
        target_w, target_h = size
        if ratio > 0:
            # respect the requested height, derive width from the measured ratio
            target_w = max(16, int(round(target_h * ratio)))
        width, height = target_w, target_h
    else:
        width, height = target_size_for_quad(quad, size)

    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(quad.points.astype(np.float32), dst)
    return cv2.warpPerspective(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def corner_movement(previous: Quad | None, current: Quad | None) -> float:
    """Mean corner displacement in pixels (large value when no history)."""
    if previous is None or current is None:
        return float("inf")
    return float(np.mean(np.linalg.norm(previous.points - current.points, axis=1)))


def area_change_ratio(previous: Quad | None, current: Quad | None) -> float:
    if previous is None or current is None:
        return 1.0
    prev_area = previous.area
    cur_area = current.area
    if max(prev_area, cur_area) <= 1e-6:
        return 1.0
    return float(abs(cur_area - prev_area) / max(prev_area, cur_area))


def polygon_to_mask(polygon: list[list[float]] | np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Rasterise a polygon into a uint8 mask of ``shape`` (h, w)."""
    mask = np.zeros(shape[:2], dtype=np.uint8)
    pts = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
    if pts.shape[0] >= 3:
        cv2.fillPoly(mask, [pts], 255)
    return mask


def rect_from_normalized(region: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """Convert a normalised {x,y,w,h} region (0..1) to pixel x1,y1,x2,y2."""
    x = float(region.get("x", 0.0))
    y = float(region.get("y", 0.0))
    w = float(region.get("w", 1.0))
    h = float(region.get("h", 1.0))
    x1 = int(round(np.clip(x, 0.0, 1.0) * width))
    y1 = int(round(np.clip(y, 0.0, 1.0) * height))
    x2 = int(round(np.clip(x + w, 0.0, 1.0) * width))
    y2 = int(round(np.clip(y + h, 0.0, 1.0) * height))
    x2 = max(x2, x1 + 1)
    y2 = max(y2, y1 + 1)
    return x1, y1, min(x2, width), min(y2, height)


def crop_normalized(image: np.ndarray, region: dict) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = rect_from_normalized(region, w, h)
    return image[y1:y2, x1:x2].copy()


def rect_overlap_ratio(rect_a: tuple[float, float, float, float], rect_b: tuple[float, float, float, float]) -> float:
    """Intersection area of A∩B divided by the area of B."""
    ax1, ay1, ax2, ay2 = rect_a
    bx1, by1, bx2, by2 = rect_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_b = max((bx2 - bx1) * (by2 - by1), 1e-6)
    return float(np.clip(inter / area_b, 0.0, 1.0))
