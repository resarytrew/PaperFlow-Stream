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


def _redact_snapshot_secrets(connection: sqlite3.Connection) -> None:
    """Remove credentials from the backup copy without touching live data."""
    try:
        rows = connection.execute("select id, payload from app_settings").fetchall()
    except sqlite3.OperationalError:
        return

    for row_id, raw_payload in rows:
        if not raw_payload:
            continue
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        vision = payload.get("vision_ocr")
        if isinstance(vision, dict) and "api_key" in vision:
            vision = dict(vision)
            vision.pop("api_key", None)
            payload = dict(payload)
            payload["vision_ocr"] = vision
            connection.execute(
                "update app_settings set payload = ? where id = ?",
                (json.dumps(payload, ensure_ascii=False), row_id),
            )
    connection.commit()


@router.get("/maintenance/backup")
def download_backup(config: Config) -> Response:
    """Create a consistent, credential-free SQLite snapshot as a ZIP archive."""
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
            _redact_snapshot_secrets(target)
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

    filename = f"chistovik_backup_{stamp}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
