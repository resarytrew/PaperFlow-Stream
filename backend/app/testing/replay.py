"""Camera-free replay harness (section 10 + E2E tests).

Feeds recorded or synthetic frames through the *real* pipeline so the
behaviour can be verified without hardware.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from app.config import RuntimeConfig
from app.cv.quality import to_gray
from app.cv.state_machine import DecisionAction
from app.cv.synthetic import empty_scene, render_scene, render_sheet, SceneOptions
from app.db import SessionLocal
from app.models import ScanSession, SessionStatus
from app.services.scan_service import scan_service

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _ensure_session(db, title: str) -> ScanSession:
    session = ScanSession(title=title, status=SessionStatus.scanning.value, expected_sheet_count=0)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def replay_frames(
    frames: list[np.ndarray],
    *,
    session_id: int | None = None,
    config: RuntimeConfig | None = None,
    background: np.ndarray | None = None,
    target_ratio: float | None = None,
    persist: bool = True,
) -> dict:
    """Run frames through the state machine; optionally persist the results."""
    config = config or RuntimeConfig()
    report: dict = {"frames": len(frames), "states": [], "outcomes": [], "errors": []}

    with SessionLocal() as db:
        if session_id is None:
            session = _ensure_session(db, "Replay")
        else:
            session = db.get(ScanSession, session_id)
            if session is None:
                session = _ensure_session(db, "Replay")
        report["sessionId"] = session.id

        runtime = scan_service.create_runtime(session.id, config)
        if background is not None:
            runtime.background_gray = cv2.GaussianBlur(to_gray(background), (5, 5), 0)
        runtime.target_ratio = target_ratio

        for index, frame in enumerate(frames):
            try:
                decision, overlay = scan_service.analyse_frame(runtime, frame)
            except Exception as exc:  # a bad frame must not stop the replay
                report["errors"].append({"frame": index, "error": str(exc)})
                logger.warning("replay frame %s failed: %s", index, exc)
                continue

            report["states"].append(
                {
                    "frame": index,
                    "state": decision.state.value,
                    "prompt": decision.prompt,
                    "hints": decision.hints,
                    "quality": overlay["metrics"].get("quality", 0.0),
                }
            )

            if decision.action == DecisionAction.PROCESS_BEST:
                if not persist:
                    runtime.machine.notify_warning("dry_run")
                    runtime.reset_candidates()
                    continue
                try:
                    outcome = scan_service.process_best_candidate(db, runtime, session)
                except Exception as exc:
                    report["errors"].append({"frame": index, "error": f"processing: {exc}"})
                    runtime.machine.notify_warning("processing_error")
                    continue
                report["outcomes"].append(outcome.to_dict() | {"thumbnail": None})
                if outcome.success:
                    runtime.machine.notify_success()
                else:
                    runtime.machine.notify_warning(outcome.reason or "warning")

        report["counters"] = dict(runtime.counters)
        report["finalState"] = runtime.machine.state.value
        report["transitions"] = [t.to_dict() for t in runtime.machine.transitions]
        scan_service.drop_runtime(session.id)

    return report


def replay_directory(path: Path, **kwargs) -> dict:
    """Replay an ordered directory of image files."""
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"frames directory not found: {path}")
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        raise ValueError(f"no images in {path}")

    frames: list[np.ndarray] = []
    for file in files:
        image = cv2.imread(str(file), cv2.IMREAD_COLOR)
        if image is not None:
            frames.append(image)

    background = None
    candidate = path / "background.png"
    if candidate.exists():
        background = cv2.imread(str(candidate), cv2.IMREAD_COLOR)

    report = replay_frames(frames, background=background, **kwargs)
    report["source"] = str(path)
    report["files"] = [f.name for f in files]
    return report


def build_scenario_sequence(
    scenario: str,
    payload: dict | None = None,
    *,
    lead_empty: int = 3,
    hold: int = 14,
    trail_empty: int = 5,
) -> tuple[list[np.ndarray], np.ndarray]:
    """Build a realistic frame sequence: empty → sheet → empty."""
    from app.cv.synthetic import DEFAULT_PAYLOAD

    payload = payload or DEFAULT_PAYLOAD
    background = empty_scene()

    # "empty" means: the work area never receives a sheet at all.
    if scenario == "empty":
        return [empty_scene(seed=i) for i in range(lead_empty + hold + trail_empty)], background

    sheet_kwargs: dict = {}
    qr_payload: dict | None = payload
    options = SceneOptions()

    if scenario == "blank_answer":
        sheet_kwargs["handwriting"] = False
    elif scenario == "strikethrough":
        sheet_kwargs["strikethrough"] = True
    elif scenario == "hard_handwriting":
        sheet_kwargs["seed"] = 99
    elif scenario == "unreadable_qr":
        qr_payload = None
    elif scenario == "hand_over_answer":
        options.hand_over_answer = True
    elif scenario == "blurred":
        options.blur = 3.5
    elif scenario == "wrong_orientation":
        options.rotation_deg = 180.0
    elif scenario == "glare":
        options.glare = 0.8
    elif scenario == "tilted":
        options.tilt = 0.2

    sheet = render_sheet(qr_payload, **sheet_kwargs)

    frames: list[np.ndarray] = [empty_scene(seed=i) for i in range(lead_empty)]

    if scenario == "moving":
        for i in range(hold):
            moving = SceneOptions(**options.__dict__)
            moving.offset = (i * 22 - 80, i * 7)
            moving.motion_blur = 19
            frames.append(render_scene(sheet, moving, seed=100 + i))
    else:
        for i in range(hold):
            frames.append(render_scene(sheet, options, seed=100))

    frames.extend(empty_scene(seed=200 + i) for i in range(trail_empty))
    return frames, background


def replay_scenario(scenario: str, repeats: int = 1, payload: dict | None = None, **kwargs) -> dict:
    """Replay one of the built-in synthetic scenarios."""
    frames, background = build_scenario_sequence(scenario, payload)
    if repeats > 1:
        frames = frames * repeats
    report = replay_frames(frames, background=background, target_ratio=1000 / 620, **kwargs)
    report["scenario"] = scenario
    return report
