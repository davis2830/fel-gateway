"""SQLAlchemy engine + session factory."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _ensure_engine() -> None:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False, future=True)


def get_engine():
    _ensure_engine()
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a DB session per request."""
    _ensure_engine()
    assert _SessionLocal is not None
    with _SessionLocal() as session:
        yield session


def session_scope() -> Session:
    """For scripts / workers: caller is responsible for commit/close."""
    _ensure_engine()
    assert _SessionLocal is not None
    return _SessionLocal()
