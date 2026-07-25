"""Sheet normalisation: perspective, orientation, lighting, enhancement."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.config import NormalizationConfig
from app.cv.geometry import Quad, quad_aspect_ratio, warp_quad
from app.cv.qr import read_qr


@dataclass
class NormalizedSheet:
    color: np.ndarray
    enhanced: np.ndarray
    thumbnail: np.ndarray
    rotation_applied: int = 0
    orientation_source: str = "aspect"


def rectify(image: np.ndarray, quad: Quad, config: NormalizationConfig, target_ratio: float | None = None) -> np.ndarray:
    """Perspective-correct the sheet to a canonical size."""
    measured = quad_aspect_ratio(quad)
    ratio = target_ratio if target_ratio and target_ratio > 0 else (measured if measured > 0 else 1.0)

    if ratio >= 1.0:  # landscape sheet
        width = config.output_width
        height = max(int(round(width / ratio)), 16)
    else:
        height = config.output_height
        width = max(int(round(height * ratio)), 16)
    return warp_quad(image, quad, size=(width, height))


def _rotate(image: np.ndarray, degrees: int) -> np.ndarray:
    if degrees % 360 == 0:
        return image
    if degrees % 360 == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if degrees % 360 == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)


def detect_orientation(image: np.ndarray, expected_ratio: float | None = None) -> tuple[int, str]:
    """Find the rotation (0/90/180/270) that puts the QR in the top-left corner.

    Falls back to aspect-ratio matching when no QR is readable.
    """
    h, w = image.shape[:2]
    candidates = [0, 90, 180, 270]

    # 1. QR based – the standard form has the QR in the top-left quadrant.
    for degrees in candidates:
        rotated = _rotate(image, degrees)
        rh, rw = rotated.shape[:2]
        corner = rotated[0 : max(int(rh * 0.45), 8), 0 : max(int(rw * 0.45), 8)]
        if read_qr(corner, backends=("opencv",), enhance=True).success:
            return degrees, "qr"

    # 2. Aspect based.
    if expected_ratio and expected_ratio > 0:
        current = w / max(h, 1)
        rotated_ratio = h / max(w, 1)
        if abs(rotated_ratio - expected_ratio) + 1e-6 < abs(current - expected_ratio):
            return 90, "aspect"
    return 0, "none"


def remove_shadows(image: np.ndarray, kernel_size: int = 41) -> np.ndarray:
    """Divide by a heavily blurred background to flatten illumination."""
    kernel_size = max(3, kernel_size | 1)
    if image.ndim == 2:
        background = cv2.medianBlur(image, kernel_size)
        background = np.where(background == 0, 1, background).astype(np.float32)
        normalized = np.clip(image.astype(np.float32) / background * 200.0, 0, 255)
        return normalized.astype(np.uint8)

    planes = cv2.split(image)
    result = []
    for plane in planes:
        dilated = cv2.dilate(plane, np.ones((7, 7), np.uint8))
        background = cv2.medianBlur(dilated, kernel_size)
        background = np.where(background == 0, 1, background).astype(np.float32)
        norm = np.clip(plane.astype(np.float32) / background * 210.0, 0, 255)
        result.append(norm.astype(np.uint8))
    return cv2.merge(result)


def remove_color_cast(image: np.ndarray) -> np.ndarray:
    """Simple gray-world white balance."""
    if image.ndim != 3:
        return image
    img = image.astype(np.float32)
    means = [max(float(img[:, :, c].mean()), 1e-3) for c in range(3)]
    gray_mean = float(np.mean(means))
    for c in range(3):
        img[:, :, c] = np.clip(img[:, :, c] * (gray_mean / means[c]), 0, 255)
    return img.astype(np.uint8)


def balance_brightness(image: np.ndarray, clip: float = 2.0) -> np.ndarray:
    """CLAHE on the L channel."""
    if image.ndim == 2:
        return cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(image)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def enhance_document(image: np.ndarray, config: NormalizationConfig) -> np.ndarray:
    """High-contrast black & white rendition suited for archiving/printing."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    flat = remove_shadows(gray, config.shadow_kernel)
    flat = cv2.createCLAHE(clipLimit=config.clahe_clip, tileGridSize=(8, 8)).apply(flat)
    denoised = cv2.bilateralFilter(flat, 5, 45, 45)
    block = max(3, config.adaptive_block | 1)
    binary = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, config.adaptive_c
    )
    # remove salt noise without eating thin strokes
    binary = cv2.medianBlur(binary, 3)
    return binary


def normalize_sheet(
    frame: np.ndarray,
    quad: Quad,
    config: NormalizationConfig,
    *,
    target_ratio: float | None = None,
    auto_orient: bool = True,
) -> NormalizedSheet:
    """Full normalisation chain – returns colour, enhanced and thumbnail images."""
    color = rectify(frame, quad, config, target_ratio)

    rotation = 0
    source = "none"
    if auto_orient:
        rotation, source = detect_orientation(color, target_ratio)
        if rotation:
            color = _rotate(color, rotation)

    color = remove_color_cast(color)
    color = balance_brightness(color, config.clahe_clip)
    enhanced = enhance_document(color, config)

    thumb_w = config.thumbnail_width
    scale = thumb_w / max(color.shape[1], 1)
    thumbnail = cv2.resize(color, (thumb_w, max(int(color.shape[0] * scale), 1)), interpolation=cv2.INTER_AREA)

    return NormalizedSheet(
        color=color,
        enhanced=enhanced,
        thumbnail=thumbnail,
        rotation_applied=rotation,
        orientation_source=source,
    )
