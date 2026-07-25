"""Scan session lifecycle and sheet archive endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    Config,
    DbSession,
    get_session_or_404,
    get_sheet_or_404,
    serialize_session,
    serialize_sheet,
    session_stats,
)
from app.models import (
    ClassGroup,
    FormTemplate,
    QrStatus,
    ScanSession,
    ScanStatus,
    ScannedSheet,
    SessionStatus,
    Student,
    Task,
)
from app.schemas import (
    ScanSessionCreate,
    ScanSessionOut,
    ScanSessionUpdate,
    ScannedSheetOut,
    SheetAssign,
    SheetStatusUpdate,
)
from app.services.scan_service import scan_service
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sessions"])

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    SessionStatus.draft.value: {SessionStatus.scanning.value, SessionStatus.completed.value},
    SessionStatus.scanning.value: {
        SessionStatus.processing.value,
        SessionStatus.review.value,
        SessionStatus.completed.value,
        SessionStatus.draft.value,
    },
    SessionStatus.processing.value: {
        SessionStatus.scanning.value,
        SessionStatus.review.value,
        SessionStatus.completed.value,
    },
    SessionStatus.review.value: {SessionStatus.scanning.value, SessionStatus.completed.value},
    SessionStatus.completed.value: {SessionStatus.review.value, SessionStatus.scanning.value},
}


@router.get("/sessions", response_model=list[ScanSessionOut])
def list_sessions(db: DbSession, status_filter: str | None = Query(None, alias="status"), limit: int = 100) -> list[ScanSessionOut]:
    query = select(ScanSession).order_by(ScanSession.created_at.desc()).limit(min(limit, 500))
    if status_filter:
        query = query.where(ScanSession.status == status_filter)
    sessions = db.execute(query).scalars().all()
    return [serialize_session(db, s) for s in sessions]


@router.post("/sessions", response_model=ScanSessionOut, status_code=status.HTTP_201_CREATED)
def create_session(payload: ScanSessionCreate, db: DbSession) -> ScanSessionOut:
    class_group = db.get(ClassGroup, payload.class_id) if payload.class_id else None
    if payload.class_id and class_group is None:
        raise HTTPException(status_code=400, detail="Класс не найден")
    task = db.get(Task, payload.task_id) if payload.task_id else None
    if payload.task_id and task is None:
        raise HTTPException(status_code=400, detail="Задание не найдено")

    template_id = payload.template_id
    if template_id is None:
        default_template = db.execute(
            select(FormTemplate).where(FormTemplate.is_default.is_(True))
        ).scalar_one_or_none()
        template_id = default_template.id if default_template else None

    title = payload.title.strip()
    if not title:
        parts = [p for p in [class_group.name if class_group else None, task.topic or task.title if task else None] if p]
        title = " / ".join(parts) or "Новая сессия"

    session = ScanSession(
        class_id=payload.class_id,
        task_id=payload.task_id,
        template_id=template_id,
        title=title,
        expected_sheet_count=payload.expected_sheet_count,
        status=SessionStatus.draft.value,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return serialize_session(db, session)


@router.get("/sessions/{session_id}", response_model=ScanSessionOut)
def get_session(session_id: int, db: DbSession) -> ScanSessionOut:
    return serialize_session(db, get_session_or_404(db, session_id))


@router.patch("/sessions/{session_id}", response_model=ScanSessionOut)
def update_session(session_id: int, payload: ScanSessionUpdate, db: DbSession) -> ScanSessionOut:
    session = get_session_or_404(db, session_id)
    data = payload.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)
    for field, value in data.items():
        setattr(session, field, value)
    if new_status is not None and new_status != session.status:
        allowed = _ALLOWED_TRANSITIONS.get(session.status, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Недопустимый переход статуса: {session.status} → {new_status}",
            )
        session.status = new_status
    db.commit()
    db.refresh(session)
    return serialize_session(db, session)


def _set_status(db: Session, session: ScanSession, target: str) -> ScanSession:
    if target == session.status:
        return session
    allowed = _ALLOWED_TRANSITIONS.get(session.status, set())
    if target not in allowed:
        raise HTTPException(status_code=400, detail=f"Недопустимый переход: {session.status} → {target}")
    session.status = target
    if target == SessionStatus.scanning.value and session.started_at is None:
        session.started_at = datetime.now(timezone.utc)
    if target == SessionStatus.completed.value:
        session.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


@router.post("/sessions/{session_id}/start", response_model=ScanSessionOut)
def start_session(session_id: int, db: DbSession, config: Config) -> ScanSessionOut:
    session = get_session_or_404(db, session_id)
    session = _set_status(db, session, SessionStatus.scanning.value)
    runtime = scan_service.ensure_runtime(session.id, config)
    runtime.machine.resume()
    return serialize_session(db, session)


@router.post("/sessions/{session_id}/pause", response_model=ScanSessionOut)
def pause_session(session_id: int, db: DbSession) -> ScanSessionOut:
    session = get_session_or_404(db, session_id)
    runtime = scan_service.get_runtime(session_id)
    if runtime:
        runtime.machine.pause()
    return serialize_session(db, session)


@router.post("/sessions/{session_id}/resume", response_model=ScanSessionOut)
def resume_session(session_id: int, db: DbSession, config: Config) -> ScanSessionOut:
    session = get_session_or_404(db, session_id)
    if session.status != SessionStatus.scanning.value:
        session = _set_status(db, session, SessionStatus.scanning.value)
    runtime = scan_service.ensure_runtime(session_id, config)
    runtime.machine.resume()
    return serialize_session(db, session)


@router.post("/sessions/{session_id}/complete", response_model=ScanSessionOut)
def complete_session(session_id: int, db: DbSession) -> ScanSessionOut:
    session = get_session_or_404(db, session_id)
    stats = session_stats(db, session)
    target = SessionStatus.review.value if stats.pending_ocr or stats.needs_review else SessionStatus.completed.value
    session = _set_status(db, session, target)
    if target == SessionStatus.review.value:
        session = _set_status(db, session, SessionStatus.completed.value) if False else session
    scan_service.drop_runtime(session_id)
    return serialize_session(db, session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: int, db: DbSession) -> None:
    """Full deletion including all stored images (privacy requirement)."""
    session = get_session_or_404(db, session_id)
    scan_service.drop_runtime(session_id)
    get_storage().delete_session(session_id)
    db.delete(session)
    db.commit()


# -------------------------------------------------------------------- sheets

_FILTERS = {
    "all": None,
    "ok": ScanStatus.ok.value,
    "no_qr": "no_qr",
    "duplicates": ScanStatus.duplicate.value,
    "low_quality": ScanStatus.low_quality.value,
    "rescan": ScanStatus.rescan_required.value,
}


@router.get("/sessions/{session_id}/sheets", response_model=list[ScannedSheetOut])
def list_sheets(
    session_id: int,
    db: DbSession,
    filter: str = Query("all"),
    include_deleted: bool = False,
) -> list[ScannedSheetOut]:
    get_session_or_404(db, session_id)
    query = select(ScannedSheet).where(ScannedSheet.session_id == session_id)
    if not include_deleted:
        query = query.where(ScannedSheet.scan_status != ScanStatus.deleted.value)

    if filter == "no_qr":
        query = query.where(ScannedSheet.qr_status != QrStatus.read.value)
    elif filter in _FILTERS and _FILTERS[filter]:
        query = query.where(ScannedSheet.scan_status == _FILTERS[filter])
    elif filter == "unassigned":
        query = query.where(ScannedSheet.student_id.is_(None))

    sheets = db.execute(query.order_by(ScannedSheet.sequence_number, ScannedSheet.id)).scalars().all()
    return [serialize_sheet(s) for s in sheets]


@router.get("/sheets/{sheet_id}", response_model=ScannedSheetOut)
def get_sheet(sheet_id: int, db: DbSession) -> ScannedSheetOut:
    return serialize_sheet(get_sheet_or_404(db, sheet_id))


@router.patch("/sheets/{sheet_id}/assign", response_model=ScannedSheetOut)
def assign_sheet(sheet_id: int, payload: SheetAssign, db: DbSession) -> ScannedSheetOut:
    """Manually link an unidentified sheet to a student / task."""
    sheet = get_sheet_or_404(db, sheet_id)

    if payload.student_id is not None:
        student = db.get(Student, payload.student_id)
        if student is None:
            raise HTTPException(status_code=400, detail="Ученик не найден")
        sheet.student_id = student.id
        sheet.qr_status = QrStatus.manual.value
        if sheet.scan_status == ScanStatus.unidentified.value:
            sheet.scan_status = ScanStatus.ok.value

    if payload.task_id is not None:
        task = db.get(Task, payload.task_id)
        if task is None:
            raise HTTPException(status_code=400, detail="Задание не найдено")
        sheet.task_id = task.id

    if payload.sheet_uid is not None:
        new_uid = payload.sheet_uid.strip()
        if new_uid:
            clash = db.execute(
                select(ScannedSheet).where(
                    ScannedSheet.sheet_uid == new_uid,
                    ScannedSheet.id != sheet.id,
                    ScannedSheet.scan_status != ScanStatus.deleted.value,
                )
            ).scalars().first()
            if clash is not None:
                raise HTTPException(status_code=409, detail=f"sheetId «{new_uid}» уже используется")
            sheet.sheet_uid = new_uid

    if payload.clear_duplicate:
        sheet.duplicate_of_id = None
        if sheet.scan_status == ScanStatus.duplicate.value:
            sheet.scan_status = ScanStatus.ok.value

    db.commit()
    db.refresh(sheet)
    return serialize_sheet(sheet)


@router.patch("/sheets/{sheet_id}/status", response_model=ScannedSheetOut)
def update_sheet_status(sheet_id: int, payload: SheetStatusUpdate, db: DbSession) -> ScannedSheetOut:
    sheet = get_sheet_or_404(db, sheet_id)
    sheet.scan_status = payload.scan_status
    db.commit()
    db.refresh(sheet)
    return serialize_sheet(sheet)


@router.post("/sheets/{sheet_id}/reprocess", response_model=ScannedSheetOut)
def reprocess_sheet(sheet_id: int, db: DbSession, config: Config) -> ScannedSheetOut:
    """Re-run QR reading (and re-link the student) on a stored sheet."""
    from app.cv.geometry import crop_normalized
    from app.cv.qr import read_qr

    sheet = get_sheet_or_404(db, sheet_id)
    source = sheet.normalized_image_path or sheet.source_frame_path
    if not source:
        raise HTTPException(status_code=400, detail="У листа нет сохранённого изображения")

    storage = get_storage()
    try:
        image = storage.load(source)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Не удалось открыть изображение: {exc}") from exc

    runtime = scan_service.get_runtime(sheet.session_id)
    qr_region = runtime.qr_region if runtime else {"x": 0.01, "y": 0.02, "w": 0.28, "h": 0.38}

    result = read_qr(crop_normalized(image, qr_region), enhance=True)
    if not result.success:
        result = read_qr(image, enhance=True)

    warnings = list(sheet.warnings or [])
    if result.success and result.payload is not None:
        payload = result.payload
        sheet.qr_payload = payload.to_dict()
        sheet.qr_status = QrStatus.read.value
        sheet.sheet_uid = payload.sheet_id
        student = db.execute(
            select(Student).where(func.lower(Student.external_id) == payload.student_id.lower())
        ).scalar_one_or_none()
        if student is not None:
            sheet.student_id = student.id
            if sheet.scan_status == ScanStatus.unidentified.value:
                sheet.scan_status = ScanStatus.ok.value
        else:
            warnings.append(f"Ученик {payload.student_id} не найден")
        duplicate = db.execute(
            select(ScannedSheet).where(
                ScannedSheet.sheet_uid == payload.sheet_id,
                ScannedSheet.id != sheet.id,
                ScannedSheet.scan_status != ScanStatus.deleted.value,
            ).order_by(ScannedSheet.id)
        ).scalars().first()
        if duplicate is not None:
            sheet.duplicate_of_id = duplicate.id
            sheet.scan_status = ScanStatus.duplicate.value
    else:
        warnings.append(f"Повторное чтение QR не удалось: {result.error or 'not_found'}")

    sheet.warnings = warnings[-12:]
    db.commit()
    db.refresh(sheet)
    return serialize_sheet(sheet)


@router.delete("/sheets/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sheet(sheet_id: int, db: DbSession, purge: bool = False) -> None:
    sheet = get_sheet_or_404(db, sheet_id)
    if purge:
        get_storage().delete_paths(
            [
                sheet.source_frame_path,
                sheet.normalized_image_path,
                sheet.enhanced_image_path,
                sheet.answer_crop_path,
                sheet.thumbnail_path,
            ]
        )
        db.delete(sheet)
    else:
        sheet.scan_status = ScanStatus.deleted.value
    db.commit()


@router.get("/sheets/{sheet_id}/image/{kind}")
def get_sheet_image(sheet_id: int, kind: str, db: DbSession) -> FileResponse:
    """Serve a stored image (original / normalized / enhanced / crop / thumb)."""
    sheet = get_sheet_or_404(db, sheet_id)
    mapping = {
        "source": sheet.source_frame_path,
        "normalized": sheet.normalized_image_path,
        "enhanced": sheet.enhanced_image_path,
        "answer": sheet.answer_crop_path,
        "thumbnail": sheet.thumbnail_path,
    }
    if kind not in mapping:
        raise HTTPException(status_code=400, detail=f"Неизвестный тип изображения: {kind}")
    relative = mapping[kind]
    if not relative:
        raise HTTPException(status_code=404, detail="Изображение отсутствует")
    try:
        path = get_storage().absolute(relative)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=3600"})
