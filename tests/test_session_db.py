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
    from opencode.session.session import create, remove, list_sessions
    s = create(title="rm")
    remove(s.id)
    sessions = list_sessions()
    assert all(x.id != s.id for x in sessions)
