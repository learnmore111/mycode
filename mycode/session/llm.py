"""LLM streaming interface using litellm.

Wraps litellm.acompletion to provide a unified streaming interface.
"""

from __future__ import annotations

import asyncio
import logging as _logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import litellm

from mycode.provider.provider import litellm_model_name
from mycode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.provider.schema import Model

logger = logmod.create(service="llm")

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False
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


# Union type for stream events
StreamEvent = TextDelta | ToolCallDelta | ToolCallPartial | ToolCallArgsPartial | FinishEvent | ErrorEvent


def _build_messages(stream_input: StreamInput) -> list[dict[str, Any]]:
    """Build the messages list with system prompts prepended."""
    messages: list[dict[str, Any]] = []

    # Add system prompts (skip whitespace-only)
    if stream_input.system:
        system_content = "\n\n".join(stream_input.system)
        if system_content.strip():
            messages.append({"role": "system", "content": system_content})

    # Add conversation messages
    messages.extend(stream_input.messages)
    return messages


def _build_tools(stream_input: StreamInput) -> list[dict[str, Any]] | None:
    """Convert tool definitions to litellm format."""
    if not stream_input.tools:
        return None
    return stream_input.tools


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
        response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=300)

        async for chunk in response:
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
        logger.error("stream error", error=str(e), model=model_name)
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
        yield ErrorEvent(error=str(e))
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
    return getattr(usage, "cache_creation_input_tokens", 0) or 0


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
