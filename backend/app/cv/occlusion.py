"""Hand / occlusion detection over the sheet.

Default implementation is a skin-tone + saturation heuristic that needs no
model. MediaPipe and YOLO backends can be enabled from the settings page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.cv.geometry import rect_from_normalized

logger = logging.getLogger(__name__)


@dataclass
class OcclusionReport:
    overall: float = 0.0
    qr_region: float = 0.0
    answer_region: float = 0.0
    border: float = 0.0
    method: str = "heuristic"
    hand_present: bool = False
    boxes: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall": round(self.overall, 4),
            "qrRegion": round(self.qr_region, 4),
            "answerRegion": round(self.answer_region, 4),
            "border": round(self.border, 4),
            "method": self.method,
            "handPresent": self.hand_present,
            "boxes": self.boxes,
        }


def skin_mask(image: np.ndarray) -> np.ndarray:
    """Skin-tone mask in YCrCb + HSV intersection (robust to white paper)."""
    if image is None or image.size == 0 or image.ndim != 3:
        return np.zeros(image.shape[:2] if image is not None else (1, 1), dtype=np.uint8)

    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    ycrcb = cv2.cvtColor(blurred, cv2.COLOR_BGR2YCrCb)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    mask_ycrcb = cv2.inRange(ycrcb, np.array([0, 133, 77], np.uint8), np.array([255, 180, 130], np.uint8))
    mask_hsv = cv2.inRange(hsv, np.array([0, 35, 60], np.uint8), np.array([25, 190, 255], np.uint8))
    mask_hsv2 = cv2.inRange(hsv, np.array([160, 35, 60], np.uint8), np.array([180, 190, 255], np.uint8))

    mask = cv2.bitwise_and(mask_ycrcb, cv2.bitwise_or(mask_hsv, mask_hsv2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    return mask


def dark_blob_mask(image: np.ndarray, sheet_mean: float) -> np.ndarray:
    """Large non-paper (dark) areas covering the sheet – e.g. a sleeve."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cutoff = max(30.0, sheet_mean * 0.55)
    mask = (gray < cutoff).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    # keep only large connected components (text strokes are small)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    result = np.zeros_like(mask)
    min_area = max(int(mask.size * 0.02), 200)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            result[labels == i] = 255
    return result


def _region_ratio(mask: np.ndarray, region: dict | None) -> float:
    if region is None or mask.size == 0:
        return 0.0
    h, w = mask.shape[:2]
    x1, y1, x2, y2 = rect_from_normalized(region, w, h)
    sub = mask[y1:y2, x1:x2]
    if sub.size == 0:
        return 0.0
    return float(np.count_nonzero(sub) / sub.size)


def _border_ratio(mask: np.ndarray, thickness_ratio: float = 0.06) -> float:
    if mask.size == 0:
        return 0.0
    h, w = mask.shape[:2]
    t_y = max(int(h * thickness_ratio), 1)
    t_x = max(int(w * thickness_ratio), 1)
    border = np.zeros_like(mask)
    border[:t_y, :] = 255
    border[-t_y:, :] = 255
    border[:, :t_x] = 255
    border[:, -t_x:] = 255
    overlap = cv2.bitwise_and(mask, border)
    denom = max(int(np.count_nonzero(border)), 1)
    return float(np.count_nonzero(overlap) / denom)


def analyse_occlusion(
    warped: np.ndarray,
    *,
    qr_region: dict | None = None,
    answer_regions: list[dict] | None = None,
    method: str = "heuristic",
    yolo_boxes: list[dict] | None = None,
) -> OcclusionReport:
    """Compute how much of the rectified sheet is covered by a hand/object."""
    report = OcclusionReport(method=method)
    if warped is None or warped.size == 0:
        return report

    if method == "off":
        return report

    gray = warped if warped.ndim == 2 else cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    sheet_mean = float(np.median(gray))

    mask = np.zeros(gray.shape, dtype=np.uint8)
    if warped.ndim == 3:
        mask = cv2.bitwise_or(mask, skin_mask(warped))
    mask = cv2.bitwise_or(mask, dark_blob_mask(warped, sheet_mean))

    if method == "mediapipe":
        mp_mask = _mediapipe_mask(warped)
        if mp_mask is not None:
            mask = cv2.bitwise_or(mask, mp_mask)
            report.method = "mediapipe"
        else:
            report.method = "heuristic(mediapipe-unavailable)"

    if method == "yolo" and yolo_boxes:
        h, w = gray.shape[:2]
        for box in yolo_boxes:
            if box.get("label") not in {"hand", "person"}:
                continue
            x1, y1, x2, y2 = (int(v) for v in box["bbox"])
            cv2.rectangle(mask, (max(x1, 0), max(y1, 0)), (min(x2, w), min(y2, h)), 255, -1)
            report.boxes.append([float(v) for v in box["bbox"]])
        report.method = "yolo"

    report.overall = float(np.count_nonzero(mask) / mask.size)
    report.qr_region = _region_ratio(mask, qr_region)
    report.border = _border_ratio(mask)
    if answer_regions:
        report.answer_region = max(_region_ratio(mask, region) for region in answer_regions)
    report.hand_present = report.overall > 0.02
    return report


def _mediapipe_mask(image: np.ndarray) -> np.ndarray | None:  # pragma: no cover - optional dep
    try:
        import mediapipe as mp  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        hands = mp.solutions.hands.Hands(static_image_mode=True, max_num_hands=2, min_detection_confidence=0.4)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        result = hands.process(rgb)
        hands.close()
        if not result.multi_hand_landmarks:
            return np.zeros(image.shape[:2], dtype=np.uint8)
        h, w = image.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)
        for landmarks in result.multi_hand_landmarks:
            pts = np.array([[lm.x * w, lm.y * h] for lm in landmarks.landmark], dtype=np.int32)
            hull = cv2.convexHull(pts)
            cv2.fillConvexPoly(mask, hull, 255)
        return cv2.dilate(mask, np.ones((25, 25), np.uint8))
    except Exception as exc:
        logger.warning("mediapipe hand detection failed: %s", exc)
        return None
