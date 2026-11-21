"""Database engine, session factory and declarative base.

SQLite is the default so the project runs with no services, and Postgres works
by changing ``DATABASE_URL`` alone. The only dialect-specific handling is the
``check_same_thread`` flag, which SQLite needs because FastAPI serves requests
from a thread pool.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every model."""


def _make_engine(url: str):
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


_settings = get_settings()
engine = _make_engine(_settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


if _settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        """WAL and foreign keys.

        WAL lets a read proceed while a write is in flight, which matters
        because ingestion writes chunks while the UI polls for status.
        Foreign keys are off by default in SQLite and must be enabled per
        connection, or deleting a document would orphan its chunks.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. Fine for this scope; Alembic is the next step."""
    from app import models  # noqa: F401  (import registers the models)

    Base.metadata.create_all(bind=engine)
