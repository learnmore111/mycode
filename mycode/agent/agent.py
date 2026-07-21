"""Agent system — defines built-in and custom agents.

compaction, title, and summary agents with their permission rulesets.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mycode.config import config as configmod
from mycode.util import log as logmod

logger = logmod.create(service="agent")

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    p = PROMPTS_DIR / f"{name}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


@dataclass
class AgentInfo:
    """Agent definition."""

    name: str
    description: str = ""
    mode: Literal["subagent", "primary", "all"] = "primary"
    native: bool = False
    hidden: bool = False
    prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    color: str | None = None
    model: dict[str, str] | None = None  # {"providerID": ..., "modelID": ...}
    variant: str | None = None
    permission: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    steps: int | None = None

    # --- M2 extensions: orchestration & composition ---
    # Role tag used by orchestration ("coordinator" / "worker" / "reviewer" / ...).
    role: str | None = None
    # Tool allow-list (None = inherit from permission/role defaults).
    tools: list[str] | None = None
    # Parent agent name for inheritance (resolved at registry load time).
    extends: str | None = None
    # Per-agent loop cap (overrides global/processor defaults when set).
    max_turns: int | None = None
    # Execution isolation hint: "none" / "worktree" / "container".
    isolation: Literal["none", "worktree", "container"] = "none"
    # Legacy field name: if True, omit deterministic project guidance.
    omit_claudemd: bool = False
    # Origin tracking — where this agent came from (for CLI/debug only).
    source: Literal["builtin", "config", "global", "project"] = "builtin"
    source_path: str | None = None

    @property
    def omit_project_guidance(self) -> bool:
        """Preferred semantic alias for the legacy ``omit_claudemd`` field."""
        return self.omit_claudemd


def _default_permission() -> list[dict[str, Any]]:
    """Default permission ruleset (allow everything, ask for sensitive)."""
    return [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "doom_loop", "pattern": "*", "action": "ask"},
        {"permission": "external_directory", "pattern": "*", "action": "ask"},
        {"permission": "question", "pattern": "*", "action": "deny"},
        {"permission": "plan_enter", "pattern": "*", "action": "deny"},
        {"permission": "plan_exit", "pattern": "*", "action": "deny"},
        {"permission": "read", "pattern": "*.env", "action": "ask"},
        {"permission": "read", "pattern": "*.env.*", "action": "ask"},
        {"permission": "read", "pattern": "*.env.example", "action": "allow"},
    ]


def _build_agents() -> dict[str, AgentInfo]:
    """Build the default set of agents."""
    base = _default_permission()

    return {
        "build": AgentInfo(
            name="build",
            description="The default agent. Executes tools based on configured permissions.",
            mode="primary",
            native=True,
            permission=[
                *base,
                {"permission": "question", "pattern": "*", "action": "allow"},
                {"permission": "plan_enter", "pattern": "*", "action": "allow"},
            ],
        ),
        "plan": AgentInfo(
            name="plan",
            description="Plan mode. Disallows all edit tools.",
            mode="primary",
            native=True,
            permission=[
                *base,
                {"permission": "question", "pattern": "*", "action": "allow"},
                {"permission": "plan_exit", "pattern": "*", "action": "allow"},
                {"permission": "edit", "pattern": "*", "action": "deny"},
            ],
        ),
        "general": AgentInfo(
            name="general",
            description="General-purpose agent for researching complex questions and executing multi-step tasks in parallel.",
            mode="subagent",
            native=True,
            permission=[
                *base,
                {"permission": "todowrite", "pattern": "*", "action": "deny"},
            ],
        ),
        "explore": AgentInfo(
            name="explore",
            description="Fast agent specialized for exploring codebases. Use for file searches, code keyword searches, or codebase Q&A.",
            mode="subagent",
            native=True,
            prompt=_load_prompt("explore"),
            permission=[
                {"permission": "*", "pattern": "*", "action": "deny"},
                {"permission": "grep", "pattern": "*", "action": "allow"},
                {"permission": "glob", "pattern": "*", "action": "allow"},
                {"permission": "list", "pattern": "*", "action": "allow"},
                {"permission": "bash", "pattern": "*", "action": "allow"},
                {"permission": "read", "pattern": "*", "action": "allow"},
                {"permission": "webfetch", "pattern": "*", "action": "allow"},
                {"permission": "websearch", "pattern": "*", "action": "allow"},
                {"permission": "codesearch", "pattern": "*", "action": "allow"},
            ],
        ),
        "coder": AgentInfo(
            name="coder",
            description="Code modification agent for isolated worktree execution. Has full write permissions.",
            mode="subagent",
            native=True,
            prompt=_load_prompt("subagent"),
            permission=[
                *base,
                {"permission": "question", "pattern": "*", "action": "deny"},
                {"permission": "todowrite", "pattern": "*", "action": "deny"},
            ],
        ),
        "compaction": AgentInfo(
            name="compaction",
            mode="primary",
            native=True,
            hidden=True,
            prompt=_load_prompt("compaction"),
            permission=[{"permission": "*", "pattern": "*", "action": "deny"}],
        ),
        "title": AgentInfo(
            name="title",
            mode="primary",
            native=True,
            hidden=True,
            temperature=0.5,
            prompt=_load_prompt("title"),
            permission=[{"permission": "*", "pattern": "*", "action": "deny"}],
        ),
        "summary": AgentInfo(
            name="summary",
            mode="primary",
            native=True,
            hidden=True,
            prompt=_load_prompt("summary"),
            permission=[{"permission": "*", "pattern": "*", "action": "deny"}],
        ),
    }


_cached_agents: dict[str, AgentInfo] | None = None
_agents_lock = threading.Lock()


def _load_agents() -> dict[str, AgentInfo]:
    """Load all agents (built-in + config)."""
    global _cached_agents
    if _cached_agents is not None:
        return _cached_agents

    with _agents_lock:
        if _cached_agents is not None:
            return _cached_agents
        _cached_agents = _build_all_agents()
        return _cached_agents


def _build_all_agents() -> dict[str, AgentInfo]:
    """Internal: build agents from defaults + config + registry (called under lock).

    Layering (later wins on name conflict):
      1. Built-in agents (from ``_build_agents``)
      2. ``agent:`` mapping in mycode.json / project config
      3. Markdown agents in ``~/.mycode/agents/*.md`` (global)
      4. Markdown agents in ``<project>/.mycode/agents/*.md`` (project)

    Steps 3 and 4 are delegated to :class:`mycode.orchestration.registry.AgentRegistry`
    which also applies the ``extends`` chain.
    """
    agents = _build_agents()
    cfg = configmod.get()

    # Merge config agents
    for name, acfg in (cfg.agent or {}).items():
        if acfg.disable:
            agents.pop(name, None)
            continue

        if name not in agents:
            agents[name] = AgentInfo(name=name, mode="all", native=False)

        agent = agents[name]
        if acfg.model:
            parts = acfg.model.split("/", 1)
            if len(parts) == 2:
                agent.model = {"providerID": parts[0], "modelID": parts[1]}
        if acfg.prompt is not None:
            agent.prompt = acfg.prompt
        if acfg.description is not None:
            agent.description = acfg.description
        if acfg.temperature is not None:
            agent.temperature = acfg.temperature
        if acfg.top_p is not None:
            agent.top_p = acfg.top_p
        if acfg.mode is not None:
            agent.mode = acfg.mode
        if acfg.color is not None:
            agent.color = acfg.color
        if acfg.hidden is not None:
            agent.hidden = acfg.hidden
        if acfg.steps is not None:
            agent.steps = acfg.steps

    # --- Registry overlay (global + project Markdown agents) ---
    # Import lazily to avoid a circular import at module load
    # (orchestration.registry imports agent.agent).
    try:
        from mycode.orchestration.registry.agent_registry import AgentRegistry
        from mycode.project.instance import current_or_none

        inst = current_or_none()
        project_dir = inst.directory if inst else None
        registry = AgentRegistry(project_dir=project_dir)

        for entry in registry.list_entries():
            # builtin/config layers are already reflected above; only overlay
            # file-based agents here so they can use extends against
            # already-materialized builtin/config agents.
            if entry.source not in ("global", "project"):
                continue
            try:
                resolved = registry.resolve(entry.name)
            except Exception as exc:
                logger.warn(
                    "agent registry resolve failed",
                    name=entry.name,
                    source=entry.source,
                    error=str(exc),
                )
                continue
            agents[entry.name] = resolved
    except Exception as exc:
        # A broken registry must never block primary agent loading.
        logger.warn("agent registry overlay skipped", error=str(exc))

    return agents


async def get(name: str) -> AgentInfo | None:
    """Get an agent by name (returns a copy to prevent cache mutation)."""
    agents = _load_agents()
    agent = agents.get(name)
    return copy.copy(agent) if agent else None


async def list_agents() -> list[AgentInfo]:
    """List user-selectable agents, sorted with default first.

    Excludes hidden agents (compaction/title/summary) and subagent-only
    agents (general/explore) which are not meant to be directly selected by users.
    """
    agents = _load_agents()
    cfg = configmod.get()
    default = cfg.default_agent or "build"

    visible = [a for a in agents.values() if not a.hidden and a.mode != "subagent"]
    return sorted(
        visible,
        key=lambda a: (a.name != default, a.name),
    )


async def default_agent() -> str:
    """Get the default agent name."""
    cfg = configmod.get()
    if cfg.default_agent:
        agents = _load_agents()
        if cfg.default_agent in agents:
            return cfg.default_agent
    return "build"


def invalidate() -> None:
    """Clear cached agents."""
    global _cached_agents
    _cached_agents = None
    logger.debug("agent cache invalidated")
