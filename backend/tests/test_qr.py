"""Unit tests: QR payload parsing, validation and decoding."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from app.cv.normalization import normalize_sheet
from app.config import DetectionConfig, NormalizationConfig
from app.cv.detection import detect_paper
from app.cv.qr import (
    OpenCvQrBackend,
    PyzbarQrBackend,
    QrPayload,
    ZxingQrBackend,
    get_backend,
    parse_payload,
    qr_readability_score,
    read_qr,
    validate_payload,
)
from app.cv.synthetic import DEFAULT_PAYLOAD, make_qr_image, render_sheet
from app.services.form_generator import FormSpec


class TestParsePayload:
    def test_parses_json(self):
        payload = parse_payload(json.dumps(DEFAULT_PAYLOAD))
        assert payload is not None
        assert payload.student_id == "9B-17"
        assert payload.class_id == "9B"
        assert payload.task_id == "history-09-04"
        assert payload.sheet_id == "9B-17-history-09-04"
        assert payload.version == 1

    def test_parses_snake_case_json(self):
        payload = parse_payload(
            json.dumps({"version": 1, "student_id": "9B-01", "class_id": "9B", "task_id": "t1", "sheet_id": "s1"})
        )
        assert payload is not None and payload.student_id == "9B-01" and payload.sheet_id == "s1"

    def test_derives_sheet_id_when_absent(self):
        payload = parse_payload(json.dumps({"studentId": "9B-02", "taskId": "math-1"}))
        assert payload is not None and payload.sheet_id == "9B-02-math-1"

    def test_parses_compact_form(self):
        payload = parse_payload("v1|9B-17|9B|history-09-04|9B-17-history-09-04")
        assert payload is not None
        assert payload.student_id == "9B-17"
        assert payload.sheet_id == "9B-17-history-09-04"

    def test_compact_without_sheet_id(self):
        payload = parse_payload("v1|9B-05|9B|task-7")
        assert payload is not None and payload.sheet_id == "9B-05-task-7"

    @pytest.mark.parametrize("text", ["", "   ", "hello world", "{not json", "[]", "v1|", "{}"])
    def test_rejects_garbage(self, text):
        assert parse_payload(text) is None

    def test_missing_student_rejected(self):
        assert parse_payload(json.dumps({"sheetId": "abc"})) is None

    def test_form_spec_payload_contains_variant_metadata(self):
        spec = FormSpec(
            student_external_id="S-101",
            student_name="Иванов Пётр",
            class_name="7Б",
            task_external_id="T-042",
            task_title="Уравнение №42",
            sheet_uid="S-101-T-042-v02",
            variant_number=2,
            variant_total=4,
        )
        payload = parse_payload(spec.payload("json"))
        assert payload is not None
        assert payload.sheet_id == "S-101-T-042-v02"
        assert payload.extra["variantNo"] == 2
        assert payload.extra["variantTotal"] == 4

    def test_keeps_raw_text(self):
        text = json.dumps(DEFAULT_PAYLOAD)
        assert parse_payload(text).raw == text


class TestValidatePayload:
    def test_valid_payload(self):
        ok, error = validate_payload(parse_payload(json.dumps(DEFAULT_PAYLOAD)))
        assert ok and error == ""

    def test_none_payload(self):
        ok, error = validate_payload(None)
        assert not ok and error == "payload_not_parsed"

    def test_bad_version(self):
        payload = QrPayload(version=0, student_id="a", class_id="b", task_id="c", sheet_id="d")
        ok, error = validate_payload(payload)
        assert not ok and error == "unsupported_version"

    def test_missing_student(self):
        payload = QrPayload(version=1, student_id="", class_id="b", task_id="c", sheet_id="d")
        assert validate_payload(payload)[1] == "missing_student_id"

    def test_missing_sheet_id(self):
        payload = QrPayload(version=1, student_id="a", class_id="b", task_id="c", sheet_id="")
        assert validate_payload(payload)[1] == "missing_sheet_id"

    def test_invalid_sheet_id_characters(self):
        payload = QrPayload(version=1, student_id="a", class_id="b", task_id="c", sheet_id="bad id!@#")
        assert validate_payload(payload)[1] == "invalid_sheet_id_format"

    def test_to_dict_round_trip(self):
        payload = parse_payload(json.dumps(DEFAULT_PAYLOAD))
        data = payload.to_dict()
        assert data["sheetId"] == DEFAULT_PAYLOAD["sheetId"]
        assert data["studentId"] == DEFAULT_PAYLOAD["studentId"]


class TestReadQr:
    def test_reads_rendered_code(self):
        image = make_qr_image(DEFAULT_PAYLOAD, size=400)
        result = read_qr(image)
        assert result.success
        assert result.payload.sheet_id == DEFAULT_PAYLOAD["sheetId"]
        assert result.backend == "opencv"

    def test_reads_compact_code(self):
        image = make_qr_image("v1|9B-03|9B|hist-1|9B-03-hist-1", size=400)
        result = read_qr(image)
        assert result.success and result.payload.student_id == "9B-03"

    def test_reads_from_full_sheet(self):
        result = read_qr(render_sheet(DEFAULT_PAYLOAD))
        assert result.success

    def test_reads_after_normalisation(self, scene_frame, background_gray):
        detection = detect_paper(scene_frame, DetectionConfig(), background=background_gray)
        normalized = normalize_sheet(scene_frame, detection.quad, NormalizationConfig(), target_ratio=1000 / 620)
        result = read_qr(normalized.color)
        assert result.success
        assert result.payload.sheet_id == DEFAULT_PAYLOAD["sheetId"]

    def test_blank_image_fails_gracefully(self):
        result = read_qr(np.full((300, 300, 3), 255, np.uint8))
        assert not result.success and result.payload is None

    def test_empty_image(self):
        result = read_qr(np.zeros((0, 0, 3), np.uint8))
        assert not result.success and result.error == "empty_image"

    def test_structurally_invalid_code_reports_error(self):
        image = make_qr_image("just some text", size=400)
        result = read_qr(image)
        assert not result.success
        assert result.raw_text == "just some text"
        assert result.error != ""

    def test_noise_tolerance(self):
        image = make_qr_image(DEFAULT_PAYLOAD, size=420)
        rng = np.random.default_rng(3)
        noisy = np.clip(image.astype(np.float32) + rng.normal(0, 12, image.shape), 0, 255).astype(np.uint8)
        assert read_qr(noisy).success

    def test_result_serialises(self):
        data = read_qr(make_qr_image(DEFAULT_PAYLOAD, size=400)).to_dict()
        assert set(data) >= {"success", "payload", "rawText", "backend"}


class TestBackends:
    def test_opencv_always_available(self):
        assert isinstance(get_backend("opencv"), OpenCvQrBackend)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            get_backend("magic")

    def test_optional_backends_degrade(self):
        """pyzbar / zxing must not crash when the native lib is absent."""
        for backend in (PyzbarQrBackend(), ZxingQrBackend()):
            text, points = backend.decode(make_qr_image(DEFAULT_PAYLOAD, size=300))
            assert isinstance(text, str)

    def test_backend_chain_skips_unavailable(self):
        result = read_qr(make_qr_image(DEFAULT_PAYLOAD, size=400), backends=("pyzbar", "zxing", "opencv"))
        assert result.success


class TestReadabilityScore:
    def test_decodable_scores_one(self):
        assert qr_readability_score(make_qr_image(DEFAULT_PAYLOAD, size=420)) == 1.0

    def test_blank_scores_low(self):
        assert qr_readability_score(np.full((300, 300, 3), 255, np.uint8)) < 0.3

    def test_bounded(self):
        rng = np.random.default_rng(0)
        noise = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
        assert 0.0 <= qr_readability_score(noise) <= 1.0

    def test_region_crop(self):
        sheet = render_sheet(DEFAULT_PAYLOAD)
        assert qr_readability_score(sheet, {"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.35}) == 1.0
