"""Recognition queue, review screen and teacher corrections (0.2)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import Config, DbSession, get_session_or_404, get_sheet_or_404, serialize_sheet
from app.models import (
    QrStatus,
    RecognitionResult,
    RecognitionStatus,
    ReviewDecision,
    ScanStatus,
    ScannedSheet,
)
from app.ocr.providers import available_providers
from app.schemas import ReviewSubmit, ScannedSheetOut
from app.services.ocr_queue import ocr_queue

logger = logging.getLogger(__name__)
router = APIRouter(tags=["review"])


#: Review queue tabs (section 7.7)
QUEUE_TABS = [
    "all",
    "high_confidence",
    "needs_review",
    "low_confidence",
    "failed",
    "blank",
    "unidentified",
    "rescan",
]


def _apply_tab(query, tab: str, config):
    """Restrict a sheet query to one review tab."""
    if tab == "high_confidence":
        return query.where(
            RecognitionResult.status == RecognitionStatus.recognized.value,
            RecognitionResult.overall_confidence >= config.ocr.high_confidence,
        )
    if tab == "needs_review":
        return query.where(RecognitionResult.status == RecognitionStatus.needs_review.value)
    if tab == "low_confidence":
        return query.where(
            RecognitionResult.overall_confidence < config.ocr.low_confidence,
            RecognitionResult.status.in_(
                [RecognitionStatus.recognized.value, RecognitionStatus.needs_review.value]
            ),
        )
    if tab == "failed":
        return query.where(RecognitionResult.status == RecognitionStatus.failed.value)
    if tab == "blank":
        return query.where(RecognitionResult.status == RecognitionStatus.blank.value)
    if tab == "unidentified":
        return query.where(ScannedSheet.student_id.is_(None))
    if tab == "rescan":
        return query.where(ScannedSheet.scan_status == ScanStatus.rescan_required.value)
    return query


@router.get("/sessions/{session_id}/review", response_model=list[ScannedSheetOut])
def list_review_sheets(
    session_id: int,
    db: DbSession,
    config: Config,
    tab: str = Query("all"),
) -> list[ScannedSheetOut]:
    get_session_or_404(db, session_id)
    if tab not in QUEUE_TABS:
        raise HTTPException(status_code=400, detail=f"Неизвестная вкладка: {tab}")

    query = (
        select(ScannedSheet)
        .outerjoin(RecognitionResult, RecognitionResult.scanned_sheet_id == ScannedSheet.id)
        .where(
            ScannedSheet.session_id == session_id,
            ScannedSheet.scan_status != ScanStatus.deleted.value,
        )
    )
    query = _apply_tab(query, tab, config)
    sheets = db.execute(query.order_by(ScannedSheet.sequence_number, ScannedSheet.id)).scalars().unique().all()
    return [serialize_sheet(s) for s in sheets]


@router.get("/sessions/{session_id}/review/counts")
def review_counts(session_id: int, db: DbSession, config: Config) -> dict:
    """Per-tab counters shown on the review screen."""
    get_session_or_404(db, session_id)
    counts: dict[str, int] = {}
    for tab in QUEUE_TABS:
        query = (
            select(func.count(func.distinct(ScannedSheet.id)))
            .select_from(ScannedSheet)
            .outerjoin(RecognitionResult, RecognitionResult.scanned_sheet_id == ScannedSheet.id)
            .where(
                ScannedSheet.session_id == session_id,
                ScannedSheet.scan_status != ScanStatus.deleted.value,
            )
        )
        query = _apply_tab(query, tab, config)
        counts[tab] = int(db.execute(query).scalar_one() or 0)
    return counts


@router.post("/sheets/{sheet_id}/recognize", response_model=ScannedSheetOut)
def enqueue_recognition(sheet_id: int, db: DbSession) -> ScannedSheetOut:
    """(Re-)run OCR for a single sheet."""
    sheet = get_sheet_or_404(db, sheet_id)
    if not (sheet.answer_crop_path or sheet.normalized_image_path):
        raise HTTPException(status_code=400, detail="У листа нет изображения для распознавания")
    if not ocr_queue.enqueue(sheet_id):
        logger.info("sheet %s already queued or queue offline", sheet_id)
    db.refresh(sheet)
    return serialize_sheet(sheet)


@router.post("/sessions/{session_id}/recognize-all")
def enqueue_session_recognition(session_id: int, db: DbSession, only_missing: bool = True) -> dict:
    """Queue OCR for every eligible sheet of the session."""
    get_session_or_404(db, session_id)
    query = select(ScannedSheet).where(
        ScannedSheet.session_id == session_id,
        ScannedSheet.scan_status.notin_([ScanStatus.deleted.value, ScanStatus.duplicate.value]),
    )
    sheets = db.execute(query).scalars().all()

    queued = 0
    skipped = 0
    for sheet in sheets:
        if not (sheet.answer_crop_path or sheet.normalized_image_path):
            skipped += 1
            continue
        if only_missing and sheet.recognition is not None and sheet.recognition.status in (
            RecognitionStatus.recognized.value,
            RecognitionStatus.needs_review.value,
            RecognitionStatus.blank.value,
        ):
            skipped += 1
            continue
        if ocr_queue.enqueue(sheet.id):
            queued += 1
    return {"queued": queued, "skipped": skipped, "total": len(sheets)}


@router.post("/sheets/{sheet_id}/review", response_model=ScannedSheetOut)
def submit_review(sheet_id: int, payload: ReviewSubmit, db: DbSession) -> ScannedSheetOut:
    """Store the teacher's decision. Corrections are always preserved."""
    sheet = get_sheet_or_404(db, sheet_id)

    decision = db.execute(
        select(ReviewDecision).where(ReviewDecision.scanned_sheet_id == sheet_id)
    ).scalar_one_or_none()
    if decision is None:
        decision = ReviewDecision(scanned_sheet_id=sheet_id)
        db.add(decision)

    decision.teacher_text = payload.teacher_text
    decision.decision = payload.decision
    decision.comment = payload.comment
    decision.reviewed_at = datetime.now(timezone.utc)

    # Reflect the decision on the sheet / recognition state.
    if payload.decision == "rescan_required":
        sheet.scan_status = ScanStatus.rescan_required.value
    elif payload.decision == "duplicate":
        sheet.scan_status = ScanStatus.duplicate.value
    elif payload.decision == "wrong_student":
        sheet.student_id = None
        sheet.scan_status = ScanStatus.unidentified.value
        sheet.qr_status = QrStatus.mismatch.value
    elif payload.decision in ("accepted", "corrected") and sheet.scan_status == ScanStatus.low_quality.value:
        sheet.scan_status = ScanStatus.ok.value

    if sheet.recognition is not None:
        if payload.decision == "unreadable":
            sheet.recognition.status = RecognitionStatus.needs_review.value
        elif payload.decision in ("accepted", "corrected"):
            sheet.recognition.status = RecognitionStatus.recognized.value

    db.commit()
    db.refresh(sheet)
    return serialize_sheet(sheet)


