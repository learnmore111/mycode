"""Tests for mycode.orchestration.registry.agent_registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mycode.orchestration.registry.agent_registry import (
    AgentLoadError,
    AgentRegistry,
    _merge_agents,
    agentinfo_from_frontmatter,
    parse_frontmatter,
)

if TYPE_CHECKING:
    from pathlib import Path


# --- parse_frontmatter -----------------------------------------------------


def test_parse_frontmatter_basic() -> None:
    text = "---\ndescription: hi\nmode: subagent\n---\nBody content here."
    data, body = parse_frontmatter(text)
    assert data == {"description": "hi", "mode": "subagent"}
    assert body == "Body content here."


def test_parse_frontmatter_missing() -> None:
    data, body = parse_frontmatter("just a body, no frontmatter")
    assert data == {}
    assert body == "just a body, no frontmatter"


def test_parse_frontmatter_unterminated() -> None:
    data, body = parse_frontmatter("---\ndescription: oops\nno closing marker")
    assert data == {}
    assert "no closing marker" in body


def test_parse_frontmatter_invalid_yaml() -> None:
    text = "---\ndescription: [unclosed\n---\nbody"
    with pytest.raises(AgentLoadError):
        parse_frontmatter(text)


def test_parse_frontmatter_not_mapping() -> None:
    text = "---\n- just\n- a list\n---\nbody"
    with pytest.raises(AgentLoadError):
        parse_frontmatter(text)


# --- agentinfo_from_frontmatter -------------------------------------------


def test_agentinfo_basic() -> None:
    info = agentinfo_from_frontmatter(
        "reviewer",
        {
            "description": "Code reviewer",
            "mode": "subagent",
            "role": "reviewer",
            "tools": ["read", "grep"],
            "max_turns": 15,
            "isolation": "worktree",
            "model": "anthropic/claude-sonnet-4-5",
            "permission": [{"permission": "edit", "pattern": "*", "action": "deny"}],
        },
        "You are a reviewer.",
        source="project",
        source_path="/tmp/reviewer.md",
    )
    assert info.name == "reviewer"
    assert info.mode == "subagent"
    assert info.role == "reviewer"
    assert info.tools == ["read", "grep"]
    assert info.max_turns == 15
    assert info.isolation == "worktree"
    assert info.model == {"providerID": "anthropic", "modelID": "claude-sonnet-4-5"}
    assert info.permission == [{"permission": "edit", "pattern": "*", "action": "deny"}]
    assert info.prompt == "You are a reviewer."
    assert info.source == "project"
    assert info.source_path == "/tmp/reviewer.md"


def test_agentinfo_unknown_field_rejected() -> None:
    with pytest.raises(AgentLoadError, match="unknown frontmatter"):
        agentinfo_from_frontmatter(
            "x",
            {"bogus_field": 1},
            "body",
            source="project",
        )


def test_agentinfo_invalid_mode() -> None:
    with pytest.raises(AgentLoadError, match="invalid mode"):
        agentinfo_from_frontmatter("x", {"mode": "nope"}, "b", source="project")


def test_agentinfo_invalid_isolation() -> None:
    with pytest.raises(AgentLoadError, match="invalid isolation"):
        agentinfo_from_frontmatter("x", {"isolation": "galaxy"}, "b", source="project")


def test_agentinfo_invalid_tools_type() -> None:
    with pytest.raises(AgentLoadError, match="tools must be a list"):
        agentinfo_from_frontmatter("x", {"tools": "read,grep"}, "b", source="project")


def test_agentinfo_invalid_model_string() -> None:
    with pytest.raises(AgentLoadError, match="providerID/modelID"):
        agentinfo_from_frontmatter("x", {"model": "noSlash"}, "b", source="project")


def test_agentinfo_prompt_field_overrides_body() -> None:
    info = agentinfo_from_frontmatter(
        "x",
        {"prompt": "From field"},
        "This body is ignored",
        source="project",
    )
    assert info.prompt == "From field"


# --- _merge_agents ---------------------------------------------------------


def test_merge_child_fills_blanks_from_parent() -> None:
    parent = agentinfo_from_frontmatter(
        "p",
        {
            "description": "parent desc",
            "mode": "subagent",
            "role": "worker",
            "tools": ["read", "grep"],
            "permission": [{"permission": "read", "pattern": "*", "action": "allow"}],
            "options": {"a": 1, "b": 2},
        },
        "parent prompt",
        source="builtin",
    )
    child = agentinfo_from_frontmatter(
        "c",
        {"description": "", "options": {"b": 99, "c": 3}},
        "child prompt",
        source="project",
    )
    # Simulate the explicit-key set the registry would pass:
    # child frontmatter specified 'description' + 'options' + body-prompt.
    merged = _merge_agents(
        parent, child,
        child_explicit={"description", "options", "prompt"},
    )
    # Child keeps own prompt + identity.
    assert merged.prompt == "child prompt"
    # Description: child's was empty but explicit — treat blank as override
    # only when we want parent-fill semantics.  With explicit_keys semantics,
    # the child wins even when blank.
    assert merged.description == ""
    # Tools inherited because child didn't set them.
    assert merged.tools == ["read", "grep"]
    # Role / mode inherited because child didn't specify them in frontmatter.
    assert merged.role == "worker"
    assert merged.mode == "subagent"
    # Permission = parent rules then child rules (child has none).
    assert merged.permission == [{"permission": "read", "pattern": "*", "action": "allow"}]
    # Options shallow-merged.
    assert merged.options == {"a": 1, "b": 99, "c": 3}


def test_merge_without_explicit_hint_inherits_blanks() -> None:
    """Fallback path: when callers don't pass explicit_keys, blank scalars inherit."""
    parent = agentinfo_from_frontmatter(
        "p",
        {"description": "parent desc", "mode": "subagent", "role": "worker"},
        "parent prompt",
        source="builtin",
    )
    # Construct child manually with blanks.
    from mycode.agent.agent import AgentInfo
    child = AgentInfo(name="c", mode="primary", prompt="child prompt")
    merged = _merge_agents(parent, child)
    assert merged.prompt == "child prompt"
    assert merged.description == "parent desc"
    assert merged.role == "worker"


