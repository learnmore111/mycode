"""Tests for mycode.orchestration.registry.flow_registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycode.orchestration.registry.flow_registry import FlowRegistry


@pytest.fixture
def isolated_registry(tmp_path: Path) -> FlowRegistry:
    """A FlowRegistry with empty builtin/global and a tmp project dir."""
    builtin = tmp_path / "builtin"
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    (project / ".mycode" / "orchestrations").mkdir(parents=True)
    return FlowRegistry(project_dir=project, global_dir=global_dir, builtin_dir=builtin)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- discovery ------------------------------------------------------------


def test_list_flows_empty(isolated_registry: FlowRegistry) -> None:
    assert isolated_registry.list_flows() == []


def test_list_flows_discovers_all_sources(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    _write(builtin / "a.yaml", "name: a\nmode: coordinator\nagents: [{name: w}]\nstages: [{id: s, spawn: [{agent: w, task: t}]}]\n")
    _write(global_dir / "b.yaml", "name: b\nmode: coordinator\nagents: [{name: w}]\nstages: [{id: s, spawn: [{agent: w, task: t}]}]\n")
    _write(
        project / ".mycode" / "orchestrations" / "c.yaml",
        "name: c\nmode: coordinator\nagents: [{name: w}]\nstages: [{id: s, spawn: [{agent: w, task: t}]}]\n",
    )

    registry = FlowRegistry(project_dir=project, global_dir=global_dir, builtin_dir=builtin)
    names = sorted(f.name for f in registry.list_flows())
    assert names == ["a", "b", "c"]

    sources = {f.name: f.source for f in registry.list_flows()}
    assert sources == {"a": "builtin", "b": "global", "c": "project"}


def test_project_overrides_builtin(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    _write(builtin / "shared.yaml", "name: builtin-version\nmode: coordinator\nagents: [{name: w}]\nstages: [{id: s, spawn: [{agent: w, task: t}]}]\n")
    _write(
        project / ".mycode" / "orchestrations" / "shared.yaml",
        "name: project-version\nmode: coordinator\nagents: [{name: w}]\nstages: [{id: s, spawn: [{agent: w, task: t}]}]\n",
    )

    registry = FlowRegistry(project_dir=project, global_dir=global_dir, builtin_dir=builtin)
    info = registry.resolve("shared")
    assert info.source == "project"


def test_resolve_missing_raises(isolated_registry: FlowRegistry) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        isolated_registry.resolve("nope")


# --- loading --------------------------------------------------------------


def test_load_resolves_and_validates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(
        project / ".mycode" / "orchestrations" / "flow.yaml",
        """
name: flow
mode: coordinator
vars:
  q: default
agents:
  - { name: worker, role: coordinator }
stages:
  - id: s1
    spawn:
      - { agent: worker, task: "{{ vars.q }}" }
""".strip(),
    )
    registry = FlowRegistry(
        project_dir=project,
        builtin_dir=tmp_path / "builtin",
        global_dir=tmp_path / "global",
    )
    spec = registry.load("flow", vars_override={"q": "override"})
    assert spec.stages[0].spawn[0].task == "override"


def test_load_supports_extends_across_files(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    _write(
        global_dir / "base.yaml",
        """
name: base
mode: coordinator
agents:
  - { name: worker, role: coordinator }
stages:
  - id: s1
    spawn:
      - { agent: worker, task: base }
""".strip(),
    )
    _write(
        project / ".mycode" / "orchestrations" / "child.yaml",
        """
name: child
extends: base
agents:
  - { name: worker, role: coordinator, model: gpt-5 }
""".strip(),
    )

    registry = FlowRegistry(
        project_dir=project,
        builtin_dir=tmp_path / "builtin",
        global_dir=global_dir,
    )
    spec = registry.load("child")
    assert spec.name == "child"
    assert spec.agents[0].model == "gpt-5"
    # Stages inherited
    assert len(spec.stages) == 1


# --- built-in flows ship correctly ---------------------------------------


def test_builtin_flows_load() -> None:
    """The flows shipped in mycode/orchestration/flows/ must parse cleanly."""
    registry = FlowRegistry(global_dir=Path("/__does_not_exist__"))
    names = [f.name for f in registry.list_flows()]
    assert "research" in names
    assert "supervised-review" in names
    assert "pair-review" in names

    spec = registry.load("research")
    assert spec.mode == "coordinator"
    assert any(a.name == "coordinator" for a in spec.agents)

    spec_swarm = registry.load("pair-review")
    assert spec_swarm.mode == "swarm"
    assert spec_swarm.lead == "reviewer-starter"

    spec_hybrid = registry.load("supervised-review")
    assert spec_hybrid.mode == "hybrid"
    assert spec_hybrid.coordinator == "review-supervisor"
