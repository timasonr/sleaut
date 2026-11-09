from __future__ import annotations

from contextlib import contextmanager
import fcntl
import threading
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, delete, event, or_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.access import seed_super_admin
from app.config import get_settings
from app.models import Base, Listing

db_lock = threading.Lock()
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


@contextmanager
def global_lock() -> Generator[None, None, None]:
    """Cross-process lock so two workers cannot claim the same job."""
    path = Path(get_settings().database_path).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        path = Path(settings.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-typed-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def session_scope() -> Generator[Session, None, None]:
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    yield from session_scope()


def purge_demo_rows(session: Session) -> int:
    result = session.execute(
        delete(Listing).where(
            or_(
                Listing.source.in_(("demo", "mock", "fixture")),
                Listing.place_id.like("demo:%"),
                Listing.maps_url.like("%cid=demo-%"),
                Listing.maps_url.like("%cid-demo-%"),
            )
        )
    )
    return int(result.rowcount or 0)


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    SessionLocal = get_session_factory()
    with db_lock:
        session = SessionLocal()
        try:
            purge_demo_rows(session)
            seed_super_admin(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
