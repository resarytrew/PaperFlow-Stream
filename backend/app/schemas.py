"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- classes


class ClassGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    school_year: str = Field(default="", max_length=32)


class ClassGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    school_year: str | None = None


class ClassGroupOut(ORMBase):
    id: int
    name: str
    school_year: str
    created_at: datetime
    updated_at: datetime
    student_count: int = 0


# ------------------------------------------------------------------ students


class StudentCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=64)
    first_name: str = Field(default="", max_length=96)
    last_name: str = Field(default="", max_length=96)
    class_id: int | None = None
    is_active: bool = True


class StudentUpdate(BaseModel):
    external_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    class_id: int | None = None
    is_active: bool | None = None


class StudentOut(ORMBase):
    id: int
    external_id: str
    first_name: str
    last_name: str
    class_id: int | None
    is_active: bool
    display_name: str
    class_name: str | None = None
    created_at: datetime
    updated_at: datetime


class StudentBulkCreate(BaseModel):
    class_id: int
    students: list[StudentCreate]


# --------------------------------------------------------------------- tasks


class TaskCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=1, max_length=255)
    subject: str = ""
    topic: str = ""
    description: str = ""
    expected_answer: str = ""
    answer_region_config: dict | None = None


class TaskUpdate(BaseModel):
    external_id: str | None = None
    title: str | None = None
    subject: str | None = None
    topic: str | None = None
    description: str | None = None
    expected_answer: str | None = None
    answer_region_config: dict | None = None


class TaskOut(ORMBase):
    id: int
    external_id: str
    title: str
    subject: str
    topic: str
    description: str
    expected_answer: str
    answer_region_config: dict | None
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------- templates


class RegionModel(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)
    label: str = ""


class FormTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    page_width_mm: float = 210.0
    page_height_mm: float = 99.0
    aspect_ratio: float | None = None
    qr_region: RegionModel | None = None
    answer_regions: list[RegionModel] = []
    is_default: bool = False


class FormTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    page_width_mm: float | None = None
    page_height_mm: float | None = None
    aspect_ratio: float | None = None
    qr_region: RegionModel | None = None
    answer_regions: list[RegionModel] | None = None
    is_default: bool | None = None


class FormTemplateOut(ORMBase):
    id: int
    name: str
    description: str
    page_width_mm: float
    page_height_mm: float
    aspect_ratio: float
    qr_region: dict
    answer_regions: list
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ sessions


class ScanSessionCreate(BaseModel):
    class_id: int | None = None
    task_id: int | None = None
    template_id: int | None = None
    title: str = ""
    expected_sheet_count: int = Field(default=0, ge=0, le=2000)


class ScanSessionUpdate(BaseModel):
    title: str | None = None
    expected_sheet_count: int | None = None
    status: str | None = None
    class_id: int | None = None
    task_id: int | None = None
    template_id: int | None = None


class SessionStats(BaseModel):
    total: int = 0
    ok: int = 0
    duplicates: int = 0
    unidentified: int = 0
    low_quality: int = 0
    rescan_required: int = 0
    recognized: int = 0
    needs_review: int = 0
    blank: int = 0
    failed_ocr: int = 0
    pending_ocr: int = 0
    average_quality: float = 0.0
    sheets_per_minute: float = 0.0


class ScanSessionOut(ORMBase):
    id: int
    class_id: int | None
    task_id: int | None
    template_id: int | None
    title: str
    expected_sheet_count: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    class_name: str | None = None
    task_title: str | None = None
    task_external_id: str | None = None
    stats: SessionStats = SessionStats()


# -------------------------------------------------------------------- sheets


class RecognitionOut(ORMBase):
    id: int
    recognized_text: str
    provider: str
    model_name: str
    overall_confidence: float
    line_results_json: list | None
    warnings: list | None
    analysis_json: dict | None
    preprocess_variant: str
    processing_time_ms: int
    status: str
    error_message: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime


class ReviewDecisionOut(ORMBase):
    id: int
    teacher_text: str
    decision: str
    comment: str
    reviewed_at: datetime


class ScannedSheetOut(ORMBase):
    id: int
    session_id: int
    student_id: int | None
    task_id: int | None
    sheet_uid: str | None
    source_frame_path: str | None
    normalized_image_path: str | None
    enhanced_image_path: str | None
    answer_crop_path: str | None
    answer_crops_json: list | None = None
    thumbnail_path: str | None
    qr_payload: dict | None
    qr_status: str
    scan_status: str
    quality_score: float
    sharpness_score: float
    glare_score: float
    occlusion_score: float
    perspective_score: float
    coverage_score: float
    motion_score: float
    duplicate_of_id: int | None
    warnings: list | None
    sequence_number: int
    processing_time_ms: int
    created_at: datetime
    updated_at: datetime

    student_name: str | None = None
    student_external_id: str | None = None
    class_name: str | None = None
    task_title: str | None = None
    recognition: RecognitionOut | None = None
    review: ReviewDecisionOut | None = None


