"""Tests for mycode.orchestration.topology (schema / loader / validator)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mycode.orchestration.topology.loader import (
    OrchestrationLoadError,
    load_file,
    load_mapping,
    render_variables,
)
from mycode.orchestration.topology.schema import (
    AgentSpec,
    OrchestrationSpec,
    SpawnSpec,
    StageSpec,
)
from mycode.orchestration.topology.validator import (
    OrchestrationValidationError,
    validate,
)

if TYPE_CHECKING:
    from pathlib import Path

# --- schema ---------------------------------------------------------------


def test_minimal_coordinator_spec_valid() -> None:
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[
            AgentSpec(name="coordinator", role="coordinator"),
            AgentSpec(name="worker", role="worker"),
        ],
        stages=[
            StageSpec(id="s1", spawn=[SpawnSpec(agent="worker", task="do x")]),
        ],
    )
    validate(spec)  # no exception


def test_agent_name_validation() -> None:
    with pytest.raises(ValueError, match="invalid agent name"):
        AgentSpec(name="bad name")


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(Exception):  # noqa: B017 — pydantic raises its own subclass
        AgentSpec.model_validate({"name": "a", "unknown_field": True})


# --- loader ---------------------------------------------------------------


def test_load_mapping_basic() -> None:
    spec = load_mapping({
        "name": "demo",
        "mode": "coordinator",
        "agents": [{"name": "w"}],
        "stages": [{"id": "s1", "spawn": [{"agent": "w", "task": "do"}]}],
    })
    assert spec.name == "demo"
    assert len(spec.agents) == 1


def test_load_file_yaml(tmp_path: Path) -> None:
    p = tmp_path / "flow.yaml"
    p.write_text(
        """
name: t
mode: coordinator
agents:
  - name: worker
stages:
  - id: s1
    spawn:
      - { agent: worker, task: "hello" }
