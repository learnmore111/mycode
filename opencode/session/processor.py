"""Session processor — the core agentic loop. Equivalent to src/session/processor.ts.

Streaming architecture: process_stream() is an async generator that yields
ProcessorEvent objects in real-time as the LLM generates text and tools execute.
This enables the CLI to render text and tool output interleaved, just like
Claude Code / Cursor / aider.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from opencode.bus.events import PART_DELTA, PART_UPDATED
from opencode.session import llm as llmmod
from opencode.session.message import AssistantMessage, Part, TextPart, ToolPart, create_text_part, create_tool_part
from opencode.tool import registry as tool_registry
from opencode.tool.base import (
    ToolBaseError,
    ToolContext,
    ToolNotFoundError,
    ToolRuntimeError,
    ToolValidateError,
)
from opencode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from opencode.bus.bus import Bus
    from opencode.provider.schema import Model

logger = logmod.create(service="session.processor")

DOOM_LOOP_THRESHOLD = 3
Result = Literal["compact", "stop", "continue"]


@dataclass
class ProcessorEvent:
    """Event yielded during streaming processing.

    Types:
      - "text_delta":  Incremental text from the LLM. data["content"] is the delta string.
      - "tool_start":  A tool call has been identified. data["tool"], data["call_id"].
      - "tool_running": Tool execution has started. data["tool"], data["call_id"].
      - "tool_done":   Tool execution completed. data["tool"], data["call_id"], data["status"],
                       data["output"], data["input"].
      - "error":       An LLM or processing error. data["message"].
      - "finish":      Processing of one iteration is done. data["result"] is the Result string,
                       data["parts"] is the list of Part objects produced.
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorContext:
    session_id: str
    model: Model
    assistant_message: AssistantMessage
    bus: Bus
    toolcalls: dict[str, ToolPart] = field(default_factory=dict)
    parts: list[Part] = field(default_factory=list)
    should_break: bool = False
    doom_count: int = 0
    permission_manager: Any = None  # PermissionManager instance
    agent_permission: list[dict[str, Any]] = field(default_factory=list)


