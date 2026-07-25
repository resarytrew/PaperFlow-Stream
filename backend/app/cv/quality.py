"""Frame quality metrics: sharpness, glare, coverage, motion, occlusion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from app.config import QualityWeights


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def laplacian_variance(image: np.ndarray) -> float:
    """Raw Laplacian variance – the classic focus measure."""
    gray = to_gray(image)
    if gray.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpness_score(image: np.ndarray, reference: float = 140.0) -> float:
    """Normalise Laplacian variance into 0..1 using a saturating curve.

    ``reference`` is the variance considered "good enough" (score ≈ 0.5).
    """
    variance = laplacian_variance(image)
    if variance <= 0.0:
        return 0.0
    return float(np.clip(variance / (variance + max(reference, 1e-6)), 0.0, 1.0))


def glare_score(image: np.ndarray, threshold: int = 252, detail_window: int = 9) -> float:
    """Fraction of the region that is a specular highlight.

    Plain white paper is bright *and* flat everywhere, so neither brightness nor
    flatness alone works. A glare spot is (a) near-clipped, (b) brighter than the
    sheet's own paper level, and (c) devoid of local detail.
    """
    gray = to_gray(image)
    if gray.size == 0:
        return 0.0

    g = gray.astype(np.float32)
    paper_level = float(np.median(g))
    # Relative cut-off: a highlight must stand out from the paper itself.
    relative_cut = paper_level + max(10.0, (255.0 - paper_level) * 0.55)
    cutoff = max(float(threshold), relative_cut)
    if cutoff >= 255.0:
        cutoff = 254.0

    bright = (g >= cutoff).astype(np.float32)
    if float(bright.mean()) <= 1e-5:
        return 0.0

    blurred = cv2.blur(g, (detail_window, detail_window))
    sq = cv2.blur(g**2, (detail_window, detail_window))
    local_var = np.clip(sq - blurred**2, 0.0, None)
    flat = (local_var < 4.0).astype(np.float32)
    return float(np.clip((bright * flat).mean() * 1.6, 0.0, 1.0))


def brightness_stats(image: np.ndarray) -> dict[str, float]:
    gray = to_gray(image)
    if gray.size == 0:
        return {"mean": 0.0, "std": 0.0, "clipped_high": 0.0, "clipped_low": 0.0}
    g = gray.astype(np.float32)
    return {
        "mean": float(g.mean()),
        "std": float(g.std()),
        "clipped_high": float((gray >= 250).mean()),
        "clipped_low": float((gray <= 6).mean()),
    }


def coverage_score(quad_area: float, frame_area: float, ideal: float = 0.55) -> float:
    """How well the sheet fills the frame (1.0 at ``ideal`` occupancy)."""
    if frame_area <= 0:
        return 0.0
    ratio = float(np.clip(quad_area / frame_area, 0.0, 1.0))
    if ratio <= 0.0:
        return 0.0
    if ratio <= ideal:
        return float(np.clip(ratio / ideal, 0.0, 1.0))
    # penalise sheets that overflow the frame (risk of cropped corners)
    return float(np.clip(1.0 - (ratio - ideal) / max(1.0 - ideal, 1e-6) * 0.8, 0.0, 1.0))


def motion_score_from_diff(previous_gray: np.ndarray | None, current_gray: np.ndarray, threshold: int = 18) -> float:
    """Fraction of pixels that changed significantly between two frames."""
    if previous_gray is None:
        return 1.0
    if previous_gray.shape != current_gray.shape:
        previous_gray = cv2.resize(previous_gray, (current_gray.shape[1], current_gray.shape[0]))
    diff = cv2.absdiff(previous_gray, current_gray)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    moved = (diff > threshold).astype(np.float32)
    return float(np.clip(moved.mean() * 3.0, 0.0, 1.0))


def optical_flow_motion(previous_gray: np.ndarray | None, current_gray: np.ndarray) -> float:
    """Lightweight sparse optical-flow magnitude, normalised to 0..1."""
    if previous_gray is None:
        return 1.0
    if previous_gray.shape != current_gray.shape:
        previous_gray = cv2.resize(previous_gray, (current_gray.shape[1], current_gray.shape[0]))
    points = cv2.goodFeaturesToTrack(previous_gray, maxCorners=60, qualityLevel=0.05, minDistance=12)
    if points is None or len(points) == 0:
        return motion_score_from_diff(previous_gray, current_gray)
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(previous_gray, current_gray, points.astype(np.float32), None)
    if nxt is None or status is None:
        return motion_score_from_diff(previous_gray, current_gray)
    ok = status.ravel() == 1
    if not np.any(ok):
        return motion_score_from_diff(previous_gray, current_gray)
    displacement = np.linalg.norm(nxt[ok] - points[ok], axis=-1).ravel()
    diagonal = float(np.hypot(*current_gray.shape[:2]))
    return float(np.clip(float(np.median(displacement)) / (diagonal * 0.01 + 1e-6), 0.0, 1.0))


@dataclass
class FrameMetrics:
    """All per-frame scores plus the aggregated quality value."""

    sharpness: float = 0.0
    coverage: float = 0.0
    perspective: float = 0.0
    glare: float = 0.0
    occlusion: float = 0.0
    motion: float = 0.0
    qr_readability: float = 0.0
    quality: float = 0.0
    brightness: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in data.items()}


def compute_quality_score(metrics: FrameMetrics, weights: QualityWeights) -> float:
    """Weighted quality per section 6.8.

    Positive terms are normalised by the sum of positive weights so the result
    stays in 0..1 regardless of how the teacher tunes the configuration.
    """
    positive = (
        weights.sharpness * metrics.sharpness
        + weights.coverage * metrics.coverage
        + weights.perspective * metrics.perspective
        + weights.qr * metrics.qr_readability
    )
    positive_weight = weights.sharpness + weights.coverage + weights.perspective + weights.qr
    if positive_weight > 0:
        positive /= positive_weight

    penalty = (
        weights.glare * metrics.glare + weights.occlusion * metrics.occlusion + weights.motion * metrics.motion
    )
    return float(np.clip(positive - penalty, 0.0, 1.0))


def evaluate_frame(
    warped: np.ndarray,
    *,
    weights: QualityWeights,
    quad_area: float,
    frame_area: float,
    perspective: float,
    motion: float,
    occlusion: float,
    qr_readability: float,
    sharpness_reference: float = 140.0,
) -> FrameMetrics:
    """Build a :class:`FrameMetrics` for one rectified candidate frame."""
    metrics = FrameMetrics(
        sharpness=sharpness_score(warped, sharpness_reference),
        coverage=coverage_score(quad_area, frame_area),
        perspective=float(np.clip(perspective, 0.0, 1.0)),
        glare=glare_score(warped),
        occlusion=float(np.clip(occlusion, 0.0, 1.0)),
        motion=float(np.clip(motion, 0.0, 1.0)),
        qr_readability=float(np.clip(qr_readability, 0.0, 1.0)),
        brightness=brightness_stats(warped),
    )
    metrics.quality = compute_quality_score(metrics, weights)
    return metrics


def select_best_frame(candidates: list[FrameMetrics]) -> int:
    """Index of the highest quality candidate (-1 when the list is empty)."""
    if not candidates:
        return -1
    return int(max(range(len(candidates)), key=lambda i: candidates[i].quality))
