"""Shared pytest fixtures.

Every test runs against a throw-away data directory so nothing touches the
teacher's real archive.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# The data dir must be set *before* app.config is first imported.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="paperflow-tests-"))
os.environ["PAPERFLOW_DATA_DIR"] = str(_TMP_ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.config import RuntimeConfig, get_settings  # noqa: E402
from app.cv.synthetic import (  # noqa: E402
    DEFAULT_PAYLOAD,
    SceneOptions,
    empty_scene,
    render_scene,
    render_sheet,
)


@pytest.fixture(scope="session", autouse=True)
def _settings():
    settings = get_settings()
    settings.ensure_dirs()
    return settings


@pytest.fixture()
def db_session(_settings):
    """Isolated in-memory-ish database per test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def api_client(_settings, tmp_path, monkeypatch):
    """TestClient bound to a fresh SQLite file and storage folder."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.db as app_db
    from app.models import Base

    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    Base.metadata.create_all(engine)

    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", Session)

    # modules that captured SessionLocal at import time
    import app.api.routes_scan as routes_scan
    import app.services.ocr_queue as ocr_queue_module
    import app.testing.replay as replay_module

    monkeypatch.setattr(routes_scan, "SessionLocal", Session)
    monkeypatch.setattr(ocr_queue_module, "SessionLocal", Session)
    monkeypatch.setattr(replay_module, "SessionLocal", Session)

    storage_root = tmp_path / "storage" / "sheets"
    storage_root.mkdir(parents=True, exist_ok=True)
    import app.services.storage as storage_module

    monkeypatch.setattr(storage_module, "_storage", storage_module.SheetStorage(storage_root))

    from app.services.settings_service import invalidate_cache

    invalidate_cache()

    # scanning runtimes are process-global; session ids repeat across tests
    from app.services.scan_service import scan_service

    scan_service._runtimes.clear()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client

    invalidate_cache()
    scan_service._runtimes.clear()
    engine.dispose()


@pytest.fixture()
def config() -> RuntimeConfig:
    return RuntimeConfig()


@pytest.fixture(scope="session")
def sheet_image() -> np.ndarray:
    return render_sheet(DEFAULT_PAYLOAD)


@pytest.fixture(scope="session")
def blank_sheet_image() -> np.ndarray:
    return render_sheet(DEFAULT_PAYLOAD, handwriting=False)


@pytest.fixture(scope="session")
def background_frame() -> np.ndarray:
    return empty_scene()


@pytest.fixture(scope="session")
def background_gray(background_frame) -> np.ndarray:
    gray = cv2.cvtColor(background_frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


@pytest.fixture(scope="session")
def scene_frame(sheet_image) -> np.ndarray:
    return render_scene(sheet_image, SceneOptions(), seed=100)


@pytest.fixture()
def make_scene(sheet_image):
    def _make(**kwargs) -> np.ndarray:
        seed = kwargs.pop("seed", 100)
        return render_scene(sheet_image, SceneOptions(**kwargs), seed=seed)

    return _make
