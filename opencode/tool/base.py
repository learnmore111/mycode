"""Tool base class, result types, errors, and output builder.

Inspired by kimi-cli's dual-layer tool architecture:
- Type-safe parameters via Pydantic BaseModel (CallableTool[Params])
- Structured return values (ToolOk / ToolError) with separate output/display
- Unified error hierarchy (ToolNotFoundError / ToolParseError / ToolValidateError / ToolRuntimeError)
- ToolResultBuilder for output truncation management
"""
from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from types import get_original_bases as get_orig_bases
from typing import Any, ClassVar, Generic, TypeVar, get_args

from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Tool context
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Execution context passed to every tool call."""
    session_id: str
    message_id: str
    agent: str
    abort: Any = None  # asyncio.Event or signal
    call_id: str = ""
    messages: list[Any] = field(default_factory=list)

    async def ask_permission(self, *, permission: str, patterns: list[str], metadata: dict[str, Any] | None = None) -> None:
        """Request permission — to be connected to PermissionManager."""
        pass  # Will be wired in processor


# ---------------------------------------------------------------------------
# Structured tool results  (replaces the old simple ToolResult)
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Structured tool return value.

    Attributes:
        output:   Text sent to the model as the tool response.
        message:  Human-readable explanation (also sent to model, optional).
        display:  Rich text shown only in the UI, NOT sent to the model.
        is_error: Whether this result represents an error.
        title:    Short title for UI display.
        metadata: Arbitrary key-value pairs for downstream consumers.
    """
    output: str = ""
    message: str = ""
    display: str = ""
    is_error: bool = False
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolOk(ToolResult):
    """Convenience constructor for a successful tool result."""
    def __init__(self, output: str = "", *, message: str = "", display: str = "", title: str = "", metadata: dict[str, Any] | None = None):
        super().__init__(output=output, message=message, display=display, is_error=False, title=title, metadata=metadata or {})


class ToolError(ToolResult):
    """Convenience constructor for an error tool result."""
    def __init__(self, output: str = "", *, message: str = "", display: str = "", title: str = "", metadata: dict[str, Any] | None = None):
        super().__init__(output=output, message=message, display=display, is_error=True, title=title, metadata=metadata or {})


# ---------------------------------------------------------------------------
# Unified error hierarchy
# ---------------------------------------------------------------------------

class ToolBaseError(Exception):
    """Base exception for all tool-related errors."""

class ToolNotFoundError(ToolBaseError):
    """Raised when a tool_call references an unknown tool id."""
    def __init__(self, tool_id: str):
        self.tool_id = tool_id
        super().__init__(f"Unknown tool: {tool_id}")

class ToolParseError(ToolBaseError):
    """Raised when tool arguments cannot be parsed from JSON."""
    def __init__(self, tool_id: str, raw: str, cause: Exception | None = None):
        self.tool_id = tool_id
        self.raw = raw
        self.cause = cause
        super().__init__(f"Failed to parse arguments for tool '{tool_id}': {cause}")

class ToolValidateError(ToolBaseError):
    """Raised when tool arguments fail Pydantic validation."""
    def __init__(self, tool_id: str, errors: list[dict[str, Any]]):
        self.tool_id = tool_id
        self.errors = errors
        msg_parts = [f"{e.get('loc', ['?'])}: {e.get('msg', '?')}" for e in errors[:5]]
        super().__init__(f"Validation failed for tool '{tool_id}': {'; '.join(msg_parts)}")

class ToolRuntimeError(ToolBaseError):
    """Raised when tool execution fails at runtime."""
    def __init__(self, tool_id: str, cause: Exception):
        self.tool_id = tool_id
        self.cause = cause
        super().__init__(f"Tool '{tool_id}' runtime error: {cause}")


# ---------------------------------------------------------------------------
# ToolResultBuilder — output buffer with character/line limits
# ---------------------------------------------------------------------------

