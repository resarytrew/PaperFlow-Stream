"""Camera profile, calibration wizard and settings endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import Config, DbSession
from app.config import get_settings
from app.cv.detection import detect_paper
from app.cv.geometry import Quad, order_corners, perspective_score, quad_aspect_ratio
from app.cv.normalization import rectify
from app.cv.qr import read_qr
from app.cv.quality import brightness_stats, glare_score, sharpness_score, to_gray
from app.models import CameraProfile, FormTemplate, HardwareEvent
from app.schemas import (
    CalibrationDetectRequest,
    CalibrationDetectResponse,
    CameraProfileIn,
    CameraProfileOut,
    CameraTestResponse,
    HardwareEventIn,
    HardwareEventOut,
    ImagePayload,
    SettingsPatch,
)
from app.services.scan_service import scan_service
from app.services.settings_service import load_config, reset_config, save_config
from app.services.storage import decode_data_url, encode_data_url, get_storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["camera"])


# ------------------------------------------------------------------ settings


@router.get("/settings")
def get_settings_endpoint(config: Config) -> dict:
    settings = get_settings()
    return {
        "config": config.model_dump(),
        "paths": {
            "dataDir": str(settings.data_dir),
            "storageDir": str(settings.storage_dir),
            "database": settings.resolved_database_url(),
        },
        "version": settings.version,
    }


@router.patch("/settings")
def patch_settings(payload: SettingsPatch, db: DbSession) -> dict:
    try:
        config = save_config(db, payload.model_dump(exclude_unset=True))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Некорректные настройки: {exc}") from exc
    # active runtimes pick up the new configuration immediately
    for runtime in scan_service._runtimes.values():  # noqa: SLF001 - internal by design
        runtime.config = config
        runtime.machine.detection = config.detection
        runtime.machine.stability = config.stability
    return {"config": config.model_dump()}


@router.post("/settings/reset")
def reset_settings(db: DbSession) -> dict:
    config = reset_config(db)
    return {"config": config.model_dump()}


# ------------------------------------------------------------------- profile


@router.get("/camera/profiles", response_model=list[CameraProfileOut])
def list_profiles(db: DbSession) -> list[CameraProfileOut]:
    profiles = db.execute(select(CameraProfile).order_by(CameraProfile.id.desc())).scalars().all()
    return [CameraProfileOut.model_validate(p) for p in profiles]


@router.get("/camera/profile", response_model=CameraProfileOut | None)
def get_active_profile(db: DbSession) -> CameraProfileOut | None:
    profile = db.execute(
        select(CameraProfile).where(CameraProfile.is_active.is_(True)).order_by(CameraProfile.id.desc())
    ).scalars().first()
    return CameraProfileOut.model_validate(profile) if profile else None


@router.put("/camera/profile", response_model=CameraProfileOut)
def save_profile(payload: CameraProfileIn, db: DbSession) -> CameraProfileOut:
    """Create or update the named camera profile (calibration result)."""
    profile = db.execute(
        select(CameraProfile).where(CameraProfile.name == payload.name)
    ).scalar_one_or_none()
    if profile is None:
        profile = CameraProfile(name=payload.name)
        db.add(profile)

    profile.device_id = payload.device_id
    profile.device_label = payload.device_label
    profile.width = payload.width
    profile.height = payload.height
    if payload.work_area_polygon is not None:
        profile.work_area_polygon = payload.work_area_polygon
    if payload.qr_region is not None:
        profile.qr_region = payload.qr_region.model_dump()
    if payload.answer_regions is not None:
        profile.answer_regions = [r.model_dump() for r in payload.answer_regions]
    profile.template_id = payload.template_id
    profile.notes = payload.notes
    profile.is_active = True

    for other in db.execute(select(CameraProfile).where(CameraProfile.name != payload.name)).scalars():
        other.is_active = False

    db.commit()
    db.refresh(profile)
    return CameraProfileOut.model_validate(profile)


# --------------------------------------------------------------- calibration


@router.post("/camera/test", response_model=CameraTestResponse)
def test_camera(payload: ImagePayload, config: Config) -> CameraTestResponse:
    """Lighting / sharpness / resolution checks (section 6.2)."""
    try:
        frame = decode_data_url(payload.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    height, width = frame.shape[:2]
    sharpness = sharpness_score(frame, config.stability.sharpness_reference)
    glare = glare_score(frame)
    brightness = brightness_stats(frame)

    warnings: list[str] = []
    if width < config.capture.min_resolution_width:
        warnings.append(f"Низкое разрешение: {width}×{height}. Рекомендуется 1920×1080 или выше.")
    if brightness["mean"] < 70:
        warnings.append("Недостаточное освещение рабочей зоны.")
    if brightness["mean"] > 215:
        warnings.append("Кадр пересвечен — уменьшите яркость освещения.")
    if glare > config.stability.max_glare:
        warnings.append("Сильные блики на рабочей поверхности.")
    if sharpness < config.stability.min_sharpness:
        warnings.append("Изображение недостаточно резкое — проверьте фокус камеры.")
    if brightness["clipped_high"] > 0.12:
        warnings.append("Значительные пересвеченные области.")

    return CameraTestResponse(
        sharpness=round(sharpness, 4),
        glare=round(glare, 4),
        brightness={k: round(v, 3) for k, v in brightness.items()},
        resolution=[width, height],
        warnings=warnings,
        passed=not warnings,
    )


@router.post("/camera/detect-sheet", response_model=CalibrationDetectResponse)
def detect_sheet(payload: CalibrationDetectRequest, config: Config) -> CalibrationDetectResponse:
    """Step 1 of the calibration wizard: find the blank form's four corners."""
    try:
        frame = decode_data_url(payload.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = detect_paper(frame, config.detection, work_area=payload.work_area)
    if not result.found or result.quad is None:
        return CalibrationDetectResponse(
            found=False,
            warnings=["Бланк не обнаружен. Положите бланк в рабочую зону на контрастный фон."],
        )

    warnings = list(result.warnings)
    if result.perspective < 0.7:
        warnings.append("Камера расположена под большим углом к листу.")
    if result.touches_border:
        warnings.append("Лист выходит за границы кадра.")

    preview = None
    try:
        warped = rectify(frame, result.quad, config.normalization, quad_aspect_ratio(result.quad))
        preview = encode_data_url(cv2.resize(warped, (480, int(480 * warped.shape[0] / warped.shape[1]))), 75)
    except Exception as exc:  # pragma: no cover
        logger.warning("calibration preview failed: %s", exc)

    return CalibrationDetectResponse(
        found=True,
        quad=result.quad.as_list(),
        aspect_ratio=round(result.aspect_ratio, 4),
        perspective=round(result.perspective, 4),
        warnings=warnings,
        preview=preview,
    )


class _WarpRequest(ImagePayload):
    quad: list[list[float]]


@router.post("/camera/preview-warp")
def preview_warp(payload: _WarpRequest, config: Config) -> dict:
    """Preview the rectified sheet after manual corner adjustment."""
    try:
        frame = decode_data_url(payload.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(payload.quad) != 4:
        raise HTTPException(status_code=400, detail="Требуются ровно 4 угла")

    try:
        quad = Quad(order_corners(np.array(payload.quad, dtype=np.float32)))
        ratio = quad_aspect_ratio(quad)
        warped = rectify(frame, quad, config.normalization, ratio)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Не удалось выровнять лист: {exc}") from exc

    qr = read_qr(warped, enhance=True)
    scale = 560 / max(warped.shape[1], 1)
    preview = cv2.resize(warped, (560, max(int(warped.shape[0] * scale), 1)))

    return {
        "preview": encode_data_url(preview, 78),
        "aspectRatio": round(ratio, 4),
        "perspective": round(perspective_score(quad), 4),
        "sharpness": round(sharpness_score(warped, config.stability.sharpness_reference), 4),
        "glare": round(glare_score(warped), 4),
        "qrDetected": qr.success,
        "qrPayload": qr.payload.to_dict() if qr.payload else None,
        "qrPoints": qr.points,
        "size": [warped.shape[1], warped.shape[0]],
    }


@router.post("/camera/background")
def capture_background(payload: ImagePayload, db: DbSession, config: Config) -> dict:
    """Store the empty work-area reference used by background subtraction."""
    try:
        frame = decode_data_url(payload.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detection = detect_paper(frame, config.detection)
    if detection.found and detection.area_ratio > 0.25:
        raise HTTPException(
            status_code=400,
            detail="В рабочей зоне обнаружен объект. Уберите все листы перед съёмкой фона.",
        )

    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = settings.calibration_dir / f"background-{stamp}.png"
    storage = get_storage()
    relative = storage.save_image(frame, path)

    profile = db.execute(
        select(CameraProfile).where(CameraProfile.is_active.is_(True)).order_by(CameraProfile.id.desc())
    ).scalars().first()
    if profile is None:
        profile = CameraProfile(name="default", is_active=True)
        db.add(profile)
    profile.background_reference_path = relative
    db.commit()

    # refresh live runtimes so the change takes effect without a restart
    gray = cv2.GaussianBlur(to_gray(frame), (5, 5), 0)
    for runtime in scan_service._runtimes.values():  # noqa: SLF001
        runtime.background_gray = gray

    stats = brightness_stats(frame)
    return {
        "path": relative,
        "brightness": {k: round(v, 3) for k, v in stats.items()},
        "warnings": (["Фон слишком тёмный — детекция листа может быть менее надёжной."] if stats["mean"] < 55 else []),
    }


@router.post("/camera/test-capture")
def test_capture(payload: ImagePayload, db: DbSession, config: Config) -> dict:
    """Final calibration step: a full dry-run capture of one sheet."""
    try:
        frame = decode_data_url(payload.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = db.execute(
        select(CameraProfile).where(CameraProfile.is_active.is_(True)).order_by(CameraProfile.id.desc())
    ).scalars().first()

    background = None
    work_area = None
    qr_region = {"x": 0.01, "y": 0.02, "w": 0.28, "h": 0.38}
    target_ratio = None
    if profile is not None:
        work_area = profile.work_area_polygon
        if profile.qr_region:
            qr_region = profile.qr_region
        if profile.background_reference_path:
            try:
                background = cv2.GaussianBlur(to_gray(get_storage().load(profile.background_reference_path)), (5, 5), 0)
            except Exception:
                background = None
        if profile.template_id:
            template = db.get(FormTemplate, profile.template_id)
            if template:
                target_ratio = template.aspect_ratio

    detection = detect_paper(frame, config.detection, background=background, work_area=work_area)
    if not detection.found or detection.quad is None:
        return {"success": False, "message": "Лист не обнаружен", "warnings": detection.warnings}

    warped = rectify(frame, detection.quad, config.normalization, target_ratio)
    from app.cv.geometry import crop_normalized

    qr = read_qr(crop_normalized(warped, qr_region), enhance=True)
    if not qr.success:
        qr = read_qr(warped, enhance=True)

    scale = 520 / max(warped.shape[1], 1)
    preview = cv2.resize(warped, (520, max(int(warped.shape[0] * scale), 1)))

    return {
        "success": True,
        "preview": encode_data_url(preview, 78),
        "quad": detection.quad.as_list(),
        "sharpness": round(sharpness_score(warped, config.stability.sharpness_reference), 4),
        "glare": round(glare_score(warped), 4),
        "perspective": round(detection.perspective, 4),
        "qrDetected": qr.success,
        "qrPayload": qr.payload.to_dict() if qr.payload else None,
        "message": "Тестовый захват выполнен" if qr.success else "Лист найден, но QR-код не прочитан",
        "warnings": detection.warnings,
    }


# ------------------------------------------------------------------ hardware


@router.get("/hardware/events", response_model=list[HardwareEventOut])
def list_hardware_events(db: DbSession, limit: int = 20) -> list[HardwareEventOut]:
    events = db.execute(
        select(HardwareEvent).order_by(HardwareEvent.id.desc()).limit(min(limit, 100))
    ).scalars().all()
    return [HardwareEventOut.model_validate(e) for e in events]


@router.post("/hardware/events", response_model=HardwareEventOut, status_code=status.HTTP_201_CREATED)
def create_hardware_event(payload: HardwareEventIn, db: DbSession) -> HardwareEventOut:
    event = HardwareEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return HardwareEventOut.model_validate(event)
