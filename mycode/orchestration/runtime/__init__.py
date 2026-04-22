"""Runtime layer: execute orchestration specs.

M5 delivers the **coordinator** runtime (sequential/parallel stages,
fan-out, coordinator synthesis).  The swarm runtime (mailbox-driven
peer agents) lands in M6.

Public surface:

- :class:`Coordinator` / :func:`run_coordinator` — top-level executor.
- :class:`AgentRunner` protocol + :class:`LiteLLMAgentRunner` default.
- :class:`RunContext` / :class:`StageOutput` / :class:`SpawnOutput` —
  aggregated results that consumers (CLI, server, tests) read after
  ``run()`` returns.
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
from mycode.orchestration.runtime.spawn import (
    DEFAULT_MAX_TURNS,
    AgentRunner,
    LiteLLMAgentRunner,
    SpawnRequest,
)

__all__ = [
    "DEFAULT_MAX_TURNS",
    "AgentRunner",
    "Coordinator",
    "CoordinatorError",
    "CoordinatorResult",
    "LiteLLMAgentRunner",
    "RunContext",
    "SpawnOutput",
    "SpawnRequest",
    "StageOutput",
    "run_coordinator",
]
