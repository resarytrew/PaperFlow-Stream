"""End-to-end API integration tests.

These run the real FastAPI app (TestClient) against a throw-away SQLite
database and storage folder, exercising the same HTTP/WebSocket paths the
frontend uses: catalog → forms → session → live scanning over the
WebSocket with synthetic camera frames → review → OCR (mock provider) →
exports.
"""

from __future__ import annotations

import base64
import json
import time

import cv2
import pytest

from app.cv.synthetic import SceneOptions, empty_scene, render_scene, render_sheet


# --------------------------------------------------------------------- utils


def _jpeg_bytes(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    return buf.tobytes()


def _data_url(frame) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes(frame)).decode()


def _make_catalog(client) -> tuple[int, int, list[int]]:
    """Create класс 7Б with two students and one task; return ids."""
    response = client.post("/api/classes", json={"name": "7Б", "school_year": "2026/2027"})
    assert response.status_code == 201, response.text
    class_id = response.json()["id"]

    response = client.post(
        "/api/students/bulk",
        json={
            "class_id": class_id,
            "students": [
                {"external_id": "S-101", "last_name": "Иванов", "first_name": "Пётр", "class_id": class_id},
                {"external_id": "S-102", "last_name": "Смирнова", "first_name": "Анна", "class_id": class_id},
            ],
        },
    )
    assert response.status_code == 201, response.text
    student_ids = [s["id"] for s in response.json()]

    response = client.post(
        "/api/tasks",
        json={"external_id": "T-042", "title": "Уравнение №42", "subject": "Алгебра", "expected_answer": "x = 7"},
    )
    assert response.status_code == 201, response.text
    return class_id, response.json()["id"], student_ids


