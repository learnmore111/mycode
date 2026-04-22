"""Pydantic models describing an orchestration spec.

An orchestration file (YAML or JSON) deserializes into :class:`OrchestrationSpec`.
It declares:

- ``mode``: coordinator | swarm | hybrid
- ``agents``: named agent definitions (may ``extend`` a registry agent)
- ``stages``: (coordinator) ordered DAG with optional fan-out / parallel
- ``lead``: (swarm) the team lead agent name

See :mod:`mycode.orchestration.topology.loader` for parsing and
:mod:`mycode.orchestration.topology.validator` for semantic checks.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Enums (string literals to keep YAML friendly) -------------------------

OrchestrationMode = Literal["coordinator", "swarm", "hybrid"]
AgentRole = Literal["coordinator", "worker", "teammate", "lead", "fork"]
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
