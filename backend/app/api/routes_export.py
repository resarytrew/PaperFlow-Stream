"""Export endpoints, PDF form generation and the dashboard."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select

from app.api.deps import DbSession, get_session_or_404, serialize_session
from app.models import (
    ClassGroup,
    HardwareEvent,
    RecognitionResult,
    RecognitionStatus,
    ScanSession,
    ScanStatus,
    ScannedSheet,
    Student,
    Task,
)
from app.schemas import DashboardOut, FormGenerationRequest, HardwareEventOut
from app.services import export_service
from app.services.form_generator import FormSpec, build_sheet_uid, generate_forms_pdf
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["export"])


def _filters(only_problems: bool, only_uncertain: bool, only_corrected: bool) -> dict:
    return {
        "only_problems": only_problems,
        "only_uncertain": only_uncertain,
        "only_corrected": only_corrected,
    }


def _stamp(session: ScanSession) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session.title or "session"))[:40]
    return f"{session.id:04d}_{safe}_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"


def _content_disposition(filename: str) -> str:
    """RFC 5987 header value that survives non-ASCII (Cyrillic) file names."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


@router.get("/sessions/{session_id}/export/csv")
def export_csv(
    session_id: int,
    db: DbSession,
    only_problems: bool = False,
    only_uncertain: bool = False,
    only_corrected: bool = False,
) -> Response:
    session = get_session_or_404(db, session_id)
    data = export_service.export_csv(db, session_id, **_filters(only_problems, only_uncertain, only_corrected))
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(f"paperflow_{_stamp(session)}.csv")},
    )


@router.get("/sessions/{session_id}/export/json")
def export_json(
    session_id: int,
    db: DbSession,
    only_problems: bool = False,
    only_uncertain: bool = False,
    only_corrected: bool = False,
) -> Response:
    session = get_session_or_404(db, session_id)
    data = export_service.export_json(db, session_id, **_filters(only_problems, only_uncertain, only_corrected))
    return Response(
        content=data,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(f"paperflow_{_stamp(session)}.json")},
    )


@router.get("/sessions/{session_id}/export/xlsx")
def export_xlsx(
    session_id: int,
    db: DbSession,
    only_problems: bool = False,
    only_uncertain: bool = False,
    only_corrected: bool = False,
) -> Response:
    session = get_session_or_404(db, session_id)
    try:
        data = export_service.export_xlsx(db, session_id, **_filters(only_problems, only_uncertain, only_corrected))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось создать XLSX: {exc}") from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(f"paperflow_{_stamp(session)}.xlsx")},
    )


@router.get("/sessions/{session_id}/export/zip")
def export_zip(
    session_id: int,
    db: DbSession,
    include_source: bool = False,
    include_enhanced: bool = True,
    include_crops: bool = True,
    thumbnails_only: bool = False,
    only_problems: bool = False,
    only_uncertain: bool = False,
    only_corrected: bool = False,
) -> Response:
    session = get_session_or_404(db, session_id)
    data = export_service.export_zip(
        db,
        session_id,
        include_source=include_source,
        include_enhanced=include_enhanced,
        include_crops=include_crops,
        thumbnails_only=thumbnails_only,
        **_filters(only_problems, only_uncertain, only_corrected),
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"paperflow_{_stamp(session)}.zip")},
    )


# ------------------------------------------------------------- form generator


@router.post("/forms/generate")
def generate_forms(payload: FormGenerationRequest, db: DbSession) -> Response:
    """Produce a printable A4 PDF with QR-coded answer forms."""
    class_group = db.get(ClassGroup, payload.class_id)
    if class_group is None:
        raise HTTPException(status_code=400, detail="Класс не найден")
    task = db.get(Task, payload.task_id)
    if task is None:
        raise HTTPException(status_code=400, detail="Задание не найдено")

    query = select(Student).where(Student.class_id == payload.class_id, Student.is_active.is_(True))
    if payload.student_ids:
        query = select(Student).where(Student.id.in_(payload.student_ids))
    students = db.execute(query.order_by(Student.last_name, Student.first_name, Student.external_id)).scalars().all()
    if not students:
        raise HTTPException(status_code=400, detail="В классе нет активных учеников")

    specs: list[FormSpec] = []
    for student in students:
        for index in range(1, payload.sheets_per_student + 1):
            specs.append(
                FormSpec(
                    student_external_id=student.external_id,
                    student_name=student.display_name,
                    class_name=class_group.name,
                    task_external_id=task.external_id,
                    task_title=payload.title_override or task.title,
                    sheet_uid=build_sheet_uid(
                        student.external_id, task.external_id, index, payload.sheets_per_student
                    ),
                    sheet_index=index,
                    sheet_total=payload.sheets_per_student,
                )
            )

    try:
        pdf = generate_forms_pdf(
            specs,
            forms_per_page=payload.forms_per_page,
            include_cut_lines=payload.include_cut_lines,
            payload_format=payload.payload_format,
            document_title=f"{class_group.name} — {task.title}",
        )
    except Exception as exc:
        logger.exception("form generation failed")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации бланков: {exc}") from exc

    filename = f"forms_{class_group.name}_{task.external_id}_{len(specs)}.pdf"
    headers = {"X-Form-Count": str(len(specs)), "Content-Disposition": _content_disposition(filename)}
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers=headers,
    )


