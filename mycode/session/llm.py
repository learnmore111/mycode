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
StreamEvent = TextDelta | ToolCallDelta | ToolCallPartial | ToolCallArgsPartial | FinishEvent | ErrorEvent


def _dashscope_explicit_cache_content(text: str) -> list[dict[str, Any]]:
    """Build a DashScope OpenAI-compatible content block with explicit cache."""
    return [{
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }]


def _should_use_dashscope_explicit_cache(stream_input: StreamInput) -> bool:
    """Whether this request should opt into DashScope explicit prompt caching."""
    model = stream_input.model
    return model.provider_id == "dashscope" and model.id == "qwen3.6-plus"


def _build_messages(stream_input: StreamInput) -> list[dict[str, Any]]:
    """Build the messages list with system prompts prepended."""
    messages: list[dict[str, Any]] = []

    # Add system prompts (skip whitespace-only)
    if stream_input.system:
        system_content = "\n\n".join(stream_input.system)
        if system_content.strip():
            if _should_use_dashscope_explicit_cache(stream_input):
                messages.append({
                    "role": "system",
                    "content": _dashscope_explicit_cache_content(system_content),
                })
            else:
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
    """Create a DashScope OpenAI-compatible stream that preserves cache_control blocks."""
    from openai import AsyncOpenAI

    from mycode.provider.transform import build_litellm_kwargs

    client = AsyncOpenAI(api_key=stream_input.api_key, base_url=stream_input.api_base)
    kwargs: dict[str, Any] = {
        "model": stream_input.model.api.id,
        "messages": messages,
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
                accumulated_usage = {
                    "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(u, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(u, "total_tokens", 0) or 0,
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

            # Text content
            if delta.content:
                yield TextDelta(text=delta.content)

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
            yield FinishEvent(
                reason=_map_finish_reason(pending_finish_reason),
                usage=accumulated_usage,
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
            yield FinishEvent(reason="error", usage=accumulated_usage, cost=cost)


def _get_reasoning_tokens(usage: Any) -> int:
    """Extract reasoning tokens from various provider formats."""
    # OpenAI: usage.completion_tokens_details.reasoning_tokens
    details = getattr(usage, "completion_tokens_details", None)
    if details:
        return getattr(details, "reasoning_tokens", 0) or 0
    # Some providers use prompt_tokens_details
    return 0


def _get_cache_read_tokens(usage: Any) -> int:
    """Extract cache read tokens."""
    # Anthropic: usage.cache_read_input_tokens
    val = getattr(usage, "cache_read_input_tokens", 0)
    if val:
        return val
    # OpenAI: usage.prompt_tokens_details.cached_tokens
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        return getattr(details, "cached_tokens", 0) or 0
    return 0


def _get_cache_write_tokens(usage: Any) -> int:
    """Extract cache write tokens."""
    val = getattr(usage, "cache_creation_input_tokens", 0)
    if val:
        return val
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        return getattr(details, "cache_creation_input_tokens", 0) or 0
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
