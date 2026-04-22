"""Runtime layer: execute orchestration specs.

M5 delivers the **coordinator** runtime (sequential/parallel stages,
fan-out, coordinator synthesis).  M6 adds the **swarm** runtime
(mailbox-driven peer agents) with an inprocess backend.

Public surface:

- :class:`Coordinator` / :func:`run_coordinator` — coordinator executor.
- :func:`run_swarm` / :class:`SwarmResult` — swarm executor.
- :class:`AgentRunner` / :class:`SwarmAgentRunner` protocols +
  :class:`LiteLLMAgentRunner` / :class:`LiteLLMSwarmRunner` defaults.
- :class:`RunContext` / :class:`StageOutput` / :class:`SpawnOutput` —
  aggregated results that consumers (CLI, server, tests) read.
- :class:`MailboxSystem` / :class:`Envelope` — swarm message plumbing.
"""

from mycode.orchestration.runtime.context import (
    RunContext,
    SpawnOutput,
    StageOutput,
)
from mycode.orchestration.runtime.coordinator import (
    Coordinator,
    CoordinatorError,
    CoordinatorResult,
    run_coordinator,
)
from mycode.orchestration.runtime.mailbox import (
    Envelope,
    EnvelopeKind,
    InprocessMailbox,
    Mailbox,
    MailboxSystem,
)
from mycode.orchestration.runtime.spawn import (
    DEFAULT_MAX_TURNS,
    AgentRunner,
    LiteLLMAgentRunner,
    SpawnRequest,
)
from mycode.orchestration.runtime.swarm import (
    LiteLLMSwarmRunner,
    SwarmAgentContext,
    SwarmAgentRunner,
    SwarmError,
    SwarmResult,
    run_swarm,
)

__all__ = [
    "DEFAULT_MAX_TURNS",
    "AgentRunner",
    "Coordinator",
    "CoordinatorError",
    "CoordinatorResult",
    "Envelope",
    "EnvelopeKind",
    "InprocessMailbox",
    "LiteLLMAgentRunner",
    "LiteLLMSwarmRunner",
    "Mailbox",
    "MailboxSystem",
    "RunContext",
    "SpawnOutput",
    "SpawnRequest",
    "StageOutput",
    "SwarmAgentContext",
    "SwarmAgentRunner",
    "SwarmError",
    "SwarmResult",
    "run_coordinator",
    "run_swarm",
]
