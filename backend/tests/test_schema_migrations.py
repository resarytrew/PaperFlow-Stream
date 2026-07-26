"""Schema migration smoke tests.

The normal API fixtures use ``Base.metadata.create_all`` for speed. This file
exercises the application startup path that teachers use: Alembic migrations on
a fresh SQLite database. It catches ORM/migration drift before a clean install
can fail at runtime.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


def _head_revision(app_db) -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(app_db._alembic_config()).get_current_head()  # noqa: SLF001


def _assert_head(engine, app_db) -> None:
    with engine.connect() as connection:
        version = connection.execute(text("select version_num from alembic_version")).scalar_one()
    assert version == _head_revision(app_db)


def test_alembic_fresh_database_matches_runtime_models(tmp_path, monkeypatch):
    import app.db as app_db
    from app.models import (
        ClassGroup,
        QrStatus,
        ReviewDecision,
        ScanSession,
        ScanStatus,
        ScannedSheet,
        SessionPreset,
        ShareToken,
        Student,
        Task,
    )

    db_file = tmp_path / "fresh-alembic.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", Session)

    app_db.init_db()

    inspector = inspect(engine)
    assert inspector.has_table("alembic_version")
    _assert_head(engine, app_db)

    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }
    assert {"max_score", "rubric"}.issubset(columns["tasks"])
    assert "answer_crops_json" in columns["scanned_sheets"]
    assert {"score", "rubric_result"}.issubset(columns["review_decisions"])
    assert inspector.has_table("session_presets")
    assert inspector.has_table("share_tokens")

    # Real ORM writes against the migrated schema must not raise OperationalError.
    with Session() as db:
        group = ClassGroup(name="7Б", school_year="2026/2027")
        task = Task(
            external_id="T-001",
            title="Контрольная",
            max_score=10.0,
            rubric=[{"id": "k1", "title": "Ответ", "points": 10}],
        )
        db.add_all([group, task])
        db.flush()

        student = Student(external_id="S-001", first_name="Анна", last_name="Иванова", class_id=group.id)
        session = ScanSession(class_id=group.id, task_id=task.id, title="7Б / Контрольная", expected_sheet_count=1)
        db.add_all([student, session])
        db.flush()

        sheet = ScannedSheet(
            session_id=session.id,
            student_id=student.id,
            task_id=task.id,
            sheet_uid="S-001-T-001-1",
            qr_status=QrStatus.read.value,
            scan_status=ScanStatus.ok.value,
            answer_crops_json=[{"label": "answer", "path": "answer-1.jpg"}],
        )
        db.add(sheet)
        db.flush()

        db.add(
            ReviewDecision(
                scanned_sheet_id=sheet.id,
                teacher_text="ответ",
                decision="accepted",
                score=9.5,
                rubric_result=[{"id": "k1", "points": 9.5}],
            )
        )
        db.add(
            SessionPreset(
                name="7Б контрольная",
                class_id=group.id,
                task_id=task.id,
                expected_sheet_count=1,
                config_override={"ocr": {"provider": "mock"}},
            )
        )
        db.add(ShareToken(token="token-001", student_id=student.id, note="demo"))
        db.commit()

    engine.dispose()


def test_init_db_adopts_pre_alembic_current_schema(tmp_path, monkeypatch):
    """The defensive create_all fallback should be adoptable without duplicate-column failures."""
    import app.db as app_db
    from app.models import Base

    db_file = tmp_path / "pre-alembic-current.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", Session)

    app_db.init_db()

    _assert_head(engine, app_db)
    engine.dispose()