class SheetAssign(BaseModel):
    student_id: int | None = None
    task_id: int | None = None
    sheet_uid: str | None = None
    clear_duplicate: bool = False


class SheetStatusUpdate(BaseModel):
    scan_status: str

    @field_validator("scan_status")
    @classmethod
    def _validate(cls, value: str) -> str:
        allowed = {"ok", "low_quality", "duplicate", "unidentified", "rescan_required", "deleted"}
        if value not in allowed:
            raise ValueError(f"scan_status must be one of {sorted(allowed)}")
        return value


class ReviewSubmit(BaseModel):
    teacher_text: str = ""
    decision: str
    comment: str = ""

    @field_validator("decision")
    @classmethod
    def _validate(cls, value: str) -> str:
        allowed = {"accepted", "corrected", "rescan_required", "unreadable", "wrong_student", "duplicate"}
        if value not in allowed:
            raise ValueError(f"decision must be one of {sorted(allowed)}")
        return value


# ------------------------------------------------------------------- camera


class CameraProfileIn(BaseModel):
    name: str = "default"
    device_id: str = ""
    device_label: str = ""
    width: int = 1920
    height: int = 1080
    work_area_polygon: list[list[float]] | None = None
    qr_region: RegionModel | None = None
    answer_regions: list[RegionModel] | None = None
    template_id: int | None = None
    notes: str = ""


class CameraProfileOut(ORMBase):
    id: int
    name: str
    device_id: str
    device_label: str
    width: int
    height: int
    work_area_polygon: list | None
    qr_region: dict | None
    answer_regions: list | None
    background_reference_path: str | None
    template_id: int | None
    is_active: bool
    notes: str
    created_at: datetime
    updated_at: datetime


class ImagePayload(BaseModel):
    """Base64 (data-URL) encoded frame from the browser."""

    image: str = Field(min_length=16)


class CalibrationDetectRequest(ImagePayload):
    work_area: list[list[float]] | None = None


class CalibrationDetectResponse(BaseModel):
    found: bool
    quad: list[list[float]] | None = None
    aspect_ratio: float = 0.0
    perspective: float = 0.0
    warnings: list[str] = []
    preview: str | None = None


class CameraTestResponse(BaseModel):
    sharpness: float
    glare: float
    brightness: dict[str, float]
    resolution: list[int]
    warnings: list[str]
    passed: bool


# ------------------------------------------------------------------ settings


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------- generation


class FormBlockModel(BaseModel):
    """One visual block in the printable answer-form constructor."""

    type: str = Field(default="lines", pattern="^(lines|choice|short|grid)$")
    title: str = Field(default="Ответ", max_length=80)
    rows: int = Field(default=4, ge=1, le=80)
    columns: int = Field(default=4, ge=1, le=12)


class FormGenerationRequest(BaseModel):
    class_id: int
    task_id: int
    student_ids: list[int] | None = None
    sheets_per_student: int = Field(default=1, ge=1, le=20)
    forms_per_page: int = Field(default=3, ge=1, le=6)
    include_cut_lines: bool = True
    payload_format: str = Field(default="json", pattern="^(json|compact)$")
    title_override: str | None = None
    layout_kind: str = Field(default="lines", pattern="^(lines|choice|short|grid|mixed)$")
    blocks: list[FormBlockModel] = Field(default_factory=list)
    variant_count: int = Field(default=1, ge=1, le=30)
    variant_mode: str = Field(default="rotate", pattern="^(rotate|all|fixed)$")


# ------------------------------------------------------------------ hardware


class HardwareEventIn(BaseModel):
    level: str = "warning"
    code: str = ""
    message: str = ""
    context: dict[str, Any] = {}


class HardwareEventOut(ORMBase):
    id: int
    level: str
    code: str
    message: str
    context: dict | None
    created_at: datetime


# ----------------------------------------------------------------- dashboard


class DashboardOut(BaseModel):
    last_session: ScanSessionOut | None = None
    sheets_today: int = 0
    needs_review: int = 0
    average_speed: float = 0.0
    hardware_events: list[HardwareEventOut] = []
    total_sessions: int = 0
    total_sheets: int = 0
    storage_bytes: int = 0
