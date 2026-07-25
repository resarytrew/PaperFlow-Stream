"""Live scanning WebSocket and diagnostics endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import cv2
from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.deps import Config, DbSession, get_session_or_404, serialize_sheet
from app.config import get_settings
from app.cv.state_machine import DecisionAction, ScanState
from app.db import SessionLocal
from app.models import CameraProfile, FormTemplate, ScanLog, ScanSession, SessionStatus
from app.services.events import hub, session_topic
from app.services.scan_service import scan_service
from app.services.settings_service import load_config
from app.services.storage import StorageError, decode_data_url, get_storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["scan"])


def _prepare_runtime(session_id: int) -> tuple[dict, str | None]:
    """Load the session, calibration profile and template into a runtime."""
    with SessionLocal() as db:
        session = db.get(ScanSession, session_id)
        if session is None:
            return {}, "Сессия не найдена"
        config = load_config(db)
        runtime = scan_service.ensure_runtime(session_id, config)

        profile = db.execute(
            select(CameraProfile).where(CameraProfile.is_active.is_(True)).order_by(CameraProfile.id.desc())
        ).scalars().first()
        template = db.get(FormTemplate, session.template_id) if session.template_id else None
        if template is None:
            template = db.execute(
                select(FormTemplate).where(FormTemplate.is_default.is_(True))
            ).scalar_one_or_none()
        runtime.apply_profile(profile, template)

        return {
            "sessionId": session_id,
            "title": session.title,
            "status": session.status,
            "expected": session.expected_sheet_count,
            "calibrated": profile is not None and profile.work_area_polygon is not None,
            "hasBackground": runtime.background_gray is not None,
            "qrRegion": runtime.qr_region,
            "answerRegions": runtime.answer_regions,
            "workArea": runtime.work_area,
            "counters": runtime.counters,
        }, None


@router.websocket("/ws/sessions/{session_id}/scan")
async def scan_socket(websocket: WebSocket, session_id: int) -> None:
    """Receive frames from the browser, return state + overlay data.

    Protocol (client → server):
        {"type": "frame", "image": "data:image/jpeg;base64,..."}
        {"type": "pause"} / {"type": "resume"} / {"type": "reset"}
        {"type": "diagnostics", "enabled": true}
    Server → client:
        {"type": "state", ...} | {"type": "scan_result", ...} | {"type": "error", ...}
    """
    await websocket.accept()
    topic = session_topic(session_id)
    await hub.subscribe(topic, websocket)

    info, error = await asyncio.to_thread(_prepare_runtime, session_id)
    if error:
        await websocket.send_json({"type": "error", "message": error})
        await websocket.close()
        await hub.unsubscribe(topic, websocket)
        return

    await websocket.send_json({"type": "ready", **info})
    runtime = scan_service.get_runtime(session_id)
    if runtime is None:
        await websocket.close()
        return

    busy = False

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid json"})
                continue

            kind = message.get("type")

            if kind == "pause":
                runtime.machine.pause()
                await websocket.send_json({"type": "paused"})
                continue
            if kind == "resume":
                runtime.machine.resume()
                await websocket.send_json({"type": "resumed"})
                continue
            if kind == "reset":
                runtime.machine.reset()
                runtime.reset_candidates()
                await websocket.send_json({"type": "reset"})
                continue
            if kind == "diagnostics":
                runtime.diagnostics_enabled = bool(message.get("enabled"))
                if not runtime.diagnostics_enabled:
                    runtime.diagnostic_frames = []
                await websocket.send_json({"type": "diagnostics", "enabled": runtime.diagnostics_enabled})
                continue
            if kind != "frame":
                continue

            if busy:
                # Drop frames while a sheet is being persisted – keeps the UI fluid.
                await websocket.send_json({"type": "busy"})
                continue

            try:
                frame = await asyncio.to_thread(decode_data_url, message.get("image", ""))
            except StorageError as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            try:
                decision, overlay = await asyncio.to_thread(scan_service.analyse_frame, runtime, frame)
            except Exception as exc:  # one bad frame must never kill the stream
                logger.exception("frame analysis failed")
                await websocket.send_json({"type": "error", "message": f"analysis failed: {exc}"})
                continue

            payload = {
                "type": "state",
                **decision.to_dict(),
                "overlay": overlay,
                "counters": runtime.counters,
                "speed": round(runtime.average_speed(), 2),
            }
            await websocket.send_json(payload)

            if decision.action == DecisionAction.PROCESS_BEST:
                busy = True
                try:
                    outcome = await asyncio.to_thread(_process_sheet, session_id, runtime)
                except Exception as exc:
                    logger.exception("sheet processing failed")
                    runtime.machine.notify_warning("processing_error")
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    busy = False
                    continue
                busy = False

                if outcome is None:
                    continue

                if outcome["success"]:
                    runtime.machine.notify_success()
                else:
                    runtime.machine.notify_warning(outcome.get("reason") or "warning")

                await websocket.send_json(
                    {
                        "type": "scan_result",
                        "result": outcome,
                        "counters": runtime.counters,
                        "speed": round(runtime.average_speed(), 2),
                        "state": runtime.machine.state.value,
                        "prompt": "ЛИСТ ПРИНЯТ" if outcome["success"] else "ПОВТОРИТЕ ПОДАЧУ",
                    }
                )
    except WebSocketDisconnect:
        logger.info("scan socket for session %s disconnected", session_id)
    except Exception:  # pragma: no cover
        logger.exception("scan socket error")
    finally:
        await hub.unsubscribe(topic, websocket)


def _process_sheet(session_id: int, runtime) -> dict | None:
    """Persist the best candidate; runs in a worker thread with its own session."""
    with SessionLocal() as db:
        session = db.get(ScanSession, session_id)
        if session is None:
            return None
        outcome = scan_service.process_best_candidate(db, runtime, session)
        data = outcome.to_dict()

        if outcome.sheet_id and outcome.success:
            # 0.2: hand the sheet to the OCR queue without blocking scanning.
            try:
                from app.services.ocr_queue import ocr_queue

                config = load_config(db)
                if config.ocr.auto_enqueue:
                    ocr_queue.enqueue(outcome.sheet_id)
                    data["ocrQueued"] = True
            except Exception as exc:  # OCR must never break scanning
                logger.warning("could not enqueue OCR for sheet %s: %s", outcome.sheet_id, exc)
        return data


@router.get("/sessions/{session_id}/scan-state")
def get_scan_state(session_id: int, db: DbSession) -> dict:
    get_session_or_404(db, session_id)
    runtime = scan_service.get_runtime(session_id)
    if runtime is None:
        return {"active": False, "state": ScanState.WAITING_EMPTY.value}
    return {
        "active": True,
        **runtime.machine.snapshot(),
        "counters": runtime.counters,
        "speed": round(runtime.average_speed(), 2),
        "candidates": len(runtime.candidates),
        "diagnostics": runtime.diagnostics_enabled,
    }


@router.get("/sessions/{session_id}/logs")
def get_scan_logs(session_id: int, db: DbSession, limit: int = 50) -> list[dict]:
    """Technical journal (section 10). Personal data is never stored here."""
    get_session_or_404(db, session_id)
    logs = db.execute(
        select(ScanLog).where(ScanLog.session_id == session_id).order_by(ScanLog.id.desc()).limit(min(limit, 300))
    ).scalars().all()
    return [
        {
            "id": log.id,
            "sheetId": log.sheet_id,
            "timestamp": log.created_at.isoformat(),
            "events": log.events,
            "corners": log.corners,
            "candidateScores": log.candidate_scores,
            "selectedFrameIndex": log.selected_frame_index,
            "qrResult": log.qr_result,
            "processingDurationMs": log.processing_duration_ms,
            "message": log.message,
        }
        for log in logs
    ]


@router.post("/sessions/{session_id}/diagnostics/export")
def export_diagnostics(session_id: int, db: DbSession) -> dict:
    """Write the recorded candidate frames + logs into a diagnostics folder."""
    session = get_session_or_404(db, session_id)
    runtime = scan_service.get_runtime(session_id)
    if runtime is None:
        raise HTTPException(status_code=400, detail="Сессия не активна")
    if not runtime.diagnostic_frames and not runtime.candidates:
        raise HTTPException(status_code=400, detail="Нет записанных кадров. Включите диагностический режим.")

    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = settings.diagnostics_dir / f"session-{session_id}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    for index, frame in enumerate(runtime.diagnostic_frames):
        cv2.imwrite(str(folder / f"stream-{index:04d}.jpg"), frame)
    for index, candidate in enumerate(runtime.candidates):
        cv2.imwrite(str(folder / f"candidate-{index:02d}.jpg"), candidate.frame)

    logs = db.execute(
        select(ScanLog).where(ScanLog.session_id == session_id).order_by(ScanLog.id.desc()).limit(50)
    ).scalars().all()
    report = {
        "sessionId": session_id,
        "title": session.title,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "state": runtime.machine.snapshot(),
        "counters": runtime.counters,
        "config": runtime.config.model_dump(),
        "logs": [
            {
                "sheetId": log.sheet_id,
                "candidateScores": log.candidate_scores,
                "selectedFrameIndex": log.selected_frame_index,
                "qrResult": log.qr_result,
                "durationMs": log.processing_duration_ms,
                "message": log.message,
                "events": log.events,
            }
            for log in logs
        ],
    }
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    frames_written = len(runtime.diagnostic_frames)
    runtime.diagnostic_frames = []
    return {
        "path": str(folder),
        "frames": frames_written,
        "candidates": len(runtime.candidates),
        "logs": len(logs),
    }


@router.get("/sessions/{session_id}/diagnostics/download")
def download_diagnostics(session_id: int, db: DbSession) -> Response:
    """One-click support bundle: recorded frames + logs + config as a ZIP.

    The teacher presses one button and gets a file to attach to a support
    message — no digging through server folders. Frames are cleared after
    the download so the next recording starts clean.
    """
    import io
    import zipfile

    session = get_session_or_404(db, session_id)
    runtime = scan_service.get_runtime(session_id)
    if runtime is None:
        raise HTTPException(status_code=400, detail="Сессия не активна — нечего выгружать")
    if not runtime.diagnostic_frames and not runtime.candidates:
        raise HTTPException(
            status_code=400,
            detail="Нет записанных кадров. Включите запись диагностики на экране сканирования и повторите проблемную подачу листа.",
        )

    logs = db.execute(
        select(ScanLog).where(ScanLog.session_id == session_id).order_by(ScanLog.id.desc()).limit(50)
    ).scalars().all()
    report = {
        "sessionId": session_id,
        "title": session.title,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "appVersion": get_settings().version,
        "state": runtime.machine.snapshot(),
        "counters": runtime.counters,
        "config": runtime.config.model_dump(),
        "logs": [
            {
                "sheetId": log.sheet_id,
                "candidateScores": log.candidate_scores,
                "selectedFrameIndex": log.selected_frame_index,
                "qrResult": log.qr_result,
                "durationMs": log.processing_duration_ms,
                "message": log.message,
                "events": log.events,
            }
            for log in logs
        ],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2))
        for index, frame in enumerate(runtime.diagnostic_frames):
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if ok:
                archive.writestr(f"stream/frame-{index:04d}.jpg", encoded.tobytes())
        for index, candidate in enumerate(runtime.candidates):
            ok, encoded = cv2.imencode(".jpg", candidate.frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if ok:
                archive.writestr(f"candidates/candidate-{index:02d}.jpg", encoded.tobytes())

    runtime.diagnostic_frames = []

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"paperflow_diagnostics_s{session_id}_{stamp}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
