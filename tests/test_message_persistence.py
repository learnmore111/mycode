"""Tests for message persistence (save_message, save_parts, persist_turn)."""
import pytest
import mycode.project.instance as inst
import mycode.storage.database as dbmod
from mycode.session.message import (
    create_user_message, create_assistant_message, create_text_part, create_tool_part,
    save_message, save_part, save_parts, persist_turn, TextPart, ToolPart,
)
from mycode.storage.models import MessageTable, PartTable, SessionTable


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    dbmod.reset()
    dbmod.get_engine()
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path), worktree=str(tmp_path),
        project=inst.ProjectInfo(id="test-proj", worktree=str(tmp_path)),
    ))
    yield
    token.reset()
    dbmod.reset()


def test_save_user_message():
    msg = create_user_message("sess1")
    save_message(msg)
    db = dbmod.get_session()
    try:
        row = db.query(MessageTable).filter(MessageTable.id == msg.id).first()
        assert row is not None
        assert row.role == "user"
        assert row.session_id == "sess1"
    finally:
        db.close()


def test_save_assistant_message():
    msg = create_assistant_message("sess1", "parent1", "anthropic", "claude-3", "build")
    msg.tokens_input = 100
    msg.tokens_output = 50
    save_message(msg)
    db = dbmod.get_session()
    try:
        row = db.query(MessageTable).filter(MessageTable.id == msg.id).first()
        assert row is not None
        assert row.role == "assistant"
        assert row.provider_id == "anthropic"
        assert row.tokens_input == 100
    finally:
        db.close()


def test_save_text_part():
    part = create_text_part("sess1", "msg1")
    part.content = "Hello world"
    save_part(part)
    db = dbmod.get_session()
    try:
        row = db.query(PartTable).filter(PartTable.id == part.id).first()
        assert row is not None
        assert row.type == "text"
        assert row.content == "Hello world"
    finally:
        db.close()


def test_save_tool_part():
    part = create_tool_part("sess1", "msg1", "bash", "tc1")
    part.state = {"status": "completed", "output": "file.py", "input": {"command": "ls"}}
    part.time_completed = 12345
    save_part(part)
    db = dbmod.get_session()
    try:
        row = db.query(PartTable).filter(PartTable.id == part.id).first()
        assert row is not None
        assert row.tool == "bash"
        assert row.tool_call_id == "tc1"
    finally:
        db.close()


def test_save_parts_batch():
    p1 = create_text_part("s1", "m1")
    p1.content = "text1"
    p2 = create_text_part("s1", "m1")
    p2.content = "text2"
    save_parts([p1, p2])
    db = dbmod.get_session()
    try:
        rows = db.query(PartTable).filter(PartTable.session_id == "s1").all()
        assert len(rows) == 2
    finally:
        db.close()


def test_save_parts_empty():
    save_parts([])  # should not raise


# ── persist_turn tests ──


def _create_session_row(session_id: str) -> None:
    """Helper: insert a SessionTable row so persist_turn can touch it."""
    import time
    db = dbmod.get_session()
    try:
        db.add(SessionTable(
            id=session_id, project_id="test-proj", slug="test",
            directory="/tmp", title="test", version="0.1.0",
            time_created=int(time.time() * 1000),
            time_updated=int(time.time() * 1000),
        ))
        db.commit()
    finally:
        db.close()


def test_persist_turn_atomic_success():
    """persist_turn should save message + parts + touch session in one transaction."""
    _create_session_row("sess-atomic")
    msg = create_assistant_message("sess-atomic", "p1", "openai", "gpt-4o", "build")
    msg.tokens_input = 200
    msg.tokens_output = 100

    p1 = create_text_part("sess-atomic", msg.id)
    p1.content = "Hello"
    p2 = create_tool_part("sess-atomic", msg.id, "bash", "tc1")
    p2.state = {"status": "completed", "output": "ok", "input": {"command": "ls"}}

    persist_turn("sess-atomic", msg, [p1, p2])

    db = dbmod.get_session()
    try:
        # Message saved
        msg_row = db.query(MessageTable).filter(MessageTable.id == msg.id).first()
        assert msg_row is not None
        assert msg_row.tokens_input == 200

        # Parts saved
        parts = db.query(PartTable).filter(PartTable.session_id == "sess-atomic").all()
        assert len(parts) == 2

        # Session touched
        sess = db.query(SessionTable).filter(SessionTable.id == "sess-atomic").first()
        assert sess is not None
        assert sess.time_updated > 0
    finally:
        db.close()


def test_persist_turn_empty_parts():
    """persist_turn with no parts should still save message and touch session."""
    _create_session_row("sess-empty")
    msg = create_assistant_message("sess-empty", "p1", "anthropic", "claude-3", "build")

    persist_turn("sess-empty", msg, [])

    db = dbmod.get_session()
    try:
        msg_row = db.query(MessageTable).filter(MessageTable.id == msg.id).first()
        assert msg_row is not None
        parts = db.query(PartTable).filter(PartTable.session_id == "sess-empty").all()
        assert len(parts) == 0
    finally:
        db.close()


def test_persist_turn_rollback_on_error(monkeypatch):
    """If an error occurs mid-transaction, nothing should be committed."""
    _create_session_row("sess-rollback")
    msg = create_assistant_message("sess-rollback", "p1", "openai", "gpt-4o", "build")
    p1 = create_text_part("sess-rollback", msg.id)
    p1.content = "Hello"

    # Patch PartTable to raise on merge after message is already merged
    original_merge = dbmod.get_session_factory()().merge.__func__
    call_count = [0]

    def _failing_merge(self, instance, **kw):
        call_count[0] += 1
        if call_count[0] > 1:  # Fail on part merge (after message merge)
            raise RuntimeError("simulated DB error")
        return original_merge(self, instance, **kw)

    from sqlalchemy.orm import Session
    monkeypatch.setattr(Session, "merge", _failing_merge)

    with pytest.raises(RuntimeError, match="simulated DB error"):
        persist_turn("sess-rollback", msg, [p1])

    # Verify nothing was committed
    monkeypatch.undo()
    db = dbmod.get_session()
    try:
        msg_row = db.query(MessageTable).filter(MessageTable.id == msg.id).first()
        assert msg_row is None, "Message should not be saved after rollback"
        parts = db.query(PartTable).filter(PartTable.session_id == "sess-rollback").all()
        assert len(parts) == 0, "Parts should not be saved after rollback"
    finally:
        db.close()
