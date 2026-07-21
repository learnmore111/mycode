"""Tests for the FastAPI server app."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.config", staticmethod(lambda: tmp_path / "config"))
    import mycode.storage.database as dbmod
    dbmod.reset()

    from mycode.server.app import create_app
    app = create_app()
    return TestClient(app)


def test_root(client):
    resp = client.get("/api/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "mycode"
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


def test_serves_web_build_from_configured_directory(tmp_path, monkeypatch):
    web = tmp_path / "web"
    assets = web / "dist" / "assets"
    assets.mkdir(parents=True)
    (web / "dist" / "index.html").write_text("<main>Web shell</main>", encoding="utf-8")
    (assets / "app.js").write_text("globalThis.web = true", encoding="utf-8")
    monkeypatch.setenv("MYCODE_FRONTEND_DIR", str(web))

    from mycode.server.app import create_app

    standalone = TestClient(create_app())
    assert standalone.get("/").text == "<main>Web shell</main>"
    assert standalone.get("/workspace/example").text == "<main>Web shell</main>"
    assert "globalThis.web" in standalone.get("/assets/app.js").text
