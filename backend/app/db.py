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
    """Create tables when Alembic has not been run (local MVP convenience)."""
    Base.metadata.create_all(bind=engine)
