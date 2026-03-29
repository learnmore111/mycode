"""Session processor — the core agentic loop. Equivalent to src/session/processor.ts."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from opencode.bus.events import PART_DELTA, PART_UPDATED
from opencode.session import llm as llmmod
from opencode.session.message import AssistantMessage, Part, TextPart, ToolPart, create_text_part, create_tool_part
from opencode.tool import registry as tool_registry
from opencode.tool.base import ToolContext
from opencode.util import log as logmod

if TYPE_CHECKING:
    from opencode.bus.bus import Bus
    from opencode.provider.schema import Model

logger = logmod.create(service="session.processor")

DOOM_LOOP_THRESHOLD = 3
Result = Literal["compact", "stop", "continue"]

@dataclass
class ProcessorEvent:
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


async def process(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> tuple[Result, list[Part]]:
    """Run one iteration of the agentic loop.

    Streams LLM output, handles tool calls, returns whether to continue.
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

        elif isinstance(event, llmmod.ToolCallPartial):
            tp = create_tool_part(ctx.session_id, ctx.assistant_message.id, event.tool_name, event.tool_call_id)
            tp.state = {"status": "pending", "input": {}}
            ctx.toolcalls[event.tool_call_id] = tp
            parts.append(tp)
            current_text = None  # reset text accumulator

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
            break

    # Execute tool calls
    if tool_calls_pending:
        has_failure = False
        blocked = False
        for tp in tool_calls_pending:
            tool = tool_registry.get(tp.tool)
            if not tool:
                tp.state["status"] = "error"
                tp.state["output"] = f"Unknown tool: {tp.tool}"
                tp.time_completed = int(time.time() * 1000)
                has_failure = True
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
                    tp.time_completed = int(time.time() * 1000)
                    blocked = True
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
                    tp.time_completed = int(time.time() * 1000)
                    return "stop", parts

            try:
                tp.state["status"] = "running"
                result = await tool.execute(tp.state.get("input", {}), tool_ctx)
                tp.state["status"] = "completed"
                tp.state["output"] = result.output
                tp.state["title"] = result.title
                tp.state["metadata"] = result.metadata
                tp.time_completed = int(time.time() * 1000)
                ctx.parts.append(tp)
                await ctx.bus.publish(PART_UPDATED, {
                    "session_id": ctx.session_id, "part": {"id": tp.id, "tool": tp.tool, "status": "completed"},
                })
            except Exception as e:
                tp.state["status"] = "error"
                tp.state["output"] = str(e)
                tp.time_completed = int(time.time() * 1000)
                has_failure = True
                logger.error("tool execution failed", tool=tp.tool, error=str(e))

        if blocked:
            return "stop", parts

        if has_failure:
            ctx.doom_count += 1
        else:
            ctx.doom_count = 0

        if ctx.doom_count >= DOOM_LOOP_THRESHOLD:
            logger.warn("doom loop threshold reached, stopping")
            return "stop", parts

        return "continue", parts

    if ctx.should_break:
        return "stop", parts

    return "stop", parts


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

    # Tool results
    for tp in tool_calls:
        messages.append({
            "role": "tool",
            "tool_call_id": tp.tool_call_id,
            "content": tp.state.get("output", ""),
        })

    return messages
