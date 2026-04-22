"""Multi-agent orchestration module.

This package provides declarative orchestration of multi-agent workflows,
supporting two modes:

- **Coordinator mode**: a central coordinator agent dispatches Worker agents
  and synthesizes their outputs. Good for single complex tasks.
- **Swarm mode**: a team of peer agents communicate via named mailboxes.
  Good for long-running collaborative work.

M1 scope (current): topology schema/loader/validator + flow registry + CLI inspect.
Runtime execution (CoordinatorRunner / SwarmRunner) is added in M4 / M5.
"""

from mycode.orchestration.topology.schema import (
    AgentSpec,
    OrchestrationSpec,
    SpawnSpec,
    StageSpec,
)

__all__ = [
    "AgentSpec",
    "OrchestrationSpec",
    "SpawnSpec",
    "StageSpec",
]
