"""Shared FastAPI dependencies and serialisation helpers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import RuntimeConfig
from app.db import get_db
from app.models import (
    ClassGroup,
    RecognitionResult,
    RecognitionStatus,
    ScanSession,
    ScanStatus,
    ScannedSheet,
    Student,
    Task,
)
from app.schemas import (
    ClassGroupOut,
    RecognitionOut,
    ReviewDecisionOut,
    ScanSessionOut,
    ScannedSheetOut,
    SessionStats,
    StudentOut,
)
from app.services.settings_service import load_config

DbSession = Annotated[Session, Depends(get_db)]


def get_config(db: DbSession) -> RuntimeConfig:
    return load_config(db)


Config = Annotated[RuntimeConfig, Depends(get_config)]


def get_session_or_404(db: Session, session_id: int) -> ScanSession:
    session = db.get(ScanSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сессия не найдена")
    return session


def get_sheet_or_404(db: Session, sheet_id: int) -> ScannedSheet:
    sheet = db.get(ScannedSheet, sheet_id)
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Лист не найден")
    return sheet


# --------------------------------------------------------------- serialisers


def serialize_class(group: ClassGroup, student_count: int | None = None) -> ClassGroupOut:
    data = ClassGroupOut.model_validate(group)
    data.student_count = student_count if student_count is not None else len(group.students)
    return data


def serialize_student(student: Student) -> StudentOut:
    data = StudentOut.model_validate(student)
    data.display_name = student.display_name
    data.class_name = student.class_group.name if student.class_group else None
    return data


def session_stats(db: Session, session: ScanSession) -> SessionStats:
    """Aggregate counters for one session."""
    rows = db.execute(
        select(ScannedSheet.scan_status, func.count(ScannedSheet.id), func.avg(ScannedSheet.quality_score))
        .where(ScannedSheet.session_id == session.id, ScannedSheet.scan_status != ScanStatus.deleted.value)
        .group_by(ScannedSheet.scan_status)
    ).all()

    stats = SessionStats()
    weighted_sum = 0.0
    for scan_status, count, avg_quality in rows:
        stats.total += count
        weighted_sum += (avg_quality or 0.0) * count
        if scan_status == ScanStatus.ok.value:
            stats.ok += count
        elif scan_status == ScanStatus.duplicate.value:
            stats.duplicates += count
        elif scan_status == ScanStatus.unidentified.value:
            stats.unidentified += count
        elif scan_status == ScanStatus.low_quality.value:
            stats.low_quality += count
        elif scan_status == ScanStatus.rescan_required.value:
            stats.rescan_required += count
    if stats.total:
        stats.average_quality = round(weighted_sum / stats.total, 4)

    ocr_rows = db.execute(
        select(RecognitionResult.status, func.count(RecognitionResult.id))
        .join(ScannedSheet, ScannedSheet.id == RecognitionResult.scanned_sheet_id)
        .where(ScannedSheet.session_id == session.id)
        .group_by(RecognitionResult.status)
    ).all()
    for ocr_status, count in ocr_rows:
        if ocr_status == RecognitionStatus.recognized.value:
            stats.recognized += count
        elif ocr_status == RecognitionStatus.needs_review.value:
            stats.needs_review += count
        elif ocr_status == RecognitionStatus.blank.value:
            stats.blank += count
        elif ocr_status == RecognitionStatus.failed.value:
            stats.failed_ocr += count
        elif ocr_status in (
            RecognitionStatus.pending.value,
            RecognitionStatus.processing.value,
            RecognitionStatus.preprocessing.value,
            RecognitionStatus.recognizing.value,
        ):
            stats.pending_ocr += count

    # Scanning speed from the actual sheet timestamps.
    if stats.total > 1:
        bounds = db.execute(
            select(func.min(ScannedSheet.created_at), func.max(ScannedSheet.created_at)).where(
                ScannedSheet.session_id == session.id
            )
        ).one()
        if bounds[0] and bounds[1]:
            span_minutes = (bounds[1] - bounds[0]).total_seconds() / 60.0
            if span_minutes > 1e-6:
                stats.sheets_per_minute = round((stats.total - 1) / span_minutes, 2)
    return stats


def serialize_session(db: Session, session: ScanSession, with_stats: bool = True) -> ScanSessionOut:
    data = ScanSessionOut.model_validate(session)
    data.class_name = session.class_group.name if session.class_group else None
    if session.task:
        data.task_title = session.task.title
        data.task_external_id = session.task.external_id
    if with_stats:
        data.stats = session_stats(db, session)
    return data


def serialize_sheet(sheet: ScannedSheet) -> ScannedSheetOut:
    data = ScannedSheetOut.model_validate(sheet)
    if sheet.student:
        data.student_name = sheet.student.display_name
        data.student_external_id = sheet.student.external_id
        if sheet.student.class_group:
            data.class_name = sheet.student.class_group.name
    if sheet.task:
        data.task_title = sheet.task.title
    if sheet.recognition:
        data.recognition = RecognitionOut.model_validate(sheet.recognition)
    if sheet.review:
        data.review = ReviewDecisionOut.model_validate(sheet.review)
    return data


def resolve_task(db: Session, task_id: int | None) -> Task | None:
    return db.get(Task, task_id) if task_id else None