async def process_stream(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> AsyncGenerator[ProcessorEvent, None]:
    """Run one iteration of the agentic loop, yielding events in real time.

    Yields ProcessorEvent objects as:
      1. text_delta — each chunk of LLM text
      2. tool_start — when a tool call is identified
      3. tool_running — when tool execution begins
      4. tool_done — when tool execution completes
      5. error — on LLM errors
      6. finish — at the end, with result="stop"|"continue" and parts list
    """
    current_text: TextPart | None = None
    tool_calls_pending: list[ToolPart] = []
    parts: list[Part] = []

    async for event in llmmod.stream(stream_input):
        if isinstance(event, llmmod.TextDelta):
            if current_text is None:
                current_text = create_text_part(ctx.session_id, ctx.assistant_message.id)
                parts.append(current_text)
            current_text.content += event.text
            await ctx.bus.publish(PART_DELTA, {
                "session_id": ctx.session_id,
                "message_id": ctx.assistant_message.id,
                "part_id": current_text.id,
                "field": "content",
                "delta": event.text,
            })
            # Yield text delta immediately for real-time rendering
            yield ProcessorEvent(type="text_delta", data={"content": event.text})

        elif isinstance(event, llmmod.ToolCallPartial):
            # A new tool call has been identified by the LLM
            tp = create_tool_part(ctx.session_id, ctx.assistant_message.id, event.tool_name, event.tool_call_id)
            tp.state = {"status": "pending", "input": {}}
            ctx.toolcalls[event.tool_call_id] = tp
            parts.append(tp)
            current_text = None  # reset text accumulator
            yield ProcessorEvent(type="tool_start", data={
                "tool": event.tool_name,
                "call_id": event.tool_call_id,
            })

        elif isinstance(event, llmmod.ToolCallArgsPartial):
            tp = ctx.toolcalls.get(event.tool_call_id)
            if tp:
                raw = tp.state.get("_raw_args", "") + event.args_delta
                tp.state["_raw_args"] = raw

        elif isinstance(event, llmmod.ToolCallDelta):
            tp = ctx.toolcalls.get(event.tool_call_id)
            if tp:
                try:
                    tp.state["input"] = json.loads(event.args) if event.args else {}
                except json.JSONDecodeError:
                    tp.state["input"] = {"_raw": event.args}
                tool_calls_pending.append(tp)

        elif isinstance(event, llmmod.FinishEvent):
            ctx.assistant_message.tokens_input += event.usage.get("input_tokens", 0)
            ctx.assistant_message.tokens_output += event.usage.get("output_tokens", 0)
            ctx.assistant_message.tokens_reasoning += event.usage.get("reasoning_tokens", 0)
            ctx.assistant_message.tokens_cache_read += event.usage.get("cache_read_tokens", 0)
            ctx.assistant_message.tokens_cache_write += event.usage.get("cache_write_tokens", 0)
            ctx.assistant_message.cost += event.cost

        elif isinstance(event, llmmod.ErrorEvent):
            logger.error("LLM error", error=event.error)
            ctx.assistant_message.error = {"message": event.error}
            ctx.should_break = True
            yield ProcessorEvent(type="error", data={"message": event.error})
            break

    # Execute tool calls (parallel when possible)
    if tool_calls_pending:
        has_failure = False
        blocked = False
        doom_detected = False

        # Phase 1: Pre-flight checks (serial) — permission + doom loop detection
        executable: list[tuple[ToolPart, Any, ToolContext]] = []  # (tp, tool_impl, tool_ctx)
        for tp in tool_calls_pending:
            # Use structured error for unknown tools
            try:
                tool = tool_registry.get_or_raise(tp.tool)
            except ToolNotFoundError as e:
                tp.state["status"] = "error"
                tp.state["output"] = str(e)
                tp.state["is_error"] = True
                tp.time_completed = int(time.time() * 1000)
                has_failure = True
                logger.warn("tool not found", tool=tp.tool)
                yield ProcessorEvent(type="tool_done", data={
                    "tool": tp.tool, "call_id": tp.tool_call_id,
                    "status": "error", "output": str(e), "input": {},
                })
                continue

            tool_ctx = ToolContext(
                session_id=ctx.session_id,
                message_id=ctx.assistant_message.id,
                agent=ctx.assistant_message.agent,
                call_id=tp.tool_call_id,
            )

            # Permission check
            if ctx.permission_manager:
                try:
                    from opencode.permission.schema import DeniedError, RejectedError
                    await ctx.permission_manager.ask(
                        session_id=ctx.session_id,
                        permission=tp.tool,
                        patterns=["*"],
                        ruleset=ctx.agent_permission,
                        metadata={"tool": tp.tool, "input": tp.state.get("input", {})},
                        always=["*"],
                    )
                except (RejectedError, DeniedError) as e:
                    tp.state["status"] = "error"
                    tp.state["output"] = str(e)
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    blocked = True
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": str(e), "input": tp.state.get("input", {}),
                    })
                    continue
                except Exception:
                    pass  # Permission manager not connected, allow

            # Doom loop detection: check if same tool+input repeated
            recent_tool_parts = [p for p in ctx.parts if isinstance(p, ToolPart) and p.tool == tp.tool]
            if len(recent_tool_parts) >= DOOM_LOOP_THRESHOLD:
                last_inputs = [json.dumps(p.state.get("input", {}), sort_keys=True) for p in recent_tool_parts[-DOOM_LOOP_THRESHOLD:]]
                current_input = json.dumps(tp.state.get("input", {}), sort_keys=True)
                if all(inp == current_input for inp in last_inputs):
                    logger.warn("doom loop detected", tool=tp.tool)
                    tp.state["status"] = "error"
                    tp.state["output"] = "Doom loop detected: same tool with same input called repeatedly"
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    doom_detected = True
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": tp.state["output"],
                        "input": tp.state.get("input", {}),
                    })
                    break

            executable.append((tp, tool, tool_ctx))

        if doom_detected:
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        # Phase 2: Execute all tools in parallel via asyncio.gather
        if executable:
            # Yield running events for all tools before execution starts
            for tp, _, _ in executable:
                tp.state["status"] = "running"
                yield ProcessorEvent(type="tool_running", data={
                    "tool": tp.tool, "call_id": tp.tool_call_id,
                    "input": tp.state.get("input", {}),
                })

            async def _run_tool(tp: ToolPart, tool_impl: Any, tool_ctx: ToolContext) -> tuple[bool, ProcessorEvent]:
                """Execute a single tool. Returns (success, event)."""
                try:
                    result = await tool_impl.execute(tp.state.get("input", {}), tool_ctx)

                    # Handle structured ToolResult with is_error flag
                    if result.is_error:
                        tp.state["status"] = "error"
                        tp.state["is_error"] = True
                    else:
                        tp.state["status"] = "completed"
                        tp.state["is_error"] = False

                    tp.state["output"] = result.output
                    tp.state["title"] = result.title
                    tp.state["metadata"] = result.metadata

                    # Store display separately (not sent to model)
                    if result.display:
                        tp.state["display"] = result.display
                    # Store message for richer context
                    if result.message:
                        tp.state["message"] = result.message

                    tp.time_completed = int(time.time() * 1000)
                    ctx.parts.append(tp)
                    await ctx.bus.publish(PART_UPDATED, {
                        "session_id": ctx.session_id,
                        "part": {
                            "id": tp.id,
                            "tool": tp.tool,
                            "status": tp.state["status"],
                            "is_error": result.is_error,
                        },
                    })
                    event = ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": tp.state["status"],
                        "output": result.output[:500],
                        "input": tp.state.get("input", {}),
                    })
                    return not result.is_error, event

                except ToolValidateError as e:
                    tp.state["status"] = "error"
                    tp.state["output"] = str(e)
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    logger.warn("tool validation failed", tool=tp.tool, error=str(e))
                    event = ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": str(e),
                        "input": tp.state.get("input", {}),
                    })
                    return False, event

                except ToolBaseError as e:
                    tp.state["status"] = "error"
                    tp.state["output"] = str(e)
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    logger.error("tool error", tool=tp.tool, error=str(e))
                    event = ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": str(e),
                        "input": tp.state.get("input", {}),
                    })
                    return False, event

                except Exception as e:
                    tp.state["status"] = "error"
                    tp.state["output"] = str(ToolRuntimeError(tp.tool, e))
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    logger.error("tool execution failed", tool=tp.tool, error=str(e))
                    event = ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": str(ToolRuntimeError(tp.tool, e)),
                        "input": tp.state.get("input", {}),
                    })
                    return False, event

            results = await asyncio.gather(
                *[_run_tool(tp, impl, tctx) for tp, impl, tctx in executable],
                return_exceptions=False,
            )

            # Yield tool_done events in order as tools complete
            all_success = True
            for success, tool_event in results:
                yield tool_event
                if not success:
                    all_success = False

            if not all_success:
                has_failure = True

        if blocked:
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        if has_failure:
            ctx.doom_count += 1
        else:
            ctx.doom_count = 0

        if ctx.doom_count >= DOOM_LOOP_THRESHOLD:
            logger.warn("doom loop threshold reached, stopping")
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        yield ProcessorEvent(type="finish", data={"result": "continue", "parts": parts})
        return

    if ctx.should_break:
        yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
        return

    yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})


