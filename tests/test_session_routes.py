from __future__ import annotations

from types import SimpleNamespace

import mycode.project.instance as inst
import mycode.storage.database as dbmod
import pytest
from fastapi.testclient import TestClient

from mycode.server.app import create_app


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "test-routes.db"))
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


def test_pause_routes_roundtrip(client: TestClient, tmp_path):
    from mycode.session.session import create

    session = create(title="pause-route")
    directory = str(tmp_path)

    resp = client.post(
        f"/session/{session.id}/pause",
        params={"directory": directory},
        json={
            "lastUserText": "继续完善后端接口",
            "partialText": "已经写完一部分",
            "model": "openai/test-model",
            "agent": "build",
            "pausedAt": 1710000000000,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is True
    assert body["state"]["lastUserText"] == "继续完善后端接口"

    resp = client.get(f"/session/{session.id}/pause", params={"directory": directory})
    assert resp.status_code == 200
    assert resp.json()["state"]["partialText"] == "已经写完一部分"

    resp = client.delete(f"/session/{session.id}/pause", params={"directory": directory})
    assert resp.status_code == 200

    resp = client.get(f"/session/{session.id}/pause", params={"directory": directory})
    assert resp.status_code == 200
    assert resp.json() == {"paused": False, "state": None}


def test_changes_route_returns_recent_files(client: TestClient, tmp_path):
    from mycode.session.message import create_assistant_message, create_tool_part, persist_turn
    from mycode.session.session import create

    session = create(title="changes-route")
    assistant = create_assistant_message(session.id, "parent-1", "openai", "test-model", "build")
    assistant.time_completed = 1710000000100
    part = create_tool_part(session.id, assistant.id, "edit", "call-1")
    part.state = {
        "input": {"file": "src/demo.py"},
        "output": "Edited src/demo.py (updated imports)",
    }
    part.time_completed = 1710000000200
    persist_turn(session.id, assistant, [part])

    resp = client.get(f"/session/{session.id}/changes", params={"directory": str(tmp_path)})
    assert resp.status_code == 200
    changes = resp.json()
    assert len(changes) == 1
    assert changes[0]["tool"] == "edit"
    assert changes[0]["filePath"] == "src/demo.py"


def test_resume_route_streams_and_clears_pause_state(client: TestClient, tmp_path, monkeypatch):
    from mycode.session.session import create, get_paused_run, set_paused_run

    session = create(title="resume-route")
    set_paused_run(session.id, last_user_text="继续处理未完成任务", partial_text="已输出部分内容")

    async def fake_prompt(prompt_input, bus, history=None):
        yield SimpleNamespace(type="started", data={"session_id": prompt_input.session_id})
        yield SimpleNamespace(type="done", data={"ok": True})

    monkeypatch.setattr("mycode.server.routes.session.prompt", fake_prompt)

    resp = client.post(f"/session/{session.id}/resume", params={"directory": str(tmp_path)})
    assert resp.status_code == 200
    assert "event: started" in resp.text
    assert "event: done" in resp.text
    assert get_paused_run(session.id) is None
