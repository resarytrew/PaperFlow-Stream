"""Maintenance endpoints: safe local backup and housekeeping."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

import app.db as app_db
from app.api.deps import Config
from app.config import get_settings

router = APIRouter(tags=["maintenance"])


def _sqlite_path() -> Path:
    url = app_db.engine.url
    if url.get_backend_name() != "sqlite":
        raise HTTPException(status_code=501, detail="Резервное копирование сейчас поддерживает только SQLite")
    if not url.database or url.database == ":memory:":
        raise HTTPException(status_code=501, detail="Для временной базы резервная копия недоступна")
    return Path(url.database)


@router.get("/maintenance/backup")
def download_backup(config: Config) -> Response:
    """Create a consistent SQLite snapshot and return it as a ZIP archive.

    SQLite's backup API is used instead of copying a live WAL database. Runtime
    configuration in the manifest is redacted, so credentials are not leaked.
    Stored sheet images are intentionally excluded to keep this operation fast;
    the archive is a metadata/database recovery point.
    """
    settings = get_settings()
    source_path = _sqlite_path().resolve()
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Файл базы данных не найден")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="paperflow-backup-") as temp_dir:
        snapshot_path = Path(temp_dir) / "paperflow.db"
        source = None
        target = None
        try:
            source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
            target = sqlite3.connect(snapshot_path)
            with target:
                source.backup(target)
        except sqlite3.Error as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось создать снимок базы: {exc}") from exc
        finally:
            if source is not None:
                source.close()
            if target is not None:
                target.close()

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
