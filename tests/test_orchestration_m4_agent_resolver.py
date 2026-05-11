"""M4 — resolve ``AgentSpec`` from a flow against ``AgentRegistry``.

Covers:
* direct ``extends`` against a built-in agent (``explore`` / ``build``)
* implicit same-name parent reopen
* inline overrides respect Pydantic ``model_fields_set`` (unset fields inherit)
* ``disallowed_tools`` subtracts from the inherited tool allow-list
* shipped flows resolve
  cleanly against the default registry
* unknown ``extends`` → ``AgentResolveError`` (resolver) and clean diagnostic
  via ``validator.validate(registry=...)``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mycode.orchestration.registry.agent_registry import AgentRegistry
from mycode.orchestration.topology import (
    AgentResolveError,
    OrchestrationValidationError,
    load_file,
    resolve_agent_spec,
    resolve_all_agents,
    validate,
)
from mycode.orchestration.topology.schema import AgentSpec, OrchestrationSpec

FLOWS_DIR = Path(__file__).resolve().parent.parent / "mycode" / "orchestration" / "flows"


# --- helpers --------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    """Isolated registry — no project/global dirs so only built-ins exist."""
    # Point project_dir / global_dir at fresh empty directories to guarantee
    # the test's assumptions don't drift with the developer's ~/.mycode.
    return AgentRegistry(project_dir=tmp_path / "proj", global_dir=tmp_path / "global")


# --- resolve_agent_spec ---------------------------------------------------


class TestResolveAgentSpec:
    def test_extends_builtin_inherits_fields(self, registry: AgentRegistry) -> None:
        spec = AgentSpec(
            name="explorer",
            extends="explore",
            role="worker",
        )
        info = resolve_agent_spec(spec, registry)

        assert info.name == "explorer"
        assert info.extends == "explore"
        assert info.role == "worker"
        # Mode / prompt come from parent "explore"
        parent = registry.resolve("explore")
        assert info.mode == parent.mode
        assert info.prompt == parent.prompt

    def test_inline_override_wins_but_unset_inherits(self, registry: AgentRegistry) -> None:
        spec = AgentSpec(
            name="custom-reviewer",
            extends="build",
            prompt="Only review; do not modify code.",
            temperature=0.1,
        )
        info = resolve_agent_spec(spec, registry)

        # Explicit fields replace the parent
        assert info.prompt == "Only review; do not modify code."
        assert info.temperature == 0.1
        # Unset fields inherit
        parent = registry.resolve("build")
        assert info.top_p == parent.top_p
        assert info.mode == parent.mode

    def test_implicit_same_name_parent(self, registry: AgentRegistry) -> None:
        # A spec named "build" without explicit extends should reopen the
        # built-in "build" agent.
        spec = AgentSpec(name="build", temperature=0.0)
        info = resolve_agent_spec(spec, registry)

        parent = registry.resolve("build")
        assert info.temperature == 0.0
        # prompt / tools etc. preserved from parent
        assert info.prompt == parent.prompt

    def test_tools_override_replaces_parent_list(self, registry: AgentRegistry) -> None:
        spec = AgentSpec(
            name="locked-explorer",
            extends="explore",
            tools=["read", "grep"],
        )
        info = resolve_agent_spec(spec, registry)

        assert info.tools == ["read", "grep"]

    def test_disallowed_tools_subtracts_from_parent(self, registry: AgentRegistry) -> None:
        # Parent "explore" has no tools allow-list; set one on the child
        # then disallow one of them.
        spec = AgentSpec(
            name="read-only-explorer",
            extends="explore",
            tools=["read", "grep", "glob"],
            disallowed_tools=["glob"],
        )
        info = resolve_agent_spec(spec, registry)

        assert info.tools is not None
        assert "glob" not in info.tools
        assert set(info.tools) == {"read", "grep"}

    def test_disallowed_tools_is_noop_without_allow_list(
        self, registry: AgentRegistry
    ) -> None:
        # When tools is None (inherit-everything), disallowed_tools has no
        # effect yet — the subtraction only narrows an explicit allow-list.
        spec = AgentSpec(
            name="open-explorer",
            extends="explore",
            disallowed_tools=["bash"],
        )
        info = resolve_agent_spec(spec, registry)

        assert info.tools is None  # still inherit-everything

    def test_permission_rules_concatenate(self, registry: AgentRegistry) -> None:
        from mycode.orchestration.topology.schema import PermissionRule

        spec = AgentSpec(
            name="restricted-build",
            extends="build",
            permission=[
                PermissionRule(permission="edit", pattern="*", action="deny"),
            ],
        )
        info = resolve_agent_spec(spec, registry)

        # Parent permission rules come first, then ours.
        assert info.permission[-1] == {
            "permission": "edit",
            "pattern": "*",
            "action": "deny",
        }
        # Parent rules preserved before ours.
        assert len(info.permission) > 1

    def test_unknown_extends_raises(self, registry: AgentRegistry) -> None:
        spec = AgentSpec(name="oops", extends="nonexistent-parent")
        with pytest.raises(AgentResolveError) as excinfo:
            resolve_agent_spec(spec, registry)
        assert "nonexistent-parent" in str(excinfo.value)
        assert "oops" in str(excinfo.value)

    def test_no_parent_self_sufficient(self, registry: AgentRegistry) -> None:
        # Spec with a unique name and no extends — should produce a bare
        # AgentInfo without raising.
        spec = AgentSpec(
            name="standalone-only-here",
            prompt="Standalone agent.",
            tools=["read"],
        )
        info = resolve_agent_spec(spec, registry)

        assert info.name == "standalone-only-here"
        assert info.prompt == "Standalone agent."
        assert info.tools == ["read"]

    def test_fallback_agent_used_when_no_extends_and_no_same_name(
        self, registry: AgentRegistry
    ) -> None:
        spec = AgentSpec(name="inline-coordinator", prompt="Coordinate things.")
        info = resolve_agent_spec(spec, registry, fallback_agent="build")

        # Inherits tool set + other fields from "build"; prompt is overridden.
        parent = registry.resolve("build")
        assert info.prompt == "Coordinate things."
        # Non-overridden fields come from parent
        assert info.mode == parent.mode


# --- resolve_all_agents ---------------------------------------------------


class TestResolveAllAgents:
    def test_research_flow_resolves(self, registry: AgentRegistry) -> None:
        spec = load_file(FLOWS_DIR / "research.yaml")
        resolved = resolve_all_agents(spec.agents, registry, fallback_agent="build")

        assert set(resolved) == {"coordinator", "explorer"}
        # explorer extends "explore" → inherits its prompt & tools (tools is
        # overridden to nothing here, so inherit).
        explorer = resolved["explorer"]
        assert explorer.extends == "explore"
        assert explorer.role == "worker"

        # coordinator has no extends but a fallback — should end up with
        # build's base config + the inline tools list.
        coord = resolved["coordinator"]
        assert coord.role == "coordinator"
        assert set(coord.tools or []) >= {"read", "grep"}

    def test_pair_review_flow_resolves(self, registry: AgentRegistry) -> None:
        spec = load_file(FLOWS_DIR / "pair-review.yaml")
        resolved = resolve_all_agents(spec.agents, registry)

        assert set(resolved) == {"reviewer-starter", "security-reviewer", "perf-reviewer"}

        lead = resolved["reviewer-starter"]
        assert lead.extends == "build"
        # Role was renamed from legacy "lead" to "entry" when the Swarm
        # semantics were aligned with OpenAI / LangGraph Swarm.
        assert lead.role == "entry"
        assert set(lead.tools or []) == {
            "send_message",
            "read",
            "grep",
            "glob",
        }

        sec = resolved["security-reviewer"]
        assert sec.extends == "explore"
        assert sec.role == "teammate"
        assert set(sec.tools or []) == {"read", "grep", "glob", "send_message"}

    def test_supervised_review_flow_resolves(self, registry: AgentRegistry) -> None:
        spec = load_file(FLOWS_DIR / "supervised-review.yaml")
        resolved = resolve_all_agents(spec.agents, registry)

        assert spec.mode == "hybrid"
        assert spec.coordinator == "review-supervisor"
        assert set(resolved) == {"review-supervisor", "architecture-reviewer", "risk-reviewer"}

        supervisor = resolved["review-supervisor"]
        assert supervisor.extends == "build"
        assert supervisor.role == "coordinator"
        assert set(supervisor.tools or []) == {"send_message", "read", "grep", "glob"}


# --- validator with registry ---------------------------------------------


class TestValidatorWithRegistry:
    def test_validate_research_flow_with_registry(self, registry: AgentRegistry) -> None:
        spec = load_file(FLOWS_DIR / "research.yaml")
        # Should not raise.
        validate(spec, registry=registry)

    def test_validate_catches_unknown_extends(self, registry: AgentRegistry) -> None:
        spec = OrchestrationSpec(
            name="bogus",
            mode="coordinator",
            agents=[AgentSpec(name="w", role="coordinator", extends="totally-missing")],
            stages=[],
        )
        # Add a stage so mode-constraint check passes (otherwise we'd get two
        # unrelated errors which would still be fine, but we want to isolate).
        spec.stages.append(
            __import__(
                "mycode.orchestration.topology.schema", fromlist=["StageSpec"]
            ).StageSpec(id="s1", spawn=[])
        )

        with pytest.raises(OrchestrationValidationError) as excinfo:
            validate(spec, registry=registry)
        issues = excinfo.value.issues
        assert any("totally-missing" in msg for msg in issues)
        assert any("extends" in msg for msg in issues)

    def test_validate_without_registry_skips_extends_check(self) -> None:
        # Without registry, unknown extends must NOT raise — the offline
        # validator keeps its pre-M4 semantics.
        from mycode.orchestration.topology.schema import StageSpec

        spec = OrchestrationSpec(
            name="bogus",
            mode="coordinator",
            agents=[AgentSpec(name="w", role="coordinator", extends="totally-missing")],
            stages=[StageSpec(id="s1", spawn=[])],
        )
        validate(spec)  # no raise