class ToolResultBuilder:
    """Accumulates tool output with automatic truncation.

    - max_chars:    hard limit on total character count (default 50_000)
    - max_line_len: individual lines longer than this are trimmed (default 2_000)
    """
    def __init__(self, *, max_chars: int = 50_000, max_line_len: int = 2_000):
        self._parts: list[str] = []
        self._total: int = 0
        self._truncated: bool = False
        self.max_chars = max_chars
        self.max_line_len = max_line_len

    def add(self, text: str) -> ToolResultBuilder:
        """Append text to the output buffer."""
        if self._truncated:
            return self
        # Trim long lines
        lines = text.split("\n")
        trimmed: list[str] = []
        for line in lines:
            if len(line) > self.max_line_len:
                trimmed.append(line[:self.max_line_len] + "... (line truncated)")
            else:
                trimmed.append(line)
        text = "\n".join(trimmed)

        if self._total + len(text) > self.max_chars:
            remaining = self.max_chars - self._total
            if remaining > 0:
                self._parts.append(text[:remaining])
            self._truncated = True
        else:
            self._parts.append(text)
            self._total += len(text)
        return self

    def add_heading(self, heading: str) -> ToolResultBuilder:
        """Add a section heading."""
        return self.add(f"\n--- {heading} ---\n")

    @property
    def truncated(self) -> bool:
        return self._truncated

    def build(self) -> str:
        """Return the accumulated output string."""
        result = "".join(self._parts)
        if self._truncated:
            result += f"\n\n... output truncated ({self._total} chars, limit {self.max_chars})"
        return result

    def __len__(self) -> int:
        return self._total


# ---------------------------------------------------------------------------
# Tool base classes
# ---------------------------------------------------------------------------

class ToolInfo(ABC):
    """Base class for all tools (backward-compatible interface).

    Subclasses must define `id` and `description` as class attributes,
    and implement `parameters_schema()` and `execute()`.
    """
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
        """Convert to litellm/OpenAI function calling format.

        Uses the .md description file from descriptions/ if available,
        falling back to the class-level description attribute.
        """
        desc = load_description(self.id) or self.description
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": desc,
                "parameters": self.parameters_schema(),
            },
        }


# Type variable for Pydantic parameter models
Params = TypeVar("Params", bound=BaseModel)


class CallableTool(ToolInfo, Generic[Params]):  # noqa: UP046
    """Type-safe tool base class with Pydantic parameter validation.

    Usage::

        class MyParams(BaseModel):
            file_path: str
            line_count: int = 10

        class MyTool(CallableTool[MyParams]):
            id = "my_tool"
            description = "Does something cool"

            async def call(self, params: MyParams, ctx: ToolContext) -> ToolResult:
                ...

    Features:
        - Automatic JSON Schema generation from Pydantic model
        - Automatic parameter validation with clear error messages
        - Subclasses implement `call(params, ctx)` instead of raw `execute(args, ctx)`
    """

    # Cache the resolved Params type per class
    _params_cls: ClassVar[type[BaseModel] | None] = None

    @classmethod
    def _resolve_params_cls(cls) -> type[BaseModel]:
        """Resolve the Params type argument from the class hierarchy."""
        if cls._params_cls is not None:
            return cls._params_cls

        for base in get_orig_bases(cls):
            args = get_args(base)
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                cls._params_cls = args[0]
                return args[0]

        raise TypeError(f"{cls.__name__} must specify a Params type, e.g. CallableTool[MyParams]")

    def parameters_schema(self) -> dict[str, Any]:
        """Auto-generate JSON Schema from the Pydantic model."""
        params_cls = self._resolve_params_cls()
        schema = params_cls.model_json_schema()
        # Ensure top-level type is "object" (Pydantic v2 should already do this)
        schema.setdefault("type", "object")
        # Remove title/description from top level (LLM gets these from tool definition)
        schema.pop("title", None)
        # Keep description if present (some models benefit from it)
        return schema

    def validate_args(self, args: dict[str, Any]) -> Params:
        """Validate raw arguments dict and return a typed Params instance."""
        params_cls = self._resolve_params_cls()
        try:
            return params_cls.model_validate(args)
        except ValidationError as e:
            raise ToolValidateError(self.id, e.errors()) from e

    @abstractmethod
    async def call(self, params: Params, ctx: ToolContext) -> ToolResult:
        """Execute the tool with validated, typed parameters.

        Subclasses implement this instead of execute().
        """
        ...

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate arguments and delegate to call(). Override-proof."""
        params = self.validate_args(args)
        return await self.call(params, ctx)


# ---------------------------------------------------------------------------
# Description template loading
# ---------------------------------------------------------------------------

_DESC_DIR = Path(__file__).parent / "descriptions"


def load_description(tool_id: str, **kwargs: str) -> str:
    """Load a tool description from a .md template file.

    Looks for ``descriptions/{tool_id}.md`` relative to this module.
    If the file is not found, returns an empty string.

    Optional keyword arguments are substituted into the template using
    ``str.format_map`` (simple ``{key}`` placeholders).
    """
    path = _DESC_DIR / f"{tool_id}.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if kwargs:
        with contextlib.suppress(KeyError):
            text = text.format_map(kwargs)
    return text
