"""Tests for message persistence (save_message, save_parts)."""
import pytest
import opencode.project.instance as inst
import opencode.storage.database as dbmod
from opencode.session.message import (
    create_user_message, create_assistant_message, create_text_part, create_tool_part,
    save_message, save_part, save_parts, TextPart, ToolPart,
)
from opencode.storage.models import MessageTable, PartTable


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
