"""LLM streaming interface using litellm.

Wraps litellm.acompletion to provide a unified streaming interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging as _logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import litellm

from mycode.provider.provider import litellm_model_name
from mycode.util import log as logmod
from mycode.util import metrics

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.provider.schema import Model

logger = logmod.create(service="llm")

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False  # type: ignore[attr-defined]
_logging.getLogger("LiteLLM").setLevel(_logging.WARNING)
_logging.getLogger("litellm").setLevel(_logging.WARNING)
_logging.getLogger("httpx").setLevel(_logging.WARNING)


@dataclass
class StreamInput:
    """Input for LLM streaming."""

    model: Model
    messages: list[dict[str, Any]]
    system: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None  # "auto" | "required" | "none"
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    api_key: str | None = None
    api_base: str | None = None
    # When set, stream() watches this event between chunks and tears the
    # HTTP response down as soon as it fires — letting the processor
    # react to user-initiated abort within one chunk rather than having
    # to wait for the LLM to stop generating on its own.
    abort_event: asyncio.Event | None = None


@dataclass
class TextDelta:
    type: str = "text-delta"
    text: str = ""


@dataclass
class ReasoningDelta:
    type: str = "reasoning-delta"
    text: str = ""


@dataclass
class ToolCallDelta:
    type: str = "tool-call"
    tool_call_id: str = ""
    tool_name: str = ""
    args: str = ""  # JSON string of arguments


@dataclass
class ToolCallPartial:
    type: str = "tool-call-streaming-start"
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass
class ToolCallArgsPartial:
    type: str = "tool-call-delta"
    tool_call_id: str = ""
    args_delta: str = ""


@dataclass
class FinishEvent:
    type: str = "finish"
    reason: str = "stop"  # "stop" | "tool-calls" | "length"
    usage: dict[str, int] = field(default_factory=dict)
    raw_usage: dict[str, Any] | None = None
    cost: float = 0.0


@dataclass
class ErrorEvent:
    type: str = "error"
    error: str = ""
    # Classified code so callers (processor, UI, retry logic) can make
    # decisions without matching on the error string. Values follow the
    # familiar HTTP-ish taxonomy:
    #   "rate_limit"       — 429 / throttled, safe to retry with backoff
    #   "auth"             — invalid/expired credentials, DO NOT retry
    #   "bad_request"      — invalid params, prompt too long, etc.
    #   "context_overflow" — prompt exceeded model context window
    #   "content_filter"   — provider refused on content policy grounds
    #   "not_found"        — model / endpoint missing
    #   "timeout"          — request timed out
    #   "connection"       — transient network error, retryable
    #   "server"           — 5xx upstream, retryable
    #   "unknown"          — everything else
    error_code: str = "unknown"
    retryable: bool = False
    status_code: int | None = None


async def _with_abort(
    response: Any, abort_event: asyncio.Event | None,
) -> AsyncGenerator[Any, None]:
    """Iterate ``response`` but stop early if ``abort_event`` fires.

    litellm's streaming response is an async iterator. If we simply
    ``async for chunk in response`` the only exit point is exhaustion or
    an upstream error — a user hitting abort could wait tens of seconds
    for the LLM to stop talking. Here we race each ``__anext__`` call
    against the abort event and break out cleanly as soon as it's set.
    """
    if abort_event is None:
        async for chunk in response:
            yield chunk
        return

    it = response.__aiter__()
    while True:
        next_task = asyncio.ensure_future(it.__anext__())
        abort_task = asyncio.ensure_future(abort_event.wait())
        done, _pending = await asyncio.wait(
            {next_task, abort_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        if next_task in done:
            abort_task.cancel()
            try:
                chunk = next_task.result()
            except StopAsyncIteration:
                return
            yield chunk
        else:
            # Abort fired first. Cancel the in-flight chunk read and
            # close the upstream response so the provider stops sending.
            next_task.cancel()
            with contextlib.suppress(BaseException):
                close = getattr(response, "aclose", None) or getattr(response, "close", None)
                if close is not None:
                    maybe = close()
                    if asyncio.iscoroutine(maybe):
                        await maybe
            logger.info("stream aborted by consumer")
            return


def _classify_exception(exc: BaseException) -> tuple[str, bool, int | None]:
    """Map a litellm / generic exception to (error_code, retryable, status)."""
    # Timeouts first — both asyncio.TimeoutError and litellm wrap one.
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "timeout", True, None

    # litellm may not import cleanly on every platform; attribute probe is
    # safer than isinstance against classes we might fail to resolve.
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)

    mapping: dict[str, tuple[str, bool]] = {
        "RateLimitError": ("rate_limit", True),
        "RouterRateLimitError": ("rate_limit", True),
        "RouterRateLimitErrorBasic": ("rate_limit", True),
        "AuthenticationError": ("auth", False),
        "PermissionDeniedError": ("auth", False),
        "BadRequestError": ("bad_request", False),
        "InvalidRequestError": ("bad_request", False),
        "UnprocessableEntityError": ("bad_request", False),
        "UnsupportedParamsError": ("bad_request", False),
        "JSONSchemaValidationError": ("bad_request", False),
        "ContextWindowExceededError": ("context_overflow", False),
        "ContentPolicyViolationError": ("content_filter", False),
        "NotFoundError": ("not_found", False),
        "APIConnectionError": ("connection", True),
        "BadGatewayError": ("server", True),
        "InternalServerError": ("server", True),
        "ServiceUnavailableError": ("server", True),
        "APIError": ("server", True),
    }
    code, retryable = mapping.get(name, ("unknown", False))
    # Fall back to status_code hints when the class name is unfamiliar.
    if code == "unknown" and isinstance(status, int):
        if status == 429:
            return "rate_limit", True, status
        if status in (401, 403):
            return "auth", False, status
        if status == 404:
            return "not_found", False, status
        if 500 <= status < 600:
            return "server", True, status
        if 400 <= status < 500:
            return "bad_request", False, status
    return code, retryable, status if isinstance(status, int) else None


# Union type for stream events
StreamEvent = ReasoningDelta | TextDelta | ToolCallDelta | ToolCallPartial | ToolCallArgsPartial | FinishEvent | ErrorEvent


DASHSCOPE_EXPLICIT_CACHE_MODELS = frozenset({
    "qwen3-max",
    "qwen3.6-max-preview",
    "qwen-max",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen-plus",
    "qwen3.6-flash",
    "qwen3.5-flash",
    "qwen-flash",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
    "qwen3-vl-plus",
    "qwen3-vl-flash",
    "deepseek-v3.2",
    "kimi-k2.6",
    "kimi-k2.5",
    "glm-5.1",
})

DASHSCOPE_EXPLICIT_CACHE_PREFIX_MODELS = (
    "qwen3.5-plus-",
)


def _usage_get(obj: Any, key: str, default: Any = 0) -> Any:
    """Read a usage field from either an object or dict-like payload."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _usage_get_path(obj: Any, *path: str, default: Any = 0) -> Any:
    """Walk nested usage payloads across object and dict representations."""
    current = obj
    for key in path:
        current = _usage_get(current, key, None)
        if current is None:
            return default
    return current


