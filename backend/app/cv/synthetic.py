"""Synthetic scene generator.

Used by tests, the E2E replay harness and the "no camera" demo mode to produce
deterministic frames covering the 12 scenarios of section 11.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass

import cv2
import numpy as np

try:  # pillow + qrcode are required for form generation anyway
    import qrcode
    from PIL import Image
except Exception:  # pragma: no cover
    qrcode = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

SHEET_W = 1000
SHEET_H = 620


def make_qr_image(payload: dict | str, size: int = 220) -> np.ndarray:
    """Render a QR code as a BGR image."""
    if qrcode is None:  # pragma: no cover
        raise RuntimeError("qrcode package is required")
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    data = np.frombuffer(buffer.getvalue(), dtype=np.uint8)
    decoded = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return cv2.resize(decoded, (size, size), interpolation=cv2.INTER_NEAREST)


def _draw_handwriting(canvas: np.ndarray, x: int, y: int, width: int, seed: int, strokes: int = 26) -> None:
    """Draw wobbly pen-like strokes that look like cursive to the CV pipeline."""
    rng = np.random.default_rng(seed)
    cursor = x
    while cursor < x + width - 20:
        word_len = int(rng.integers(45, 130))
        points = []
        steps = max(word_len // 6, 4)
        for i in range(steps):
            px = cursor + i * (word_len / steps)
            py = y + math.sin(i * 1.3 + seed) * 6 + rng.normal(0, 2.2)
            points.append((int(px), int(py)))
        for i in range(len(points) - 1):
            cv2.line(canvas, points[i], points[i + 1], (35, 35, 90), 2, cv2.LINE_AA)
        if rng.random() < 0.35 and points:
            mid = points[len(points) // 2]
            cv2.line(canvas, (mid[0], mid[1] - 12), (mid[0] + 3, mid[1] + 4), (35, 35, 90), 2, cv2.LINE_AA)
        cursor += word_len + int(rng.integers(14, 30))
        if cursor > x + width - 40:
            break


def render_sheet(
    payload: dict | str | None,
    *,
    answer_lines: int = 3,
    handwriting: bool = True,
    seed: int = 7,
    strikethrough: bool = False,
    header: str = "9Б • Ученик 17 • Задание 04",
) -> np.ndarray:
    """Render a standard Чистовик form as a flat BGR image."""
    sheet = np.full((SHEET_H, SHEET_W, 3), 250, np.uint8)
    cv2.rectangle(sheet, (6, 6), (SHEET_W - 7, SHEET_H - 7), (170, 170, 170), 2)

    if payload is not None:
        qr = make_qr_image(payload, size=170)
        sheet[26 : 26 + 170, 26 : 26 + 170] = qr

    cv2.putText(sheet, header, (220, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2, cv2.LINE_AA)
    cv2.line(sheet, (20, 215), (SHEET_W - 20, 215), (170, 170, 170), 2)
    cv2.putText(sheet, "Otvet:", (40, 265), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (90, 90, 90), 2, cv2.LINE_AA)

    top = 320
    spacing = 78
    for i in range(answer_lines):
        y = top + i * spacing
        cv2.line(sheet, (50, y), (SHEET_W - 50, y), (185, 185, 185), 2)
        if handwriting:
            _draw_handwriting(sheet, 60, y - 16, SHEET_W - 140, seed + i * 13)
        if strikethrough and handwriting and i == 1:
            cv2.line(sheet, (70, y - 18), (SHEET_W - 200, y - 14), (40, 40, 100), 3, cv2.LINE_AA)
    return sheet


@dataclass
class SceneOptions:
    """Parameters describing one synthetic camera frame."""

    frame_size: tuple[int, int] = (1280, 720)
    sheet_scale: float = 0.72
    offset: tuple[int, int] = (0, 0)
    rotation_deg: float = 0.0
    tilt: float = 0.0  # 0..0.4 perspective skew
    blur: float = 0.0  # gaussian sigma
    motion_blur: int = 0  # kernel size in px
    glare: float = 0.0  # 0..1 intensity of a specular highlight
    brightness: float = 1.0
    hand: bool = False
    hand_over_answer: bool = False
    hand_over_qr: bool = False
    noise: float = 2.0
    background_value: int = 96
    include_sheet: bool = True


def _sheet_quad(opts: SceneOptions, sheet_shape: tuple[int, int]) -> np.ndarray:
    fw, fh = opts.frame_size
    sh, sw = sheet_shape[:2]
    scale = min(fw / sw, fh / sh) * opts.sheet_scale
    w, h = sw * scale, sh * scale
    cx, cy = fw / 2 + opts.offset[0], fh / 2 + opts.offset[1]

    corners = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]], np.float32)

    if opts.tilt:
        # squeeze the top edge to fake a camera looking from an angle
        corners[0, 0] *= 1.0 - opts.tilt
        corners[1, 0] *= 1.0 - opts.tilt
        corners[0, 1] *= 1.0 - opts.tilt * 0.25
        corners[1, 1] *= 1.0 - opts.tilt * 0.25

    if opts.rotation_deg:
        a = math.radians(opts.rotation_deg)
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]], np.float32)
        corners = corners @ rot.T

    corners[:, 0] += cx
    corners[:, 1] += cy
    return corners.astype(np.float32)


def _draw_hand(frame: np.ndarray, quad: np.ndarray, over_answer: bool, over_qr: bool) -> None:
    """Draw a skin-coloured blob entering the sheet."""
    tl, tr, br, bl = quad
    if over_qr:
        anchor = tl * 0.75 + br * 0.25
    elif over_answer:
        anchor = (bl + br) / 2 * 0.6 + (tl + tr) / 2 * 0.4
    else:
        anchor = bl * 0.8 + br * 0.2

    cx, cy = int(anchor[0]), int(anchor[1])
    skin = (120, 158, 205)  # BGR skin tone
    cv2.ellipse(frame, (cx, cy + 40), (110, 78), 12, 0, 360, skin, -1, cv2.LINE_AA)
    for i, angle in enumerate((-55, -25, 5, 32)):
        rad = math.radians(angle)
        fx = int(cx + math.cos(rad) * 130)
        fy = int(cy + math.sin(rad) * 130) + 10
        cv2.line(frame, (cx, cy + 20), (fx, fy), skin, 34, cv2.LINE_AA)
        cv2.circle(frame, (fx, fy), 17, skin, -1, cv2.LINE_AA)
    cv2.ellipse(frame, (cx - 20, cy + 120), (86, 110), 0, 0, 360, skin, -1, cv2.LINE_AA)


def render_scene(sheet: np.ndarray, opts: SceneOptions, seed: int = 0) -> np.ndarray:
    """Compose a full camera frame containing (optionally) the sheet."""
    fw, fh = opts.frame_size
    rng = np.random.default_rng(seed)

    # desk background with a subtle gradient + texture
    frame = np.full((fh, fw, 3), opts.background_value, np.uint8)
    gradient = np.linspace(-16, 16, fw, dtype=np.float32)[None, :, None]
    frame = np.clip(frame.astype(np.float32) + gradient, 0, 255).astype(np.uint8)
    texture = rng.normal(0, 3.5, (fh, fw, 3))
    frame = np.clip(frame.astype(np.float32) + texture, 0, 255).astype(np.uint8)

    quad = _sheet_quad(opts, sheet.shape)

    if opts.include_sheet:
        sh, sw = sheet.shape[:2]
        src = np.array([[0, 0], [sw - 1, 0], [sw - 1, sh - 1], [0, sh - 1]], np.float32)
        matrix = cv2.getPerspectiveTransform(src, quad)
        warped = cv2.warpPerspective(sheet, matrix, (fw, fh), flags=cv2.INTER_CUBIC)
        mask = cv2.warpPerspective(np.full((sh, sw), 255, np.uint8), matrix, (fw, fh), flags=cv2.INTER_NEAREST)

        # soft drop shadow so the sheet separates from the desk
        shadow = cv2.GaussianBlur(mask, (61, 61), 0).astype(np.float32) / 255.0
        shadow_shift = np.roll(np.roll(shadow, 12, axis=0), 8, axis=1)[:, :, None]
        frame = np.clip(frame.astype(np.float32) * (1.0 - shadow_shift * 0.35), 0, 255).astype(np.uint8)
        frame[mask > 0] = warped[mask > 0]

        if opts.hand or opts.hand_over_answer or opts.hand_over_qr:
            _draw_hand(frame, quad, opts.hand_over_answer, opts.hand_over_qr)

    if opts.glare > 0:
        overlay = np.zeros((fh, fw), np.float32)
        gx, gy = int(fw * 0.42), int(fh * 0.38)
        cv2.ellipse(overlay, (gx, gy), (int(fw * 0.14), int(fh * 0.10)), 25, 0, 360, 1.0, -1)
        overlay = cv2.GaussianBlur(overlay, (121, 121), 0)
        overlay = overlay / max(overlay.max(), 1e-6) * opts.glare
        frame = np.clip(frame.astype(np.float32) + overlay[:, :, None] * 255.0, 0, 255).astype(np.uint8)

    if opts.brightness != 1.0:
        frame = np.clip(frame.astype(np.float32) * opts.brightness, 0, 255).astype(np.uint8)

    if opts.blur > 0:
        k = int(opts.blur * 4) | 1
        frame = cv2.GaussianBlur(frame, (k, k), opts.blur)

    if opts.motion_blur > 1:
        k = int(opts.motion_blur) | 1
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2, :] = 1.0 / k
        frame = cv2.filter2D(frame, -1, kernel)

    if opts.noise > 0:
        frame = np.clip(frame.astype(np.float32) + rng.normal(0, opts.noise, frame.shape), 0, 255).astype(np.uint8)

    return frame


def empty_scene(opts: SceneOptions | None = None, seed: int = 0) -> np.ndarray:
    options = opts or SceneOptions()
    options = SceneOptions(**{**options.__dict__, "include_sheet": False})
    return render_scene(np.zeros((10, 10, 3), np.uint8), options, seed)


DEFAULT_PAYLOAD = {
    "version": 1,
    "studentId": "9B-17",
    "classId": "9B",
    "taskId": "history-09-04",
    "sheetId": "9B-17-history-09-04",
}


def scenario_frames(name: str, count: int = 6, payload: dict | None = None) -> list[np.ndarray]:
    """Produce a short frame sequence for one of the 12 test scenarios."""
    payload = payload or DEFAULT_PAYLOAD
    base = SceneOptions()
    frames: list[np.ndarray] = []

    if name == "empty":
        return [empty_scene(base, seed=i) for i in range(count)]

    sheet_kwargs: dict = {}
    if name == "blank_answer":
        sheet_kwargs["handwriting"] = False
    if name == "strikethrough":
        sheet_kwargs["strikethrough"] = True
    if name == "hard_handwriting":
        sheet_kwargs["seed"] = 99

    qr_payload: dict | str | None = payload
    if name == "unreadable_qr":
        qr_payload = None

    sheet = render_sheet(qr_payload, **sheet_kwargs)

    for i in range(count):
        opts = SceneOptions(**base.__dict__)
        if name == "hand_over_answer":
            opts.hand_over_answer = True
        elif name == "blurred":
            opts.blur = 3.5
        elif name == "moving":
            opts.offset = (i * 26 - 60, i * 9)
            opts.motion_blur = 21
        elif name == "wrong_orientation":
            opts.rotation_deg = 180.0
        elif name == "tilted":
            opts.tilt = 0.22
        elif name == "glare":
            opts.glare = 0.75
        elif name == "unreadable_qr":
            opts.blur = 0.6
        frames.append(render_scene(sheet, opts, seed=i))
    return frames


SCENARIOS = [
    "empty",
    "hand_over_answer",
    "blurred",
    "moving",
    "readable_qr",
    "unreadable_qr",
    "duplicate",
    "blank_answer",
    "clear_handwriting",
    "hard_handwriting",
    "strikethrough",
    "wrong_orientation",
]