def test_merge_child_tools_replace_parent() -> None:
    parent = agentinfo_from_frontmatter(
        "p", {"tools": ["read", "grep"]}, "b", source="builtin"
    )
    child = agentinfo_from_frontmatter(
        "c", {"tools": ["edit"]}, "b", source="project"
    )
    merged = _merge_agents(parent, child)
    assert merged.tools == ["edit"]


def test_merge_permission_concatenation() -> None:
    parent = agentinfo_from_frontmatter(
        "p",
        {"permission": [{"permission": "*", "pattern": "*", "action": "deny"}]},
        "b",
        source="builtin",
    )
    child = agentinfo_from_frontmatter(
        "c",
        {"permission": [{"permission": "read", "pattern": "*", "action": "allow"}]},
        "b",
        source="project",
    )
    merged = _merge_agents(parent, child)
    assert merged.permission == [
        {"permission": "*", "pattern": "*", "action": "deny"},
        {"permission": "read", "pattern": "*", "action": "allow"},
    ]


# --- Registry discovery ----------------------------------------------------


def _write_agent(base: Path, name: str, frontmatter: str, body: str = "") -> None:
    (base / ".mycode" / "agents").mkdir(parents=True, exist_ok=True)
    (base / ".mycode" / "agents" / f"{name}.md").write_text(
        f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8"
    )


def test_registry_project_discovery(tmp_path: Path) -> None:
    _write_agent(
        tmp_path,
        "reviewer",
        "description: pr review\nmode: subagent\nrole: reviewer",
        "review this code",
    )
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    names = [e.name for e in reg.list_entries()]
    assert "reviewer" in names
    info = reg.resolve("reviewer")
    assert info.role == "reviewer"
    assert info.prompt == "review this code"
    assert info.source == "project"


def test_registry_project_overrides_builtin(tmp_path: Path) -> None:
    _write_agent(
        tmp_path,
        "explore",
        "description: project-custom explore\nmode: subagent",
        "custom prompt",
    )
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    info = reg.resolve("explore")
    assert info.source == "project"
    assert info.description == "project-custom explore"
    assert info.prompt == "custom prompt"


def test_registry_extends_resolution(tmp_path: Path) -> None:
    # Create a 'lite-reviewer' that extends the built-in 'explore' agent.
    _write_agent(
        tmp_path,
        "lite-reviewer",
        "description: lite reviewer\nmode: subagent\nextends: explore\nrole: reviewer",
        "Do a quick review.",
    )
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    info = reg.resolve("lite-reviewer")
    assert info.name == "lite-reviewer"
    assert info.role == "reviewer"
    assert info.prompt == "Do a quick review."
    # Parent permission rules must be inherited (explore has 9 rules).
    assert len(info.permission) >= 9
    # The parent's deny-all rule should appear first.
    assert info.permission[0]["action"] == "deny"


def test_registry_extends_cycle_detected(tmp_path: Path) -> None:
    _write_agent(tmp_path, "a", "extends: b\nmode: subagent")
    _write_agent(tmp_path, "b", "extends: a\nmode: subagent")
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    with pytest.raises(AgentLoadError, match="extends cycle"):
        reg.resolve("a")


def test_registry_extends_missing_parent(tmp_path: Path) -> None:
    _write_agent(tmp_path, "orphan", "extends: nonexistent-parent\nmode: subagent")
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    with pytest.raises(KeyError):
        reg.resolve("orphan")


def test_registry_invalid_file_propagates_path(tmp_path: Path) -> None:
    _write_agent(tmp_path, "bad", "mode: totally-wrong")
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    with pytest.raises(AgentLoadError) as excinfo:
        reg.list_entries()
    assert "bad.md" in str(excinfo.value)


def test_registry_resolve_all_includes_builtins(tmp_path: Path) -> None:
    reg = AgentRegistry(project_dir=tmp_path, global_dir=tmp_path / "nonexistent")
    resolved = reg.resolve_all()
    # Built-in agents must always be present.
    for expected in ("build", "plan", "explore", "general", "coder"):
        assert expected in resolved


def test_registry_missing_name_raises() -> None:
    reg = AgentRegistry(project_dir=None, global_dir=None)
    with pytest.raises(KeyError):
        reg.resolve("does-not-exist")


# --- Extended AgentInfo fields ---------------------------------------------


def test_builtin_agents_have_default_new_fields() -> None:
    from mycode.agent.agent import _build_agents
    agents = _build_agents()
    for name, info in agents.items():
        assert info.role is None, name
        assert info.extends is None, name
        assert info.tools is None, name
        assert info.isolation == "none", name
        assert info.omit_claudemd is False, name
