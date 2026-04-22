"""Topology layer: parse orchestration files into in-memory specs."""

from mycode.orchestration.topology.agent_resolver import (
    AgentResolveError,
    resolve_agent_spec,
    resolve_all_agents,
)
from mycode.orchestration.topology.loader import (
    OrchestrationLoadError,
    load_file,
    load_mapping,
    render_variables,
)
from mycode.orchestration.topology.schema import (
    AgentSpec,
    OrchestrationSpec,
    SpawnSpec,
    StageSpec,
)
from mycode.orchestration.topology.validator import (
    OrchestrationValidationError,
    validate,
)

__all__ = [
    "AgentResolveError",
    "AgentSpec",
    "OrchestrationLoadError",
    "OrchestrationSpec",
    "OrchestrationValidationError",
    "SpawnSpec",
    "StageSpec",
    "load_file",
    "load_mapping",
    "render_variables",
    "resolve_agent_spec",
    "resolve_all_agents",
    "validate",
]
