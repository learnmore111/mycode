from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opencode.server.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True, text=True)

    (tmp_path / "tracked.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    return tmp_path


def test_git_status_lists_modified_and_untracked_files(client: TestClient, git_repo: Path):
    (git_repo / "tracked.txt").write_text("hello\nworld\n", encoding="utf-8")
    (git_repo / "new_file.py").write_text("print('hi')\n", encoding="utf-8")

    response = client.get("/git/status", params={"directory": str(git_repo)})
    assert response.status_code == 200

    body = response.json()
    assert body["available"] is True
    assert body["clean"] is False
    assert body["summary"]["changed"] == 2

    files = {item["path"]: item for item in body["files"]}
    assert files["tracked.txt"]["status"] == "modified"
    assert files["new_file.py"]["status"] == "untracked"


def test_git_diff_returns_patch_for_selected_file(client: TestClient, git_repo: Path):
    (git_repo / "tracked.txt").write_text("hello\nworld\n", encoding="utf-8")

    response = client.get("/git/diff", params={"directory": str(git_repo), "path": "tracked.txt"})
    assert response.status_code == 200

    body = response.json()
    assert body["path"] == "tracked.txt"
    assert body["status"] == "modified"
    assert body["stats"]["additions"] == 1
    assert body["stats"]["deletions"] == 0
    assert "+world" in body["diff"]


def test_git_diff_returns_patch_for_untracked_file(client: TestClient, git_repo: Path):
    (git_repo / "scratch.ts").write_text("export const answer = 42\n", encoding="utf-8")

    response = client.get("/git/diff", params={"directory": str(git_repo), "path": "scratch.ts"})
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "untracked"
    assert body["stats"]["additions"] == 1
    assert "scratch.ts" in body["diff"]
