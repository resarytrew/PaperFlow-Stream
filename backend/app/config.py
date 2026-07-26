"""Application configuration.

Every sensitive CV / OCR parameter lives here so it can be tuned from the
settings screen without touching code. Values are persisted in the database
(``app_settings`` table) and layered on top of these defaults at runtime.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"

# Raised only by the release that contains the real workspace-scoped schema,
# tenant predicates, users and audit log. An environment variable alone must
# never be able to turn the current personal database into a fake School Hub.
SCHOOL_TENANCY_SCHEMA_VERSION = 0


class QualityWeights(BaseModel):
    """Weights used by :func:`app.cv.quality.compute_quality_score`."""

    sharpness: float = 0.34
    coverage: float = 0.16
    perspective: float = 0.16
    qr: float = 0.34
    glare: float = 0.25
    occlusion: float = 0.60
    motion: float = 0.35


class CaptureConfig(BaseModel):
    """Streaming / capture parameters."""

    stream_width: int = Field(1280, ge=320, le=4096)
    capture_width: int = Field(1920, ge=640, le=4096)
    analysis_width: int = Field(640, ge=240, le=1920)
    stream_quality: float = Field(0.72, ge=0.3, le=1.0)
    capture_quality: float = Field(0.92, ge=0.3, le=1.0)
    capture_fps: int = Field(12, ge=1, le=30)
    preferred_resolution: str = "1920x1080"
    min_resolution_width: int = 1280


class DetectionConfig(BaseModel):
    """Paper detection thresholds."""

    min_area_ratio: float = Field(0.10, ge=0.005, le=0.95)
    max_area_ratio: float = Field(0.95, ge=0.05, le=1.0)
    min_aspect_ratio: float = Field(0.35, ge=0.05, le=1.0)
    max_aspect_ratio: float = Field(3.0, ge=1.0, le=10.0)
    entering_diff_ratio: float = Field(0.04, ge=0.001, le=0.9)
    empty_diff_ratio: float = Field(0.025, ge=0.001, le=0.9)
    canny_low: int = 40
    canny_high: int = 120
    approx_epsilon: float = 0.02
    use_yolo: bool = False
    yolo_model_path: str = ""
    yolo_confidence: float = 0.35
    yolo_every_n_frames: int = 5
    hand_detector: Literal["heuristic", "mediapipe", "yolo", "off"] = "heuristic"


class StabilityConfig(BaseModel):
    """Motion / stability gating."""

    motion_threshold: float = Field(0.06, ge=0.0, le=1.0)
    stability_duration_ms: int = Field(320, ge=0, le=5000)
    stable_frames_required: int = Field(3, ge=1, le=30)
    candidate_window_ms: int = Field(600, ge=100, le=5000)
    max_candidates: int = Field(8, ge=1, le=40)
    min_sharpness: float = Field(0.30, ge=0.0, le=1.0)
    max_glare: float = Field(0.35, ge=0.0, le=1.0)
    max_hand_overlap: float = Field(0.08, ge=0.0, le=1.0)
    min_quality_score: float = Field(0.42, ge=0.0, le=1.0)
    removal_frames_required: int = Field(3, ge=1, le=30)
    success_hold_ms: int = Field(700, ge=0, le=5000)
    warning_hold_ms: int = Field(1500, ge=0, le=10000)
    sharpness_reference: float = Field(140.0, gt=1.0)
    qr_timeout_ms: int = Field(900, ge=50, le=10000)
    qr_readability_every_n_frames: int = Field(4, ge=1, le=60)


class NormalizationConfig(BaseModel):
    """Output geometry of the normalised sheet."""

    output_width: int = 1240
    output_height: int = 1754
    keep_source_frame: bool = True
    jpeg_quality: int = 92
    thumbnail_width: int = 320
    shadow_kernel: int = 41
    clahe_clip: float = 2.0
    adaptive_block: int = 35
    adaptive_c: int = 12


class OcrConfig(BaseModel):
    """Handwriting recognition parameters (0.2)."""

    provider: str = "local"
    model_name: str = "trocr-like-local"
    language: str = "ru"
    concurrency: int = Field(2, ge=1, le=8)
    high_confidence: float = Field(0.85, ge=0.0, le=1.0)
    low_confidence: float = Field(0.60, ge=0.0, le=1.0)
    critical_token_confidence: float = Field(0.45, ge=0.0, le=1.0)
    blank_ink_ratio: float = Field(0.004, ge=0.0, le=1.0)
    min_line_height: int = 12
    auto_enqueue: bool = True
    max_retries: int = 1
    keyword_analysis: bool = True


class VisionOcrConfig(BaseModel):
    """Yandex Vision OCR integration settings.

    The provider is privacy-gated separately. By default it is inactive and no
    student image leaves the computer.
    """

    provider: str = "yandex"
    endpoint: str = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
    api_key: str = ""
    folder_id: str = ""
    model: str = "page"
    mime_type: str = "image/jpeg"
    mock_mode: bool = False


class PrivacyConfig(BaseModel):
    file_retention_days: int = Field(180, ge=0, le=3650)
    anonymise_logs: bool = True
    allow_cloud_providers: bool = False
    vision_ocr_enabled: bool = False
    vision_send_full_sheet: bool = False
    diagnostics_recording_enabled: bool = False
    diagnostics_max_clip_frames: int = Field(120, ge=10, le=2000)


class RuntimeConfig(BaseModel):
    """The full mutable configuration tree exposed on /settings.

    Secret values are redacted from ``model_dump()`` by default. Internal code
    that intentionally needs to persist the complete configuration must pass
    ``include_secrets=True`` explicitly.
    """

    capture: CaptureConfig = CaptureConfig()
    detection: DetectionConfig = DetectionConfig()
    stability: StabilityConfig = StabilityConfig()
    normalization: NormalizationConfig = NormalizationConfig()
    quality_weights: QualityWeights = QualityWeights()
    ocr: OcrConfig = OcrConfig()
    vision_ocr: VisionOcrConfig = VisionOcrConfig()
    privacy: PrivacyConfig = PrivacyConfig()

    def model_dump(self, *, include_secrets: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Serialise safely, hiding credentials unless explicitly requested."""
        data = super().model_dump(**kwargs)
        if include_secrets:
            return data

        vision = data.get("vision_ocr")
        if isinstance(vision, dict):
            configured = bool(vision.get("api_key"))
            vision["api_key"] = ""
            vision["api_key_configured"] = configured
        return data


