"""Tool base class, result types, errors, and output builder.

Inspired by kimi-cli's dual-layer tool architecture:
- Type-safe parameters via Pydantic BaseModel (CallableTool[Params])
- Structured return values (ToolOk / ToolError) with separate output/display
- Unified error hierarchy (ToolNotFoundError / ToolParseError / ToolValidateError / ToolRuntimeError)
- ToolResultBuilder for output truncation management
- Capability declarations (is_read_only / is_destructive / is_concurrency_safe)
- Path safety validation (prevent directory escape)
- Atomic file write helper
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from types import get_original_bases as get_orig_bases
from typing import Any, ClassVar, Generic, TypeVar, get_args

from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Tool context
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Session-level file-read tracking (for read-before-edit guard)
# ---------------------------------------------------------------------------

_session_read_files: dict[str, dict[str, float]] = {}
"""Maps session_id -> {absolute_path: mtime_at_read}."""


def _record_file_read(session_id: str, absolute_path: str) -> None:
    """Record that a file was read in this session."""
    import os as _os
    try:
        mtime = _os.path.getmtime(absolute_path)
    except OSError:
        mtime = 0.0
    _session_read_files.setdefault(session_id, {})[absolute_path] = mtime


def _assert_file_read(session_id: str, absolute_path: str) -> str | None:
    """Return error message if file was not read in this session or was modified externally."""
    import os as _os
    reads = _session_read_files.get(session_id, {})
    if absolute_path not in reads:
        return (
            f"You must read this file before editing it. "
            f"Use the read tool first to get the exact content."
        )
    last_mtime = reads[absolute_path]
    try:
        current_mtime = _os.path.getmtime(absolute_path)
    except OSError:
        return None  # File gone — let the tool handle it
    if current_mtime != last_mtime:
        return (
            f"File has been modified since it was last read. "
            f"Please read the file again before editing it."
        )
    return None


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
                self._total += remaining
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
            result += f"\n\n... output truncated (wrote {self._total} chars, limit {self.max_chars})"
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

    Capability declarations (override in subclasses):
        is_read_only(args)       — True if the operation only reads (no side effects)
        is_destructive(args)     — True if the operation is irreversible (delete, overwrite, send)
        is_concurrency_safe(args)— True if safe to run in parallel with other tool calls
        is_enabled()             — True if the tool is currently available
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

    # ── Capability declarations ──────────────────────────────────────

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        """Whether this tool call only reads data (no side effects).

        Used by the permission system: plan mode auto-allows read-only tools.
        """
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        """Whether this tool call is irreversible (delete, overwrite, send).

        Destructive tools may require extra confirmation.
        """
        return False

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        """Whether this tool can safely run in parallel with other calls.

        Tools that modify files are generally NOT concurrency-safe.
        """
        return True

    def is_enabled(self) -> bool:
        """Whether the tool is currently available."""
        return True

    # ── LLM format ───────────────────────────────────────────────────

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
            return params_cls.model_validate(args)  # type: ignore[return-value]
        except ValidationError as e:
            raise ToolValidateError(self.id, e.errors()) from e  # type: ignore[arg-type]

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


# ---------------------------------------------------------------------------
# Path safety validation
# ---------------------------------------------------------------------------


def validate_path_safety(file_path: str, base_dir: str) -> str | None:
    """Validate that a resolved path stays within the project directory.

    Returns an error message string if the path is unsafe, or None if safe.
    This prevents directory traversal attacks (e.g. ``../../etc/passwd``).
    """
    try:
        resolved = os.path.realpath(os.path.join(base_dir, file_path) if not os.path.isabs(file_path) else file_path)
        base_real = os.path.realpath(base_dir)
        if not resolved.startswith(base_real + os.sep) and resolved != base_real:
            return (
                f"Path '{file_path}' resolves to '{resolved}' which is outside the project directory "
                f"'{base_real}'. For safety, file operations are restricted to the project directory."
            )
    except (ValueError, OSError) as e:
        return f"Invalid path '{file_path}': {e}"
    return None


def resolve_tool_path(file_path: str, base_dir: str) -> tuple[str, str | None]:
    """Resolve a file path relative to the project base and validate safety.

    Returns:
        (resolved_absolute_path, error_message_or_none)
    """
    full = os.path.join(base_dir, file_path) if not os.path.isabs(file_path) else file_path
    error = validate_path_safety(file_path, base_dir)
    return full, error


# ---------------------------------------------------------------------------
# Atomic file write
# ---------------------------------------------------------------------------


def atomic_write(file_path: str, content: str, encoding: str = "utf-8") -> None:
    """Write content to a file atomically using a temporary file + rename.

    This prevents file corruption if the process is interrupted mid-write.
    The temporary file is created in the same directory as the target to
    ensure they are on the same filesystem (required for atomic rename).

    After a successful write we fan-out a ``post_write`` notification so
    consumers (today: the LSP layer sending ``textDocument/didChange``)
    can react without having to wrap every call site. Listeners run
    best-effort — failures are logged and never propagate.
    """
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    _fire_post_write(file_path, content)


# --- Post-write notification hook -----------------------------------------

# List of (sync | async) callables invoked after ``atomic_write`` succeeds.
# Keep it small: LSP didChange, possibly a watcher in the future.
_PostWriteHook = Any  # Callable[[str, str], None | Awaitable[None]]
_post_write_hooks: list[_PostWriteHook] = []


def register_post_write_hook(fn: _PostWriteHook) -> None:
    """Register a callback fired after every successful ``atomic_write``.

    The callback receives ``(file_path, content)``. It may be a sync
    function or a coroutine; coroutines are scheduled onto the running
    event loop if one exists, else dropped (a sync-only caller like a
    standalone CLI has no loop to post to, and losing an LSP notice
    there is acceptable).
    """
    if fn not in _post_write_hooks:
        _post_write_hooks.append(fn)


def _fire_post_write(file_path: str, content: str) -> None:
    import asyncio as _asyncio
    import inspect as _inspect

    for fn in list(_post_write_hooks):
        try:
            result = fn(file_path, content)
            if _inspect.iscoroutine(result):
                try:
                    loop = _asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop — drop the coroutine so it doesn't
                    # leak a never-awaited warning.
                    result.close()
                    continue
                loop.create_task(result)
        except Exception:  # noqa: BLE001 — hooks must not break writes
            # Caller-side hook failures are non-fatal; we don't even log
            # to avoid spamming on every edit when an LSP is down.
            pass
