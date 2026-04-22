"""SQLite database connection management.

Provides both sync and async access to the SQLite database.
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from mycode.storage.models import Base
from mycode.util import log as logmod
from mycode.util.paths import GlobalPaths

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logmod.create(service="db")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
# Use RLock (reentrant lock) because get_session_factory() holds the lock
# and calls get_engine() which also needs to acquire the same lock.
_db_lock = threading.RLock()


def get_db_path() -> str:
    """Get the database file path."""
    custom = os.environ.get("OPENCODE_DB")
    if custom:
        if custom == ":memory:" or os.path.isabs(custom):
            return custom
        return str(GlobalPaths.data() / custom)
    return str(GlobalPaths.data() / "mycode.db")


def _configure_sqlite(dbapi_conn: Any, _: Any) -> None:
    """Configure SQLite pragmas for performance.

    Tuning targets the agent workload (many small reads from a single DB
    under moderate write contention from one worker):

    - ``journal_mode = WAL``   — concurrent readers, single writer
    - ``synchronous = NORMAL`` — crash-safe enough with WAL
    - ``busy_timeout = 10s``   — tolerate a busy writer; tune via env
    - ``cache_size = -64 MB``  — negative = KB; ``-65536`` == 64 MB
    - ``temp_store = MEMORY``  — keep temp btrees off disk
    - ``mmap_size ≈ 64 MB``    — memory-map the DB for cheap reads
    - ``foreign_keys = ON``    — enforce FKs for future schema additions
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    # Busy timeout (ms). Overridable for tests / high-contention setups.
    busy_ms = os.environ.get("MYCODE_SQLITE_BUSY_MS", "10000")
    try:
        busy_int = max(1000, int(busy_ms))
    except ValueError:
        busy_int = 10000
    cursor.execute(f"PRAGMA busy_timeout = {busy_int}")
    cursor.execute("PRAGMA cache_size = -65536")     # 64 MB page cache
    cursor.execute("PRAGMA temp_store = MEMORY")
    # 64 MB mmap — safe default; providers with very large DBs can bump
    # via env without code changes.
    mmap_bytes = os.environ.get("MYCODE_SQLITE_MMAP_BYTES", str(64 * 1024 * 1024))
    try:
        mmap_int = max(0, int(mmap_bytes))
    except ValueError:
        mmap_int = 64 * 1024 * 1024
    cursor.execute(f"PRAGMA mmap_size = {mmap_int}")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def get_engine() -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is not None:
        return _engine

    with _db_lock:
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

        # Lightweight migrations for columns added after initial schema
        _migrate(_engine)

        return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    with _db_lock:
        if _session_factory is not None:
            return _session_factory
        engine = get_engine()
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        return _session_factory


def get_session() -> Session:
    """Create a new database session.

    Prefer :func:`session_scope` for call sites that can use a context
    manager — it guarantees the session is closed even if the caller
    raises. Plain ``get_session()`` remains for legacy code but callers
    MUST wrap it in ``try/finally: session.close()``.
    """
    factory = get_session_factory()
    return factory()


@contextlib.contextmanager
def session_scope() -> Any:
    """Context-managed DB session.

    Usage::

        with session_scope() as db:
            rows = db.query(MessageTable).all()

    Guarantees ``close()`` on every exit path (normal, exception,
    generator abandonment). Does NOT auto-commit — callers opt in via
    :func:`use` / :func:`transaction` or explicit ``db.commit()``.
    """
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def use(fn: Any) -> Any:
    """Execute a function within a database session.

    Usage:
        result = Database.use(lambda session: session.query(Model).all())
    """
    with session_scope() as session:
        try:
            result = fn(session)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def transaction(fn: Any) -> Any:
    """Execute a function within an explicit transaction."""
    with session_scope() as session:
        with session.begin():
            result = fn(session)
        return result


def close() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        _engine.dispose()
        _engine = None
        _session_factory = None
        logger.debug("database closed")


def reset() -> None:
    """Reset for testing — close and clear."""
    close()
    logger.debug("database reset")


def _migrate(engine: Engine) -> None:
    """Run lightweight column migrations for existing databases.

    SQLAlchemy's ``create_all`` only creates missing **tables**; it will not add
    new columns to tables that already exist.  This function inspects the live
    schema and adds any missing columns so that upgrades are seamless.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)

    _add_column_if_missing(
        engine, inspector,
        table="session",
        column="visible",
        ddl="ALTER TABLE session ADD COLUMN visible INTEGER NOT NULL DEFAULT 1",
    )

    _add_column_if_missing(
        engine, inspector,
        table="message",
        column="turn_number",
        ddl="ALTER TABLE message ADD COLUMN turn_number INTEGER",
    )

    _add_column_if_missing(
        engine, inspector,
        table="message",
        column="snapshot_ref",
        ddl="ALTER TABLE message ADD COLUMN snapshot_ref TEXT",
    )

    _add_index_if_missing(
        engine, inspector,
        table="message",
        index_name="ix_message_session_turn",
        ddl="CREATE INDEX IF NOT EXISTS ix_message_session_turn ON message(session_id, turn_number)",
    )

    _add_index_if_missing(
        engine, inspector,
        table="message",
        index_name="ix_message_session_created",
        ddl="CREATE INDEX IF NOT EXISTS ix_message_session_created ON message(session_id, time_created)",
    )

    _add_index_if_missing(
        engine, inspector,
        table="part",
        index_name="ix_part_message_created",
        ddl="CREATE INDEX IF NOT EXISTS ix_part_message_created ON part(message_id, time_created)",
    )

    # Best-effort unique constraint on (session_id, tool_call_id). If the
    # existing table has dupes the CREATE will fail — we log and continue
    # so the process still boots; callers can remediate offline.
    _add_unique_index_if_missing(
        engine, inspector,
        table="part",
        index_name="uq_part_session_tool_call",
        ddl="CREATE UNIQUE INDEX IF NOT EXISTS uq_part_session_tool_call ON part(session_id, tool_call_id) WHERE tool_call_id IS NOT NULL",
    )


def _add_index_if_missing(
    engine: Engine,
    inspector: Any,
    *,
    table: str,
    index_name: str,
    ddl: str,
) -> None:
    try:
        existing = {idx.get("name") for idx in inspector.get_indexes(table)}
    except Exception:
        existing = set()
    if index_name in existing:
        return
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info("migration: added index", table=table, index=index_name)
    except Exception as exc:
        logger.warn("migration: failed to add index", table=table, index=index_name, error=str(exc))


def _add_unique_index_if_missing(
    engine: Engine,
    inspector: Any,
    *,
    table: str,
    index_name: str,
    ddl: str,
) -> None:
    try:
        existing = {idx.get("name") for idx in inspector.get_indexes(table)}
    except Exception:
        existing = set()
    if index_name in existing:
        return
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info("migration: added unique index", table=table, index=index_name)
    except Exception as exc:
        # Likely duplicates exist — keep boot non-fatal, but flag it.
        logger.warn(
            "migration: failed to add unique index (existing duplicates?)",
            table=table, index=index_name, error=str(exc),
        )


def _add_column_if_missing(
    engine: Engine,
    inspector: Any,
    *,
    table: str,
    column: str,
    ddl: str,
) -> None:
    """Add a column to *table* if it does not already exist."""
    from sqlalchemy import text

    columns = {c["name"] for c in inspector.get_columns(table)}
    if column not in columns:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info("migration: added column", table=table, column=column)