""".strip(),
        encoding="utf-8",
    )
    spec = load_file(p)
    assert spec.source_path is not None
    assert spec.name == "t"


def test_load_file_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("name: t\n  broken: [", encoding="utf-8")
    with pytest.raises(OrchestrationLoadError, match="YAML parse error"):
        load_file(p)


def test_load_file_not_mapping(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(OrchestrationLoadError, match="must be a mapping"):
        load_file(p)


def test_load_file_missing(tmp_path: Path) -> None:
    with pytest.raises(OrchestrationLoadError, match="not found"):
        load_file(tmp_path / "nope.yaml")


# --- variable rendering ---------------------------------------------------


def test_render_vars_basic() -> None:
    out = render_variables({"msg": "hello {{ name }}"}, {"name": "world"})
    assert out == {"msg": "hello world"}


def test_render_vars_with_vars_prefix() -> None:
    out = render_variables("x={{ vars.k }}", {"k": 42})
    assert out == "x=42"


def test_render_nested_dict_key() -> None:
    out = render_variables("{{ user.name }}", {"user": {"name": "alice"}})
    assert out == "alice"


def test_render_missing_var_left_verbatim() -> None:
    out = render_variables("{{ unknown }}", {})
    assert out == "{{ unknown }}"


def test_vars_override_applied_at_load() -> None:
    spec = load_mapping(
        {
            "name": "demo",
            "mode": "coordinator",
            "vars": {"q": "default"},
            "agents": [{"name": "w"}],
            "stages": [{"id": "s1", "spawn": [{"agent": "w", "task": "{{ vars.q }}"}]}],
        },
        vars_override={"q": "overridden"},
    )
    assert spec.stages[0].spawn[0].task == "overridden"


# --- extends --------------------------------------------------------------


def test_extends_deep_merge() -> None:
    parent = OrchestrationSpec(
        name="parent",
        mode="coordinator",
        agents=[AgentSpec(name="w", model="gpt-4")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="orig")])],
    )

    def resolver(n: str) -> OrchestrationSpec:
        assert n == "parent"
        return parent

    child = load_mapping(
        {
            "name": "child",
            "extends": "parent",
            "agents": [{"name": "w", "model": "gpt-5"}],  # override just the model
        },
        parent_resolver=resolver,
    )
    assert child.name == "child"
    assert len(child.agents) == 1
    assert child.agents[0].model == "gpt-5"
    # Stages inherited from parent
    assert len(child.stages) == 1


def test_extends_without_resolver_raises() -> None:
    with pytest.raises(OrchestrationLoadError, match="no parent_resolver"):
        load_mapping({"name": "c", "extends": "p", "agents": [{"name": "w"}]})


# --- validator ------------------------------------------------------------


def test_validator_rejects_unknown_agent_in_spawn() -> None:
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="w", role="coordinator")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="ghost", task="x")])],
    )
    with pytest.raises(OrchestrationValidationError, match="unknown agent 'ghost'"):
        validate(spec)


def test_validator_rejects_cycle() -> None:
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="w", role="coordinator")],
        stages=[
            StageSpec(id="a", depends_on=["b"], spawn=[SpawnSpec(agent="w", task="x")]),
            StageSpec(id="b", depends_on=["a"], spawn=[SpawnSpec(agent="w", task="x")]),
        ],
    )
    with pytest.raises(OrchestrationValidationError, match="cycle detected"):
        validate(spec)


def test_validator_allows_swarm_without_explicit_entry() -> None:
    """Swarm is a decentralized / peer-to-peer topology; specifying an
    entry agent (historically called ``lead``) is **optional**.  When
    omitted, the runtime picks the first declared agent as the entry
    point.  See docs/multi-agent-architecture.md for the rationale.
    """
    spec = OrchestrationSpec(
        name="demo",
        mode="swarm",
        agents=[AgentSpec(name="a"), AgentSpec(name="b")],
    )
    # Should pass validation without raising.
    validate(spec)


def test_validator_rejects_swarm_with_single_agent() -> None:
    """Swarm collaboration requires at least two peers."""
    spec = OrchestrationSpec(
        name="demo",
        mode="swarm",
        agents=[AgentSpec(name="a")],
    )
    with pytest.raises(OrchestrationValidationError, match="at least 2 agents"):
        validate(spec)


def test_validator_rejects_swarm_entry_unknown_agent() -> None:
    """If an entry agent is pinned, it must reference a declared agent."""
    spec = OrchestrationSpec(
        name="demo",
        mode="swarm",
        entry="ghost",
        agents=[AgentSpec(name="a"), AgentSpec(name="b")],
    )
    with pytest.raises(OrchestrationValidationError, match="unknown agent"):
        validate(spec)


def test_validator_rejects_coordinator_without_leader() -> None:
    """Coordinator mode follows the orchestrator-worker pattern; a
    centralised leader must be designated either via the top-level
    ``coordinator`` field or via exactly one agent with ``role='coordinator'``.
    """
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="w"), AgentSpec(name="x")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="x")])],
    )
    with pytest.raises(OrchestrationValidationError, match="designated leader"):
        validate(spec)


def test_validator_rejects_coordinator_with_ambiguous_leader() -> None:
    """Two agents claiming role='coordinator' without an explicit
    top-level ``coordinator`` is ambiguous → reject.
    """
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[
            AgentSpec(name="a", role="coordinator"),
            AgentSpec(name="b", role="coordinator"),
        ],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="a", task="x")])],
    )
    with pytest.raises(OrchestrationValidationError, match="multiple agents with role='coordinator'"):
        validate(spec)


def test_validator_allows_coordinator_with_explicit_field() -> None:
    """Explicit top-level ``coordinator: <agent>`` satisfies the leader rule
    even if no agent carries role='coordinator'.
    """
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        coordinator="a",
        agents=[AgentSpec(name="a"), AgentSpec(name="b")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="a", task="x")])],
    )
    validate(spec)  # no raise


def test_validator_auto_derives_coordinator_from_single_role() -> None:
    """Exactly one ``role=coordinator`` agent → coordinator field is
    derived automatically by the schema's model_validator.
    """
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="boss", role="coordinator"), AgentSpec(name="worker", role="worker")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="worker", task="x")])],
    )
    assert spec.coordinator == "boss"
    validate(spec)  # no raise


def test_validator_rejects_coordinator_unknown_reference() -> None:
    """Explicit ``coordinator`` must reference a declared agent."""
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        coordinator="ghost",
        agents=[AgentSpec(name="a"), AgentSpec(name="b")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="a", task="x")])],
    )
    with pytest.raises(OrchestrationValidationError, match="coordinator references unknown agent"):
        validate(spec)


def test_validator_rejects_coordinator_without_stages() -> None:
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="w")],
    )
    with pytest.raises(OrchestrationValidationError, match="at least one stage"):
        validate(spec)


def test_validator_detects_unresolved_placeholder() -> None:
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="w", role="coordinator")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="{{ missing }}")])],
    )
    with pytest.raises(OrchestrationValidationError, match="unresolved placeholder"):
        validate(spec)


def test_validator_allows_runtime_tokens() -> None:
    # $item / $index are resolved at runtime, not load time
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        agents=[AgentSpec(name="w", role="coordinator")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="process {{ $item }}")])],
    )
    validate(spec)  # no exception


def test_validator_detects_duplicate_names() -> None:
    spec = OrchestrationSpec(
        name="demo",
        mode="coordinator",
        coordinator="w",
        agents=[AgentSpec(name="w"), AgentSpec(name="w")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="x")])],
    )
    with pytest.raises(OrchestrationValidationError, match="duplicate agent name"):
        validate(spec)
