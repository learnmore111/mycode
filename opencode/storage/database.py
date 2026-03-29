"""SQLite database connection management.

Provides both sync and async access to the SQLite database.
Equivalent to src/storage/db.ts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from opencode.storage.models import Base
from opencode.util import log as logmod
from opencode.util.paths import GlobalPaths

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logmod.create(service="db")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_db_path() -> str:
    """Get the database file path."""
    custom = os.environ.get("OPENCODE_DB")
    if custom:
        if custom == ":memory:" or os.path.isabs(custom):
            return custom
        return str(GlobalPaths.data() / custom)
    return str(GlobalPaths.data() / "opencode.db")


def _configure_sqlite(dbapi_conn: Any, _: Any) -> None:
    """Configure SQLite pragmas for performance."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.execute("PRAGMA cache_size = -64000")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def get_engine() -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is not None:
        return _engine

    db_path = get_db_path()
    logger.info("opening database", path=db_path)

    # Ensure directory exists
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        pool_pre_ping=True,
    )

    # Register SQLite pragmas
    event.listen(_engine, "connect", _configure_sqlite)

    # Create all tables
    Base.metadata.create_all(_engine)

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    engine = get_engine()
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def get_session() -> Session:
    """Create a new database session."""
    factory = get_session_factory()
    return factory()


def use(fn: Any) -> Any:
    """Execute a function within a database session.

    Usage:
        result = Database.use(lambda session: session.query(Model).all())
    """
    session = get_session()
    try:
        result = fn(session)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def transaction(fn: Any) -> Any:
    """Execute a function within an explicit transaction."""
    session = get_session()
    try:
        with session.begin():
            result = fn(session)
        return result
    finally:
        session.close()


def close() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        _engine.dispose()
        _engine = None
        _session_factory = None


def reset() -> None:
    """Reset for testing — close and clear."""
    close()
