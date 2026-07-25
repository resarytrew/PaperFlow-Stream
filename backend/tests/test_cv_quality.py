"""Unit tests: sharpness, glare, coverage, motion and the quality score."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.config import QualityWeights
from app.cv.quality import (
    FrameMetrics,
    brightness_stats,
    compute_quality_score,
    coverage_score,
    evaluate_frame,
    glare_score,
    laplacian_variance,
    motion_score_from_diff,
    optical_flow_motion,
    select_best_frame,
    sharpness_score,
)


def _textured(size=(300, 300), seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.full((*size, 3), 235, np.uint8)
    for _ in range(40):
        x1, y1 = rng.integers(0, size[1] - 40, 2)
        cv2.line(image, (int(x1), int(y1)), (int(x1 + 35), int(y1 + 12)), (20, 20, 20), 2)
    return image


class TestSharpness:
    def test_flat_image_has_zero_variance(self):
        assert laplacian_variance(np.full((100, 100), 128, np.uint8)) == pytest.approx(0.0, abs=1e-6)

    def test_score_in_unit_range(self):
        for image in (np.full((50, 50, 3), 200, np.uint8), _textured()):
            assert 0.0 <= sharpness_score(image) <= 1.0

    def test_blur_reduces_sharpness(self):
        sharp = _textured()
        blurred = cv2.GaussianBlur(sharp, (15, 15), 6)
        assert sharpness_score(blurred) < sharpness_score(sharp)

    def test_monotonic_in_blur_amount(self):
        sharp = _textured()
        scores = [sharpness_score(cv2.GaussianBlur(sharp, (k, k), k / 3)) for k in (3, 9, 21)]
        assert scores == sorted(scores, reverse=True)

    def test_reference_shifts_curve(self):
        image = _textured()
        assert sharpness_score(image, reference=10.0) > sharpness_score(image, reference=5000.0)

    def test_empty_image(self):
        assert sharpness_score(np.zeros((0, 0), np.uint8)) == 0.0


class TestGlare:
    def test_plain_white_paper_is_not_glare(self):
        """A uniformly lit white sheet must not be reported as glare."""
        paper = np.full((300, 300, 3), 246, np.uint8)
        assert glare_score(paper) < 0.05

    def test_specular_highlight_detected(self):
        image = np.full((300, 300, 3), 200, np.uint8)
        cv2.circle(image, (150, 150), 70, (255, 255, 255), -1)
        assert glare_score(image) > 0.05

    def test_dark_image_has_no_glare(self):
        assert glare_score(np.full((100, 100, 3), 30, np.uint8)) == pytest.approx(0.0, abs=1e-6)

    def test_score_bounded(self):
        assert 0.0 <= glare_score(np.full((100, 100, 3), 255, np.uint8)) <= 1.0


class TestBrightness:
    def test_stats_keys(self):
        stats = brightness_stats(np.full((50, 50, 3), 128, np.uint8))
        assert set(stats) == {"mean", "std", "clipped_high", "clipped_low"}
        assert stats["mean"] == pytest.approx(128, abs=1)

    def test_clipping_detected(self):
        assert brightness_stats(np.full((50, 50), 255, np.uint8))["clipped_high"] == pytest.approx(1.0)
        assert brightness_stats(np.zeros((50, 50), np.uint8))["clipped_low"] == pytest.approx(1.0)


class TestCoverage:
    def test_ideal_occupancy_scores_one(self):
        assert coverage_score(55.0, 100.0, ideal=0.55) == pytest.approx(1.0, abs=1e-6)

    def test_small_sheet_scores_low(self):
        assert coverage_score(5.0, 100.0) < 0.2

    def test_overflow_penalised(self):
        assert coverage_score(98.0, 100.0) < coverage_score(55.0, 100.0)

    def test_zero_frame_area(self):
        assert coverage_score(10.0, 0.0) == 0.0


class TestMotion:
    def test_no_previous_frame_is_max_motion(self):
        assert motion_score_from_diff(None, np.zeros((50, 50), np.uint8)) == 1.0

    def test_identical_frames_have_no_motion(self):
        frame = _textured()[:, :, 0]
        assert motion_score_from_diff(frame, frame) == pytest.approx(0.0, abs=1e-6)

    def test_shifted_frame_has_motion(self):
        frame = _textured()[:, :, 0]
        shifted = np.roll(frame, 25, axis=1)
        assert motion_score_from_diff(frame, shifted) > 0.05

    def test_handles_size_mismatch(self):
        a = _textured((200, 200))[:, :, 0]
        b = _textured((300, 300))[:, :, 0]
        assert 0.0 <= motion_score_from_diff(a, b) <= 1.0

    def test_optical_flow_bounded(self):
        frame = _textured()[:, :, 0]
        assert 0.0 <= optical_flow_motion(frame, np.roll(frame, 5, axis=1)) <= 1.0
        assert optical_flow_motion(None, frame) == 1.0


class TestQualityScore:
    def test_perfect_frame_scores_high(self):
        metrics = FrameMetrics(
            sharpness=1.0, coverage=1.0, perspective=1.0, qr_readability=1.0, glare=0.0, occlusion=0.0, motion=0.0
        )
        assert compute_quality_score(metrics, QualityWeights()) == pytest.approx(1.0, abs=1e-6)

    def test_worst_frame_scores_zero(self):
        metrics = FrameMetrics(
            sharpness=0.0, coverage=0.0, perspective=0.0, qr_readability=0.0, glare=1.0, occlusion=1.0, motion=1.0
        )
        assert compute_quality_score(metrics, QualityWeights()) == pytest.approx(0.0)

    def test_always_bounded(self):
        rng = np.random.default_rng(0)
        weights = QualityWeights()
        for _ in range(200):
            values = rng.random(7)
            metrics = FrameMetrics(*values)  # type: ignore[arg-type]
            assert 0.0 <= compute_quality_score(metrics, weights) <= 1.0

    def test_occlusion_penalises(self):
        weights = QualityWeights()
        base = FrameMetrics(sharpness=0.9, coverage=0.9, perspective=0.9, qr_readability=1.0)
        occluded = FrameMetrics(sharpness=0.9, coverage=0.9, perspective=0.9, qr_readability=1.0, occlusion=0.5)
        assert compute_quality_score(occluded, weights) < compute_quality_score(base, weights)

    def test_weights_are_configurable(self):
        metrics = FrameMetrics(sharpness=1.0, coverage=0.0, perspective=0.0, qr_readability=0.0)
        sharp_heavy = QualityWeights(sharpness=1.0, coverage=0.0, perspective=0.0, qr=0.0)
        assert compute_quality_score(metrics, sharp_heavy) == pytest.approx(1.0)

    def test_evaluate_frame_populates_all_fields(self):
        metrics = evaluate_frame(
            _textured(),
            weights=QualityWeights(),
            quad_area=5000.0,
            frame_area=10000.0,
            perspective=0.95,
            motion=0.01,
            occlusion=0.0,
            qr_readability=1.0,
        )
        assert 0.0 <= metrics.quality <= 1.0
        assert metrics.brightness["mean"] > 0
        assert set(metrics.to_dict()) >= {"sharpness", "coverage", "quality", "glare"}


class TestBestFrameSelection:
    def test_empty_list_returns_minus_one(self):
        assert select_best_frame([]) == -1

    def test_picks_highest_quality_not_first(self):
        candidates = [FrameMetrics(quality=q) for q in (0.4, 0.9, 0.55, 0.2)]
        assert select_best_frame(candidates) == 1

    def test_single_candidate(self):
        assert select_best_frame([FrameMetrics(quality=0.3)]) == 0
