"""Round-trip tests for session export/import archive."""

from __future__ import annotations

import json

import mycode.project.instance as inst
import mycode.storage.database as dbmod
import pytest

from mycode.session.archive import export_session, fork_session, import_session
from mycode.session.message import (
    create_assistant_message,
    create_text_part,
    create_user_message,
    persist_turn,
    save_message,
    save_part,
)
from mycode.session.session import create as create_session
from mycode.session.session import get as get_session


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "archive.db"))
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


def _seed(session_id: str, text_in: str, text_out: str, turn: int = 1) -> None:
    user_msg = create_user_message(session_id)
    save_message(user_msg)
    p = create_text_part(session_id, user_msg.id)
    p.content = text_in
    save_part(p)
    a_msg = create_assistant_message(session_id, user_msg.id, "openai", "gpt-4", "build")
    a_msg.turn_number = turn  # type: ignore[attr-defined]
    a_part = create_text_part(session_id, a_msg.id)
    a_part.content = text_out
    persist_turn(session_id, a_msg, [a_part])


def test_export_basic_shape():
    s = create_session(title="archive-a")
    _seed(s.id, "hello", "world")
    archive = export_session(s.id)
    assert archive["format"] == "mycode-session-archive"
    assert archive["version"] == 1
    assert archive["session"]["title"] == "archive-a"
    assert len(archive["messages"]) == 2  # one user + one assistant
    assert any(m.get("role") == "assistant" for m in archive["messages"])


def test_export_unknown_session_raises():
    with pytest.raises(KeyError):
        export_session("session_definitely_not_here")


def test_roundtrip_preserves_content():
    s = create_session(title="archive-b")
    _seed(s.id, "u1", "a1", turn=1)
    _seed(s.id, "u2", "a2", turn=2)

    archive = export_session(s.id)
    imported = import_session(archive, title_prefix="[restored] ")
    assert imported.id != s.id  # new ID by default
    assert imported.title.startswith("[restored]")

    # Re-export the copy and check the assistant texts survived.
    again = export_session(imported.id)
    texts = []
    for m in again["messages"]:
        for p in m.get("parts", []):
            if p.get("type") == "text":
                texts.append((m.get("role"), p.get("content")))
    assert ("assistant", "a1") in texts
    assert ("assistant", "a2") in texts


def test_import_rejects_bad_format():
    with pytest.raises(ValueError):
        import_session({"format": "wrong", "version": 1, "session": {}, "messages": []})
    with pytest.raises(ValueError):
        import_session({"format": "mycode-session-archive", "version": 99, "session": {}, "messages": []})


def test_archive_is_json_serializable():
    s = create_session(title="archive-c")
    _seed(s.id, "q", "r")
    archive = export_session(s.id)
    # Should round-trip through json.dumps / loads without losing anything.
    dumped = json.dumps(archive, ensure_ascii=False)
    reloaded = json.loads(dumped)
    assert reloaded == archive


def test_fork_creates_new_session_with_parent_lineage():
    s = create_session(title="fork-src")
    _seed(s.id, "u1", "a1", turn=1)
    _seed(s.id, "u2", "a2", turn=2)

    forked = fork_session(s.id, turn=1, title="explore alt path")
    assert forked.id != s.id
    assert forked.parent_id == s.id
    assert forked.title == "explore alt path"

    # The fork contains only turn 1's exchange.
    arch = export_session(forked.id)
    assistants = [m for m in arch["messages"] if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert (assistants[0].get("parts") or [{}])[0].get("content") == "a1"


def test_fork_unknown_turn_raises():
    s = create_session(title="fork-bad")
    _seed(s.id, "u1", "a1", turn=1)
    with pytest.raises(KeyError):
        fork_session(s.id, turn=99)


def test_fork_rejects_invalid_turn():
    s = create_session(title="fork-invalid")
    _seed(s.id, "u1", "a1", turn=1)
    with pytest.raises(ValueError):
        fork_session(s.id, turn=0)
