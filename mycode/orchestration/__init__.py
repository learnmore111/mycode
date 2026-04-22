"""Multi-agent orchestration module.

This package provides declarative orchestration of multi-agent workflows,
supporting two modes:

- **Coordinator mode**: a central coordinator agent dispatches Worker agents
  and synthesizes their outputs. Good for single complex tasks.
- **Swarm mode**: a team of peer agents communicate via named mailboxes.
  Good for long-running collaborative work.

Milestones:

- M1 ✅ topology schema/loader/validator + flow registry + CLI inspect.
- M2 ✅ agent registry (.md frontmatter + extends chain).
- M3 ✅ registry overlay on ``agentmod.get`` + subagent tools/max_turns.
- M4 ✅ flow ``AgentSpec.extends`` → registry resolver.
- M5 ✅ coordinator runtime (DAG + parallel + fan-out + synthesis).
- M6 ✅ swarm runtime (mailbox-driven peer agents, inprocess backend).
- M7 ✅ event bus integration + HTTP routes + SSE stream per run.
"""

from mycode.orchestration.runtime import (
    AgentRunner,
    BusOrchestrationEmitter,
    Coordinator,
    CoordinatorError,
    CoordinatorResult,
    Envelope,
    LiteLLMAgentRunner,
    LiteLLMSwarmRunner,
    MailboxSystem,
    OrchestrationEventEmitter,
    RecordingEmitter,
    RunContext,
    SpawnOutput,
    SpawnRequest,
    StageOutput,
    SwarmAgentContext,
    SwarmAgentRunner,
    SwarmError,
    SwarmResult,
    run_coordinator,
    run_swarm,
)
from mycode.orchestration.topology.schema import (
    AgentSpec,
    OrchestrationSpec,
    SpawnSpec,
    StageSpec,
)

__all__ = [
    "AgentRunner",
    "AgentSpec",
    "BusOrchestrationEmitter",
    "Coordinator",
    "CoordinatorError",
    "CoordinatorResult",
    "Envelope",
    "LiteLLMAgentRunner",
    "LiteLLMSwarmRunner",
    "MailboxSystem",
    "OrchestrationEventEmitter",
    "OrchestrationSpec",
    "RecordingEmitter",
    "RunContext",
    "SpawnOutput",
    "SpawnRequest",
    "SpawnSpec",
    "StageOutput",
    "StageSpec",
    "SwarmAgentContext",
    "SwarmAgentRunner",
    "SwarmError",
    "SwarmResult",
    "run_coordinator",
    "run_swarm",
]
