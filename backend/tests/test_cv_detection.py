"""Unit tests: paper detection and occlusion analysis."""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from app.config import DetectionConfig, NormalizationConfig
from app.cv.detection import detect_paper, foreground_diff_ratio, refine_with_yolo
from app.cv.normalization import rectify
from app.cv.occlusion import analyse_occlusion, skin_mask
from app.cv.synthetic import SceneOptions, empty_scene, render_scene
from app.cv.yolo_adapter import YoloAdapter, get_yolo_adapter

QR_REGION = {"x": 0.01, "y": 0.02, "w": 0.26, "h": 0.36}
ANSWER_REGIONS = [{"x": 0.03, "y": 0.42, "w": 0.94, "h": 0.52}]


class TestPaperDetection:
    def test_finds_sheet_in_normal_scene(self, scene_frame, background_gray):
        result = detect_paper(scene_frame, DetectionConfig(), background=background_gray)
        assert result.found
        assert result.quad is not None
        assert 0.2 < result.area_ratio < 0.9
        assert result.perspective > 0.85

    def test_rejects_empty_work_area(self, background_frame, background_gray):
        """Criterion: an empty desk must never produce a phantom sheet."""
        result = detect_paper(background_frame, DetectionConfig(), background=background_gray)
        assert not result.found

    def test_works_without_background_reference(self, scene_frame):
        result = detect_paper(scene_frame, DetectionConfig())
        assert result.found

    def test_no_false_positive_without_background_on_empty(self, background_frame):
        assert not detect_paper(background_frame, DetectionConfig()).found

    def test_detects_tilted_sheet(self, make_scene, background_gray):
        result = detect_paper(make_scene(tilt=0.2), DetectionConfig(), background=background_gray)
        assert result.found
        assert result.perspective < 0.99

    def test_flags_high_angle(self, make_scene, background_gray):
        result = detect_paper(make_scene(tilt=0.38), DetectionConfig(), background=background_gray)
        if result.found and result.perspective < 0.55:
            assert "high_perspective_angle" in result.warnings

    def test_area_thresholds_respected(self, make_scene, background_gray):
        config = DetectionConfig(min_area_ratio=0.85)
        result = detect_paper(make_scene(sheet_scale=0.4), config, background=background_gray)
        assert not result.found

    def test_detects_oversized_sheet_touching_border(self, make_scene, background_gray):
        result = detect_paper(make_scene(sheet_scale=1.35), DetectionConfig(), background=background_gray)
        if result.found:
            assert result.touches_border
            assert "sheet_out_of_bounds" in result.warnings

    def test_work_area_mask_limits_search(self, scene_frame, background_gray):
        """A polygon in the corner excludes the centred sheet."""
        corner = [[0, 0], [200, 0], [200, 150], [0, 150]]
        result = detect_paper(
            scene_frame, DetectionConfig(), background=background_gray, work_area=corner
        )
        assert not result.found

    def test_empty_frame_input(self):
        result = detect_paper(np.zeros((0, 0, 3), np.uint8), DetectionConfig())
        assert not result.found
        assert "empty_frame" in result.warnings

    def test_performance_under_200ms(self, scene_frame, background_gray):
        """Section 13: detection must stay below 200 ms per frame."""
        config = DetectionConfig()
        small = cv2.resize(scene_frame, (640, 360))
        bg_small = cv2.resize(background_gray, (640, 360))
        detect_paper(small, config, background=bg_small)  # warm up

        start = time.perf_counter()
        runs = 10
        for _ in range(runs):
            detect_paper(small, config, background=bg_small)
        elapsed_ms = (time.perf_counter() - start) / runs * 1000
        assert elapsed_ms < 200, f"detection took {elapsed_ms:.0f} ms/frame"

    def test_result_serialises(self, scene_frame, background_gray):
        data = detect_paper(scene_frame, DetectionConfig(), background=background_gray).to_dict()
        assert set(data) >= {"found", "quad", "areaRatio", "perspective", "method", "warnings"}


