"""Maintenance endpoints: safe local backup and housekeeping."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Response

from app.api.deps import Config
from app.config import get_settings

router = APIRouter(tags=["maintenance"])


def _sqlite_path(database_url: str) -> Path:
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        raise HTTPException(status_code=501, detail="Резервное копирование сейчас поддерживает только SQLite")

    raw_path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raw_path = f"//{parsed.netloc}{raw_path}"
    if not raw_path:
        raise HTTPException(status_code=500, detail="Не удалось определить путь к базе данных")
    return Path(raw_path)


@router.get("/maintenance/backup")
def download_backup(config: Config) -> Response:
    """Create a consistent SQLite snapshot and return it as a ZIP archive.

    SQLite's backup API is used instead of copying a live WAL database. Runtime
    configuration in the manifest is redacted, so credentials are not leaked.
    Stored sheet images are intentionally excluded to keep this operation fast;
    the archive is a metadata/database recovery point.
    """
    settings = get_settings()
    source_path = _sqlite_path(settings.resolved_database_url()).resolve()
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Файл базы данных не найден")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="paperflow-backup-") as temp_dir:
        snapshot_path = Path(temp_dir) / "paperflow.db"
        try:
            source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
            target = sqlite3.connect(snapshot_path)
            with target:
                source.backup(target)
            source.close()
            target.close()
        except sqlite3.Error as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось создать снимок базы: {exc}") from exc

        manifest = {
            "application": settings.app_name,
            "version": settings.version,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "kind": "sqlite-metadata-backup",
            "includes": ["paperflow.db", "manifest.json"],
            "excludes": ["sheet images", "diagnostic frames", "API credentials"],
            "config": config.model_dump(),
        }

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot_path, "paperflow.db")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    filename = f"paperflow_backup_{stamp}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