@router.delete("/sheets/{sheet_id}/review", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(sheet_id: int, db: DbSession) -> None:
    get_sheet_or_404(db, sheet_id)
    decision = db.execute(
        select(ReviewDecision).where(ReviewDecision.scanned_sheet_id == sheet_id)
    ).scalar_one_or_none()
    if decision is not None:
        db.delete(decision)
        db.commit()


@router.post("/sheets/{sheet_id}/blank-override", response_model=ScannedSheetOut)
def override_blank(sheet_id: int, db: DbSession, is_blank: bool = True) -> ScannedSheetOut:
    """Teacher confirms or overturns the automatic blank-answer verdict."""
    sheet = get_sheet_or_404(db, sheet_id)
    result = sheet.recognition
    if result is None:
        raise HTTPException(status_code=400, detail="Для листа нет результата распознавания")

    if is_blank:
        result.status = RecognitionStatus.blank.value
        result.recognized_text = ""
    else:
        # Not blank after all – re-run the model.
        result.status = RecognitionStatus.pending.value
        db.commit()
        ocr_queue.enqueue(sheet_id)
        db.refresh(sheet)
        return serialize_sheet(sheet)

    db.commit()
    db.refresh(sheet)
    return serialize_sheet(sheet)


@router.get("/ocr/status")
def ocr_status(config: Config) -> dict:
    """Queue health + provider availability for the settings page."""
    return {
        "queue": ocr_queue.snapshot(),
        "providers": available_providers(),
        "active": config.ocr.provider,
        "thresholds": {
            "high": config.ocr.high_confidence,
            "low": config.ocr.low_confidence,
            "criticalToken": config.ocr.critical_token_confidence,
        },
        "concurrency": config.ocr.concurrency,
    }