class Settings(BaseSettings):
    """Process-level and Hybrid Hub settings (environment driven)."""

    model_config = SettingsConfigDict(env_prefix="PAPERFLOW_", env_file=".env", extra="ignore")

    app_name: str = "PaperFlow Hub"
    version: str = "0.3.1"
    data_dir: Path = DEFAULT_DATA_DIR
    database_url: str = ""
    log_level: str = "INFO"

    # Hybrid Local Hub transport. The public web UI is allowed to connect only
    # when its exact Origin is configured and the browser has paired locally.
    hub_mode: Literal["personal", "school"] = "personal"
    hub_bind_host: str = "127.0.0.1"
    hub_port: int = Field(default=17841, ge=1024, le=65535)
    hub_public_url: str = "https://127.0.0.1:17841"
    hub_default_workspace_id: str = "personal"
    hub_allowed_origins: list[str] = []
    hub_trusted_unpaired_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    hub_require_pairing: bool = False
    hub_pairing_code_ttl_seconds: int = Field(default=300, ge=60, le=1800)
    hub_token_ttl_days: int = Field(default=365, ge=1, le=3650)
    hub_pairing_dev_echo_code: bool = False

    # This flag is consumed only after the release itself declares a supported
    # tenant schema version. It cannot bypass the code-level capability guard.
    hub_school_tenancy_enabled: bool = False

    # Backward-compatible local development origins.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @model_validator(mode="after")
    def validate_deployment_mode(self) -> "Settings":
        if self.hub_mode == "school" and (
            not self.hub_school_tenancy_enabled or SCHOOL_TENANCY_SCHEMA_VERSION < 1
        ):
            raise ValueError(
                "school mode is blocked until this release contains workspace-scoped persistence, users and audit"
            )
        return self

    @property
    def all_cors_origins(self) -> list[str]:
        return list(dict.fromkeys([*self.cors_origins, *self.hub_allowed_origins]))

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def sheets_dir(self) -> Path:
        return self.storage_dir / "sheets"

    @property
    def diagnostics_dir(self) -> Path:
        return self.storage_dir / "diagnostics"

    @property
    def forms_dir(self) -> Path:
        return self.storage_dir / "forms"

    @property
    def exports_dir(self) -> Path:
        return self.storage_dir / "exports"

    @property
    def calibration_dir(self) -> Path:
        return self.storage_dir / "calibration"

    @property
    def hub_dir(self) -> Path:
        return self.data_dir / "hub"

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'paperflow.db').as_posix()}"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.storage_dir,
            self.sheets_dir,
            self.diagnostics_dir,
            self.forms_dir,
            self.exports_dir,
            self.calibration_dir,
            self.hub_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if os.getenv("PAPERFLOW_DATA_DIR"):
        settings.data_dir = Path(os.environ["PAPERFLOW_DATA_DIR"])
    settings.ensure_dirs()
    return settings


DEFAULT_RUNTIME_CONFIG = RuntimeConfig()
