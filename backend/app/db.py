"""SQLAlchemy engine and session management.

Engines are cached per database URL and resolved from the application's
Settings (not the process environment), so tests that build an app with an
unreachable URL never touch a real database even when one is running nearby.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def get_engine(settings: Settings) -> Engine:
    url = settings.database_url
    with _lock:
        engine = _engines.get(url)
        if engine is None:
            engine = create_engine(
                url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout_seconds,
                pool_pre_ping=True,
                connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
            )
            _engines[url] = engine
        return engine


def get_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(settings), autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session for the app's database."""
    session = get_session_factory(request.app.state.settings)()
    try:
        yield session
    finally:
        session.close()


def ping(session: Session) -> bool:
    return session.execute(text("SELECT 1")).scalar_one() == 1


def dispose_engines() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
