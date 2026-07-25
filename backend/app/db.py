"""Database engine / session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

_settings = get_settings()

_connect_args = {"check_same_thread": False} if _settings.resolved_database_url().startswith("sqlite") else {}

engine: Engine = create_engine(
    _settings.resolved_database_url(),
    connect_args=_connect_args,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # pragma: no cover - driver hook
    module = type(dbapi_connection).__module__
    if "sqlite" in module:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
        # SQLite's built-in lower()/upper() are ASCII-only, which breaks
        # case-insensitive search for Cyrillic names. Override with Python's
        # Unicode-aware implementations.
        dbapi_connection.create_function("lower", 1, _unicode_lower)
        dbapi_connection.create_function("upper", 1, _unicode_upper)


def _unicode_lower(value):  # pragma: no cover - trivial
    return value.lower() if isinstance(value, str) else value


def _unicode_upper(value):  # pragma: no cover - trivial
    return value.upper() if isinstance(value, str) else value


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for background workers."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Bring the database to the current schema.

    Strategy (single-teacher local app, SQLite):

    * fresh database             -> run Alembic migrations from scratch;
    * pre-Alembic database       -> tables exist but no ``alembic_version``:
      stamp it with the baseline revision, then upgrade to head;
    * migrated database          -> plain ``alembic upgrade head``.

    Falls back to ``create_all`` if Alembic is unavailable for any reason —
    the teacher's data must never be held hostage by migration tooling.
    """
    try:
        _init_db_with_alembic()
    except Exception:  # pragma: no cover - defensive fallback
        import logging

        logging.getLogger(__name__).exception("alembic upgrade failed; falling back to create_all")
        Base.metadata.create_all(bind=engine)


#: Revision that matches the schema created by pre-Alembic ``create_all``.
BASELINE_REVISION = "4c4d40c18081"


def _alembic_config():
    from pathlib import Path

    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def _init_db_with_alembic() -> None:
    from alembic import command
    from sqlalchemy import inspect

    config = _alembic_config()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.connect() as connection:
        # run everything on the application's own engine (see migrations/env.py)
        config.attributes["connection"] = connection
        if "alembic_version" not in tables and "scan_sessions" in tables:
            # Existing pre-Alembic installation: adopt it without touching data.
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        connection.commit()