@router.get("/forms/preview")
def preview_form(db: DbSession, class_id: int, task_id: int, count: int = Query(3, ge=1, le=6)) -> Response:
    """Small PDF preview used by the templates screen."""
    class_group = db.get(ClassGroup, class_id)
    task = db.get(Task, task_id)
    if class_group is None or task is None:
        raise HTTPException(status_code=400, detail="Класс или задание не найдены")

    students = db.execute(
        select(Student).where(Student.class_id == class_id).order_by(Student.external_id).limit(count)
    ).scalars().all()
    if not students:
        students = []

    specs = [
        FormSpec(
            student_external_id=s.external_id,
            student_name=s.display_name,
            class_name=class_group.name,
            task_external_id=task.external_id,
            task_title=task.title,
            sheet_uid=build_sheet_uid(s.external_id, task.external_id),
        )
        for s in students
    ] or [
        FormSpec("DEMO-01", "Пример Ученика", class_group.name, task.external_id, task.title, "DEMO-01-preview")
    ]

    pdf = generate_forms_pdf(specs, forms_per_page=min(count, 3))
    return Response(content=pdf, media_type="application/pdf")


# ------------------------------------------------------------------ dashboard


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: DbSession) -> DashboardOut:
    """Home screen summary (section 8)."""
    last_session = db.execute(select(ScanSession).order_by(ScanSession.created_at.desc()).limit(1)).scalar_one_or_none()

    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sheets_today = int(
        db.execute(
            select(func.count(ScannedSheet.id)).where(
                ScannedSheet.created_at >= midnight,
                ScannedSheet.scan_status != ScanStatus.deleted.value,
            )
        ).scalar_one()
        or 0
    )

    needs_review = int(
        db.execute(
            select(func.count(RecognitionResult.id)).where(
                RecognitionResult.status.in_(
                    [RecognitionStatus.needs_review.value, RecognitionStatus.failed.value]
                )
            )
        ).scalar_one()
        or 0
    )
    needs_review += int(
        db.execute(
            select(func.count(ScannedSheet.id)).where(
                ScannedSheet.student_id.is_(None),
                ScannedSheet.scan_status != ScanStatus.deleted.value,
            )
        ).scalar_one()
        or 0
    )

    # Average speed across today's sessions.
    average_speed = 0.0
    today_sessions = db.execute(
        select(ScanSession).where(ScanSession.created_at >= midnight - timedelta(days=1))
    ).scalars().all()
    speeds: list[float] = []
    for session in today_sessions:
        bounds = db.execute(
            select(
                func.min(ScannedSheet.created_at),
                func.max(ScannedSheet.created_at),
                func.count(ScannedSheet.id),
            ).where(ScannedSheet.session_id == session.id)
        ).one()
        if bounds[2] and bounds[2] > 1 and bounds[0] and bounds[1]:
            minutes = (bounds[1] - bounds[0]).total_seconds() / 60.0
            if minutes > 1e-6:
                speeds.append((bounds[2] - 1) / minutes)
    if speeds:
        average_speed = round(sum(speeds) / len(speeds), 2)

    events = db.execute(select(HardwareEvent).order_by(HardwareEvent.id.desc()).limit(5)).scalars().all()

    storage_bytes = 0
    try:
        storage_bytes = get_storage().disk_usage_bytes()
    except Exception:  # pragma: no cover
        pass

    return DashboardOut(
        last_session=serialize_session(db, last_session) if last_session else None,
        sheets_today=sheets_today,
        needs_review=needs_review,
        average_speed=average_speed,
        hardware_events=[HardwareEventOut.model_validate(e) for e in events],
        total_sessions=int(db.execute(select(func.count(ScanSession.id))).scalar_one() or 0),
        total_sheets=int(
            db.execute(
                select(func.count(ScannedSheet.id)).where(ScannedSheet.scan_status != ScanStatus.deleted.value)
            ).scalar_one()
            or 0
        ),
        storage_bytes=storage_bytes,
    )


@router.post("/maintenance/retention")
def apply_retention(db: DbSession) -> dict:
    """Delete stored images older than the configured retention period."""
    from app.services.settings_service import load_config

    config = load_config(db)
    days = config.privacy.file_retention_days
    removed = get_storage().apply_retention(days)
    return {"retentionDays": days, "filesRemoved": removed}