def _dashscope_explicit_cache_content(text: str) -> list[dict[str, Any]]:
    """Build a DashScope OpenAI-compatible content block with explicit cache."""
    return [{
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }]


def _add_cache_control_to_content(
    content: str | list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]]:
    """Inject a cache_control marker into a message's content field.

    Handles three formats:
      - ``None`` / falsy → returned as-is (e.g. assistant with only tool_calls)
      - ``str`` → wrapped into a single content block with the marker
      - ``list`` (existing content blocks) → marker appended to the last text block,
        or a new text block is created if none exists.
    """
    if not content:
        return content

    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]

    # list of content blocks — mutate a shallow copy
    blocks = list(content)
    # Find the last text-type block to attach the marker to
    for idx in range(len(blocks) - 1, -1, -1):
        block = blocks[idx]
        if isinstance(block, dict) and block.get("type") == "text":
            blocks[idx] = {**block, "cache_control": {"type": "ephemeral"}}
            return blocks

    # No text block found; append an empty anchor block
    blocks.append({"type": "text", "text": "", "cache_control": {"type": "ephemeral"}})
    return blocks


_MAX_CACHE_MARKERS = 4  # DashScope per-request limit


def _inject_dashscope_cache_markers(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Place DashScope explicit-cache markers at the optimal prefix boundary.

    **Strategy**

    In an agentic loop the *last* message in ``messages`` is always the
    newest turn (typically a user message carrying a system-reminder or a fresh
    instruction).  Everything preceding it – system prompt, prior conversation
    turns, tool results – forms a stable **prefix** that will be re-sent
    verbatim on the next iteration.

    We place **one** ``cache_control`` marker on the last message *before* the
    final one.  DashScope then caches the entire prefix from the beginning of
    the ``messages`` array up to that marker, so on subsequent requests only
    the trailing new message incurs full input-token cost.

    **Fallback** – when there are fewer than 2 messages (e.g. the very first
    turn), we mark only the system message.

    **Constraint** – DashScope allows at most :data:`_MAX_CACHE_MARKERS` markers
    per request.  This function uses at most 2 (system + boundary).
    """
    if not messages:
        return messages

    result: list[dict[str, Any]] = []
    n = len(messages)

    for i, msg in enumerate(messages):
        new_msg: dict[str, Any] = {k: v for k, v in msg.items()}

        if i == 0 and new_msg.get("role") == "system":
            # Always mark system prompt as cacheable
            new_msg["content"] = _add_cache_control_to_content(new_msg.get("content"))
        elif i == n - 2 and n >= 2:
            # Boundary: last "historical" message before the newest one.
            # This closes the cacheable prefix.
            new_msg["content"] = _add_cache_control_to_content(new_msg.get("content"))

        result.append(new_msg)

    logger.debug(
        "dashscope_cache_markers_injected",
        total_messages=n,
        marked_positions=[
            i for i, m in enumerate(result)
            if _msg_has_cache_control(m)
        ],
    )
    return result


def _msg_has_cache_control(msg: dict[str, Any]) -> bool:
    """Check whether a message contains any cache_control in its content."""
    content = msg.get("content")
    if not content:
        return False
    if isinstance(content, str):
        return False
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and "cache_control" in block
            for block in content
        )
    return False


def _should_use_dashscope_explicit_cache(stream_input: StreamInput) -> bool:
    """Whether this request should opt into DashScope explicit prompt caching."""
    model = stream_input.model
    if model.provider_id != "dashscope":
        return False

    model_id = model.id.lower()
    if model_id in DASHSCOPE_EXPLICIT_CACHE_MODELS:
        return True
    return any(model_id.startswith(prefix) for prefix in DASHSCOPE_EXPLICIT_CACHE_PREFIX_MODELS)


def _build_messages(stream_input: StreamInput) -> list[dict[str, Any]]:
    """Build the messages list with system prompts prepended."""
    messages: list[dict[str, Any]] = []

    # Add system prompts (skip whitespace-only)
    if stream_input.system:
        system_content = "\n\n".join(stream_input.system)
        if system_content.strip():
            # For DashScope explicit-cache models, cache markers are injected
            # later by _inject_dashscope_cache_markers() so we emit plain text here.
            messages.append({"role": "system", "content": system_content})

    # Add conversation messages
    messages.extend(stream_input.messages)
    return messages


def _build_tools(stream_input: StreamInput) -> list[dict[str, Any]] | None:
    """Convert tool definitions to litellm format."""
    if not stream_input.tools:
        return None
    return stream_input.tools


async def _openai_stream_with_client(client: Any, response: Any) -> AsyncGenerator[Any, None]:
    """Yield OpenAI stream chunks and close the client afterward."""
    try:
        async for chunk in response:
            yield chunk
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe


async def _dashscope_explicit_cache_response(
    stream_input: StreamInput,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> AsyncGenerator[Any, None]:
    """Create a DashScope OpenAI-compatible stream that preserves cache_control blocks.

    Cache-control markers are injected at optimal prefix boundaries by
    :func:`_inject_dashscope_cache_markers` before the request is sent,
    so that the stable conversation history (system + prior turns) is
    cached and only the newest message pays full input-token cost.
    """
    from openai import AsyncOpenAI

    from mycode.provider.transform import build_litellm_kwargs

    # Inject cache_control markers at system prompt and history boundary
    marked_messages = _inject_dashscope_cache_markers(messages)

    client = AsyncOpenAI(api_key=stream_input.api_key, base_url=stream_input.api_base)
    kwargs: dict[str, Any] = {
        "model": stream_input.model.api.id,
        "messages": marked_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    kwargs.update(build_litellm_kwargs(stream_input.model))

    if tools:
        kwargs["tools"] = tools
    if stream_input.tool_choice:
        kwargs["tool_choice"] = stream_input.tool_choice
    if stream_input.temperature is not None:
        kwargs["temperature"] = stream_input.temperature
    if stream_input.top_p is not None:
        kwargs["top_p"] = stream_input.top_p
    if stream_input.max_tokens is not None:
        kwargs["max_tokens"] = stream_input.max_tokens
    if stream_input.stop:
        kwargs["stop"] = stream_input.stop

    response = await client.chat.completions.create(**kwargs)
    return _openai_stream_with_client(client, response)


async def stream(stream_input: StreamInput) -> AsyncGenerator[StreamEvent, None]:
    """Stream LLM responses using litellm.

    Yields StreamEvent objects as the model generates tokens.
    """
    model_name = litellm_model_name(stream_input.model)
    messages = _build_messages(stream_input)
    tools = _build_tools(stream_input)

    logger.info(
        "stream",
        model=model_name,
        provider=stream_input.model.provider_id,
        message_count=len(messages),
        tool_count=len(tools) if tools else 0,
    )

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    # Apply provider-specific transforms
    from mycode.provider.transform import build_litellm_kwargs
    provider_kwargs = build_litellm_kwargs(stream_input.model)
    kwargs.update(provider_kwargs)

    if tools:
        kwargs["tools"] = tools
    if stream_input.tool_choice:
        kwargs["tool_choice"] = stream_input.tool_choice
    if stream_input.temperature is not None:
        kwargs["temperature"] = stream_input.temperature
    if stream_input.top_p is not None:
        kwargs["top_p"] = stream_input.top_p
    if stream_input.max_tokens is not None:
        kwargs["max_tokens"] = stream_input.max_tokens
    if stream_input.stop:
        kwargs["stop"] = stream_input.stop
    if stream_input.api_key:
        kwargs["api_key"] = stream_input.api_key
    if stream_input.api_base:
        kwargs["api_base"] = stream_input.api_base

    # Track tool calls being built across chunks
    tool_calls_in_progress: dict[int, dict[str, Any]] = {}
    # Accumulate usage across chunks (some providers send usage in a separate final chunk)
    accumulated_usage: dict[str, int] = {}
    raw_usage_payload: dict[str, Any] | None = None
    # Defer FinishEvent until stream ends (usage may arrive after finish_reason)
    pending_finish_reason: str | None = None

    try:
        if _should_use_dashscope_explicit_cache(stream_input):
            response = await asyncio.wait_for(
                _dashscope_explicit_cache_response(stream_input, messages, tools),
                timeout=300,
            )
        else:
            response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=300)

        # Consumer-initiated abort: race the next chunk against the abort
        # event. Without this the user has to wait out the whole LLM
        # response before the agent loop can unwind. We still let the
        # current chunk land to avoid tearing the SSE parser mid-frame.
        async for chunk in _with_abort(response, stream_input.abort_event):
            # Collect usage from any chunk that has it
            if hasattr(chunk, "usage") and chunk.usage:
                u = chunk.usage
                raw_usage_payload = _serialize_usage(u)
                logger.info(
                    "usage_received",
                    usage_type=type(u).__name__,
                    raw_usage=raw_usage_payload,
                    input_tokens=_usage_get(u, "prompt_tokens", 0) or _usage_get(u, "input_tokens", 0) or 0,
                    output_tokens=_usage_get(u, "completion_tokens", 0) or _usage_get(u, "output_tokens", 0) or 0,
                    cache_read=_get_cache_read_tokens(u),
                    cache_write=_get_cache_write_tokens(u),
                )
                accumulated_usage = {
                    "input_tokens": _usage_get(u, "prompt_tokens", 0) or _usage_get(u, "input_tokens", 0) or 0,
                    "output_tokens": _usage_get(u, "completion_tokens", 0) or _usage_get(u, "output_tokens", 0) or 0,
                    "total_tokens": _usage_get(u, "total_tokens", 0) or 0,
                    "reasoning_tokens": _get_reasoning_tokens(u),
                    "cache_read_tokens": _get_cache_read_tokens(u),
                    "cache_write_tokens": _get_cache_write_tokens(u),
                }

            # Check for finish_reason (may come with or without delta)
            finish_reason = chunk.choices[0].finish_reason if chunk.choices else None
            if finish_reason:
                pending_finish_reason = finish_reason
                # Emit completed tool calls when finish_reason arrives
                for entry in tool_calls_in_progress.values():
                    if entry["name"]:
                        yield ToolCallDelta(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                            args=entry["args"],
                        )
                tool_calls_in_progress.clear()

            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # Provider reasoning / thinking content
            for reasoning_text in _extract_reasoning_segments(delta):
                if reasoning_text:
                    yield ReasoningDelta(text=reasoning_text)

            # User-visible text content
            for text in _extract_text_segments(delta):
                if text:
                    yield TextDelta(text=text)

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if hasattr(tc, "index") else 0

                    if idx not in tool_calls_in_progress:
                        tool_calls_in_progress[idx] = {
                            "id": tc.id or "",
                            "name": "",
                            "args": "",
                        }

                    entry = tool_calls_in_progress[idx]
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] = tc.function.name
                        yield ToolCallPartial(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                        )
                    if tc.function and tc.function.arguments:
                        entry["args"] += tc.function.arguments
                        yield ToolCallArgsPartial(
                            tool_call_id=entry["id"],
                            args_delta=tc.function.arguments,
                        )

        # Stream ended — emit FinishEvent with complete usage data
        if pending_finish_reason:
            cost = _calc_cost(model_name, accumulated_usage)
            metrics.counter("llm_request_total", model=model_name, outcome="ok")
            # Fallback: if provider didn't send raw usage, synthesise from accumulated
            # so the frontend always has something to display.
            final_raw_usage = raw_usage_payload if raw_usage_payload is not None else dict(accumulated_usage)
            yield FinishEvent(
                reason=_map_finish_reason(pending_finish_reason),
                usage=accumulated_usage,
                raw_usage=final_raw_usage,
                cost=cost,
            )

    except asyncio.CancelledError:
        # Propagate cancellation — consumers will run their own cleanup.
        # Ensure we don't swallow the cancel by adding new yields below.
        raise
    except Exception as e:
        code, retryable, status = _classify_exception(e)
        metrics.counter("llm_request_total", model=model_name, outcome="error", code=code)
        logger.error(
            "stream error",
            error=str(e),
            error_type=type(e).__name__,
            error_code=code,
            status_code=status,
            retryable=retryable,
            model=model_name,
        )
        # Surface any in-flight tool calls as best-effort deltas BEFORE the
        # error event. Without this, the processor layer would never see
        # the tool call that was mid-assembly when the provider died, so
        # subsequent retries could not attribute the failure to a specific
        # call and would happily re-issue it.
        for entry in list(tool_calls_in_progress.values()):
            if entry.get("name"):
                yield ToolCallDelta(
                    tool_call_id=entry["id"],
                    tool_name=entry["name"],
                    args=entry.get("args", ""),
                )
        tool_calls_in_progress.clear()
        yield ErrorEvent(
            error=str(e),
            error_code=code,
            retryable=retryable,
            status_code=status,
        )
        # Ensure a FinishEvent is always emitted so consumers don't hang
        if not pending_finish_reason:
            cost = _calc_cost(model_name, accumulated_usage)
            yield FinishEvent(reason="error", usage=accumulated_usage, raw_usage=raw_usage_payload, cost=cost)


def _get_reasoning_tokens(usage: Any) -> int:
    """Extract reasoning tokens from various provider formats."""
    # OpenAI: usage.completion_tokens_details.reasoning_tokens
    val = _usage_get_path(usage, "completion_tokens_details", "reasoning_tokens", default=0)
    if val:
        return val
    # Some providers use prompt_tokens_details
    return 0


def _get_cache_read_tokens(usage: Any) -> int:
    """Extract cache read tokens."""
    # Anthropic: usage.cache_read_input_tokens
    val = _usage_get(usage, "cache_read_input_tokens", 0)
    if val:
        return val
    # DashScope/OpenAI chat.completions: usage.prompt_tokens_details.cached_tokens
    val = _usage_get_path(usage, "prompt_tokens_details", "cached_tokens", default=0)
    if val:
        return val
    # DashScope/OpenAI responses: usage.input_tokens_details.cached_tokens
    val = _usage_get_path(usage, "input_tokens_details", "cached_tokens", default=0)
    if val:
        return val
    # Some DashScope model/region variants expose cached_tokens at top level.
    val = _usage_get(usage, "cached_tokens", 0)
    if val:
        return val
    return 0


def _get_cache_write_tokens(usage: Any) -> int:
    """Extract cache write tokens."""
    val = _usage_get(usage, "cache_creation_input_tokens", 0)
    if val:
        return val
    # DashScope/OpenAI chat.completions explicit cache accounting.
    val = _usage_get_path(usage, "prompt_tokens_details", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "prompt_tokens_details", "cache_creation", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "prompt_tokens_details", "cache_creation", "ephemeral_5m_input_tokens", default=0)
    if val:
        return val
    # DashScope/OpenAI responses explicit cache accounting.
    val = _usage_get_path(usage, "input_tokens_details", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "input_tokens_details", "cache_creation", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "input_tokens_details", "cache_creation", "ephemeral_5m_input_tokens", default=0)
    if val:
        return val
    return 0


def _calc_cost(model_name: str, usage: dict[str, int]) -> float:
    """Calculate cost using litellm's pricing data."""
    if not usage or not usage.get("input_tokens"):
        return 0.0
    try:
        cost = litellm.completion_cost(
            model=model_name,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )
        return float(cost)
    except Exception:
        return 0.0


def _map_finish_reason(reason: str) -> str:
    """Map provider finish reasons to our standard format."""
    if reason == "tool_calls":
        return "tool-calls"
    if reason == "length":
        return "length"
    return "stop"


def _extract_reasoning_segments(delta: Any) -> list[str]:
    """Extract provider-specific reasoning/thinking text from a delta chunk."""
    return _extract_delta_segments(
        delta,
        field_names=("reasoning_content", "reasoning", "thinking"),
    )


def _extract_text_segments(delta: Any) -> list[str]:
    """Extract normal assistant text from a delta chunk."""
    return _extract_delta_segments(delta, field_names=("content",))


def _extract_delta_segments(delta: Any, *, field_names: tuple[str, ...]) -> list[str]:
    segments: list[str] = []
    for name in field_names:
        value = _delta_get(delta, name)
        segments.extend(_coerce_delta_segments(value))
    return [segment for segment in segments if segment]


def _delta_get(delta: Any, key: str) -> Any:
    if delta is None:
        return None
    if isinstance(delta, dict):
        return delta.get(key)
    value = getattr(delta, key, None)
    if value is not None:
        return value
    model_extra = getattr(delta, "model_extra", None)
    if isinstance(model_extra, dict) and key in model_extra:
        return model_extra[key]
    extra = getattr(delta, "__dict__", None)
    if isinstance(extra, dict) and key in extra:
        return extra[key]
    return None


def _coerce_delta_segments(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        segments: list[str] = []
        for item in value:
            segments.extend(_coerce_delta_segments(item))
        return segments
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            nested = value.get(key)
            if isinstance(nested, str):
                return [nested]
            if nested is not None:
                nested_segments = _coerce_delta_segments(nested)
                if nested_segments:
                    return nested_segments
        return []
    for key in ("text", "content", "value"):
        nested = getattr(value, key, None)
        if isinstance(nested, str):
            return [nested]
        if nested is not None:
            nested_segments = _coerce_delta_segments(nested)
            if nested_segments:
                return nested_segments
    return []


def _serialize_usage(value: Any) -> dict[str, Any] | None:
    """Convert provider usage payloads into plain JSON-safe objects."""
    serialized = _serialize_jsonable(value)
    if isinstance(serialized, dict):
        return serialized
    # Fallback: wrap string representation so we never lose data entirely
    return {"_raw": str(value)} if value is not None else None


def _serialize_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(k): _serialize_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_serialize_jsonable(v) for v in value]
    # pydantic v2
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _serialize_jsonable(model_dump())
        except Exception:
            pass
    # pydantic v1
    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        try:
            return _serialize_jsonable(dict_method())
        except Exception:
            pass
    # dataclass / plain object
    as_dict = getattr(value, "__dict__", None)
    if isinstance(as_dict, dict) and as_dict:
        return {
            str(k): _serialize_jsonable(v)
            for k, v in as_dict.items()
            if not str(k).startswith("_")
        }
    # Some litellm internals expose .json()
    json_method = getattr(value, "json", None)
    if callable(json_method):
        try:
            import json as _json
            return _serialize_jsonable(_json.loads(json_method()))
        except Exception:
            pass
    return str(value)