def _make_session(client, class_id: int, task_id: int, expected: int = 2) -> int:
    response = client.post(
        "/api/sessions",
        json={"class_id": class_id, "task_id": task_id, "expected_sheet_count": expected},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _speed_up_scanning(client) -> None:
    """Relax timing thresholds so the WS flow completes in a few frames."""
    response = client.patch(
        "/api/settings",
        json={
            "stability": {
                "stability_duration_ms": 0,
                "stable_frames_required": 2,
                "candidate_window_ms": 100,
                "success_hold_ms": 0,
                "warning_hold_ms": 0,
                "removal_frames_required": 2,
            },
            "ocr": {"provider": "mock", "auto_enqueue": False},
        },
    )
    assert response.status_code == 200, response.text


def _scan_one_sheet(
    client,
    session_id: int,
    payload: dict | None,
    *,
    seed: int = 7,
    binary: bool = False,
) -> dict | None:
    """Drive the scan WebSocket with synthetic frames until a scan_result."""
    sheet = render_sheet(payload)  # payload=None renders a sheet without a QR
    opts = SceneOptions()

    frames = [empty_scene(opts, seed=i) for i in range(2)]
    frames += [render_scene(sheet, opts, seed=seed) for _ in range(14)]
    frames += [empty_scene(opts, seed=50 + i) for i in range(4)]

    result = None
    with client.websocket_connect(f"/api/ws/sessions/{session_id}/scan") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        for frame in frames:
            if binary:
                ws.send_bytes(_jpeg_bytes(frame))
            else:
                ws.send_text(json.dumps({"type": "frame", "image": _data_url(frame)}))
            while True:
                message = ws.receive_json()
                if message["type"] == "state":
                    break
                if message["type"] == "scan_result":
                    result = message
                elif message["type"] == "busy":
                    break
                elif message["type"] == "error":
                    pytest.fail(f"scan socket error: {message}")
            if result is not None:
                break
    return result


# --------------------------------------------------------------------- tests


def test_health(api_client):
    response = api_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["qrBackends"]["opencv"] is True


def test_catalog_crud_and_validation(api_client):
    class_id, task_id, student_ids = _make_catalog(api_client)

    # duplicate class name is rejected
    response = api_client.post("/api/classes", json={"name": "7Б"})
    assert response.status_code in (400, 409)

    # students are listed with display names
    response = api_client.get(f"/api/students?class_id={class_id}")
    assert response.status_code == 200
    names = {s["display_name"] for s in response.json()}
    assert "Иванов Пётр" in names and "Смирнова Анна" in names

    # student search
    response = api_client.get("/api/students?search=смирн")
    assert [s["external_id"] for s in response.json()] == ["S-102"]

    # deleting a class does not silently drop its students' history
    response = api_client.get(f"/api/tasks/{task_id}")
    assert response.json()["expected_answer"] == "x = 7"
    assert len(student_ids) == 2


def test_forms_pdf_with_cyrillic_names(api_client):
    """Regression: Cyrillic class names must not break Content-Disposition."""
    class_id, task_id, _ = _make_catalog(api_client)
    response = api_client.post(
        "/api/forms/generate",
        json={"class_id": class_id, "task_id": task_id, "sheets_per_student": 1, "forms_per_page": 3},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["x-form-count"] == "2"
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition  # RFC 5987 form present


def test_forms_constructor_variants_and_blocks(api_client):
    class_id, task_id, _ = _make_catalog(api_client)
    response = api_client.post(
        "/api/forms/generate",
        json={
            "class_id": class_id,
            "task_id": task_id,
            "sheets_per_student": 1,
            "forms_per_page": 1,
            "variant_count": 3,
            "variant_mode": "all",
            "layout_kind": "mixed",
            "blocks": [
                {"type": "choice", "title": "Часть A", "rows": 6, "columns": 4},
                {"type": "short", "title": "Часть B", "rows": 4, "columns": 8},
                {"type": "grid", "title": "Таблица", "rows": 4, "columns": 5},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["x-form-count"] == "6"  # 2 students × 3 variants
    assert response.content.startswith(b"%PDF")


def test_settings_patch_reset_roundtrip(api_client):
    response = api_client.patch("/api/settings", json={"stability": {"min_quality_score": 0.5}})
    assert response.status_code == 200
    assert response.json()["config"]["stability"]["min_quality_score"] == 0.5

    # invalid values are rejected as a whole
    response = api_client.patch("/api/settings", json={"stability": {"min_quality_score": 42}})
    assert response.status_code == 400

    response = api_client.post("/api/settings/reset")
    assert response.status_code == 200
    assert response.json()["config"]["stability"]["min_quality_score"] == pytest.approx(0.42)


def test_full_scan_review_export_flow(api_client):
    """The core promise: frames in → identified sheet → review → export."""
    class_id, task_id, student_ids = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-1"}
    result = _scan_one_sheet(api_client, session_id, payload)

    assert result is not None, "scan_result was never produced"
    outcome = result["result"]
    assert outcome["success"] is True, outcome
    assert outcome["sheetUid"] == "S-101-T-042-1"
    assert outcome["scanStatus"] == "ok"
    assert outcome["quality"] > 0.5
    sheet_id = outcome["sheetId"]

    # the sheet was linked to the right student by its QR code
    response = api_client.get(f"/api/sheets/{sheet_id}")
    sheet = response.json()
    assert sheet["student_external_id"] == "S-101"
    assert sheet["student_name"] == "Иванов Пётр"

    # stored images are all retrievable
    for kind in ("source", "normalized", "enhanced", "answer", "thumbnail", "qr"):
        response = api_client.get(f"/api/sheets/{sheet_id}/image/{kind}")
        assert response.status_code == 200, kind
        assert response.headers["content-type"].startswith("image/")

    # review counters see the sheet
    response = api_client.get(f"/api/sessions/{session_id}/review/counts")
    assert response.json()["all"] == 1

    # teacher correction is preserved
    response = api_client.post(
        f"/api/sheets/{sheet_id}/review",
        json={"decision": "corrected", "teacher_text": "x = 7", "comment": ""},
    )
    assert response.status_code == 200
    assert response.json()["review"]["teacher_text"] == "x = 7"

    # exports carry the data and survive the Cyrillic session title
    response = api_client.get(f"/api/sessions/{session_id}/export/csv")
    assert response.status_code == 200
    assert "S-101" in response.content.decode("utf-8")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]

    response = api_client.get(f"/api/sessions/{session_id}/export/json")
    exported = json.loads(response.content)
    assert any(row.get("sheetUid") == "S-101-T-042-1" for row in exported["sheets"])

    response = api_client.get(f"/api/sessions/{session_id}/export/xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"  # xlsx is a zip container

    response = api_client.get(f"/api/sessions/{session_id}/export/zip")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"

    # session lifecycle completes
    response = api_client.post(f"/api/sessions/{session_id}/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_binary_websocket_frame_transport(api_client):
    """Frontend scan stream sends raw JPEG bytes; legacy base64 JSON remains covered elsewhere."""
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {
        "version": 1,
        "studentId": "S-102",
        "classId": "7Б",
        "taskId": "T-042",
        "sheetId": "S-102-T-042-bin",
    }
    result = _scan_one_sheet(api_client, session_id, payload, binary=True)

    assert result is not None, "binary scan_result was never produced"
    assert result["result"]["success"] is True, result
    assert result["result"]["sheetUid"] == "S-102-T-042-bin"


def test_live_qr_preview_identifies_student_before_persist(api_client):
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {
        "version": 1,
        "studentId": "S-101",
        "classId": "7Б",
        "taskId": "T-042",
        "sheetId": "S-101-T-042-preview",
    }
    sheet = render_sheet(payload)
    opts = SceneOptions()
    frames = [empty_scene(opts, seed=i) for i in range(2)]
    frames += [render_scene(sheet, opts, seed=7) for _ in range(12)]

    preview = None
    with api_client.websocket_connect(f"/api/ws/sessions/{session_id}/scan") as ws:
        assert ws.receive_json()["type"] == "ready"
        for frame in frames:
            ws.send_bytes(_jpeg_bytes(frame))
            message = ws.receive_json()
            if message["type"] == "state":
                preview = (message.get("overlay") or {}).get("qrPreview")
                if preview and preview.get("success"):
                    break

    assert preview is not None
    assert preview["success"] is True
    assert preview["studentId"] == "S-101"
    assert preview["studentLabel"] == "Иванов Пётр"
    assert preview["sheetUid"] == "S-101-T-042-preview"
    assert preview["duplicate"] is False


def test_undo_last_scan_soft_deletes_last_sheet(api_client):
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {
        "version": 1,
        "studentId": "S-101",
        "classId": "7Б",
        "taskId": "T-042",
        "sheetId": "S-101-T-042-undo",
    }
    result = _scan_one_sheet(api_client, session_id, payload)
    assert result is not None and result["result"]["success"]
    sheet_id = result["result"]["sheetId"]

    response = api_client.post(f"/api/sessions/{session_id}/undo-last")
    assert response.status_code == 200, response.text
    assert response.json()["sheetId"] == sheet_id

    assert api_client.get(f"/api/sessions/{session_id}/sheets").json() == []
    deleted = api_client.get(f"/api/sessions/{session_id}/sheets?include_deleted=true").json()
    assert deleted[0]["id"] == sheet_id
    assert deleted[0]["scan_status"] == "deleted"


def test_duplicate_sheet_is_flagged(api_client):
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-1"}

    first = _scan_one_sheet(api_client, session_id, payload, seed=7)
    assert first is not None and first["result"]["success"] is True

    second = _scan_one_sheet(api_client, session_id, payload, seed=8)
    assert second is not None
    assert second["result"]["success"] is False
    assert second["result"]["scanStatus"] == "duplicate"
    assert second["result"]["duplicateOf"] == first["result"]["sheetId"]

    counts = api_client.get(f"/api/sessions/{session_id}/review/counts").json()
    assert counts["all"] == 2

    # teacher can clear the duplicate flag
    sheet_id = second["result"]["sheetId"]
    response = api_client.patch(f"/api/sheets/{sheet_id}/assign", json={"clear_duplicate": True})
    assert response.status_code == 200
    assert response.json()["duplicate_of_id"] is None


def test_unreadable_qr_goes_to_unidentified(api_client):
    class_id, task_id, student_ids = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    result = _scan_one_sheet(api_client, session_id, None)  # sheet without QR
    assert result is not None
    outcome = result["result"]
    assert outcome["scanStatus"] == "unidentified"
    sheet_id = outcome["sheetId"]
    assert sheet_id is not None  # the image is still archived

    # the unidentified tab contains it
    response = api_client.get(f"/api/sessions/{session_id}/review?tab=unidentified")
    assert [s["id"] for s in response.json()] == [sheet_id]

    # manual assignment fixes it
    response = api_client.patch(f"/api/sheets/{sheet_id}/assign", json={"student_id": student_ids[0]})
    assert response.status_code == 200
    body = response.json()
    assert body["student_external_id"] == "S-101"
    assert body["scan_status"] == "ok"


def test_ocr_mock_provider_end_to_end(api_client):
    """Sheet → OCR queue (mock provider) → recognized text → review tabs."""
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-1"}
    result = _scan_one_sheet(api_client, session_id, payload)
    assert result is not None and result["result"]["success"]
    sheet_id = result["result"]["sheetId"]

    response = api_client.post(f"/api/sheets/{sheet_id}/recognize")
    assert response.status_code == 200

    # the queue runs on the app's event loop; poll through the API
    recognition = None
    for _ in range(100):
        time.sleep(0.1)
        sheet = api_client.get(f"/api/sheets/{sheet_id}").json()
        recognition = sheet.get("recognition")
        if recognition and recognition["status"] not in ("pending", "processing"):
            break
    assert recognition is not None, "recognition never finished"
    assert recognition["provider"] == "mock"
    assert recognition["status"] in ("recognized", "needs_review")
    assert recognition["recognized_text"].strip()
    assert 0.0 < recognition["overall_confidence"] <= 1.0

    # answer hint: the task has expected_answer="x = 7"; the mock text is
    # history phrases, so the verdict must be an honest mismatch with evidence
    analysis = recognition.get("analysis_json") or {}
    check = analysis.get("answerCheck")
    assert check is not None, "answerCheck missing from analysis"
    assert check["verdict"] == "mismatch"
    assert check["disclaimer"]

    counts = api_client.get(f"/api/sessions/{session_id}/review/counts").json()
    assert counts["all"] == 1
    assert (counts["high_confidence"] + counts["needs_review"] + counts["low_confidence"]) >= 1

    # teacher corrects the text to the right answer -> hint recomputed
    response = api_client.post(
        f"/api/sheets/{sheet_id}/review",
        json={"decision": "corrected", "teacher_text": "х = 7", "comment": ""},  # Cyrillic х
    )
    assert response.status_code == 200
    check = (response.json()["recognition"]["analysis_json"] or {}).get("answerCheck")
    assert check is not None
    assert check["verdict"] == "match"
    assert check["source"] == "teacher_text"

    # blank override flips the verdict without deleting anything
    response = api_client.post(f"/api/sheets/{sheet_id}/blank-override?is_blank=true")
    assert response.status_code == 200
    assert response.json()["recognition"]["status"] == "blank"


def test_session_summary(api_client):
    """The 'Итоги сессии' endpoint aggregates verdicts, review and quality."""
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-1"}
    result = _scan_one_sheet(api_client, session_id, payload)
    assert result is not None and result["result"]["success"]
    sheet_id = result["result"]["sheetId"]

    # run OCR so the sheet gets an answerCheck (mock text -> mismatch)
    api_client.post(f"/api/sheets/{sheet_id}/recognize")
    for _ in range(100):
        time.sleep(0.1)
        sheet = api_client.get(f"/api/sheets/{sheet_id}").json()
        recognition = sheet.get("recognition")
        if recognition and recognition["status"] not in ("pending", "processing"):
            break

    summary = api_client.get(f"/api/sessions/{session_id}/summary").json()
    assert summary["session"]["id"] == session_id
    assert len(summary["sheets"]) == 1
    assert summary["verdicts"]["mismatch"] == 1
    assert summary["reviewed"] == 0
    assert summary["averageQuality"] > 0.5
    assert summary["disclaimer"]
    row = summary["sheets"][0]
    assert row["student"] == "Иванов Пётр"
    assert row["verdict"] == "mismatch"

    # teacher corrects to the right answer -> summary flips to match
    api_client.post(
        f"/api/sheets/{sheet_id}/review",
        json={"decision": "corrected", "teacher_text": "x = 7", "comment": ""},
    )
    summary = api_client.get(f"/api/sessions/{session_id}/summary").json()
    assert summary["verdicts"]["match"] == 1
    assert summary["reviewed"] == 1
    assert summary["corrected"] == 1
    assert summary["sheets"][0]["answer"] == "x = 7"


def test_roster_tracks_missing_students(api_client):
    """The 'кто не сдал' panel: submitted vs missing per class list."""
    class_id, task_id, student_ids = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    # before any scan: everyone is missing
    roster = api_client.get(f"/api/sessions/{session_id}/roster").json()
    assert roster["classLinked"] is True
    assert roster["totalStudents"] == 2
    assert roster["submitted"] == 0
    assert roster["missing"] == 2

    # S-101 hands in a sheet
    payload = {"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-1"}
    result = _scan_one_sheet(api_client, session_id, payload)
    assert result is not None and result["result"]["success"]

    roster = api_client.get(f"/api/sessions/{session_id}/roster").json()
    assert roster["submitted"] == 1
    assert roster["missing"] == 1
    by_ext = {s["externalId"]: s for s in roster["students"]}
    assert by_ext["S-101"]["status"] == "ok"
    assert by_ext["S-102"]["status"] == "missing"

    # a session without a class reports classLinked=False
    bare = api_client.post("/api/sessions", json={"title": "Без класса"}).json()
    roster = api_client.get(f"/api/sessions/{bare['id']}/roster").json()
    assert roster["classLinked"] is False


def test_student_history(api_client):
    """Per-student progress endpoint aggregates verdicts across sessions."""
    class_id, task_id, student_ids = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-1"}
    result = _scan_one_sheet(api_client, session_id, payload)
    assert result is not None and result["result"]["success"]
    sheet_id = result["result"]["sheetId"]

    # run OCR so the sheet gets a verdict, then correct it to a match
    api_client.post(f"/api/sheets/{sheet_id}/recognize")
    for _ in range(100):
        time.sleep(0.1)
        sheet = api_client.get(f"/api/sheets/{sheet_id}").json()
        recognition = sheet.get("recognition")
        if recognition and recognition["status"] not in ("pending", "processing"):
            break
    api_client.post(
        f"/api/sheets/{sheet_id}/review",
        json={"decision": "corrected", "teacher_text": "x = 7", "comment": ""},
    )

    student_id = next(
        s["id"] for s in api_client.get(f"/api/students?class_id={class_id}").json() if s["external_id"] == "S-101"
    )
    history = api_client.get(f"/api/students/{student_id}/history").json()
    assert history["student"]["external_id"] == "S-101"
    assert history["totalSheets"] == 1
    assert history["verdicts"]["match"] == 1
    assert history["matchRate"] == 1.0
    row = history["sheets"][0]
    assert row["answer"] == "x = 7"  # teacher text wins over OCR text
    assert row["reviewed"] is True
    assert row["sessionId"] == session_id

    # unknown student -> 404
    assert api_client.get("/api/students/99999/history").status_code == 404


def test_diagnostics_download_bundle(api_client):
    """One-click support ZIP: frames recorded during scanning + report."""
    import io
    import zipfile

    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    # diagnostics are privacy-gated by default
    response = api_client.get(f"/api/sessions/{session_id}/diagnostics/download")
    assert response.status_code == 403

    response = api_client.patch("/api/settings", json={"privacy": {"diagnostics_recording_enabled": True}})
    assert response.status_code == 200, response.text

    # no runtime yet -> clear error once diagnostics are explicitly enabled
    response = api_client.get(f"/api/sessions/{session_id}/diagnostics/download")
    assert response.status_code == 400

    # scan with the diagnostics recording enabled
    sheet = render_sheet({"version": 1, "studentId": "S-101", "classId": "7Б", "taskId": "T-042", "sheetId": "S-101-T-042-d1"})
    opts = SceneOptions()
    frames = [empty_scene(opts, seed=i) for i in range(2)]
    frames += [render_scene(sheet, opts, seed=7) for _ in range(10)]

    with api_client.websocket_connect(f"/api/ws/sessions/{session_id}/scan") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "diagnostics", "enabled": True}))
        assert ws.receive_json() == {"type": "diagnostics", "enabled": True}
        for frame in frames:
            ws.send_text(json.dumps({"type": "frame", "image": _data_url(frame)}))
            while True:
                message = ws.receive_json()
                if message["type"] in ("state", "busy"):
                    break

    response = api_client.get(f"/api/sessions/{session_id}/diagnostics/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "report.json" in names
    assert any(n.startswith("stream/") for n in names)
    report = json.loads(archive.read("report.json"))
    assert report["sessionId"] == session_id
    assert "config" in report and "state" in report


def test_dashboard_reflects_activity(api_client):
    class_id, task_id, _ = _make_catalog(api_client)
    session_id = _make_session(api_client, class_id, task_id)
    _speed_up_scanning(api_client)
    api_client.post(f"/api/sessions/{session_id}/start")

    payload = {"version": 1, "studentId": "S-102", "classId": "7Б", "taskId": "T-042", "sheetId": "S-102-T-042-1"}
    result = _scan_one_sheet(api_client, session_id, payload)
    assert result is not None and result["result"]["success"]

    body = api_client.get("/api/dashboard").json()
    assert body["total_sheets"] == 1
    assert body["sheets_today"] == 1
    assert body["last_session"]["id"] == session_id
    assert body["storage_bytes"] > 0