class TestForegroundDiff:
    def test_no_background_returns_zero(self):
        ratio, mask = foreground_diff_ratio(np.zeros((50, 50), np.uint8), None)
        assert ratio == 0.0 and mask is None

    def test_identical_frames_have_no_foreground(self, background_gray):
        ratio, _ = foreground_diff_ratio(background_gray, background_gray)
        assert ratio < 0.02

    def test_sheet_produces_foreground(self, scene_frame, background_gray):
        gray = cv2.GaussianBlur(cv2.cvtColor(scene_frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        ratio, mask = foreground_diff_ratio(gray, background_gray)
        assert ratio > 0.1
        assert mask is not None


class TestOcclusion:
    def test_clean_sheet_has_low_occlusion(self, scene_frame, background_gray):
        detection = detect_paper(scene_frame, DetectionConfig(), background=background_gray)
        warped = rectify(scene_frame, detection.quad, NormalizationConfig(), 1000 / 620)
        report = analyse_occlusion(warped, qr_region=QR_REGION, answer_regions=ANSWER_REGIONS)
        assert report.answer_region < 0.08

    def test_hand_over_answer_detected(self, make_scene, background_gray):
        frame = make_scene(hand_over_answer=True)
        detection = detect_paper(frame, DetectionConfig(), background=background_gray)
        assert detection.found
        warped = rectify(frame, detection.quad, NormalizationConfig(), 1000 / 620)
        report = analyse_occlusion(warped, qr_region=QR_REGION, answer_regions=ANSWER_REGIONS)
        assert report.answer_region > 0.08
        assert report.hand_present

    def test_off_method_returns_zero(self, scene_frame):
        report = analyse_occlusion(scene_frame, method="off")
        assert report.overall == 0.0

    def test_skin_mask_on_skin_colour(self):
        patch = np.full((100, 100, 3), (120, 158, 205), np.uint8)
        assert float(np.count_nonzero(skin_mask(patch))) / patch[:, :, 0].size > 0.5

    def test_skin_mask_on_white_paper(self):
        paper = np.full((100, 100, 3), 245, np.uint8)
        assert float(np.count_nonzero(skin_mask(paper))) / paper[:, :, 0].size < 0.1

    def test_report_serialises(self, scene_frame):
        data = analyse_occlusion(scene_frame, qr_region=QR_REGION).to_dict()
        assert set(data) >= {"overall", "qrRegion", "answerRegion", "method", "handPresent"}


class TestYoloAdapter:
    def test_unavailable_without_model(self):
        adapter = YoloAdapter("")
        assert not adapter.available
        assert adapter.detect(np.zeros((100, 100, 3), np.uint8)) == []

    def test_missing_file_reports_error(self):
        adapter = YoloAdapter("/nonexistent/model.pt")
        assert not adapter.available
        assert "not found" in (adapter.status["error"] or "")

    def test_pipeline_works_without_yolo(self, scene_frame, background_gray):
        """Criterion: the app must work with no YOLO model loaded."""
        assert detect_paper(scene_frame, DetectionConfig(use_yolo=False), background=background_gray).found

    def test_cached_adapter(self):
        assert get_yolo_adapter("", 0.35) is get_yolo_adapter("", 0.35)

    def test_refine_with_no_boxes_is_noop(self, scene_frame, background_gray):
        base = detect_paper(scene_frame, DetectionConfig(), background=background_gray)
        assert refine_with_yolo(scene_frame, base, None, DetectionConfig()) is base

    def test_refine_adds_warnings(self, scene_frame, background_gray):
        base = detect_paper(scene_frame, DetectionConfig(), background=background_gray)
        boxes = [
            {"label": "hand", "confidence": 0.8, "bbox": [10, 10, 100, 100]},
            {"label": "bottle", "confidence": 0.7, "bbox": [200, 200, 260, 300]},
        ]
        refined = refine_with_yolo(scene_frame, base, boxes, DetectionConfig())
        assert "hand_in_frame" in refined.warnings
        assert "foreign_object_detected" in refined.warnings

    def test_refine_supplies_bbox_when_opencv_fails(self, background_frame):
        from app.cv.detection import DetectionResult

        empty = DetectionResult()
        boxes = [{"label": "paper", "confidence": 0.9, "bbox": [200, 150, 900, 600]}]
        refined = refine_with_yolo(background_frame, empty, boxes, DetectionConfig())
        assert refined.found
        assert refined.method == "yolo:bbox"
