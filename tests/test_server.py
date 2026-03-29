"""Tests for the FastAPI server app."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    monkeypatch.setattr("opencode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    monkeypatch.setattr("opencode.util.paths.GlobalPaths.config", staticmethod(lambda: tmp_path / "config"))
    import opencode.storage.database as dbmod
    dbmod.reset()

    from opencode.server.app import create_app
    app = create_app()
    return TestClient(app)


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "opencode"
    assert "version" in data


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_agent_list(client):
    resp = client.get("/agent")
    assert resp.status_code == 200
    agents = resp.json()
    assert isinstance(agents, list)
    names = {a["name"] for a in agents}
    assert "build" in names


def test_permission_list(client):
    resp = client.get("/permission")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_mcp_status(client):
    resp = client.get("/mcp")
    assert resp.status_code == 200


def test_project_current(client):
    resp = client.get("/project/current")
    assert resp.status_code == 200
