"""Registry layer: discover orchestration flows and custom agents.

Three-level override (later wins):

1. Built-in:  ``mycode/orchestration/flows/*.yaml``
2. Global:    ``~/.mycode/orchestrations/*.{yaml,yml,json}``
3. Project:   ``<project>/.mycode/orchestrations/*.{yaml,yml,json}``

Agent registry follows the same layering under ``agents/`` directories.
"""

from mycode.orchestration.registry.agent_registry import (
    AgentLoadError,
    AgentRegistry,
    AgentSourceEntry,
)
from mycode.orchestration.registry.agent_registry import (
    get_default_registry as get_default_agent_registry,
)
from mycode.orchestration.registry.flow_registry import (
    FlowInfo,
    FlowRegistry,
    get_default_registry,
)

__all__ = [
    "AgentLoadError",
    "AgentRegistry",
    "AgentSourceEntry",
    "FlowInfo",
    "FlowRegistry",
    "get_default_agent_registry",
    "get_default_registry",
]
