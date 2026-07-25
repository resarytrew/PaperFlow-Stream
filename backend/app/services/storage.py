"""Local file storage for sheet images and exports."""

from __future__ import annotations

import base64
import binascii
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

_DATA_URL_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)


class StorageError(RuntimeError):
    pass


def decode_data_url(data: str) -> np.ndarray:
    """Decode a base64 (optionally data-URL prefixed) image into BGR."""
    if not data:
        raise StorageError("empty image payload")
    payload = _DATA_URL_RE.sub("", data.strip())
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise StorageError(f"invalid base64 image: {exc}") from exc
    array = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise StorageError("could not decode image bytes")
    return image


def encode_data_url(image: np.ndarray, quality: int = 85, fmt: str = ".jpg") -> str:
    params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)] if fmt in (".jpg", ".jpeg") else []
    ok, buffer = cv2.imencode(fmt, image, params)
    if not ok:
        raise StorageError("failed to encode image")
    mime = "jpeg" if fmt in (".jpg", ".jpeg") else fmt.lstrip(".")
    return f"data:image/{mime};base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


class SheetStorage:
    """Filesystem layout: ``storage/sheets/<session_id>/<sheet_uid>/<kind>.jpg``."""

    def __init__(self, root: Path | None = None) -> None:
        settings = get_settings()
        self.root = root or settings.sheets_dir
        # The storage base is the parent "storage/" directory: sheet images,
        # calibration references and diagnostics all live under it and DB rows
        # store paths relative to this base.
        self.base = self.root.parent if root is not None else settings.storage_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: int) -> Path:
        path = self.root / f"session-{session_id:05d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sheet_dir(self, session_id: int, slug: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", slug)[:80] or "sheet"
        path = self.session_dir(session_id) / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_image(self, image: np.ndarray, path: Path, quality: int = 92) -> str:
        """Write an image, returning a path relative to the storage root."""
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        params: list[int] = []
        if suffix in (".jpg", ".jpeg"):
            params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        elif suffix == ".png":
            params = [int(cv2.IMWRITE_PNG_COMPRESSION), 4]
        if not cv2.imwrite(str(path), image, params):
            raise StorageError(f"failed to write {path}")
        return self.relative(path)

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.base).as_posix()
        except ValueError:
            return path.as_posix()

    def absolute(self, relative_path: str) -> Path:
        candidate = (self.base / relative_path).resolve()
        storage_root = self.base.resolve()
        if not str(candidate).startswith(str(storage_root)):
            raise StorageError("path traversal detected")
        return candidate

    def load(self, relative_path: str) -> np.ndarray:
        path = self.absolute(relative_path)
        if not path.exists():
            raise StorageError(f"file not found: {relative_path}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise StorageError(f"could not read image: {relative_path}")
        return image

    def delete_session(self, session_id: int) -> None:
        path = self.root / f"session-{session_id:05d}"
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def delete_paths(self, relative_paths: list[str | None]) -> None:
        for rel in relative_paths:
            if not rel:
                continue
            try:
                path = self.absolute(rel)
            except StorageError:
                continue
            if path.exists() and path.is_file():
                try:
                    path.unlink()
                except OSError as exc:  # pragma: no cover
                    logger.warning("could not delete %s: %s", path, exc)

    def apply_retention(self, days: int) -> int:
        """Delete stored images older than ``days``. Returns files removed."""
        if days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:  # pragma: no cover
                continue
            if mtime < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:  # pragma: no cover
                    pass
        return removed

    def disk_usage_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())


_storage: SheetStorage | None = None


def get_storage() -> SheetStorage:
    global _storage
    if _storage is None:
        _storage = SheetStorage()
    return _storage
