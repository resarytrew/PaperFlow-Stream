"""Recovery of OCR jobs interrupted by an application restart."""

from __future__ import annotations

import logging

from sqlalchemy import select

import app.db as app_db
from app.models import RecognitionResult, RecognitionStatus, ScannedSheet, ScanStatus
from app.services.ocr_queue import ocr_queue

logger = logging.getLogger(__name__)

_RECOVERABLE_STATUSES = (
    RecognitionStatus.pending.value,
    RecognitionStatus.preprocessing.value,
    RecognitionStatus.recognizing.value,
)


def recover_interrupted_ocr_jobs() -> int:
    """Requeue persisted unfinished OCR jobs after the worker pool starts.

    Jobs are selected from the database rather than process memory, so a crash,
    reboot or forced shutdown cannot leave them permanently stuck. Deleted
    sheets and rows without a usable image are ignored.
    """
    with app_db.SessionLocal() as db:
        sheet_ids = db.execute(
            select(RecognitionResult.scanned_sheet_id)
            .join(ScannedSheet, ScannedSheet.id == RecognitionResult.scanned_sheet_id)
            .where(
                RecognitionResult.status.in_(_RECOVERABLE_STATUSES),
                ScannedSheet.scan_status != ScanStatus.deleted.value,
                (
                    ScannedSheet.answer_crop_path.is_not(None)
                    | ScannedSheet.normalized_image_path.is_not(None)
                ),
            )
            .order_by(RecognitionResult.id)
        ).scalars().all()

    recovered = sum(1 for sheet_id in sheet_ids if ocr_queue.enqueue(int(sheet_id)))
    if recovered:
        logger.info("requeued %s interrupted OCR job(s)", recovered)
    return recovered
