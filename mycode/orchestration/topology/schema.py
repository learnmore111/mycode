"""Pydantic models describing an orchestration spec.

An orchestration file (YAML or JSON) deserializes into :class:`OrchestrationSpec`.
It declares:

- ``mode``: coordinator | swarm | hybrid
- ``agents``: named agent definitions (may ``extend`` a registry agent)
- ``stages``: (coordinator) ordered DAG with optional fan-out / parallel
- ``coordinator``: (coordinator) the leader agent that synthesises worker
  outputs; (hybrid) the supervisor agent that receives the task, delegates
  through mailboxes, and produces the final answer.
- ``entry``: (swarm) the initial task receiver; (hybrid) fallback supervisor
  when ``coordinator`` is omitted. ``lead`` is accepted as a backward-
  compatible alias.

See :mod:`mycode.orchestration.topology.loader` for parsing and
:mod:`mycode.orchestration.topology.validator` for semantic checks.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Enums (string literals to keep YAML friendly) -------------------------

OrchestrationMode = Literal["coordinator", "swarm", "hybrid"]
# ``lead`` kept for backwards-compat; ``entry`` is the preferred role for the
# swarm entry agent (the initial task receiver — not a centralized leader).
AgentRole = Literal["coordinator", "worker", "teammate", "lead", "entry", "fork"]
IsolationMode = Literal["none", "worktree", "process"]


# --- Nested specs ----------------------------------------------------------


class PermissionRule(BaseModel):
    """One permission rule. Mirrors :mod:`mycode.permission`."""

    model_config = ConfigDict(extra="forbid")

    permission: str
    pattern: str = "*"
    action: Literal["allow", "ask", "deny"] = "allow"


class AgentSpec(BaseModel):
    """An agent participating in the orchestration.

    ``extends`` references a registry agent name (built-in or ``.md`` defined).
    Remaining fields **override** the extended agent on merge.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    role: AgentRole | None = None
    extends: str | None = None
    description: str | None = None
    prompt: str | None = None
    prompt_file: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] = Field(default_factory=list)
    permission: list[PermissionRule] = Field(default_factory=list)
    isolation: IsolationMode = "none"
    max_turns: int | None = None
    background: bool = False
    omit_claudemd: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent name must be non-empty")
        if any(c in v for c in " \t\n/\\"):
            raise ValueError(f"invalid agent name: {v!r}")
        return v


class SpawnSpec(BaseModel):
    """A single worker spawn inside a stage."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    task: str
    vars: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None


class StageSpec(BaseModel):
    """One stage in a coordinator pipeline.

    Semantics:
      - ``parallel=True`` → all spawns run concurrently (up to ``max_concurrency``)
      - ``runs_on=<agent>`` → stage body is executed *by that agent directly*
        (used for coordinator synthesis, which is uncommittable to workers)
      - ``fan_out_from=<stage_id>`` → splits previous stage outputs into N items,
        each becoming a ``$item`` for the spawn template.
      - ``depends_on=[...]`` → explicit DAG edges (implicit edges: previous
        stage by declaration order).
      - ``inputs=["research.*"]`` → collect outputs matching stage-id glob.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str | None = None
    parallel: bool = False
    max_concurrency: int = 5
    runs_on: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    fan_out_from: str | None = None
    inputs: list[str] = Field(default_factory=list)
    spawn: list[SpawnSpec] = Field(default_factory=list)
    prompt: str | None = None  # used when runs_on is set

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("stage id must be non-empty")
        return v


class BackendSpec(BaseModel):
    """Mailbox backend preferences for Swarm mode."""

    model_config = ConfigDict(extra="forbid")

    prefer: Literal["auto", "inprocess", "file", "tmux", "iterm"] = "auto"
    root_dir: str | None = None


# --- Top-level spec --------------------------------------------------------


class OrchestrationSpec(BaseModel):
    """The root spec parsed from a ``.yaml`` / ``.json`` orchestration file."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    mode: OrchestrationMode = "coordinator"
    extends: str | None = None
    model: str | None = None
    vars: dict[str, Any] = Field(default_factory=dict)
    agents: list[AgentSpec] = Field(default_factory=list)
    stages: list[StageSpec] = Field(default_factory=list)
    # In coordinator mode ``coordinator`` names the leader agent that
    # synthesises worker outputs — the orchestrator-worker pattern. In hybrid
    # mode it names the supervisor for mailbox-based collaboration.
    # Required for coordinator mode; may be omitted in pure swarm mode.
    # If not explicitly set, the loader derives it from the unique agent
    # whose ``role == "coordinator"`` (see ``_sync_coordinator``).
    coordinator: str | None = None
    # In swarm mode ``entry`` is the initial task receiver. ``lead`` is kept
    # as a backward-compatible alias and mirrored to ``entry`` on load.
    entry: str | None = None
    lead: str | None = None
    backend: BackendSpec | None = None
    max_depth: int = 3
    # Where the file was loaded from (populated by loader, optional)
    source_path: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("orchestration name must be non-empty")
        return v

    @model_validator(mode="after")
    def _sync_entry_lead(self) -> OrchestrationSpec:
        """Keep ``entry`` and ``lead`` mirrored.

        - If only one of them is set, populate the other so downstream code
          (and legacy tests) can read either name transparently.
        - If both are set and disagree, raise — the caller must pick one.
        """
        if self.entry and self.lead and self.entry != self.lead:
            raise ValueError(
                f"'entry' ({self.entry!r}) and 'lead' ({self.lead!r}) disagree; "
                f"prefer 'entry' and remove 'lead'"
            )
        if self.entry and not self.lead:
            self.lead = self.entry
        elif self.lead and not self.entry:
            self.entry = self.lead
        return self

    @model_validator(mode="after")
    def _sync_coordinator(self) -> OrchestrationSpec:
        """Derive ``coordinator`` from ``role=coordinator`` agents when absent.

        Business rule (coordinator / orchestrator-worker pattern):
        a centralised coordinator is **required** for ``mode=coordinator``.
        We *infer* it here from agent roles so existing flows that only
        declare ``role: coordinator`` on one of the agents keep working,
        while ``_check_mode_constraints`` in the validator still enforces
        presence/uniqueness at semantic-validation time.

        Rules:
        - If ``coordinator`` is unset and exactly one agent has
          ``role == "coordinator"``, adopt that agent's name.
        - If ``coordinator`` is set but no matching agent exists with
          ``role == "coordinator"``, leave it alone (validator will
          catch any truly invalid state).
        - Multiple ``role=coordinator`` agents are ambiguous — leave
          ``coordinator`` unset so the validator raises a clear error.
        """
        if self.coordinator is None:
            coord_agents = [a.name for a in self.agents if a.role == "coordinator"]
            if len(coord_agents) == 1:
                self.coordinator = coord_agents[0]
        return self

    def agent_by_name(self, name: str) -> AgentSpec | None:
        for a in self.agents:
            if a.name == name:
                return a
        return None

    def stage_by_id(self, stage_id: str) -> StageSpec | None:
        for s in self.stages:
            if s.id == stage_id:
                return s
        return None
