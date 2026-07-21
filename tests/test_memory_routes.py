"""FastAPI coverage for memory CRUD and inbox lifecycle."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mycode.server.app import create_app
from mycode.storage import database as db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "memory-api.db"))
    db.reset()
    yield TestClient(create_app()), tmp_path
    db.reset()


def test_memory_api_lifecycle(client):
    http, project = client
    params = {"directory": str(project)}
    created = http.post(
        "/memory",
        params=params,
        json={
            "subject": "API preference",
            "content": "Prefer compact API error messages.",
            "memory_type": "user_preference",
            "source_message_ids": ["m1"],
        },
    )
    assert created.status_code == 200
    memory_id = created.json()["id"]
    assert created.json()["status"] == "active"
    assert http.get("/memory", params=params).json()[0]["id"] == memory_id

    updated = http.patch(
        f"/memory/{memory_id}",
        params=params,
        json={"content": "Prefer compact and actionable API error messages."},
    )
    assert updated.status_code == 200
    new_id = updated.json()["id"]
    history = http.get(f"/memory/{new_id}/history", params=params).json()
    assert [item["status"] for item in history] == ["superseded", "active"]

    deleted = http.request("DELETE", f"/memory/{new_id}", params=params, json={"reason": "obsolete"})
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"


def test_memory_api_pending_inbox(client):
    http, project = client
    params = {"directory": str(project)}
    candidate = http.post(
        "/memory",
        params=params,
        json={"subject": "Candidate", "content": "Review this candidate before use.", "pending": True},
    ).json()
    assert http.get("/memory/inbox", params=params).json()[0]["id"] == candidate["id"]
    edited = http.patch(
        f"/memory/{candidate['id']}",
        params=params,
        json={"content": "Review this edited candidate before use."},
    )
    assert edited.status_code == 200
    assert edited.json()["id"] == candidate["id"]
    assert edited.json()["status"] == "pending"

    second = http.post(
        "/memory",
        params=params,
        json={"subject": "Second candidate", "content": "Approve this candidate too.", "pending": True},
    ).json()
    approved = http.post(
        "/memory/inbox/batch",
        params=params,
        json={"memory_ids": [candidate["id"], second["id"], "missing"], "action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json()["succeeded"] == [candidate["id"], second["id"]]
    assert "missing" in approved.json()["failed"]
    assert http.get("/memory/inbox", params=params).json() == []


def test_memory_api_scope_delete(client):
    http, project = client
    params = {"directory": str(project)}
    for subject in ("One", "Two"):
        response = http.post(
            "/memory",
            params=params,
            json={"subject": subject, "content": f"Project memory {subject}."},
        )
        assert response.status_code == 200

    deleted = http.request(
        "DELETE",
        "/memory/scope",
        params=params,
        json={"scope_type": "project", "reason": "privacy request"},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_roots": 2}
    assert http.get("/memory", params=params).json() == []