# Keep backward-compatible wrapper for any code that uses the old API
async def process(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> tuple[Result, list[Part]]:
    """Backward-compatible wrapper around process_stream().

    Consumes the entire stream and returns (result, parts) as before.
    """
    result: Result = "stop"
    parts: list[Part] = []
    async for event in process_stream(ctx, stream_input, messages_for_tools):
        if event.type == "finish":
            result = event.data.get("result", "stop")
            parts = event.data.get("parts", [])
    return result, parts


def build_tool_results_messages(parts: list[Part]) -> list[dict[str, Any]]:
    """Convert tool parts to assistant + tool_result messages for the next LLM call."""
    tool_calls = [p for p in parts if isinstance(p, ToolPart)]
    if not tool_calls:
        return []

    # Assistant message with tool_calls
    assistant_tool_calls = []
    for tp in tool_calls:
        assistant_tool_calls.append({
            "id": tp.tool_call_id,
            "type": "function",
            "function": {"name": tp.tool, "arguments": json.dumps(tp.state.get("input", {}))},
        })

    messages: list[dict[str, Any]] = []

    # Text + tool_calls in one assistant message
    text_parts = [p for p in parts if isinstance(p, TextPart)]
    text_content = "".join(p.content for p in text_parts)
    messages.append({
        "role": "assistant",
        "content": text_content or None,
        "tool_calls": assistant_tool_calls,
    })

    # Tool results — only send output (not display) to the model
    for tp in tool_calls:
        output = tp.state.get("output", "")
        # Append message field if present for richer context
        tool_message = tp.state.get("message", "")
        if tool_message:
            output = f"{output}\n\n{tool_message}"
        messages.append({
            "role": "tool",
            "tool_call_id": tp.tool_call_id,
            "content": output,
        })

    return messages
