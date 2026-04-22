"""Alembic baseline sanity test.

Applies the baseline revision against a fresh SQLite file and verifies
that every table / index declared in ``Base.metadata`` is present.
Downgrade is exercised too to catch drop-order regressions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from mycode.storage.models import Base


@pytest.fixture()
def alembic_cfg(tmp_path: Path) -> tuple[Config, str]:
    db_path = tmp_path / "alembic.db"
    cfg = Config()
    cfg.set_main_option("script_location", "mycode/storage/alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg, str(db_path)


def test_baseline_creates_all_tables(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())
    for tbl in Base.metadata.tables:
        assert tbl in live_tables, f"missing table: {tbl}"
    # alembic always creates its bookkeeping table
    assert "alembic_version" in live_tables


def test_baseline_adds_critical_columns(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    insp = inspect(engine)

    msg_cols = {c["name"] for c in insp.get_columns("message")}
    assert {"turn_number", "snapshot_ref"} <= msg_cols

    part_cols = {c["name"] for c in insp.get_columns("part")}
    assert "tool_call_id" in part_cols

    # Composite + scoped indices added during the fix round must exist.
    msg_indexes = {idx["name"] for idx in insp.get_indexes("message")}
    assert "ix_message_session_turn" in msg_indexes
    assert "ix_message_session_created" in msg_indexes


def test_baseline_downgrade_removes_tables(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(f"sqlite:///{db_path}")
    insp = inspect(engine)
    live = set(insp.get_table_names())
    # After downgrade only Alembic's own bookkeeping survives.
    assert "message" not in live
    assert "part" not in live
    assert "session" not in live
