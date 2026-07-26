"""Regression tests for secrets, backups and restart recovery."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import app.db as app_db
from app.models import RecognitionResult, RecognitionStatus, ScanSession, ScannedSheet
from app.services.ocr_queue import ocr_queue
from app.services.ocr_recovery import recover_interrupted_ocr_jobs
from app.services.settings_service import load_config


def test_settings_never_return_api_key_and_blank_patch_preserves_it(api_client):
    secret = "test-secret-must-not-leak"
    response = api_client.patch(
        "/api/settings",
        json={"vision_ocr": {"api_key": secret, "folder_id": "folder-1"}},
    )
    assert response.status_code == 200, response.text
    public = response.json()["config"]["vision_ocr"]
    assert public["api_key"] == ""
    assert public["api_key_configured"] is True
    assert secret not in response.text

    response = api_client.patch(
        "/api/settings",
        json={"vision_ocr": {"api_key": "", "model": "page"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"]["vision_ocr"]["api_key_configured"] is True

    with app_db.SessionLocal() as db:
        internal = load_config(db, use_cache=False)
        assert internal.vision_ocr.api_key == secret

    response = api_client.get("/api/settings")
    assert response.status_code == 200
    assert secret not in response.text


def test_backup_is_consistent_and_manifest_is_redacted(api_client):
    secret = "backup-secret-must-not-leak"
    response = api_client.patch("/api/settings", json={"vision_ocr": {"api_key": secret}})
    assert response.status_code == 200

    response = api_client.get("/api/maintenance/backup")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.content[:2] == b"PK"
    assert secret.encode() not in response.content

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"paperflow.db", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["config"]["vision_ocr"]["api_key"] == ""
        assert manifest["config"]["vision_ocr"]["api_key_configured"] is True

        database = io.BytesIO(archive.read("paperflow.db"))
        # sqlite3 cannot open BytesIO directly; materialise through deserialize
        connection = sqlite3.connect(":memory:")
        connection.deserialize(database.getvalue())
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        connection.close()
        assert "scan_sessions" in tables
        assert "app_settings" in tables


def test_interrupted_ocr_jobs_are_requeued(api_client, monkeypatch):
    with app_db.SessionLocal() as db:
        session = ScanSession(title="recovery")
        db.add(session)
        db.flush()
        sheet = ScannedSheet(
            session_id=session.id,
            normalized_image_path="sheets/recovery.jpg",
            sequence_number=1,
        )
        db.add(sheet)
        db.flush()
        db.add(
            RecognitionResult(
                scanned_sheet_id=sheet.id,
                status=RecognitionStatus.recognizing.value,
            )
        )
        db.commit()
        sheet_id = sheet.id

    queued: list[int] = []
    monkeypatch.setattr(ocr_queue, "enqueue", lambda value: queued.append(value) or True)

    assert recover_interrupted_ocr_jobs() == 1
    assert queued == [sheet_id]
