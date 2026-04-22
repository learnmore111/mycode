"""Session processor — the core agentic loop.

Streaming architecture: process_stream() is an async generator that yields
ProcessorEvent objects in real-time as the LLM generates text and tools execute.
This enables the CLI to render text and tool output interleaved, just like
Claude Code / Cursor / aider.

Enhanced with:
- Result caching for read-only tools (skip duplicate calls)
- Retry logic for transient failures
- Read/write separation: read-only tools run in parallel, mutating tools run sequentially
- Step-level state recording for the loop guard
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from mycode.bus.events import PART_DELTA, PART_UPDATED
from mycode.session import llm as llmmod
from mycode.session.loop_guard import MUTATING_TOOLS, LoopGuard
from mycode.session.message import AssistantMessage, Part, TextPart, ToolPart, create_text_part, create_tool_part
from mycode.tool import registry as tool_registry
from mycode.tool.base import (
    ToolBaseError,
    ToolContext,
    ToolNotFoundError,
    ToolRuntimeError,
    ToolValidateError,
)
from mycode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.bus.bus import Bus
    from mycode.permission.permission import PermissionManager
    from mycode.permission.schema import Rule
    from mycode.provider.schema import Model

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
    permission_manager: PermissionManager | None = None
    agent_permission: list[Rule] = field(default_factory=list)
    loop_guard: LoopGuard | None = None  # Injected by prompt.py


async def process_stream(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> AsyncGenerator[ProcessorEvent, None]:
    """Run one iteration of the agentic loop, yielding events in real time."""
    current_text: TextPart | None = None
    tool_calls_pending: list[ToolPart] = []
    parts: list[Part] = []
    text_length = 0

    async for event in llmmod.stream(stream_input):
        if isinstance(event, llmmod.TextDelta):
            if current_text is None:
                current_text = create_text_part(ctx.session_id, ctx.assistant_message.id)
                parts.append(current_text)
            current_text.content += event.text
            text_length += len(event.text)
            await ctx.bus.publish(PART_DELTA, {
                "session_id": ctx.session_id,
                "message_id": ctx.assistant_message.id,
                "part_id": current_text.id,
                "field": "content",
                "delta": event.text,
            })
            yield ProcessorEvent(type="text_delta", data={"content": event.text})

        elif isinstance(event, llmmod.ToolCallPartial):
            tp = create_tool_part(ctx.session_id, ctx.assistant_message.id, event.tool_name, event.tool_call_id)
            tp.state = {"status": "pending", "input": {}}
            ctx.toolcalls[event.tool_call_id] = tp
            parts.append(tp)
            current_text = None
            yield ProcessorEvent(type="tool_start", data={
                "tool": event.tool_name,
                "call_id": event.tool_call_id,
            })

        elif isinstance(event, llmmod.ToolCallArgsPartial):
            tp_partial = ctx.toolcalls.get(event.tool_call_id)
            if tp_partial:
                raw = tp_partial.state.get("_raw_args", "") + event.args_delta
                tp_partial.state["_raw_args"] = raw

        elif isinstance(event, llmmod.ToolCallDelta):
            tp_delta = ctx.toolcalls.get(event.tool_call_id)
            if tp_delta:
                try:
                    parsed = json.loads(event.args) if event.args else {}
                    if not isinstance(parsed, dict):
                        logger.warn("tool args parsed to non-dict", tool=tp_delta.tool, type=type(parsed).__name__)
                        tp_delta.state["input"] = {}
                    else:
                        tp_delta.state["input"] = parsed
                except json.JSONDecodeError as e:
                    logger.error("malformed tool arguments", tool=tp_delta.tool, error=str(e))
                    tp_delta.state["input"] = {}
                    tp_delta.state["_parse_error"] = str(e)
                tool_calls_pending.append(tp_delta)

        elif isinstance(event, llmmod.FinishEvent):
            ctx.assistant_message.tokens_input += event.usage.get("input_tokens", 0)
            ctx.assistant_message.tokens_output += event.usage.get("output_tokens", 0)
            ctx.assistant_message.tokens_reasoning += event.usage.get("reasoning_tokens", 0)
            ctx.assistant_message.tokens_cache_read += event.usage.get("cache_read_tokens", 0)
            ctx.assistant_message.tokens_cache_write += event.usage.get("cache_write_tokens", 0)
            ctx.assistant_message.cost += event.cost

        elif isinstance(event, llmmod.ErrorEvent):
            logger.error(
                "LLM error",
                error=event.error,
                error_code=event.error_code,
                retryable=event.retryable,
            )
            ctx.assistant_message.error = {
                "message": event.error,
                "code": event.error_code,
                "retryable": event.retryable,
                "status_code": event.status_code,
            }
            ctx.should_break = True
            # Any tool calls that were partially streamed before the error
            # must be surfaced as failed so the doom-loop guard & UI see
            # them instead of silently discarding. They are not executed.
            for partial_tp in list(ctx.toolcalls.values()):
                if partial_tp.state.get("status") in (None, "pending"):
                    partial_tp.state["status"] = "error"
                    partial_tp.state["is_error"] = True
                    partial_tp.state["output"] = f"LLM stream aborted before tool args finalised: {event.error}"
                    partial_tp.time_completed = int(time.time() * 1000)
                    if partial_tp not in ctx.parts:
                        ctx.parts.append(partial_tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": partial_tp.tool,
                        "call_id": partial_tp.tool_call_id,
                        "status": "error",
                        "output": partial_tp.state["output"],
                        "input": partial_tp.state.get("input", {}),
                    })
            # Drop the pending list so the executor phase does not try to
            # run a partially-formed call.
            tool_calls_pending.clear()
            yield ProcessorEvent(type="error", data={
                "message": event.error,
                "code": event.error_code,
                "retryable": event.retryable,
                "status_code": event.status_code,
            })
            break

    # Execute tool calls
    if tool_calls_pending:
        has_failure = False
        blocked = False
        doom_detected = False
        cache = ctx.loop_guard.cache if ctx.loop_guard else None

        # Phase 1: Pre-flight — permission, doom loop, cache check
        executable: list[tuple[ToolPart, Any, ToolContext]] = []
        cached_results: list[tuple[ToolPart, str]] = []

        for tp in tool_calls_pending:
            try:
                tool = tool_registry.get_or_raise(tp.tool)
            except ToolNotFoundError as e:
                tp.state["status"] = "error"
                tp.state["output"] = str(e)
                tp.state["is_error"] = True
                tp.time_completed = int(time.time() * 1000)
                has_failure = True  # Count tool-not-found as a failure for doom detection
                ctx.parts.append(tp)
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
                    from mycode.permission.schema import DeniedError, RejectedError
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
                    ctx.parts.append(tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": str(e), "input": tp.state.get("input", {}),
                    })
                    continue
                except Exception as e:
                    # Fail-safe: block tool execution on unexpected permission errors
                    logger.error(
                        "permission check failed unexpectedly",
                        tool=tp.tool,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    tp.state["status"] = "error"
                    tp.state["output"] = f"Permission check failed: {e}"
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    blocked = True
                    ctx.parts.append(tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": f"Permission check failed: {e}",
                        "input": tp.state.get("input", {}),
                    })
                    continue

            # Doom loop detection (legacy, loop_guard has more advanced detection)
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
                    ctx.parts.append(tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": tp.state["output"],
                        "input": tp.state.get("input", {}),
                    })
                    break

            # Cache check — skip execution if we have a cached result
            if cache:
                cached = cache.get(tp.tool, tp.state.get("input", {}))
                if cached is not None:
                    cached_results.append((tp, cached))
                    logger.debug("cache hit", tool=tp.tool)
                    continue

            executable.append((tp, tool, tool_ctx))

        if doom_detected:
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        # Phase 1.5: Serve cached results immediately
        for tp, cached_output in cached_results:
            tp.state["status"] = "completed"
            tp.state["output"] = cached_output
            tp.state["is_error"] = False
            tp.state["metadata"] = {"cached": True}
            tp.time_completed = int(time.time() * 1000)
            ctx.parts.append(tp)
            yield ProcessorEvent(type="tool_done", data={
                "tool": tp.tool, "call_id": tp.tool_call_id,
                "status": "completed", "output": cached_output[:500],
                "input": tp.state.get("input", {}),
            })

        # Phase 2: Execute tools with read/write separation
        if executable:
            # Separate read-only tools from mutating tools using capability declarations
            readonly_tasks: list[tuple[ToolPart, Any, ToolContext]] = []
            mutating_tasks: list[tuple[ToolPart, Any, ToolContext]] = []
            for tp, tool_impl, tool_ctx in executable:
                # Use capability declaration if available, fallback to hardcoded set
                if hasattr(tool_impl, "is_concurrency_safe") and hasattr(tool_impl, "is_read_only"):
                    tool_input = tp.state.get("input", {})
                    if tool_impl.is_read_only(tool_input) and tool_impl.is_concurrency_safe(tool_input):
                        readonly_tasks.append((tp, tool_impl, tool_ctx))
                    else:
                        mutating_tasks.append((tp, tool_impl, tool_ctx))
                elif tp.tool in MUTATING_TOOLS:
                    mutating_tasks.append((tp, tool_impl, tool_ctx))
                else:
                    readonly_tasks.append((tp, tool_impl, tool_ctx))

            # Safety: if a batch mixes readonly and mutating calls, run the
            # mutating ones FIRST so any cached readonly result produced in
            # the same iteration observes the post-mutation filesystem.
            # Previously readonly ran before mutating, which allowed a mixed
            # batch like [read(foo.py), edit(foo.py)] to cache a pre-edit
            # snapshot of foo.py and hand it back to the next iteration.
            mutating_first = bool(mutating_tasks) and bool(readonly_tasks)

            # Yield running events
            for tp, _, _ in executable:
                tp.state["status"] = "running"
                yield ProcessorEvent(type="tool_running", data={
                    "tool": tp.tool, "call_id": tp.tool_call_id,
                    "input": tp.state.get("input", {}),
                })

            all_results: list[tuple[bool, ProcessorEvent]] = []

            async def _run_mutating() -> None:
                for tp, impl, tctx in mutating_tasks:
                    result = await _run_tool_with_retry(tp, impl, tctx, ctx)
                    all_results.append(result)

            async def _run_readonly() -> None:
                if not readonly_tasks:
                    return
                ro_results = await asyncio.gather(
                    *[_run_tool_with_retry(tp, impl, tctx, ctx) for tp, impl, tctx in readonly_tasks],
                    return_exceptions=True,
                )
                for i, result in enumerate(ro_results):
                    if isinstance(result, BaseException):
                        tp_err, _, _ = readonly_tasks[i]
                        logger.error(
                            "read-only tool raised unexpected exception",
                            tool=tp_err.tool,
                            error=str(result),
                            error_type=type(result).__name__,
                        )
                        tp_err.state["status"] = "error"
                        tp_err.state["output"] = f"Tool execution failed: {result}"
                        tp_err.state["is_error"] = True
                        tp_err.time_completed = int(time.time() * 1000)
                        ctx.parts.append(tp_err)
                        all_results.append((False, ProcessorEvent(type="tool_done", data={
                            "tool": tp_err.tool, "call_id": tp_err.tool_call_id,
                            "status": "error", "output": f"Tool execution failed: {result}",
                            "input": tp_err.state.get("input", {}),
                        })))
                    else:
                        all_results.append(result)

            if mutating_first:
                await _run_mutating()
                await _run_readonly()
            else:
                await _run_readonly()
                await _run_mutating()

            # Yield results and track failures
            all_success = True
            for success, tool_event in all_results:
                yield tool_event
                if not success:
                    all_success = False

            if not all_success:
                has_failure = True

        if blocked:
            # Reset doom_count to avoid stale state polluting next iteration
            ctx.doom_count = 0
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

        yield ProcessorEvent(type="finish", data={
            "result": "continue", "parts": parts, "text_length": text_length,
        })
        return

    if ctx.should_break:
        yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts, "text_length": text_length})
        return

    yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts, "text_length": text_length})


async def _run_tool_with_retry(
    tp: ToolPart, tool_impl: Any, tool_ctx: ToolContext, ctx: ProcessorContext,
) -> tuple[bool, ProcessorEvent]:
    """Execute a tool with retry logic for transient failures."""
    guard = ctx.loop_guard
    max_retries = guard.config.max_retries if guard else 0
    last_error = ""

    for attempt in range(max_retries + 1):
        success, event = await _run_tool(tp, tool_impl, tool_ctx, ctx)

        if success:
            # Record in loop guard and cache
            if guard:
                guard.record_tool_call(
                    tp.tool, tp.state.get("input", {}),
                    output=tp.state.get("output", ""), is_error=False,
                )
            return success, event

        last_error = tp.state.get("output", "")

        # Check if should retry
        if guard and attempt < max_retries and guard.should_retry(tp.tool, last_error, attempt):
            logger.info("retrying tool", tool=tp.tool, attempt=attempt + 1, error=last_error[:100])
            # Reset tool state for retry
            tp.state["status"] = "running"
            tp.state.pop("output", None)
            tp.state.pop("is_error", None)
            await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
            continue

        # No retry — record failure and return
        if guard:
            guard.record_tool_call(
                tp.tool, tp.state.get("input", {}),
                output=last_error, is_error=True,
            )
        return success, event

    # Defensive fallback: should be unreachable since every loop iteration returns,
    # but guards against future refactors that might break the invariant.
    if guard:
        guard.record_tool_call(tp.tool, tp.state.get("input", {}), output=last_error, is_error=True)
    return False, ProcessorEvent(type="tool_done", data={
        "tool": tp.tool, "call_id": tp.tool_call_id,
        "status": "error", "output": f"Failed after {max_retries + 1} attempts: {last_error}",
        "input": tp.state.get("input", {}),
    })


async def _run_tool(
    tp: ToolPart, tool_impl: Any, tool_ctx: ToolContext, ctx: ProcessorContext,
) -> tuple[bool, ProcessorEvent]:
    """Execute a single tool. Returns (success, event)."""
    from mycode.util import metrics as _metrics

    try:
        with _metrics.span("tool_call", tool=tp.tool, session_id=ctx.session_id):
            result = await tool_impl.execute(tp.state.get("input", {}), tool_ctx)
        _metrics.counter("tool_call_total", tool=tp.tool, outcome="error" if result.is_error else "ok")

        if result.is_error:
            tp.state["status"] = "error"
            tp.state["is_error"] = True
        else:
            tp.state["status"] = "completed"
            tp.state["is_error"] = False

        tp.state["output"] = result.output
        tp.state["title"] = result.title
        tp.state["metadata"] = result.metadata

        if result.display:
            tp.state["display"] = result.display
        if result.message:
            tp.state["message"] = result.message

        tp.time_completed = int(time.time() * 1000)
        ctx.parts.append(tp)
        await ctx.bus.publish(PART_UPDATED, {
            "session_id": ctx.session_id,
            "part": {
                "id": tp.id, "tool": tp.tool,
                "status": tp.state["status"], "is_error": result.is_error,
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
        return _tool_error(tp, str(e), "tool validation failed")

    except ToolBaseError as e:
        return _tool_error(tp, str(e), "tool error")

    except Exception as e:
        return _tool_error(tp, str(ToolRuntimeError(tp.tool, e)), "tool execution failed")


def _tool_error(tp: ToolPart, error_msg: str, log_msg: str) -> tuple[bool, ProcessorEvent]:
    """Helper to handle tool errors uniformly."""
    tp.state["status"] = "error"
    tp.state["output"] = error_msg
    tp.state["is_error"] = True
    tp.time_completed = int(time.time() * 1000)
    logger.error(log_msg, tool=tp.tool, error=error_msg[:200])
    event = ProcessorEvent(type="tool_done", data={
        "tool": tp.tool, "call_id": tp.tool_call_id,
        "status": "error", "output": error_msg,
        "input": tp.state.get("input", {}),
    })
    return False, event


# Backward-compatible wrapper
async def process(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> tuple[Result, list[Part]]:
    """Backward-compatible wrapper around process_stream()."""
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

    assistant_tool_calls = []
    for tp in tool_calls:
        assistant_tool_calls.append({
            "id": tp.tool_call_id,
            "type": "function",
            "function": {"name": tp.tool, "arguments": json.dumps(tp.state.get("input", {}))},
        })

    messages: list[dict[str, Any]] = []

    text_parts = [p for p in parts if isinstance(p, TextPart)]
    text_content = "".join(p.content for p in text_parts)
    messages.append({
        "role": "assistant",
        "content": text_content or None,
        "tool_calls": assistant_tool_calls,
    })

    for tp in tool_calls:
        output = tp.state.get("output", "")
        tool_message = tp.state.get("message", "")
        if tool_message:
            output = f"{output}\n\n{tool_message}"
        messages.append({
            "role": "tool",
            "tool_call_id": tp.tool_call_id,
            "content": output,
        })

    return messages
