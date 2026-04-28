"""Semantic validation for :class:`OrchestrationSpec`.

Pydantic handles schema-level checks (types, enums, required fields).
This module enforces cross-field / whole-graph invariants:

- unique agent names / stage ids
- ``spawn.agent`` references exist in ``agents``
- ``runs_on`` / ``entry`` (``lead`` alias) / ``fan_out_from`` references exist
- no cycles in ``depends_on`` / ``fan_out_from`` edges
- mode-specific constraints:

  * **coordinator** mode — *centralised* orchestrator-worker pattern:
    must have ≥1 stage and exactly one designated coordinator/leader
    agent (either via the top-level ``coordinator`` field or exactly
    one agent with ``role == "coordinator"``).  See
    https://docs.anthropic.com/en/docs/agents / LangGraph supervisor.
  * **swarm** mode — *decentralised* peer-to-peer pattern:
    requires ≥2 agents, forbids stages, entry is optional.
- unknown ``{{ var.X }}`` placeholders leftover after rendering
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycode.orchestration.registry.agent_registry import AgentRegistry
    from mycode.orchestration.topology.schema import OrchestrationSpec

_REMAINING_VAR_RE = re.compile(r"\{\{\s*[a-zA-Z_][a-zA-Z0-9_.]*\s*\}\}")


class OrchestrationValidationError(ValueError):
    """Raised when a spec fails semantic validation.

    Carries the full list of issues via :attr:`issues`.
    """

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("orchestration validation failed:\n  - " + "\n  - ".join(issues))


@dataclass
class _Report:
    errors: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.errors.append(msg)

    def raise_if_any(self) -> None:
        if self.errors:
            raise OrchestrationValidationError(self.errors)


def validate(
    spec: OrchestrationSpec,
    *,
    registry: AgentRegistry | None = None,
) -> None:
    """Validate a fully-loaded orchestration spec. Raises on failure.

    When ``registry`` is provided, additional cross-module checks are run:

    - Every ``agent.extends`` reference must resolve against the registry.
      Missing names are reported with the full list of known agents in the
      error message to aid debugging.
    """
    r = _Report()

    _check_unique_names(spec, r)
    _check_references(spec, r)
    _check_mode_constraints(spec, r)
    _check_stage_dag(spec, r)
    _check_unresolved_placeholders(spec, r)
    if registry is not None:
        _check_registry_extends(spec, registry, r)

    r.raise_if_any()


# --- individual checks -----------------------------------------------------


def _check_unique_names(spec: OrchestrationSpec, r: _Report) -> None:
    seen_agents: set[str] = set()
    for a in spec.agents:
        if a.name in seen_agents:
            r.add(f"duplicate agent name: {a.name!r}")
        seen_agents.add(a.name)

    seen_stages: set[str] = set()
    for s in spec.stages:
        if s.id in seen_stages:
            r.add(f"duplicate stage id: {s.id!r}")
        seen_stages.add(s.id)


def _check_references(spec: OrchestrationSpec, r: _Report) -> None:
    agent_names = {a.name for a in spec.agents}
    stage_ids = {s.id for s in spec.stages}

    for s in spec.stages:
        for spawn in s.spawn:
            if spawn.agent not in agent_names:
                r.add(f"stage {s.id!r}: spawn references unknown agent {spawn.agent!r}")
        if s.runs_on and s.runs_on not in agent_names:
            r.add(f"stage {s.id!r}: runs_on references unknown agent {s.runs_on!r}")
        if s.fan_out_from and s.fan_out_from not in stage_ids:
            r.add(f"stage {s.id!r}: fan_out_from references unknown stage {s.fan_out_from!r}")
        for dep in s.depends_on:
            if dep not in stage_ids:
                r.add(f"stage {s.id!r}: depends_on references unknown stage {dep!r}")
        for inp in s.inputs:
            # inputs may be globs like "research.*"
            base = inp.split(".", 1)[0]
            if base not in stage_ids:
                r.add(f"stage {s.id!r}: inputs references unknown stage {base!r}")

    if spec.lead and spec.lead not in agent_names:
        r.add(f"lead references unknown agent {spec.lead!r}")
    if spec.entry and spec.entry not in agent_names:
        r.add(f"entry references unknown agent {spec.entry!r}")
    if spec.coordinator and spec.coordinator not in agent_names:
        r.add(f"coordinator references unknown agent {spec.coordinator!r}")


def _check_mode_constraints(spec: OrchestrationSpec, r: _Report) -> None:
    if spec.mode == "coordinator":
        if not spec.stages:
            r.add("coordinator mode requires at least one stage")
        # Coordinator is a CENTRALISED orchestrator-worker topology: exactly
        # one leader agent must be designated.  We accept either:
        #   (a) an explicit top-level ``coordinator: <agent>`` field, or
        #   (b) exactly one agent with ``role == "coordinator"`` (which the
        #       schema's ``_sync_coordinator`` already lifts into
        #       ``spec.coordinator`` for us).
        # Zero or multiple role=coordinator agents without an explicit
        # ``coordinator`` field is an error.
        coord_role_agents = [a.name for a in spec.agents if a.role == "coordinator"]
        if not spec.coordinator:
            if not coord_role_agents:
                r.add(
                    "coordinator mode requires a designated leader: set top-level "
                    "'coordinator: <agent>' or mark exactly one agent with "
                    "'role: coordinator'"
                )
            elif len(coord_role_agents) > 1:
                r.add(
                    "coordinator mode has multiple agents with role='coordinator' "
                    f"({sorted(coord_role_agents)!r}); pick one by setting the "
                    "top-level 'coordinator: <agent>' field"
                )
        elif len(coord_role_agents) > 1 and spec.coordinator not in coord_role_agents:
            # Explicit coordinator is set, but it doesn't match any role.
            # We still allow this (role is informational), but warn if there
            # are *other* agents claiming the role — likely a misconfig.
            r.add(
                f"coordinator {spec.coordinator!r} is declared but other agents "
                f"also carry role='coordinator': {sorted(coord_role_agents)!r}; "
                "only the designated coordinator should have that role"
            )
    elif spec.mode == "swarm":
        # Swarm is peer-to-peer / decentralized; an explicit entry agent
        # (formerly "lead") is **optional**. When omitted, the runtime uses
        # the first declared agent as the initial task receiver.
        if spec.stages:
            r.add("swarm mode should not declare stages (it is message-driven)")
        if len(spec.agents) < 2:
            r.add("swarm mode requires at least 2 agents")
    elif spec.mode == "hybrid" and not spec.stages and not spec.entry:
        r.add("hybrid mode requires stages and/or an entry agent")


def _check_stage_dag(spec: OrchestrationSpec, r: _Report) -> None:
    """Detect cycles in depends_on + fan_out_from edges."""
    nodes = [s.id for s in spec.stages]
    edges: dict[str, set[str]] = {s.id: set() for s in spec.stages}

    for s in spec.stages:
        for dep in s.depends_on:
            if dep in edges:
                edges[s.id].add(dep)
        if s.fan_out_from and s.fan_out_from in edges:
            edges[s.id].add(s.fan_out_from)

    # Recursive DFS with three-color marking (white/gray/black).
    white, gray, black = 0, 1, 2
    color = dict.fromkeys(nodes, white)
    reported: set[tuple[str, ...]] = set()

    def _dfs(node: str, path: list[str]) -> None:
        color[node] = gray
        for nxt in edges.get(node, ()):
            if color.get(nxt) == gray:
                # Found back-edge → cycle; extract the cycle portion of the path
                try:
                    start = path.index(nxt)
                    cycle_nodes = tuple(path[start:] + [node, nxt])
                except ValueError:
                    cycle_nodes = (node, nxt)
                key = tuple(sorted(cycle_nodes))
                if key not in reported:
                    reported.add(key)
                    r.add("cycle detected in stage graph: " + " → ".join(cycle_nodes))
                continue
            if color.get(nxt) == white:
                _dfs(nxt, path + [node])
        color[node] = black

    for n in nodes:
        if color[n] == white:
            _dfs(n, [])


def _check_unresolved_placeholders(spec: OrchestrationSpec, r: _Report) -> None:
    """Find leftover ``{{ ... }}`` that didn't resolve against vars."""
    for s in spec.stages:
        for spawn in s.spawn:
            _scan_string(f"stage {s.id!r} spawn.task", spawn.task, r)
        if s.prompt:
            _scan_string(f"stage {s.id!r} prompt", s.prompt, r)
    for a in spec.agents:
        if a.prompt:
            _scan_string(f"agent {a.name!r} prompt", a.prompt, r)
        if a.prompt_file:
            _scan_string(f"agent {a.name!r} prompt_file", a.prompt_file, r)


_IGNORED_TOKENS = {"$item", "$index"}


def _scan_string(context: str, s: str, r: _Report) -> None:
    for m in _REMAINING_VAR_RE.finditer(s):
        token = m.group(0)
        # Skip allow-listed runtime tokens (resolved by runner, not loader)
        if any(tok in token for tok in _IGNORED_TOKENS):
            continue
        r.add(f"{context}: unresolved placeholder {token}")


def _check_registry_extends(
    spec: OrchestrationSpec,
    registry: AgentRegistry,
    r: _Report,
) -> None:
    """Verify every ``agent.extends`` names an agent the registry can resolve.

    Does *not* raise on registry I/O errors — those are treated as hard
    configuration problems and surface via the registry's own exceptions
    during ``resolve_agent_spec``.  Here we only confirm the parent name
    exists in the discovery map, which is cheap and offline.
    """
    known: set[str] = {e.name for e in registry.list_entries()}
    for a in spec.agents:
        if a.extends and a.extends not in known:
            # Friendly error with the first few known names to avoid noise.
            sample = sorted(known)[:8]
            suffix = f"; known agents include: {', '.join(sample)}" if sample else ""
            r.add(f"agent {a.name!r}: extends references unknown agent {a.extends!r}{suffix}")
