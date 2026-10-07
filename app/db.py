"""Engine & session SQLAlchemy.

- Produksi : MariaDB 10.11 (PyMySQL), session di-set time_zone='+07:00'.
- Dev/uji  : SQLite file (portabel untuk pytest & demo lokal).

Aplikasi tidak pernah menulis ke schema lain; semua perubahan skema lewat Alembic.
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    url = url or settings.DATABASE_URL
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_recycle"] = 1800
    eng = create_engine(url, **kwargs)

    if eng.dialect.name == "mysql":

        @event.listens_for(eng, "connect")
        def _set_tz(dbapi_conn, _record):  # pragma: no cover - butuh MariaDB
            cur = dbapi_conn.cursor()
            cur.execute("SET time_zone='+07:00'")
            cur.close()

    elif eng.dialect.name == "sqlite":

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db():
    """Dependency FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_all() -> None:
    """Dipakai hanya oleh bootstrap dev/test — produksi memakai Alembic."""
    from app import models  # noqa: F401  (registrasi mapper)

    Base.metadata.create_all(bind=engine)


__all__ = ["Base", "Session", "SessionLocal", "engine", "get_db", "session_scope", "create_all"]
