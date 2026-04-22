"""Alembic integration tests.

Covers four concerns:

1. **Baseline round-trip** — ``alembic upgrade head`` on a fresh
   sqlite file creates every table the ORM models declare, and
   ``alembic downgrade base`` drops them all (only ``alembic_version``
   lingers, which Alembic owns).
2. **Schema drift guard** — columns declared on ``Base.metadata``
   must exactly match the columns the baseline revision creates.
   This prevents silent drift between the SQLAlchemy models and the
   migration — the exact failure mode that makes Alembic adoption a
   foot-gun for every project that tries it.
3. **Startup switch** — ``MYCODE_ALEMBIC=1`` routes
   :func:`mycode.storage.database.get_engine` through
   :func:`apply_migrations`; unset keeps the legacy ``create_all`` +
   ``_migrate`` path; a **legacy** database flipped to Alembic
   mid-flight gets stamped instead of blowing up.
4. **is_alembic_enabled** — accepts the documented truthy values and
   nothing else (so a stray ``MYCODE_ALEMBIC=maybe`` does not silently
   switch code paths).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData, create_engine, inspect

from mycode.storage import database as db
from mycode.storage.alembic_runner import (
    BASELINE_REVISION,
    apply_migrations,
    is_alembic_enabled,
)
from mycode.storage.models import Base

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_cfg(db_path: Path) -> object:
    """Build a :class:`alembic.config.Config` bound to ``db_path``."""
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture(autouse=True)
def _reset_db_singleton() -> None:
    """Clear the process-global engine before and after every test so
    each case owns its own sqlite file — otherwise tests would reuse
    the engine pointed at an unrelated tempfile."""
    db.reset()
    yield
    db.reset()


# ---------------------------------------------------------------------------
# 1. Baseline round-trip
# ---------------------------------------------------------------------------


def test_baseline_upgrade_creates_all_tables(tmp_path: Path) -> None:
    from alembic import command

    db_file = tmp_path / "fresh.db"
    cfg = _alembic_cfg(db_file)
    command.upgrade(cfg, "head")

    insp = inspect(create_engine(f"sqlite:///{db_file}"))
    tables = set(insp.get_table_names())

    # Every ORM table must be present.
    expected = {t.name for t in Base.metadata.sorted_tables}
    missing = expected - tables
    assert not missing, f"baseline did not create {missing}"
    assert "alembic_version" in tables


def test_baseline_downgrade_removes_all_tables(tmp_path: Path) -> None:
    from alembic import command

    db_file = tmp_path / "down.db"
    cfg = _alembic_cfg(db_file)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    insp = inspect(create_engine(f"sqlite:///{db_file}"))
    remaining = set(insp.get_table_names())
    # Alembic leaves its own bookkeeping table behind; everything
    # application-owned must be gone.
    assert remaining <= {"alembic_version"}, f"leaked tables: {remaining - {'alembic_version'}}"


# ---------------------------------------------------------------------------
# 2. Schema drift guard
# ---------------------------------------------------------------------------


def test_baseline_matches_orm_models(tmp_path: Path) -> None:
    """The baseline revision must be column-for-column equal to
    :attr:`Base.metadata`.  If someone adds a model column without a
    matching Alembic migration, this test fails immediately with a
    precise diff."""
    from alembic import command

    db_file = tmp_path / "drift.db"
    cfg = _alembic_cfg(db_file)
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_file}")
    live = MetaData()
    live.reflect(bind=engine)

    live_tables = {t.name: t for t in live.sorted_tables if t.name != "alembic_version"}
    model_tables = {t.name: t for t in Base.metadata.sorted_tables}

    assert set(live_tables) == set(model_tables), (
        f"table set drift: live-only={set(live_tables) - set(model_tables)}, "
        f"model-only={set(model_tables) - set(live_tables)}"
    )

    for name in sorted(model_tables):
        live_cols = {c.name for c in live_tables[name].columns}
        model_cols = {c.name for c in model_tables[name].columns}
        assert live_cols == model_cols, (
            f"[{name}] column drift: "
            f"live-only={live_cols - model_cols}, "
            f"model-only={model_cols - live_cols}"
        )


# ---------------------------------------------------------------------------
# 3. Startup switch
# ---------------------------------------------------------------------------


def test_get_engine_uses_legacy_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MYCODE_ALEMBIC", raising=False)
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "legacy.db"))

    engine = db.get_engine()
    tables = set(inspect(engine).get_table_names())
    # Legacy path never creates the alembic bookkeeping table.
    assert "alembic_version" not in tables
    # Full ORM schema is still present.
    for t in Base.metadata.sorted_tables:
        assert t.name in tables


def test_get_engine_with_alembic_flag_fresh_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYCODE_ALEMBIC", "1")
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "fresh-alembic.db"))

    engine = db.get_engine()
    tables = set(inspect(engine).get_table_names())
    assert "alembic_version" in tables
    for t in Base.metadata.sorted_tables:
        assert t.name in tables


def test_get_engine_with_alembic_flag_stamps_legacy_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing legacy databases must not lose data when flipping the flag."""
    db_file = tmp_path / "migrated.db"
    monkeypatch.setenv("OPENCODE_DB", str(db_file))

    # Step 1: bring the DB up legacy-style and insert a marker row.
    monkeypatch.delenv("MYCODE_ALEMBIC", raising=False)
    engine = db.get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text(
                "INSERT INTO project(id, worktree, time_created, time_updated, sandboxes) "
                "VALUES ('p1', '/tmp/x', 0, 0, '[]')"
            )
        )
    db.reset()

    # Step 2: flip the flag, reopen — expect alembic to stamp baseline
    # and the existing row to survive.
    monkeypatch.setenv("MYCODE_ALEMBIC", "1")
    engine = db.get_engine()
    insp = inspect(engine)
    assert "alembic_version" in insp.get_table_names()
    with engine.connect() as conn:
        from sqlalchemy import text

        rows = list(conn.execute(text("SELECT id FROM project")))
    assert [r[0] for r in rows] == ["p1"]


# ---------------------------------------------------------------------------
# 4. is_alembic_enabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("maybe", False),
    ],
)
def test_is_alembic_enabled(value: str, expected: bool) -> None:
    assert is_alembic_enabled({"MYCODE_ALEMBIC": value}) is expected


def test_is_alembic_enabled_missing() -> None:
    # Empty env → falsy, no KeyError.
    assert is_alembic_enabled({}) is False


# ---------------------------------------------------------------------------
# 5. apply_migrations action reporting
# ---------------------------------------------------------------------------


def test_apply_migrations_fresh_reports_fresh(tmp_path: Path) -> None:
    action = apply_migrations(tmp_path / "fresh.db")
    assert action == "fresh"


def test_apply_migrations_stamps_legacy(tmp_path: Path) -> None:
    """A DB with ORM tables but no alembic_version must be stamped."""
    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()

    action = apply_migrations(db_file)
    assert action == "stamp+upgrade"

    insp = inspect(create_engine(f"sqlite:///{db_file}"))
    assert "alembic_version" in insp.get_table_names()

    # Stored revision must be the baseline.
    with create_engine(f"sqlite:///{db_file}").connect() as conn:
        from sqlalchemy import text

        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev == BASELINE_REVISION


def test_apply_migrations_idempotent_on_already_head(tmp_path: Path) -> None:
    db_file = tmp_path / "head.db"
    apply_migrations(db_file)  # fresh
    action2 = apply_migrations(db_file)
    # Second call sees alembic_version already present → plain upgrade,
    # which is a no-op at head.
    assert action2 == "upgrade"
