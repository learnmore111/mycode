"""LLM streaming interface using litellm.

Wraps litellm.acompletion to provide a unified streaming interface.
Equivalent to src/session/llm.ts.
"""

from __future__ import annotations

import logging as _logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import litellm

from opencode.provider.provider import litellm_model_name
from opencode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from opencode.provider.schema import Model

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


@dataclass
class ErrorEvent:
    type: str = "error"
    error: str = ""


# Union type for stream events
StreamEvent = TextDelta | ToolCallDelta | ToolCallPartial | ToolCallArgsPartial | FinishEvent | ErrorEvent


def _build_messages(stream_input: StreamInput) -> list[dict[str, Any]]:
    """Build the messages list with system prompts prepended."""
    messages: list[dict[str, Any]] = []

    # Add system prompts
    if stream_input.system:
        system_content = "\n\n".join(stream_input.system)
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
    }

    # Apply provider-specific transforms
    from opencode.provider.transform import build_litellm_kwargs
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

    try:
        response = await litellm.acompletion(**kwargs)

        async for chunk in response:
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

            # Finish
            finish_reason = chunk.choices[0].finish_reason if chunk.choices else None
            if finish_reason:
                # Emit completed tool calls
                for entry in tool_calls_in_progress.values():
                    if entry["name"]:
                        yield ToolCallDelta(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                            args=entry["args"],
                        )

                usage = {}
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {
                        "input_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
                    }

                reason = "stop"
                if finish_reason == "tool_calls":
                    reason = "tool-calls"
                elif finish_reason == "length":
                    reason = "length"

                yield FinishEvent(reason=reason, usage=usage)

    except Exception as e:
        logger.error("stream error", error=str(e), model=model_name)
        yield ErrorEvent(error=str(e))
