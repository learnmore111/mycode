"""Comprehensive tests for session export / import / fork.

Covers:
- archive.py core logic (export/import/fork round-trips, edge cases)
- API routes (GET /session/{id}/export, POST /session/import, POST /session/{id}/fork)
- ToolPart / FilePart preservation across export/import/fork
- Compaction events included in archive
- CLI-level import_session_json / export_session_json convenience wrappers
- Negative tests (missing session, bad archive, invalid turn)
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

import mycode.project.instance as inst
import mycode.storage.database as dbmod
from mycode.server.app import create_app
from mycode.session.archive import (
    ARCHIVE_FORMAT,
    ARCHIVE_VERSION,
    export_session,
    export_session_json,
    fork_session,
    import_session,
    import_session_json,
)
from mycode.session.message import (
    create_assistant_message,
    create_file_part,
    create_text_part,
    create_tool_part,
    create_user_message,
    persist_turn,
    save_compaction_event,
    save_message,
    save_part,
)
from mycode.session.session import create as create_session
from mycode.session.session import get as get_session
from mycode.session.session import list_sessions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "export_fork.db"))
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


@pytest.fixture
def client():
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_turn(session_id: str, text_in: str, text_out: str, turn: int) -> None:
    """Add one user + assistant exchange to a session."""
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


def _seed_tool_turn(session_id: str, text_in: str, tool_name: str, tool_output: str, turn: int) -> None:
    """Add a turn with a tool call."""
    user_msg = create_user_message(session_id)
    save_message(user_msg)
    p = create_text_part(session_id, user_msg.id)
    p.content = text_in
    save_part(p)
    a_msg = create_assistant_message(session_id, user_msg.id, "openai", "gpt-4", "build")
    a_msg.turn_number = turn  # type: ignore[attr-defined]
    tool_part = create_tool_part(session_id, a_msg.id, tool_name, f"call_{turn}")
    tool_part.state = {"status": "done", "input": {"file": "demo.py"}, "output": tool_output}
    tool_part.time_completed = int(time.time() * 1000)
    persist_turn(session_id, a_msg, [tool_part])


def _seed_file_turn(session_id: str, text_in: str, turn: int) -> None:
    """Add a turn with a user file attachment."""
    user_msg = create_user_message(session_id)
    save_message(user_msg)
    text_p = create_text_part(session_id, user_msg.id)
    text_p.content = text_in
    save_part(text_p)
    fp = create_file_part(session_id, user_msg.id, mime_type="image/png", content="base64data", filename="img.png")
    save_part(fp)
    a_msg = create_assistant_message(session_id, user_msg.id, "openai", "gpt-4", "build")
    a_msg.turn_number = turn  # type: ignore[attr-defined]
    a_part = create_text_part(session_id, a_msg.id)
    a_part.content = "I see an image"
    persist_turn(session_id, a_msg, [a_part])


# ===================================================================
# 1. Export tests
# ===================================================================

class TestExport:
    def test_export_basic_shape(self):
        s = create_session(title="export-shape")
        _seed_turn(s.id, "hello", "world", turn=1)
        archive = export_session(s.id)
        assert archive["format"] == ARCHIVE_FORMAT
        assert archive["version"] == ARCHIVE_VERSION
        assert isinstance(archive["exported_at"], int)
        assert archive["session"]["title"] == "export-shape"
        assert len(archive["messages"]) == 2
        assert any(m["role"] == "assistant" for m in archive["messages"])
        assert any(m["role"] == "user" for m in archive["messages"])
        # project_id stripped for portability
        assert "project_id" not in archive["session"]

    def test_export_multi_turn(self):
        s = create_session(title="export-multi")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        _seed_turn(s.id, "q3", "a3", turn=3)
        archive = export_session(s.id)
        assert len(archive["messages"]) == 6  # 3 user + 3 assistant

    def test_export_preserves_tool_parts(self):
        s = create_session(title="export-tool")
        _seed_tool_turn(s.id, "edit this", "edit", "Edited demo.py", turn=1)
        archive = export_session(s.id)
        assistant_msgs = [m for m in archive["messages"] if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        tool_parts = assistant_msgs[0].get("parts", [])
        assert len(tool_parts) == 1
        assert tool_parts[0]["type"] == "tool"
        assert tool_parts[0]["tool"] == "edit"
        assert tool_parts[0]["state"]["output"] == "Edited demo.py"

    def test_export_preserves_file_parts(self):
        s = create_session(title="export-file")
        _seed_file_turn(s.id, "see this image", turn=1)
        archive = export_session(s.id)
        user_msgs = [m for m in archive["messages"] if m["role"] == "user"]
        assert len(user_msgs) == 1
        file_parts = [p for p in user_msgs[0].get("parts", []) if p["type"] == "file"]
        assert len(file_parts) == 1
        assert file_parts[0]["content"] == "base64data"
        # mime_type is stored in tool_call_id column
        assert file_parts[0]["tool_call_id"] == "image/png"
        # filename is stored in tool column
        assert file_parts[0]["tool"] == "img.png"

    def test_export_includes_compaction_events(self):
        s = create_session(title="export-compact")
        _seed_turn(s.id, "q1", "a1", turn=1)
        save_compaction_event(
            session_id=s.id,
            iteration=0,
            metrics={"old_message_count": 4, "old_message_tokens": 1000, "summary_length": 200, "removed_turn_count": 2},
            old_messages=[{"role": "user", "content": "old"}],
            summary="compressed summary",
        )
        archive = export_session(s.id)
        events = archive.get("compaction_events", [])
        assert len(events) == 1
        assert events[0]["summary"] == "compressed summary"

    def test_export_unknown_session_raises(self):
        with pytest.raises(KeyError):
            export_session("does_not_exist")

    def test_export_session_json_wrapper(self):
        s = create_session(title="json-wrap")
        _seed_turn(s.id, "q", "a", turn=1)
        payload = export_session_json(s.id)
        parsed = json.loads(payload)
        assert parsed["format"] == ARCHIVE_FORMAT

    def test_export_json_round_trip(self):
        s = create_session(title="json-rt")
        _seed_turn(s.id, "q", "a", turn=1)
        archive = export_session(s.id)
        dumped = json.dumps(archive, ensure_ascii=False)
        assert json.loads(dumped) == archive


# ===================================================================
# 2. Import tests
# ===================================================================

class TestImport:
    def test_import_creates_new_session(self):
        s = create_session(title="import-src")
        _seed_turn(s.id, "q1", "a1", turn=1)
        archive = export_session(s.id)
        imported = import_session(archive, title_prefix="[copy] ")
        assert imported.id != s.id
        assert imported.title.startswith("[copy]")

    def test_import_preserves_message_content(self):
        s = create_session(title="import-content")
        _seed_turn(s.id, "user-text", "assistant-text", turn=1)
        archive = export_session(s.id)
        imported = import_session(archive)
        re_archive = export_session(imported.id)
        texts = [(m["role"], next((p["content"] for p in m.get("parts", []) if p.get("type") == "text"), None))
                 for m in re_archive["messages"]]
        assert ("user", "user-text") in texts
        assert ("assistant", "assistant-text") in texts

    def test_import_preserves_tool_parts(self):
        s = create_session(title="import-tool")
        _seed_tool_turn(s.id, "run edit", "edit", "Edited file.py", turn=1)
        archive = export_session(s.id)
        imported = import_session(archive)
        re_archive = export_session(imported.id)
        tool_parts = [p for m in re_archive["messages"] for p in m.get("parts", []) if p["type"] == "tool"]
        assert len(tool_parts) == 1
        assert tool_parts[0]["tool"] == "edit"
        assert tool_parts[0]["state"]["output"] == "Edited file.py"

    def test_import_preserves_file_parts(self):
        s = create_session(title="import-file")
        _seed_file_turn(s.id, "look at image", turn=1)
        archive = export_session(s.id)
        imported = import_session(archive)
        re_archive = export_session(imported.id)
        file_parts = [p for m in re_archive["messages"] for p in m.get("parts", []) if p["type"] == "file"]
        assert len(file_parts) == 1
        assert file_parts[0]["content"] == "base64data"

    def test_import_keep_id(self):
        s = create_session(title="keep-id")
        _seed_turn(s.id, "q", "a", turn=1)
        archive = export_session(s.id)
        # Delete original first so we don't collide
        from mycode.session.session import remove
        remove(s.id)
        imported = import_session(archive, new_id=False)
        # ID comes from the archive which omits project_id, so we check title
        assert imported.title == "keep-id"

    def test_import_session_json_wrapper(self):
        s = create_session(title="json-import")
        _seed_turn(s.id, "q", "a", turn=1)
        payload = export_session_json(s.id)
        imported = import_session_json(payload)
        assert imported.id != s.id
        assert imported.title == "json-import"

    def test_import_rejects_bad_format(self):
        with pytest.raises(ValueError, match="format"):
            import_session({"format": "wrong", "version": 1, "session": {}, "messages": []})

    def test_import_rejects_bad_version(self):
        with pytest.raises(ValueError, match="version"):
            import_session({"format": ARCHIVE_FORMAT, "version": 99, "session": {}, "messages": []})

    def test_import_rejects_missing_session(self):
        with pytest.raises(ValueError, match="session"):
            import_session({"format": ARCHIVE_FORMAT, "version": 1, "session": "not-dict", "messages": []})

    def test_import_rejects_missing_messages(self):
        with pytest.raises(ValueError, match="messages"):
            import_session({"format": ARCHIVE_FORMAT, "version": 1, "session": {}, "messages": "not-list"})

    def test_import_session_json_rejects_non_dict(self):
        with pytest.raises(ValueError, match="JSON object"):
            import_session_json("[1,2,3]")

    def test_import_visible_in_list(self):
        s = create_session(title="list-visible")
        _seed_turn(s.id, "q", "a", turn=1)
        archive = export_session(s.id)
        imported = import_session(archive, title_prefix="[imported] ")
        all_sessions = list_sessions()
        ids = [x.id for x in all_sessions]
        assert imported.id in ids


# ===================================================================
# 3. Fork tests
# ===================================================================

class TestFork:
    def test_fork_basic(self):
        s = create_session(title="fork-basic")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        forked = fork_session(s.id, turn=1)
        assert forked.id != s.id
        assert forked.parent_id == s.id
        assert "fork @turn1" in forked.title

    def test_fork_custom_title(self):
        s = create_session(title="fork-title")
        _seed_turn(s.id, "q1", "a1", turn=1)
        forked = fork_session(s.id, turn=1, title="my branch")
        assert forked.title == "my branch"

    def test_fork_trims_messages_after_turn(self):
        s = create_session(title="fork-trim")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        _seed_turn(s.id, "q3", "a3", turn=3)
        forked = fork_session(s.id, turn=2)
        arch = export_session(forked.id)
        # Should contain turns 1 and 2 (user + assistant each = 4 messages)
        assistants = [m for m in arch["messages"] if m["role"] == "assistant"]
        assert len(assistants) == 2
        contents = [(a["parts"][0]["content"]) for a in assistants]
        assert "a1" in contents
        assert "a2" in contents
        assert "a3" not in contents

    def test_fork_preserves_tool_parts(self):
        s = create_session(title="fork-tool")
        _seed_tool_turn(s.id, "edit file", "edit", "Edited src/main.py", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        forked = fork_session(s.id, turn=1)
        arch = export_session(forked.id)
        tool_parts = [p for m in arch["messages"] for p in m.get("parts", []) if p["type"] == "tool"]
        assert len(tool_parts) == 1
        assert tool_parts[0]["state"]["output"] == "Edited src/main.py"

    def test_fork_preserves_file_parts(self):
        s = create_session(title="fork-file")
        _seed_file_turn(s.id, "see image", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        forked = fork_session(s.id, turn=1)
        arch = export_session(forked.id)
        file_parts = [p for m in arch["messages"] for p in m.get("parts", []) if p["type"] == "file"]
        assert len(file_parts) == 1

    def test_fork_clears_summary(self):
        from mycode.session.session import set_summary
        s = create_session(title="fork-summary")
        _seed_turn(s.id, "q1", "a1", turn=1)
        set_summary(s.id, {"additions": 10, "deletions": 2, "files": 3})
        forked = fork_session(s.id, turn=1)
        info = get_session(forked.id)
        assert info.summary is None

    def test_fork_does_not_mutate_source(self):
        s = create_session(title="fork-immutable")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        fork_session(s.id, turn=1)
        # Source should still have all messages
        arch = export_session(s.id)
        assert len(arch["messages"]) == 4

    def test_fork_unknown_turn_raises(self):
        s = create_session(title="fork-bad-turn")
        _seed_turn(s.id, "q1", "a1", turn=1)
        with pytest.raises(KeyError, match="turn 99"):
            fork_session(s.id, turn=99)

    def test_fork_turn_zero_raises(self):
        s = create_session(title="fork-zero")
        _seed_turn(s.id, "q1", "a1", turn=1)
        with pytest.raises(ValueError, match="turn must be >= 1"):
            fork_session(s.id, turn=0)

    def test_fork_negative_turn_raises(self):
        s = create_session(title="fork-neg")
        _seed_turn(s.id, "q1", "a1", turn=1)
        with pytest.raises(ValueError):
            fork_session(s.id, turn=-1)

    def test_fork_unknown_session_raises(self):
        with pytest.raises(KeyError):
            fork_session("nonexistent_session", turn=1)

    def test_fork_at_last_turn(self):
        """Forking at the last turn should include everything."""
        s = create_session(title="fork-last")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        forked = fork_session(s.id, turn=2)
        arch = export_session(forked.id)
        assistants = [m for m in arch["messages"] if m["role"] == "assistant"]
        assert len(assistants) == 2

    def test_double_fork(self):
        """Fork a fork — lineage chain should work."""
        s = create_session(title="fork-chain")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        fork1 = fork_session(s.id, turn=1)
        assert fork1.parent_id == s.id
        # Add a turn to fork1 before forking again
        _seed_turn(fork1.id, "q-fork", "a-fork", turn=2)
        fork2 = fork_session(fork1.id, turn=2)
        assert fork2.parent_id == fork1.id
        # Fork2 should have 2 assistant turns
        arch = export_session(fork2.id)
        assistants = [m for m in arch["messages"] if m["role"] == "assistant"]
        assert len(assistants) == 2


# ===================================================================
# 4. API route tests
# ===================================================================

class TestAPIRoutes:
    def test_export_route(self, client, tmp_path):
        s = create_session(title="api-export")
        _seed_turn(s.id, "q1", "a1", turn=1)
        resp = client.get(f"/session/{s.id}/export", params={"directory": str(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == ARCHIVE_FORMAT
        assert data["session"]["title"] == "api-export"

    def test_export_route_404(self, client, tmp_path):
        resp = client.get("/session/not_a_session/export", params={"directory": str(tmp_path)})
        assert resp.status_code == 404

    def test_import_route(self, client, tmp_path):
        s = create_session(title="api-import")
        _seed_turn(s.id, "q1", "a1", turn=1)
        archive = export_session(s.id)
        archive["_new_id"] = True
        archive["_title_prefix"] = "[api] "
        resp = client.post("/session/import", params={"directory": str(tmp_path)}, json=archive)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"].startswith("[api]")
        assert data["id"] != s.id

    def test_import_route_bad_body(self, client, tmp_path):
        resp = client.post("/session/import", params={"directory": str(tmp_path)}, json={"format": "bad"})
        assert resp.status_code == 400

    def test_fork_route(self, client, tmp_path):
        s = create_session(title="api-fork")
        _seed_turn(s.id, "q1", "a1", turn=1)
        _seed_turn(s.id, "q2", "a2", turn=2)
        resp = client.post(
            f"/session/{s.id}/fork",
            params={"directory": str(tmp_path)},
            json={"turn": 1, "title": "api forked"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "api forked"
        assert data["parentID"] == s.id

    def test_fork_route_bad_turn(self, client, tmp_path):
        s = create_session(title="api-fork-bad")
        _seed_turn(s.id, "q1", "a1", turn=1)
        resp = client.post(
            f"/session/{s.id}/fork",
            params={"directory": str(tmp_path)},
            json={"turn": 0},
        )
        assert resp.status_code == 400

    def test_fork_route_missing_turn(self, client, tmp_path):
        s = create_session(title="api-fork-no-turn")
        _seed_turn(s.id, "q1", "a1", turn=1)
        resp = client.post(
            f"/session/{s.id}/fork",
            params={"directory": str(tmp_path)},
            json={},
        )
        assert resp.status_code == 400

    def test_fork_route_404(self, client, tmp_path):
        resp = client.post(
            "/session/not_a_session/fork",
            params={"directory": str(tmp_path)},
            json={"turn": 1},
        )
        assert resp.status_code == 404

    def test_fork_route_nonexistent_turn(self, client, tmp_path):
        s = create_session(title="api-fork-bad-turn")
        _seed_turn(s.id, "q1", "a1", turn=1)
        resp = client.post(
            f"/session/{s.id}/fork",
            params={"directory": str(tmp_path)},
            json={"turn": 99},
        )
        assert resp.status_code == 404
