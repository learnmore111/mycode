"""Tests for session SQLite persistence."""
import os, pytest
import opencode.project.instance as inst
import opencode.storage.database as dbmod


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    """Use an in-memory DB and set instance context for each test."""
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    dbmod.reset()
    dbmod.get_engine()  # init tables
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path), worktree=str(tmp_path),
        project=inst.ProjectInfo(id="test-proj", worktree=str(tmp_path)),
    ))
    yield
    token.reset()
    dbmod.reset()


def test_create_and_get():
    from opencode.session.session import create, get
    s = create(title="hello")
    assert s.title == "hello"
    loaded = get(s.id)
    assert loaded.title == "hello"
    assert loaded.project_id == "test-proj"


def test_list_sessions():
    from opencode.session.session import create, list_sessions
    create(title="a")
    create(title="b")
    sessions = list_sessions()
    assert len(sessions) >= 2
    titles = {s.title for s in sessions}
    assert "a" in titles and "b" in titles


def test_touch():
    from opencode.session.session import create, get, touch
    s = create(title="t")
    old = s.time_updated
    import time; time.sleep(0.01)
    touch(s.id)
    updated = get(s.id)
    assert updated.time_updated >= old


def test_set_title():
    from opencode.session.session import create, get, set_title
    s = create(title="old")
    set_title(s.id, "new")
    assert get(s.id).title == "new"


def test_remove():
    from opencode.session.session import create, remove, list_sessions, list_deleted, get
    s = create(title="rm")
    remove(s.id)
    # Should not appear in active sessions
    sessions = list_sessions()
    assert all(x.id != s.id for x in sessions)
    # Should appear in deleted sessions
    deleted = list_deleted()
    assert any(x.id == s.id for x in deleted)
    # Should still be gettable (soft-deleted, not destroyed)
    info = get(s.id)
    assert info.visible is False


def test_restore():
    from opencode.session.session import create, remove, restore, list_sessions, list_deleted
    s = create(title="restore-me")
    remove(s.id)
    # Verify it's in deleted
    assert any(x.id == s.id for x in list_deleted())
    assert all(x.id != s.id for x in list_sessions())
    # Restore
    restore(s.id)
    # Now back in active list
    assert any(x.id == s.id for x in list_sessions())
    assert all(x.id != s.id for x in list_deleted())


def test_restore_nonexistent():
    from opencode.session.session import restore
    with pytest.raises(KeyError):
        restore("nonexistent-id")


def test_set_summary_persists_diffs():
    from opencode.session.session import create, get, set_summary

    s = create(title="summary-diffs")
    summary = {
        "additions": 3,
        "deletions": 1,
        "files": 2,
        "diffs": ["src/app.py", {"file": "src/api.py"}],
    }

    set_summary(s.id, summary)

    loaded = get(s.id)
    assert loaded.summary is not None
    assert loaded.summary["additions"] == 3
    assert loaded.summary["deletions"] == 1
    assert loaded.summary["files"] == 2
    assert loaded.summary["diffs"] == summary["diffs"]


def test_paused_run_roundtrip():
    from opencode.session.session import clear_paused_run, create, get_paused_run, set_paused_run

    s = create(title="paused")
    saved = set_paused_run(
        s.id,
        last_user_text="继续修复接口",
        partial_text="已完成一半",
        model="openai/test-model",
        agent="build",
        paused_at=1234567890,
    )

    assert saved.session_id == s.id
    assert saved.last_user_text == "继续修复接口"

    loaded = get_paused_run(s.id)
    assert loaded is not None
    assert loaded.session_id == s.id
    assert loaded.last_user_text == "继续修复接口"
    assert loaded.partial_text == "已完成一半"
    assert loaded.model == "openai/test-model"
    assert loaded.agent == "build"
    assert loaded.paused_at == 1234567890

    clear_paused_run(s.id)
    assert get_paused_run(s.id) is None
