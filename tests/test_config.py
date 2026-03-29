"""Tests for the config system."""
import json
import os
import pytest
from opencode.config.config import parse_jsonc, get, invalidate, ConfigParseError
from opencode.config.models import Config, AgentConfig
from opencode.config.paths import global_config_file, project_files, config_directories


@pytest.fixture(autouse=True)
def _clear(tmp_path, monkeypatch):
    invalidate()
    monkeypatch.setattr("opencode.util.paths.GlobalPaths.config", staticmethod(lambda: tmp_path / "config"))
    monkeypatch.setattr("opencode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    yield
    invalidate()


def test_parse_jsonc_basic():
    data = parse_jsonc('{"model": "anthropic/claude-3"}')
    assert data["model"] == "anthropic/claude-3"


def test_parse_jsonc_with_comments():
    data = parse_jsonc('{\n  // comment\n  "model": "test"\n}')
    assert data["model"] == "test"


def test_parse_jsonc_invalid():
    with pytest.raises(ConfigParseError):
        parse_jsonc("not json at all", "test.json")


def test_parse_jsonc_non_object():
    with pytest.raises(ConfigParseError):
        parse_jsonc('"just a string"')


def test_get_default():
    cfg = get()
    assert isinstance(cfg, Config)
    assert cfg.username  # should be set to current user


def test_get_with_directory(tmp_path):
    cfg_file = tmp_path / "opencode.json"
    cfg_file.write_text(json.dumps({"model": "openai/gpt-4o"}))
    cfg = get(str(tmp_path))
    assert cfg.model == "openai/gpt-4o"


def test_config_model_validation():
    cfg = Config(model="anthropic/claude-3", default_agent="build")
    assert cfg.model == "anthropic/claude-3"
    assert cfg.default_agent == "build"


def test_agent_config():
    ac = AgentConfig(model="openai/gpt-4o", temperature=0.7, steps=50)
    assert ac.model == "openai/gpt-4o"
    assert ac.temperature == 0.7


def test_project_files(tmp_path):
    (tmp_path / "opencode.json").write_text("{}")
    files = project_files(str(tmp_path))
    assert len(files) == 1
    assert files[0].endswith("opencode.json")


def test_project_files_empty(tmp_path):
    files = project_files(str(tmp_path))
    assert files == []


def test_config_directories(tmp_path):
    dot = tmp_path / ".opencode"
    dot.mkdir()
    dirs = config_directories(str(tmp_path))
    assert any(".opencode" in d for d in dirs)


def test_global_config_file():
    p = global_config_file()
    assert "opencode" in str(p)
