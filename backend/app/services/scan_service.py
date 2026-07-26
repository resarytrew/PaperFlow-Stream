"""Scan pipeline orchestration.

Holds one :class:`SessionRuntime` per active scanning session: the state
machine, background reference, candidate buffer and diagnostics. Frames arrive
from the browser over WebSocket; this module analyses them and persists the
best one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import RuntimeConfig
from app.cv.detection import DetectionResult, detect_paper, refine_with_yolo
from app.cv.geometry import Quad, corner_movement, crop_normalized
from app.cv.normalization import normalize_sheet, rectify
from app.cv.occlusion import analyse_occlusion
from app.cv.qr import QrReadResult, read_qr
from app.cv.quality import FrameMetrics, evaluate_frame, motion_score_from_diff, select_best_frame, to_gray
from app.cv.state_machine import (
    Decision,
    DecisionAction,
    FrameObservation,
    ScanState,
    ScanStateMachine,
)
from app.cv.yolo_adapter import get_yolo_adapter
from app.models import (
    CameraProfile,
    QrStatus,
    ScanLog,
    ScanSession,
    ScanStatus,
    ScannedSheet,
    Student,
    Task,
)
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

DEFAULT_QR_REGION = {"x": 0.01, "y": 0.02, "w": 0.28, "h": 0.38}
DEFAULT_ANSWER_REGIONS = [{"x": 0.03, "y": 0.42, "w": 0.94, "h": 0.54}]


@dataclass
class Candidate:
    """One frame kept during the SELECTING_BEST_FRAME window."""

    index: int
    frame: np.ndarray
    quad: Quad
    metrics: FrameMetrics
    detection: DetectionResult
    qr: QrReadResult | None = None
    timestamp_ms: float = 0.0


@dataclass
class SheetOutcome:
    """Result of processing one physical sheet."""

    success: bool
    sheet_id: int | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    student_label: str = ""
    sheet_uid: str = ""
    quality: float = 0.0
    qr_status: str = QrStatus.unreadable.value
    scan_status: str = ScanStatus.ok.value
    duplicate_of: int | None = None
    processing_ms: int = 0
    thumbnail: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "sheetId": self.sheet_id,
            "reason": self.reason,
            "warnings": self.warnings,
            "studentLabel": self.student_label,
            "sheetUid": self.sheet_uid,
            "quality": round(self.quality, 4),
            "qrStatus": self.qr_status,
            "scanStatus": self.scan_status,
            "duplicateOf": self.duplicate_of,
            "processingMs": self.processing_ms,
            "thumbnail": self.thumbnail,
        }


class SessionRuntime:
    """Per-session scanning state (lives in memory while scanning)."""

    def __init__(self, session_id: int, config: RuntimeConfig) -> None:
        self.session_id = session_id
        self.config = config
        self.machine = ScanStateMachine(config.detection, config.stability)
        self.background_gray: np.ndarray | None = None
        self.work_area: list[list[float]] | None = None
        self.qr_region: dict = dict(DEFAULT_QR_REGION)
        self.answer_regions: list[dict] = [dict(r) for r in DEFAULT_ANSWER_REGIONS]
        self.target_ratio: float | None = None

        self.candidates: list[Candidate] = []
        self.frame_index = 0
        self.previous_gray: np.ndarray | None = None
        self.previous_quad: Quad | None = None
        self.started_monotonic = time.monotonic()
        self.scan_timestamps: list[float] = []
        self.last_outcome: SheetOutcome | None = None
        self.current_events: list[dict] = []
        self.diagnostics_enabled = False
        self.diagnostic_frames: list[np.ndarray] = []
        self.yolo_counter = 0
        self.last_yolo_boxes: list[dict] = []
        self.last_qr_readability = 0.5
        self.last_qr_readability_frame = -10_000
        self.last_qr_preview: dict | None = None
        self.student_labels: dict[str, str] = {}
        self.scanned_sheet_uids: set[str] = set()
        self.counters = {"scanned": 0, "errors": 0, "duplicates": 0, "unidentified": 0}
        # Process-wide per-session frame gate used by the WebSocket layer.
        # Prevents two browser tabs from mutating the same runtime concurrently.
        self.processing = False

    # -------------------------------------------------------------- utilities

    @property
    def elapsed_minutes(self) -> float:
        return max((time.monotonic() - self.started_monotonic) / 60.0, 1e-6)

    def average_speed(self) -> float:
        """Sheets per minute over the last 10 scans."""
        if len(self.scan_timestamps) < 2:
            return 0.0
        recent = self.scan_timestamps[-10:]
        span = recent[-1] - recent[0]
        if span <= 1e-6:
            return 0.0
        return (len(recent) - 1) / (span / 60.0)

    def log_event(self, kind: str, data: dict | None = None) -> None:
        self.current_events.append(
            {"t": round((time.monotonic() - self.started_monotonic) * 1000.0, 1), "event": kind, **(data or {})}
        )
        if len(self.current_events) > 600:
            del self.current_events[:300]

    def reset_candidates(self) -> None:
        self.candidates = []

    def apply_profile(self, profile: CameraProfile | None, template: Any | None = None) -> None:
        """Load calibration polygon / regions into the runtime."""
        if profile is not None:
            if profile.work_area_polygon:
                self.work_area = [[float(p[0]), float(p[1])] for p in profile.work_area_polygon]
            if profile.qr_region:
                self.qr_region = dict(profile.qr_region)
            if profile.answer_regions:
                self.answer_regions = [dict(r) for r in profile.answer_regions]
            if profile.background_reference_path:
                try:
                    image = get_storage().load(profile.background_reference_path)
                    self.background_gray = cv2.GaussianBlur(to_gray(image), (5, 5), 0)
                except Exception as exc:
                    logger.warning("could not load background reference: %s", exc)
        if template is not None:
            if getattr(template, "qr_region", None):
                self.qr_region = dict(template.qr_region)
            if getattr(template, "answer_regions", None):
                self.answer_regions = [dict(r) for r in template.answer_regions]
            ratio = getattr(template, "aspect_ratio", None)
            if ratio:
                self.target_ratio = float(ratio)


class ScanService:
    """Stateless helper operating on :class:`SessionRuntime` objects."""

    def __init__(self) -> None:
        self._runtimes: dict[int, SessionRuntime] = {}

    # ------------------------------------------------------------- lifecycle

    def get_runtime(self, session_id: int) -> SessionRuntime | None:
        return self._runtimes.get(session_id)

    def create_runtime(self, session_id: int, config: RuntimeConfig) -> SessionRuntime:
        runtime = SessionRuntime(session_id, config)
        self._runtimes[session_id] = runtime
        return runtime

    def drop_runtime(self, session_id: int) -> None:
        self._runtimes.pop(session_id, None)

    def ensure_runtime(self, session_id: int, config: RuntimeConfig) -> SessionRuntime:
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            runtime = self.create_runtime(session_id, config)
        else:
            runtime.config = config
            runtime.machine.detection = config.detection
            runtime.machine.stability = config.stability
        return runtime

    # ------------------------------------------------------------ frame path

    @staticmethod
    def _qr_texture_score(image: np.ndarray) -> float:
        """Fallback QR readability proxy when decoding is not yet possible."""
        if image is None or image.size == 0:
            return 0.0
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return float(np.clip(variance / 400.0, 0.0, 0.75))

    @staticmethod
    def _qr_preview_dict(runtime: SessionRuntime, result: QrReadResult, score: float) -> dict:
        payload = result.payload
        if result.success and payload is not None:
            student_key = payload.student_id.lower()
            sheet_key = payload.sheet_id.lower()
            return {
                "success": True,
                "readability": round(score, 3),
                "studentId": payload.student_id,
                "studentLabel": runtime.student_labels.get(student_key) or payload.student_id,
                "classId": payload.class_id,
                "taskId": payload.task_id,
                "sheetUid": payload.sheet_id,
                "variantNo": payload.extra.get("variantNo") or payload.extra.get("variant_no"),
                "variantTotal": payload.extra.get("variantTotal") or payload.extra.get("variant_total"),
                "duplicate": sheet_key in runtime.scanned_sheet_uids,
                "backend": result.backend,
                "error": "",
            }
        return {
            "success": False,
            "readability": round(score, 3),
            "studentId": "",
            "studentLabel": "",
            "classId": "",
            "taskId": "",
            "sheetUid": "",
            "variantNo": None,
            "variantTotal": None,
            "duplicate": False,
            "backend": result.backend,
            "error": result.error or "not_found",
        }

    def _read_qr_preview(self, runtime: SessionRuntime, warped: np.ndarray) -> tuple[float, dict]:
        """Decode QR on the analysis-sized warped frame for live operator feedback."""
        roi = crop_normalized(warped, runtime.qr_region)
        target = roi if roi.size > 0 else warped
        result = read_qr(target, backends=("opencv",), enhance=True)
        if not result.success:
            result = read_qr(warped, backends=("opencv",), enhance=True)
        score = 1.0 if result.success else self._qr_texture_score(target)
        return score, self._qr_preview_dict(runtime, result, score)

    @staticmethod
    def _remember_candidate(runtime: SessionRuntime, candidate: Candidate) -> None:
        """Keep a bounded candidate buffer, replacing the weakest frame if needed."""
        limit = max(runtime.config.stability.max_candidates, 1)
        if len(runtime.candidates) < limit:
            runtime.candidates.append(candidate)
            return
        worst_index, worst = min(
            enumerate(runtime.candidates), key=lambda item: item[1].metrics.quality
        )
        if candidate.metrics.quality > worst.metrics.quality:
            runtime.candidates[worst_index] = candidate

    def analyse_frame(self, runtime: SessionRuntime, frame: np.ndarray) -> tuple[Decision, dict]:
        """Analyse one incoming frame and advance the state machine."""
        started = time.perf_counter()
        config = runtime.config
        runtime.frame_index += 1
        now_ms = time.monotonic() * 1000.0

        # Work on a reduced copy – keeps per-frame cost well under 200 ms.
        height, width = frame.shape[:2]
        scale = min(1.0, config.capture.analysis_width / max(width, 1))
        small = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale < 1.0 else frame
        small_gray = cv2.GaussianBlur(to_gray(small), (5, 5), 0)

        work_area_small = None
        if runtime.work_area:
            work_area_small = [[p[0] * scale, p[1] * scale] for p in runtime.work_area]

        background_small = runtime.background_gray
        if background_small is not None and background_small.shape != small_gray.shape:
            background_small = cv2.resize(background_small, (small_gray.shape[1], small_gray.shape[0]))

        detection = detect_paper(
            small, config.detection, background=background_small, work_area=work_area_small
        )

        # Optional YOLO assist – throttled, never required.
        if config.detection.use_yolo:
            runtime.yolo_counter += 1
            if runtime.yolo_counter % max(config.detection.yolo_every_n_frames, 1) == 0:
                adapter = get_yolo_adapter(config.detection.yolo_model_path, config.detection.yolo_confidence)
                if adapter.available:
                    runtime.last_yolo_boxes = [d.to_dict() for d in adapter.detect(small)]
            if runtime.last_yolo_boxes:
                detection = refine_with_yolo(small, detection, runtime.last_yolo_boxes, config.detection)

        motion = motion_score_from_diff(runtime.previous_gray, small_gray)
        runtime.previous_gray = small_gray

        quad_full: Quad | None = None
        metrics = FrameMetrics(motion=motion)
        occlusion_answer = 0.0
        occlusion_overall = 0.0
        warped_small: np.ndarray | None = None

        if detection.found and detection.quad is not None:
            quad_full = detection.quad.scaled(1.0 / scale) if scale < 1.0 else detection.quad
            corner_shift = corner_movement(runtime.previous_quad, detection.quad)
            runtime.previous_quad = detection.quad
            if np.isfinite(corner_shift):
                diagonal = float(np.hypot(*small_gray.shape[:2]))
                motion = max(motion, float(np.clip(corner_shift / (diagonal * 0.02 + 1e-6), 0.0, 1.0)))

            try:
                warped_small = rectify(small, detection.quad, config.normalization, runtime.target_ratio)
                occ = analyse_occlusion(
                    warped_small,
                    qr_region=runtime.qr_region,
                    answer_regions=runtime.answer_regions,
                    method=config.detection.hand_detector,
                    yolo_boxes=runtime.last_yolo_boxes,
                )
                occlusion_answer = max(occ.answer_region, occ.qr_region * 0.6)
                occlusion_overall = occ.overall
                frame_area = float(small.shape[0] * small.shape[1])
                # Full QR decode is comparatively expensive. Sample it only once
                # every N frames and reuse the last score for interim quality
                # overlays; authoritative QR reading still happens on selected
                # full-resolution candidates in process_best_candidate().
                every = max(config.stability.qr_readability_every_n_frames, 1)
                should_sample_qr = (
                    runtime.frame_index - runtime.last_qr_readability_frame >= every
                    or (runtime.machine.state == ScanState.SELECTING_BEST_FRAME and not runtime.candidates)
                )
                qr_readability = runtime.last_qr_readability
                if should_sample_qr:
                    qr_readability, runtime.last_qr_preview = self._read_qr_preview(runtime, warped_small)
                    runtime.last_qr_readability = qr_readability
                    runtime.last_qr_readability_frame = runtime.frame_index

                metrics = evaluate_frame(
                    warped_small,
                    weights=config.quality_weights,
                    quad_area=detection.quad.area,
                    frame_area=frame_area,
                    perspective=detection.perspective,
                    motion=motion,
                    occlusion=occlusion_answer,
                    qr_readability=qr_readability,
                    sharpness_reference=config.stability.sharpness_reference,
                )
            except Exception as exc:
                logger.warning("frame evaluation failed: %s", exc)
                metrics = FrameMetrics(motion=motion)
        else:
            runtime.previous_quad = None
            runtime.last_qr_preview = None

        observation = FrameObservation(
            timestamp_ms=now_ms,
            paper_found=detection.found,
            area_ratio=detection.area_ratio,
            diff_ratio=detection.diff_ratio,
            motion_score=metrics.motion if detection.found else motion,
            sharpness=metrics.sharpness,
            glare=metrics.glare,
            occlusion_answer=occlusion_answer,
            occlusion_overall=occlusion_overall,
            corners_visible=detection.found and not detection.touches_border,
            touches_border=detection.touches_border,
            perspective=detection.perspective,
            quality=metrics.quality,
            warnings=detection.warnings,
        )

        decision = runtime.machine.update(observation)
        if decision.changed:
            runtime.log_event(
                "state",
                {"state": decision.state.value, "motion": round(observation.motion_score, 3)},
            )

        if decision.action == DecisionAction.RESET_CANDIDATES:
            runtime.reset_candidates()

        if decision.action in (DecisionAction.COLLECT_CANDIDATE, DecisionAction.PROCESS_BEST):
            if quad_full is not None:
                self._remember_candidate(
                    runtime,
                    Candidate(
                        index=runtime.frame_index,
                        frame=frame.copy(),
                        quad=quad_full,
                        metrics=metrics,
                        detection=detection,
                        timestamp_ms=now_ms,
                    ),
                )

        if runtime.diagnostics_enabled and len(runtime.diagnostic_frames) < config.privacy.diagnostics_max_clip_frames:
            runtime.diagnostic_frames.append(cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1]))))

        analysis_ms = (time.perf_counter() - started) * 1000.0

        overlay = {
            "quad": (detection.quad.scaled(1.0 / scale).as_list() if detection.quad and scale < 1.0 else (detection.quad.as_list() if detection.quad else None)),
            "workArea": runtime.work_area,
            "qrRegion": runtime.qr_region,
            "answerRegions": runtime.answer_regions,
            "qrPreview": runtime.last_qr_preview,
            "detection": detection.to_dict(),
            "metrics": metrics.to_dict(),
            "occlusionAnswer": round(occlusion_answer, 4),
            "frameIndex": runtime.frame_index,
            "analysisMs": round(analysis_ms, 1),
            "candidates": len(runtime.candidates),
            "yoloBoxes": runtime.last_yolo_boxes if config.detection.use_yolo else [],
        }
        return decision, overlay

    # --------------------------------------------------------- sheet persist

    def process_best_candidate(self, db: Session, runtime: SessionRuntime, session: ScanSession) -> SheetOutcome:
        """Pick the best candidate, normalise, read QR, dedupe and store it."""
        started = time.perf_counter()
        config = runtime.config

        if not runtime.candidates:
            return SheetOutcome(success=False, reason="no_candidates")

        # Refine QR readability on the *full resolution* candidates before choosing.
        for candidate in runtime.candidates:
            try:
                warped = rectify(candidate.frame, candidate.quad, config.normalization, runtime.target_ratio)
                qr_result = read_qr(crop_normalized(warped, runtime.qr_region), enhance=True)
                if not qr_result.success:
                    qr_result = read_qr(warped, enhance=True)
                candidate.qr = qr_result
                candidate.metrics.qr_readability = 1.0 if qr_result.success else 0.25
                from app.cv.quality import compute_quality_score

                candidate.metrics.quality = compute_quality_score(candidate.metrics, config.quality_weights)
            except Exception as exc:
                logger.warning("candidate refinement failed: %s", exc)
                candidate.metrics.qr_readability = 0.0

        best_index = select_best_frame([c.metrics for c in runtime.candidates])
        if best_index < 0:
            return SheetOutcome(success=False, reason="no_candidates")
        best = runtime.candidates[best_index]

        candidate_scores = [
            {"index": c.index, "quality": round(c.metrics.quality, 4), "sharpness": round(c.metrics.sharpness, 4),
             "qr": round(c.metrics.qr_readability, 3)}
            for c in runtime.candidates
        ]
        runtime.log_event("best_frame", {"selected": best.index, "of": len(runtime.candidates)})

        if best.metrics.quality < config.stability.min_quality_score:
            outcome = SheetOutcome(
                success=False,
                reason="low_quality",
                quality=best.metrics.quality,
                warnings=["Качество кадра ниже порога"],
            )
            self._store_failed_log(db, runtime, session, candidate_scores, best_index, outcome)
            runtime.counters["errors"] += 1
            runtime.reset_candidates()
            return outcome

        warnings: list[str] = list(best.detection.warnings)

        try:
            normalized = normalize_sheet(
                best.frame,
                best.quad,
                config.normalization,
                target_ratio=runtime.target_ratio,
                auto_orient=True,
            )
        except Exception as exc:
            logger.exception("normalisation failed")
            runtime.counters["errors"] += 1
            runtime.reset_candidates()
            return SheetOutcome(success=False, reason=f"normalization_failed: {exc}")

        # QR from the *normalised* sheet is the authoritative read.
        qr_result = read_qr(crop_normalized(normalized.color, runtime.qr_region), enhance=True)
        if not qr_result.success:
            qr_result = read_qr(normalized.color, enhance=True)
        if not qr_result.success and best.qr is not None and best.qr.success:
            qr_result = best.qr

        payload = qr_result.payload
        qr_status = QrStatus.read.value if qr_result.success else QrStatus.unreadable.value
        scan_status = ScanStatus.ok.value
        student: Student | None = None
        task: Task | None = None
        duplicate_of: int | None = None
        sheet_uid: str | None = None

        if qr_result.success and payload is not None:
            sheet_uid = payload.sheet_id
            student = db.execute(
                select(Student).where(func.lower(Student.external_id) == payload.student_id.lower())
            ).scalar_one_or_none()
            if payload.task_id:
                task = db.execute(
                    select(Task).where(func.lower(Task.external_id) == payload.task_id.lower())
                ).scalar_one_or_none()

            if student is None:
                warnings.append(f"Ученик {payload.student_id} не найден в базе")
                qr_status = QrStatus.mismatch.value
                scan_status = ScanStatus.unidentified.value
            elif session.class_id and student.class_id and student.class_id != session.class_id:
                warnings.append("Ученик принадлежит другому классу")
                qr_status = QrStatus.mismatch.value

            if task is not None and session.task_id and task.id != session.task_id:
                warnings.append("Задание не соответствует текущей сессии")
                qr_status = QrStatus.mismatch.value

            # Duplicate detection by sheet_uid (section 6.10 / criterion 8).
            # Scoped to the current session: the same student+task scanned in a
            # *later* session (e.g. a retake) must not be flagged as a duplicate
            # of the earlier session's sheet.
            existing = db.execute(
                select(ScannedSheet)
                .where(
                    ScannedSheet.sheet_uid == sheet_uid,
                    ScannedSheet.session_id == session.id,
                    ScannedSheet.scan_status != ScanStatus.deleted.value,
                )
                .order_by(ScannedSheet.id)
            ).scalars().first()
            if existing is not None:
                duplicate_of = existing.id
                scan_status = ScanStatus.duplicate.value
                warnings.append(f"Дубликат листа {sheet_uid}")
        else:
            if qr_result.error and qr_result.error != "not_found":
                qr_status = QrStatus.invalid.value
                warnings.append(f"QR-код некорректен: {qr_result.error}")
            else:
                warnings.append("QR-код не прочитан")
            scan_status = ScanStatus.unidentified.value

        # ------------------------------------------------------------ persist
        storage = get_storage()
        slug = sheet_uid or f"unknown-{int(time.time() * 1000)}"
        directory = storage.sheet_dir(session.id, slug)
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")[:-3]

        try:
            source_path = None
            if config.normalization.keep_source_frame:
                source_path = storage.save_image(
                    best.frame, directory / f"source-{stamp}.jpg", config.normalization.jpeg_quality
                )
            normalized_path = storage.save_image(
                normalized.color, directory / f"normalized-{stamp}.jpg", config.normalization.jpeg_quality
            )
            enhanced_path = storage.save_image(normalized.enhanced, directory / f"enhanced-{stamp}.png")
            thumb_path = storage.save_image(normalized.thumbnail, directory / f"thumb-{stamp}.jpg", 80)

            answer_crop_path = None
            answer_crops_json: list[dict] = []
            for region_index, region in enumerate(runtime.answer_regions):
                crop = crop_normalized(normalized.color, region)
                if crop.size <= 0:
                    continue
                label = str(region.get("label") or f"answer-{region_index + 1}")
                suffix = "answer" if region_index == 0 else f"answer-{region_index + 1}"
                crop_path = storage.save_image(
                    crop, directory / f"{suffix}-{stamp}.jpg", config.normalization.jpeg_quality
                )
                if answer_crop_path is None:
                    answer_crop_path = crop_path
                answer_crops_json.append(
                    {
                        "index": region_index,
                        "label": label,
                        "path": crop_path,
                        "region": dict(region),
                    }
                )
        except Exception as exc:
            logger.exception("storage failure")
            runtime.counters["errors"] += 1
            runtime.reset_candidates()
            return SheetOutcome(success=False, reason=f"storage_failed: {exc}")

        sequence = int(
            db.execute(
                select(func.count(ScannedSheet.id)).where(
                    ScannedSheet.session_id == session.id,
                    ScannedSheet.scan_status != ScanStatus.deleted.value,
                )
            ).scalar_one()
            or 0
        ) + 1

        processing_ms = int((time.perf_counter() - started) * 1000.0)

        sheet = ScannedSheet(
            session_id=session.id,
            student_id=student.id if student else None,
            task_id=(task.id if task else session.task_id),
            sheet_uid=sheet_uid,
            source_frame_path=source_path,
            normalized_image_path=normalized_path,
            enhanced_image_path=enhanced_path,
            answer_crop_path=answer_crop_path,
            answer_crops_json=answer_crops_json or None,
            thumbnail_path=thumb_path,
            qr_payload=payload.to_dict() if payload else None,
            qr_status=qr_status,
            scan_status=scan_status,
            quality_score=best.metrics.quality,
            sharpness_score=best.metrics.sharpness,
            glare_score=best.metrics.glare,
            occlusion_score=best.metrics.occlusion,
            perspective_score=best.metrics.perspective,
            coverage_score=best.metrics.coverage,
            motion_score=best.metrics.motion,
            duplicate_of_id=duplicate_of,
            warnings=warnings,
            sequence_number=sequence,
            processing_time_ms=processing_ms,
        )
        db.add(sheet)
        db.flush()

        log = ScanLog(
            session_id=session.id,
            sheet_id=sheet.id,
            events=list(runtime.current_events[-80:]),
            corners=best.quad.as_list(),
            candidate_scores=candidate_scores,
            selected_frame_index=best_index,
            qr_result=qr_status,
            processing_duration_ms=processing_ms,
            message="; ".join(warnings) if warnings else None,
        )
        db.add(log)
        db.commit()
        db.refresh(sheet)

        if sheet_uid:
            runtime.scanned_sheet_uids.add(sheet_uid.lower())

        runtime.current_events = []
        runtime.reset_candidates()
        runtime.scan_timestamps.append(time.monotonic())
        runtime.counters["scanned"] += 1
        if duplicate_of is not None:
            runtime.counters["duplicates"] += 1
        if scan_status == ScanStatus.unidentified.value:
            runtime.counters["unidentified"] += 1

        thumbnail_data = None
        try:
            from app.services.storage import encode_data_url

            thumbnail_data = encode_data_url(normalized.thumbnail, 70)
        except Exception:  # pragma: no cover
            pass

        outcome = SheetOutcome(
            success=duplicate_of is None and scan_status != ScanStatus.duplicate.value,
            sheet_id=sheet.id,
            reason="duplicate" if duplicate_of else ("unidentified" if not qr_result.success else ""),
            warnings=warnings,
            student_label=(student.display_name if student else (payload.student_id if payload else "не определён")),
            sheet_uid=sheet_uid or "",
            quality=best.metrics.quality,
            qr_status=qr_status,
            scan_status=scan_status,
            duplicate_of=duplicate_of,
            processing_ms=processing_ms,
            thumbnail=thumbnail_data,
        )
        runtime.last_outcome = outcome
        return outcome

    def _store_failed_log(
        self,
        db: Session,
        runtime: SessionRuntime,
        session: ScanSession,
        candidate_scores: list[dict],
        best_index: int,
        outcome: SheetOutcome,
    ) -> None:
        try:
            db.add(
                ScanLog(
                    session_id=session.id,
                    sheet_id=None,
                    events=list(runtime.current_events[-60:]),
                    candidate_scores=candidate_scores,
                    selected_frame_index=best_index,
                    qr_result="rejected",
                    processing_duration_ms=outcome.processing_ms,
                    message=outcome.reason,
                )
            )
            db.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("could not persist failure log: %s", exc)
            db.rollback()


scan_service = ScanService()
