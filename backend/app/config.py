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


class NormalizationConfig(BaseModel):
    """Perspective and image-normalisation parameters."""

    target_width: int = 1654
    target_height: int = 2339
    perspective_padding: int = 20
    clahe_clip_limit: float = 2.0
    clahe_grid_size: int = 8
    unsharp_amount: float = 0.8
    unsharp_radius: float = 1.0


class OcrConfig(BaseModel):
    """Local OCR configuration."""

    engine: Literal["rapidocr", "vision"] = "rapidocr"
    language: str = "ru"
    max_workers: int = Field(2, ge=1, le=8)
    confidence_threshold: float = Field(0.55, ge=0.0, le=1.0)
    preprocessing: bool = True
    detect_orientation: bool = True


class VisionOcrConfig(BaseModel):
    """Optional Yandex Vision configuration."""

    enabled: bool = False
    api_key: str = ""
    folder_id: str = ""
    model: str = "page"
    language_codes: list[str] = ["ru", "en"]


class PrivacyConfig(BaseModel):
    """Privacy and retention defaults."""

    retain_originals: bool = True
    retain_normalized: bool = True
    diagnostics_enabled: bool = False
    anonymize_exports: bool = False
    auto_delete_days: int = Field(0, ge=0, le=3650)


class AppConfig(BaseModel):
    """Persisted, user-facing application configuration.

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
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'paperflow.db').as_posix()}"

    def ensure_directories(self) -> None:
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
    settings.ensure_directories()
    return settings


settings = get_settings()
