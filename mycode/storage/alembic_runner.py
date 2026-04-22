"""Programmatic Alembic driver used by :mod:`mycode.storage.database`.

The baseline milestone ships ``0001_baseline`` + an ``alembic.ini``
opt-in via ``MYCODE_ALEMBIC=1``.  This module hides the sharp edges so
the startup path stays a single line:

    if is_alembic_enabled():
        apply_migrations(db_path)

Behaviour, in order:

1. **Fresh database** — no tables at all.  Run ``upgrade head`` normally
   and the baseline revision creates the full schema.
2. **Existing legacy database** — schema was created by the in-code
   ``Base.metadata.create_all`` + ``_migrate`` path and no
   ``alembic_version`` table is present.  We ``stamp 0001_baseline`` so
   Alembic inherits authority without re-running DDL (which would
   fail with "table already exists"), then ``upgrade head`` catches up
   on any newer revisions.
3. **Alembic-managed database** — ``alembic_version`` already present.
   Straight ``upgrade head``; a no-op when already at head.

All failures are logged and re-raised; the caller decides whether to
degrade to ``create_all``.  We intentionally avoid ``from alembic
import command`` at module import time because importing Alembic is
slow (~80ms on cold interpreters) and the default path does not use
it — the imports are deferred to :func:`apply_migrations`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from mycode.util.log import create as create_logger

if TYPE_CHECKING:
    from pathlib import Path

_log = create_logger(service="storage.alembic")

#: Environment variable that gates the Alembic startup path.  Accepts
#: ``1`` / ``true`` / ``yes`` (case-insensitive); any other value —
#: including unset — keeps the legacy ``_migrate`` behaviour.
ENV_FLAG = "MYCODE_ALEMBIC"

#: The baseline revision id.  Hard-coded instead of discovered at
#: runtime because a stamping step that picks up the *wrong* revision
#: after a partial checkout would silently corrupt ordering.
BASELINE_REVISION = "0001_baseline"


def is_alembic_enabled(env: dict[str, str] | None = None) -> bool:
    """Return ``True`` if ``MYCODE_ALEMBIC`` is set to a truthy value."""
    src = os.environ if env is None else env
    val = src.get(ENV_FLAG, "")
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _alembic_config(db_path: str | Path, ini_path: str | Path) -> object:
    """Build an :class:`alembic.config.Config` pointing at ``db_path``.

    Deferred import: the only places that call this already paid the
    Alembic import cost.
    """
    from alembic.config import Config

    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _inspect_tables(db_path: str | Path) -> set[str]:
    """Return the set of tables currently present in ``db_path``.

    Works on a brand-new file (returns ``set()``) and on an in-memory
    ``:memory:`` URL (callers don't use that path, but guard anyway).
    """
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def apply_migrations(
    db_path: str | Path,
    *,
    ini_path: str | Path = "alembic.ini",
) -> str:
    """Bring ``db_path`` up to ``head`` using Alembic.

    Returns the action actually taken — one of ``"fresh"``,
    ``"stamp+upgrade"``, ``"upgrade"``, or ``"already-head"`` — so
    callers (and tests) can assert on the flow.

    Raises :class:`alembic.util.CommandError` or :class:`OSError` on
    unrecoverable failure.  The database layer catches these and
    degrades to :func:`Base.metadata.create_all` after logging.
    """
    from alembic import command

    cfg = _alembic_config(db_path, ini_path)
    tables = _inspect_tables(db_path)
    has_alembic = "alembic_version" in tables
    has_schema = any(t != "alembic_version" for t in tables)

    if not has_schema and not has_alembic:
        _log.info("alembic: fresh database, upgrading to head", db_path=str(db_path))
        command.upgrade(cfg, "head")
        return "fresh"

    if has_schema and not has_alembic:
        _log.info(
            "alembic: legacy schema detected, stamping baseline then upgrading",
            db_path=str(db_path),
            baseline=BASELINE_REVISION,
        )
        command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
        return "stamp+upgrade"

    # has_alembic == True — let Alembic compute whether there's work.
    _log.info("alembic: upgrading to head", db_path=str(db_path))
    command.upgrade(cfg, "head")
    return "upgrade"


__all__ = [
    "BASELINE_REVISION",
    "ENV_FLAG",
    "apply_migrations",
    "is_alembic_enabled",
]
