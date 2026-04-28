"""Tests for module enhancements based on ARCHITECTURE_SUPPLEMENT.md.

Covers:
- Message system: SystemMessage, isMeta/origin, normalization pipeline
- Memory freshness: age calculation and staleness warnings
- Auth system: OAuth expiry, env auto-discovery, auth status
- Skill tool: home directory search
"""
from __future__ import annotations

import os
import time

import pytest

from mycode.auth.auth import OAuthAuth, auth_source, get_env_key
from mycode.session.memory.memory import memory_age_days, memory_age_text, memory_freshness_note
from mycode.session.message import (
    SystemMessage,
    UserMessage,
    create_system_message,
    create_user_message,
    normalize_messages_for_api,
)


# ── SystemMessage ──────────────────────────────────────────────────


def test_system_message_creation():
    msg = create_system_message("s1", "Compaction complete", "info")
    assert msg.role == "system"
    assert msg.subtype == "info"
    assert msg.content == "Compaction complete"
    assert msg.session_id == "s1"
    assert msg.id  # should have an auto-generated ID


def test_system_message_compact_boundary():
    msg = create_system_message("s1", "[Summary]...", "compact_boundary")
    assert msg.subtype == "compact_boundary"


def test_system_message_local_command():
    msg = create_system_message("s1", "/compact output", "local_command")
    assert msg.subtype == "local_command"


# ── UserMessage metadata ───────────────────────────────────────────


def test_user_message_is_meta():
    msg = create_user_message("s1", is_meta=True, origin="system")
    assert msg.is_meta is True
    assert msg.origin == "system"


def test_user_message_default_origin():
    msg = create_user_message("s1")
    assert msg.origin == "human"
    assert msg.is_meta is False


# ── Message normalization pipeline ─────────────────────────────────


def test_normalize_filters_local_command():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "system", "subtype": "local_command", "content": "/compact output"},
        {"role": "assistant", "content": "hi"},
    ]
    result = normalize_messages_for_api(messages)
    assert len(result) == 2
    assert result[0]["content"] == "hello"
    assert result[1]["content"] == "hi"


def test_normalize_filters_compact_boundary():
    messages = [
        {"role": "system", "subtype": "compact_boundary", "content": "[Summary]"},
        {"role": "user", "content": "continue"},
    ]
    result = normalize_messages_for_api(messages)
    assert len(result) == 1
    assert result[0]["content"] == "continue"


def test_normalize_converts_system_info_to_user():
    messages = [
        {"role": "system", "subtype": "info", "content": "Model switched to claude-4"},
    ]
    result = normalize_messages_for_api(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert "Model switched" in result[0]["content"]


def test_normalize_include_system():
    messages = [
        {"role": "system", "subtype": "info", "content": "test"},
    ]
    result = normalize_messages_for_api(messages, include_system=True)
    assert len(result) == 1
    assert result[0]["role"] == "system"



# ── Memory freshness ───────────────────────────────────────────────


def test_memory_age_today():
    now_ms = time.time() * 1000
    assert memory_age_days(now_ms) == 0
    assert memory_age_text(now_ms) == "today"


def test_memory_freshness_note_fresh():
    now_ms = time.time() * 1000
    assert memory_freshness_note(now_ms) is None


def test_memory_freshness_note_stale():
    two_days_ago_ms = (time.time() - 2 * 86400) * 1000
    note = memory_freshness_note(two_days_ago_ms)
    assert note is not None
    assert "system-reminder" in note
    assert "2 days ago" in note
    assert "outdated" in note


def test_memory_age_old():
    thirty_days_ago_ms = (time.time() - 30 * 86400) * 1000
    assert memory_age_days(thirty_days_ago_ms) == 30
    assert memory_age_text(thirty_days_ago_ms) == "30 days ago"


# ── Auth system enhancements ──────────────────────────────────────


def test_oauth_not_expired():
    auth = OAuthAuth(type="oauth", access="token", expires=int(time.time()) + 3600)
    assert auth.is_expired is False
    assert auth.expires_in_seconds > 0


def test_oauth_expired():
    auth = OAuthAuth(type="oauth", access="token", expires=int(time.time()) - 100)
    assert auth.is_expired is True
    assert auth.expires_in_seconds < 0


def test_oauth_no_expiry():
    auth = OAuthAuth(type="oauth", access="token")
    assert auth.is_expired is False
    assert auth.expires_in_seconds is None


def test_get_env_key_found(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert get_env_key("anthropic") == "sk-test-123"


def test_get_env_key_not_found(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert get_env_key("anthropic") is None


def test_get_env_key_unknown_provider():
    assert get_env_key("unknown_provider_xyz") is None


def test_auth_source_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert auth_source("openai") == "env"


def test_auth_source_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Ensure no stored auth file
    assert auth_source("nonexistent_provider_xyz") == "none"


# ── Skill tool home directory search ──────────────────────────────


@pytest.mark.asyncio
async def test_skill_home_dir_search(tmp_path, monkeypatch):
    import mycode.project.instance as inst
    from mycode.tool.base import ToolContext
    from mycode.tool.skill import tool as skill_tool

    # Create skill in home directory
    home_skills = tmp_path / "home_skills"
    home_skills.mkdir(parents=True)
    (home_skills / "python.md").write_text("# Python\nUse type hints.")

    # Monkey-patch Path.home to return our tmp dir parent
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # Create a project without skills dir
    project = tmp_path / "project"
    project.mkdir()

    token = inst.set_context(inst.InstanceContext(
        directory=str(project), worktree=str(project),
        project=inst.ProjectInfo(id="t", worktree=str(project)),
    ))
    try:
        # The skill should NOT be found because home_skills != ~/.mycode/skills
        # Create proper path
        proper_home = tmp_path / ".mycode" / "skills"
        proper_home.mkdir(parents=True)
        (proper_home / "python.md").write_text("# Python\nUse type hints.")

        ctx = ToolContext(session_id="test", message_id="m1", agent="build")
        result = await skill_tool.execute({"name": "python"}, ctx)
        assert result.metadata["found"] is True
        assert "type hints" in result.output
    finally:
        token.reset()


@pytest.mark.asyncio
async def test_skill_lists_available(tmp_path):
    import mycode.project.instance as inst
    from mycode.tool.base import ToolContext
    from mycode.tool.skill import tool as skill_tool

    # Create project with skills
    skills_dir = tmp_path / ".mycode" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "python.md").write_text("py")
    (skills_dir / "rust.md").write_text("rs")

    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path), worktree=str(tmp_path),
        project=inst.ProjectInfo(id="t", worktree=str(tmp_path)),
    ))
    try:
        ctx = ToolContext(session_id="test", message_id="m1", agent="build")
        result = await skill_tool.execute({"name": "nonexistent"}, ctx)
        assert result.is_error
        assert "python" in result.output.lower()
        assert "rust" in result.output.lower()
    finally:
        token.reset()
