"""Unit tests: corner sorting, perspective transform, geometry scoring."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.cv.geometry import (
    Quad,
    area_change_ratio,
    corner_movement,
    crop_normalized,
    order_corners,
    perspective_score,
    quad_angles,
    quad_aspect_ratio,
    rect_from_normalized,
    rect_overlap_ratio,
    warp_quad,
)


class TestOrderCorners:
    def test_orders_shuffled_rectangle(self):
        rectangle = np.array([[10, 10], [110, 10], [110, 60], [10, 60]], np.float32)
        for permutation in ([2, 0, 3, 1], [3, 2, 1, 0], [1, 3, 0, 2]):
            shuffled = rectangle[permutation]
            ordered = order_corners(shuffled)
            np.testing.assert_allclose(ordered, rectangle, atol=1e-4)

    def test_output_is_tl_tr_br_bl(self):
        pts = np.array([[100, 50], [300, 60], [310, 200], [90, 190]], np.float32)
        tl, tr, br, bl = order_corners(pts)
        assert tl[0] < tr[0] and tl[1] < bl[1]
        assert br[0] > bl[0] and br[1] > tr[1]

    def test_rejects_wrong_point_count(self):
        with pytest.raises(ValueError):
            order_corners(np.array([[0, 0], [1, 1], [2, 2]], np.float32))

    def test_handles_rotated_square(self):
        """A 45°-rotated square is the classic degenerate case for sum/diff."""
        pts = np.array([[50, 0], [100, 50], [50, 100], [0, 50]], np.float32)
        ordered = order_corners(pts)
        assert ordered.shape == (4, 2)
        assert len({tuple(p) for p in ordered}) == 4


class TestQuadMetrics:
    def test_area_of_rectangle(self):
        quad = Quad(order_corners(np.array([[0, 0], [200, 0], [200, 100], [0, 100]], np.float32)))
        assert quad.area == pytest.approx(20000, rel=1e-3)

    def test_aspect_ratio(self):
        quad = Quad(order_corners(np.array([[0, 0], [200, 0], [200, 100], [0, 100]], np.float32)))
        assert quad_aspect_ratio(quad) == pytest.approx(2.0, rel=1e-3)

    def test_angles_of_rectangle_are_right(self):
        quad = Quad(order_corners(np.array([[0, 0], [200, 0], [200, 100], [0, 100]], np.float32)))
        for angle in quad_angles(quad):
            assert angle == pytest.approx(90.0, abs=0.5)

    def test_perspective_score_perfect_rectangle(self):
        quad = Quad(order_corners(np.array([[0, 0], [400, 0], [400, 250], [0, 250]], np.float32)))
        assert perspective_score(quad) > 0.98

    def test_perspective_score_penalises_skew(self):
        straight = Quad(order_corners(np.array([[0, 0], [400, 0], [400, 250], [0, 250]], np.float32)))
        skewed = Quad(order_corners(np.array([[60, 0], [340, 0], [400, 250], [0, 250]], np.float32)))
        assert perspective_score(skewed) < perspective_score(straight)
        assert 0.0 <= perspective_score(skewed) <= 1.0

    def test_scaled(self):
        quad = Quad(order_corners(np.array([[0, 0], [100, 0], [100, 50], [0, 50]], np.float32)))
        assert quad.scaled(2.0).area == pytest.approx(quad.area * 4, rel=1e-3)


class TestWarp:
    def test_warp_returns_requested_size(self, scene_frame):
        quad = Quad(order_corners(np.array([[100, 80], [900, 90], [890, 560], [110, 550]], np.float32)))
        warped = warp_quad(scene_frame, quad, size=(400, 250))
        assert warped.shape[:2] == (250, 400)

    def test_warp_recovers_known_rectangle(self):
        """Warping a synthetically projected pattern must restore it."""
        source = np.zeros((200, 300, 3), np.uint8)
        cv2.rectangle(source, (20, 20), (280, 180), (255, 255, 255), -1)
        cv2.circle(source, (60, 60), 18, (0, 0, 255), -1)

        canvas = np.zeros((600, 800, 3), np.uint8)
        destination = np.array([[120, 90], [660, 140], [640, 470], [150, 430]], np.float32)
        matrix = cv2.getPerspectiveTransform(
            np.array([[0, 0], [299, 0], [299, 199], [0, 199]], np.float32), destination
        )
        cv2.warpPerspective(source, matrix, (800, 600), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT)

        recovered = warp_quad(canvas, Quad(order_corners(destination)), size=(300, 200))
        assert recovered.shape[:2] == (200, 300)
        # The red dot must land back in the upper-left quadrant.
        red = recovered[:100, :150, 2].astype(int) - recovered[:100, :150, 1].astype(int)
        assert red.max() > 100

    def test_warp_rejects_empty_image(self):
        quad = Quad(order_corners(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], np.float32)))
        with pytest.raises(ValueError):
            warp_quad(np.zeros((0, 0, 3), np.uint8), quad)


class TestMovement:
    def test_no_history_is_infinite(self):
        quad = Quad(order_corners(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], np.float32)))
        assert corner_movement(None, quad) == float("inf")

    def test_identical_quads_have_zero_movement(self):
        quad = Quad(order_corners(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], np.float32)))
        assert corner_movement(quad, quad) == pytest.approx(0.0)

    def test_shifted_quad_movement_equals_shift(self):
        a = Quad(order_corners(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], np.float32)))
        b = Quad(order_corners(np.array([[5, 0], [15, 0], [15, 10], [5, 10]], np.float32)))
        assert corner_movement(a, b) == pytest.approx(5.0, rel=1e-3)

    def test_area_change_ratio(self):
        a = Quad(order_corners(np.array([[0, 0], [100, 0], [100, 100], [0, 100]], np.float32)))
        b = Quad(order_corners(np.array([[0, 0], [200, 0], [200, 100], [0, 100]], np.float32)))
        assert area_change_ratio(a, a) == pytest.approx(0.0)
        assert area_change_ratio(a, b) == pytest.approx(0.5, rel=1e-3)


class TestRegions:
    def test_rect_from_normalized(self):
        assert rect_from_normalized({"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}, 200, 100) == (0, 0, 100, 50)

    def test_rect_clamps_out_of_range(self):
        x1, y1, x2, y2 = rect_from_normalized({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}, 100, 100)
        assert x2 <= 100 and y2 <= 100 and x1 < x2 and y1 < y2

    def test_crop_normalized_shape(self):
        image = np.zeros((200, 400, 3), np.uint8)
        crop = crop_normalized(image, {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.5})
        assert crop.shape[:2] == (100, 200)

    def test_rect_overlap_ratio(self):
        assert rect_overlap_ratio((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
        assert rect_overlap_ratio((0, 0, 5, 10), (0, 0, 10, 10)) == pytest.approx(0.5)
        assert rect_overlap_ratio((20, 20, 30, 30), (0, 0, 10, 10)) == pytest.approx(0.0)
