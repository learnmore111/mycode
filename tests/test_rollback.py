"""Tests for the `rollback_to_turn` helper."""

from __future__ import annotations

import mycode.project.instance as inst
import mycode.storage.database as dbmod
import pytest

from mycode.session.message import (
    create_assistant_message,
    create_text_part,
    create_user_message,
    next_turn_number,
    persist_turn,
    rebuild_history_from_db,
    rollback_to_turn,
    save_message,
    save_part,
)
from mycode.session.session import create as create_session


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "rollback.db"))
    dbmod.reset()
    dbmod.get_engine()
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path),
        worktree=str(tmp_path),
        project=inst.ProjectInfo(id="global", worktree=str(tmp_path)),
    ))
    yield tmp_path
    token.reset()
    dbmod.reset()


def _round_trip(session_id: str, user_text: str, assistant_text: str, turn: int) -> None:
    """Persist one full user+assistant turn to the DB for test fixtures."""
    user_msg = create_user_message(session_id)
    save_message(user_msg)
    user_text_part = create_text_part(session_id, user_msg.id)
    user_text_part.content = user_text
    save_part(user_text_part)

    assistant_msg = create_assistant_message(session_id, user_msg.id, "openai", "gpt-4", "build")
    assistant_msg.turn_number = turn  # type: ignore[attr-defined]
    a_part = create_text_part(session_id, assistant_msg.id)
    a_part.content = assistant_text
    persist_turn(session_id, assistant_msg, [a_part])


def test_next_turn_number_starts_at_one():
    s = create_session(title="rollback-turn-1")
    assert next_turn_number(s.id) == 1


def test_next_turn_number_increments():
    s = create_session(title="rollback-increment")
    _round_trip(s.id, "hi", "hello", turn=1)
    assert next_turn_number(s.id) == 2
    _round_trip(s.id, "again", "world", turn=2)
    assert next_turn_number(s.id) == 3


def test_rollback_drops_later_turns():
    s = create_session(title="rollback-keep")
    _round_trip(s.id, "u1", "a1", turn=1)
    _round_trip(s.id, "u2", "a2", turn=2)
    _round_trip(s.id, "u3", "a3", turn=3)

    result = rollback_to_turn(s.id, 1)
    assert result["removed"] > 0

    # History after rollback must retain only turn 1's messages.
    history = rebuild_history_from_db(s.id)
    assistants = [m for m in history if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert (assistants[0].get("content") or "").strip() == "a1"


def test_rollback_unknown_turn_raises():
    s = create_session(title="rollback-unknown")
    _round_trip(s.id, "u1", "a1", turn=1)
    with pytest.raises(KeyError):
        rollback_to_turn(s.id, 99)


def test_rollback_rejects_negative_turn():
    s = create_session(title="rollback-negative")
    with pytest.raises(ValueError):
        rollback_to_turn(s.id, -1)
