"""Tool base class and context. Equivalent to src/tool/tool.ts."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    title: str
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class ToolContext:
    session_id: str
    message_id: str
    agent: str
    abort: Any = None  # asyncio.Event or signal
    call_id: str = ""
    messages: list[Any] = field(default_factory=list)

    async def ask_permission(self, *, permission: str, patterns: list[str], metadata: dict[str, Any] | None = None) -> None:
        """Request permission — to be connected to PermissionManager."""
        pass  # Will be wired in processor

class ToolInfo(ABC):
    """Base class for all tools."""
    id: str = ""
    description: str = ""

    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """Return JSON Schema for the tool's parameters."""
        ...

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    def to_llm_tool(self) -> dict[str, Any]:
        """Convert to litellm/OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.parameters_schema(),
            },
        }
