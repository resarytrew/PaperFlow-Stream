"""SQLAlchemy ORM models for Чистовик."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- enums


class SessionStatus(str, enum.Enum):
    draft = "draft"
    scanning = "scanning"
    processing = "processing"
    review = "review"
    completed = "completed"


class QrStatus(str, enum.Enum):
    read = "read"
    unreadable = "unreadable"
    invalid = "invalid"
    manual = "manual"
    mismatch = "mismatch"


class ScanStatus(str, enum.Enum):
    ok = "ok"
    low_quality = "low_quality"
    duplicate = "duplicate"
    unidentified = "unidentified"
    rescan_required = "rescan_required"
    deleted = "deleted"


class RecognitionStatus(str, enum.Enum):
    pending = "pending"
    preprocessing = "preprocessing"
    processing = "processing"
    recognizing = "recognizing"
    recognized = "recognized"
    needs_review = "needs_review"
    blank = "blank"
    failed = "failed"


class ReviewDecisionType(str, enum.Enum):
    accepted = "accepted"
    corrected = "corrected"
    rescan_required = "rescan_required"
    unreadable = "unreadable"
    wrong_student = "wrong_student"
    duplicate = "duplicate"


# --------------------------------------------------------------------------- core


class ClassGroup(TimestampMixin, Base):
    __tablename__ = "class_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    school_year: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    students: Mapped[list["Student"]] = relationship(
        back_populates="class_group", cascade="all, delete-orphan", lazy="selectin"
    )
    sessions: Mapped[list["ScanSession"]] = relationship(back_populates="class_group")


class Student(TimestampMixin, Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("external_id", name="uq_students_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class_groups.id", ondelete="SET NULL"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    class_group: Mapped[ClassGroup | None] = relationship(back_populates="students")

    @property
    def display_name(self) -> str:
        full = f"{self.last_name} {self.first_name}".strip()
        return full or self.external_id


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer_region_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Grading support (section 5.1 / 5.8): the teacher records a mark here.
    max_score: Mapped[float | None] = mapped_column(Float, default=None)
    rubric: Mapped[list | None] = mapped_column(JSON, nullable=True)


class FormTemplate(TimestampMixin, Base):
    """Sheet layout: where the QR lives and where the answer regions are."""

    __tablename__ = "form_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    page_width_mm: Mapped[float] = mapped_column(Float, default=210.0)
    page_height_mm: Mapped[float] = mapped_column(Float, default=99.0)
    aspect_ratio: Mapped[float] = mapped_column(Float, default=210.0 / 99.0)
    # normalised (0..1) rectangles relative to the rectified sheet
    qr_region: Mapped[dict] = mapped_column(JSON, default=dict)
    answer_regions: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ScanSession(TimestampMixin, Base):
    __tablename__ = "scan_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class_groups.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("form_templates.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    expected_sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default=SessionStatus.draft.value, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    class_group: Mapped[ClassGroup | None] = relationship(back_populates="sessions", lazy="selectin")
    task: Mapped[Task | None] = relationship(lazy="selectin")
    template: Mapped[FormTemplate | None] = relationship(lazy="selectin")
    sheets: Mapped[list["ScannedSheet"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ScannedSheet.id"
    )


class ScannedSheet(TimestampMixin, Base):
    __tablename__ = "scanned_sheets"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("scan_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    sheet_uid: Mapped[str | None] = mapped_column(String(160), index=True)

    source_frame_path: Mapped[str | None] = mapped_column(String(512))
    normalized_image_path: Mapped[str | None] = mapped_column(String(512))
    enhanced_image_path: Mapped[str | None] = mapped_column(String(512))
    answer_crop_path: Mapped[str | None] = mapped_column(String(512))
    answer_crops_json: Mapped[list | None] = mapped_column(JSON, default=None)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))

    qr_payload: Mapped[dict | None] = mapped_column(JSON)
    qr_status: Mapped[str] = mapped_column(String(24), default=QrStatus.unreadable.value, index=True)
    scan_status: Mapped[str] = mapped_column(String(24), default=ScanStatus.ok.value, index=True)

    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    sharpness_score: Mapped[float] = mapped_column(Float, default=0.0)
    glare_score: Mapped[float] = mapped_column(Float, default=0.0)
    occlusion_score: Mapped[float] = mapped_column(Float, default=0.0)
    perspective_score: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0)
    motion_score: Mapped[float] = mapped_column(Float, default=0.0)

    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("scanned_sheets.id", ondelete="SET NULL"))
    warnings: Mapped[list | None] = mapped_column(JSON, default=list)
    sequence_number: Mapped[int] = mapped_column(Integer, default=0)
    processing_time_ms: Mapped[int] = mapped_column(Integer, default=0)

    session: Mapped[ScanSession] = relationship(back_populates="sheets")
    student: Mapped[Student | None] = relationship(lazy="selectin")
    task: Mapped[Task | None] = relationship(lazy="selectin")
    recognition: Mapped["RecognitionResult | None"] = relationship(
        back_populates="sheet", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    review: Mapped["ReviewDecision | None"] = relationship(
        back_populates="sheet", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    scan_log: Mapped["ScanLog | None"] = relationship(
        back_populates="sheet", cascade="all, delete-orphan", uselist=False
    )


class RecognitionResult(TimestampMixin, Base):
    __tablename__ = "recognition_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    scanned_sheet_id: Mapped[int] = mapped_column(
        ForeignKey("scanned_sheets.id", ondelete="CASCADE"), index=True, unique=True
    )
    recognized_text: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    line_results_json: Mapped[list | None] = mapped_column(JSON, default=list)
    warnings: Mapped[list | None] = mapped_column(JSON, default=list)
    analysis_json: Mapped[dict | None] = mapped_column(JSON, default=dict)
    preprocess_variant: Mapped[str] = mapped_column(String(64), default="")
    processing_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default=RecognitionStatus.pending.value, index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    sheet: Mapped[ScannedSheet] = relationship(back_populates="recognition")


class ReviewDecision(TimestampMixin, Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scanned_sheet_id: Mapped[int] = mapped_column(
        ForeignKey("scanned_sheets.id", ondelete="CASCADE"), index=True, unique=True
    )
    teacher_text: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(32), default=ReviewDecisionType.accepted.value, index=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    # Grading support (section 5.1 / 5.8).
    score: Mapped[float | None] = mapped_column(Float, default=None)
    rubric_result: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sheet: Mapped[ScannedSheet] = relationship(back_populates="review")


class ScanLog(TimestampMixin, Base):
    """Technical journal for one scan attempt (section 10)."""

    __tablename__ = "scan_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("scan_sessions.id", ondelete="CASCADE"), index=True)
    sheet_id: Mapped[int | None] = mapped_column(ForeignKey("scanned_sheets.id", ondelete="CASCADE"), index=True)
    events: Mapped[list | None] = mapped_column(JSON, default=list)
    corners: Mapped[list | None] = mapped_column(JSON)
    candidate_scores: Mapped[list | None] = mapped_column(JSON, default=list)
    selected_frame_index: Mapped[int] = mapped_column(Integer, default=-1)
    qr_result: Mapped[str | None] = mapped_column(String(255))
    processing_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)

    sheet: Mapped[ScannedSheet | None] = relationship(back_populates="scan_log")


class CameraProfile(TimestampMixin, Base):
    """Saved camera + calibration parameters."""

    __tablename__ = "camera_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="default", unique=True)
    device_id: Mapped[str] = mapped_column(String(255), default="")
    device_label: Mapped[str] = mapped_column(String(255), default="")
    width: Mapped[int] = mapped_column(Integer, default=1920)
    height: Mapped[int] = mapped_column(Integer, default=1080)
    work_area_polygon: Mapped[list | None] = mapped_column(JSON)
    qr_region: Mapped[dict | None] = mapped_column(JSON)
    answer_regions: Mapped[list | None] = mapped_column(JSON, default=list)
    background_reference_path: Mapped[str | None] = mapped_column(String(512))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("form_templates.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class AppSetting(TimestampMixin, Base):
    """Single-row runtime configuration override store."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class HardwareEvent(TimestampMixin, Base):
    """Camera / hardware problems shown on the dashboard."""

    __tablename__ = "hardware_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="warning")
    code: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict | None] = mapped_column(JSON, default=dict)


class SessionPreset(TimestampMixin, Base):
    """Reusable scan configuration (section 5.12): class + task + template + camera + settings.

    Lets a teacher start a familiar session (e.g. "9Б · history-09-04") in two clicks
    instead of walking through the calibration/settings wizard every lesson.
    """

    __tablename__ = "session_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class_groups.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("form_templates.id", ondelete="SET NULL"))
    camera_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("camera_profiles.id", ondelete="SET NULL")
    )
    expected_sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    config_override: Mapped[dict] = mapped_column(JSON, default=dict)


class ShareToken(TimestampMixin, Base):
    """Time-limited read-only link to one student's results (section 5.11).

    Generates a URL the teacher can hand to a pupil/parent. It exposes only that
    student's own sheets and never other pupils' data.
    """

    __tablename__ = "share_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(255), default="")
