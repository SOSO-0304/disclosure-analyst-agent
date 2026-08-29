"""Database connection and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from disclosure_agent.config import get_settings
from disclosure_agent.storage.db_models import Base


@lru_cache(maxsize=4)
def get_engine(database_url: str | None = None) -> Engine:
    """Create and cache a SQLAlchemy engine for the requested database URL."""

    url = database_url or get_settings().database_url
    return create_engine(url, pool_pre_ping=True)


def create_schema(engine: Engine) -> None:
    """Create currently declared tables for local/dev bootstrap."""

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Provide one transactional SQLAlchemy session."""

    target = engine or get_engine()
    factory = sessionmaker(bind=target, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
