"""Runtime layer: execute orchestration specs.

M5 delivers the **coordinator** runtime (sequential/parallel stages,
fan-out, coordinator synthesis).  M6 adds the **swarm** runtime
(mailbox-driven peer agents) with an inprocess backend.  M6.5 plugs in
file-backed and terminal-mirroring mailbox backends so swarm peers can
span multiple processes (and optionally show up inside a live tmux or
iTerm2 session).  M7 adds an **event emitter** so both runtimes can
publish lifecycle events onto :class:`mycode.bus.bus.Bus` — letting the
FastAPI SSE endpoints and the web UI observe progress in real time.

Public surface:

- :class:`Coordinator` / :func:`run_coordinator` — coordinator executor.
- :func:`run_swarm` / :class:`SwarmResult` — swarm executor.
- :class:`AgentRunner` / :class:`SwarmAgentRunner` protocols +
  :class:`LiteLLMAgentRunner` / :class:`LiteLLMSwarmRunner` defaults.
- :class:`RunContext` / :class:`StageOutput` / :class:`SpawnOutput` —
  aggregated results that consumers (CLI, server, tests) read.
- :class:`MailboxSystem` / :class:`Envelope` — swarm message plumbing.
- :class:`InprocessMailbox` / :class:`FileMailbox` / :class:`TmuxMailbox`
  / :class:`ItermMailbox` — M6 + M6.5 concrete backends.
- :class:`BusOrchestrationEmitter` / :class:`RecordingEmitter` /
  :class:`OrchestrationEventEmitter` — M7 event bridge.
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
from mycode.orchestration.runtime.events import (
    BusOrchestrationEmitter,
    OrchestrationEventEmitter,
    RecordingEmitter,
)
from mycode.orchestration.runtime.mailbox import (
    BackendKind,
    Envelope,
    EnvelopeKind,
    InprocessMailbox,
    Mailbox,
    MailboxSystem,
)
from mycode.orchestration.runtime.mailbox_file import FileMailbox, FileSeqCounter
from mycode.orchestration.runtime.mailbox_terminal import ItermMailbox, TmuxMailbox
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
    run_supervisor_collaboration,
    run_swarm,
)

__all__ = [
    "DEFAULT_MAX_TURNS",
    "AgentRunner",
    "BackendKind",
    "BusOrchestrationEmitter",
    "Coordinator",
    "CoordinatorError",
    "CoordinatorResult",
    "Envelope",
    "EnvelopeKind",
    "FileMailbox",
    "FileSeqCounter",
    "InprocessMailbox",
    "ItermMailbox",
    "LiteLLMAgentRunner",
    "LiteLLMSwarmRunner",
    "Mailbox",
    "MailboxSystem",
    "OrchestrationEventEmitter",
    "RecordingEmitter",
    "RunContext",
    "SpawnOutput",
    "SpawnRequest",
    "StageOutput",
    "SwarmAgentContext",
    "SwarmAgentRunner",
    "SwarmError",
    "SwarmResult",
    "TmuxMailbox",
    "run_coordinator",
    "run_supervisor_collaboration",
    "run_swarm",
]
