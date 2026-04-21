"""Task tool — spawn a sub-agent for complex multi-step work.

Features:
- Multi-turn agentic loop (up to MAX_TURNS)
- Abort signal support (checks ctx.abort between turns)
- Permission enforcement via agent's ruleset
- Loop guard integration (pattern detection + cache)
- Capability declarations
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from mycode.agent import agent as agentmod
from mycode.provider import provider as providermod
from mycode.session import llm as llmmod
from mycode.session.loop_guard import LoopGuard, LoopGuardConfig
from mycode.session.system import build as build_system
from mycode.tool import registry as tool_registry
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder
from mycode.util import log as logmod
from mycode.util.subagent import build_agent_ruleset, check_tool_permission, is_aborted

logger = logmod.create(service="tool.task")

MAX_TURNS = 8

_EXCLUDED_TOOLS = frozenset({"task", "todo", "question", "batch"})



# Shared helpers imported from mycode.util.subagent:
# is_aborted, build_agent_ruleset, check_tool_permission


class TaskParams(BaseModel):
    """Parameters for the task tool."""
    description: str = Field(description="A clear description of the task for the sub-agent to accomplish")
    agent: str = Field(default="general", description="Agent to use (default: 'general'). Options: general, explore")


class TaskTool(CallableTool[TaskParams]):
    id = "task"
    description = (
        "Launch a sub-agent to handle a complex task. The sub-agent runs independently with its own context "
        "and can perform multi-step reasoning (search, read, edit, etc.). "
        "Use this when you need to research, explore, or execute multi-step work in parallel."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False  # Sub-agents can perform writes

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True  # Each sub-agent has independent context

    async def call(self, params: TaskParams, ctx: ToolContext) -> ToolResult:
        description = params.description
        agent_name = params.agent

        agent = await agentmod.get(agent_name)
        if not agent:
            return ToolError(f"Agent '{agent_name}' not found", title=f"Task ({agent_name})")

        try:
            provider_id, model_id = await providermod.default_model()
            model = await providermod.get_model(provider_id, model_id)
        except Exception as e:
            return ToolError(f"Model error: {e}", title=f"Task ({agent_name})")

        # Build agent permission ruleset for tool authorization
        agent_ruleset = build_agent_ruleset(agent)

        # Initialize sub-agent loop guard for pattern detection and caching
        guard_config = LoopGuardConfig(
            max_iterations=MAX_TURNS,
            repeat_threshold=3,
            stall_threshold=3,
            cache_enabled=True,
            cache_max_size=50,
            max_retries=1,
        )
        guard = LoopGuard(config=guard_config)

        system = build_system(agent_prompt=agent.prompt)
        messages: list[dict[str, Any]] = [{"role": "user", "content": description}]
        tools = [t for t in tool_registry.to_llm_tools() if t["function"]["name"] not in _EXCLUDED_TOOLS]

        builder = ToolResultBuilder(max_chars=50_000)
        total_tool_calls = 0
        turn = 0

        for turn in range(MAX_TURNS):
            # Check abort signal before each turn
            if is_aborted(ctx):
                builder.add("\n\n(Sub-agent aborted by user)")
                break

            # Loop guard check before each turn
            verdict = guard.check(turn)
            if verdict.action.value in ("stop", "force_stop"):
                logger.warn("sub-agent loop guard stop", reason=verdict.reason, agent=agent_name)
                builder.add(f"\n\n(Sub-agent stopped by loop guard: {verdict.reason})")
                break

            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
            )

            text_parts: list[str] = []
            pending_tool_calls: list[llmmod.ToolCallDelta] = []
            finish_reason = "stop"

            async for event in llmmod.stream(stream_input):
                # Check abort during streaming
                if is_aborted(ctx):
                    builder.add("\n\n(Sub-agent aborted by user)")
                    return ToolOk(
                        builder.build() or "Sub-agent aborted.",
                        title=f"Task: {description[:60]}",
                        metadata={"agent": agent_name, "tool_calls": total_tool_calls, "turns": turn + 1, "aborted": True},
                    )

                if isinstance(event, llmmod.TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, llmmod.ToolCallDelta):
                    pending_tool_calls.append(event)
                elif isinstance(event, llmmod.FinishEvent):
                    finish_reason = event.reason
                elif isinstance(event, llmmod.ErrorEvent):
                    builder.add(f"\nError: {event.error}")
                    return ToolError(
                        builder.build() or f"Sub-agent error: {event.error}",
                        title=f"Task: {description[:60]}",
                        metadata={"agent": agent_name, "tool_calls": total_tool_calls, "turns": turn + 1},
                    )

            assistant_text = "".join(text_parts)
            if assistant_text:
                builder.add(assistant_text)

            # Record step text production for loop guard
            guard.begin_step(turn)
            step = guard.steps[-1]
            guard.complete_step(step, text_length=len(assistant_text))

            if not pending_tool_calls or finish_reason != "tool-calls":
                break

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": assistant_text or None}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.tool_call_id,
                    "type": "function",
                    "function": {"name": tc.tool_name, "arguments": tc.args},
                }
                for tc in pending_tool_calls
            ]
            messages.append(assistant_msg)

            for tc in pending_tool_calls:
                total_tool_calls += 1

                # Check abort before each tool execution
                if is_aborted(ctx):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "content": "Aborted by user",
                    })
                    continue

                # Permission check — enforce agent's permission rules
                perm_error = check_tool_permission(tc.tool_name, agent_ruleset)
                if perm_error:
                    logger.warn("sub-agent tool denied", tool=tc.tool_name, agent=agent_name, reason=perm_error)
                    guard.record_tool_call(tc.tool_name, {}, output=perm_error, is_error=True)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "content": perm_error,
                    })
                    continue

                tool_impl = tool_registry.get(tc.tool_name)
                tool_output = ""
                is_error = False

                if tool_impl:
                    try:
                        tool_args = json.loads(tc.args) if tc.args and tc.args.strip() else {}
                    except json.JSONDecodeError as e:
                        tool_output = f"Invalid JSON arguments: {e}"
                        is_error = True
                        guard.record_tool_call(tc.tool_name, {}, output=tool_output, is_error=True)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.tool_call_id,
                            "content": tool_output,
                        })
                        continue

                    # Cache check — skip if we have a cached result for read-only tools
                    cached = guard.cache.get(tc.tool_name, tool_args)
                    if cached is not None:
                        logger.debug("sub-agent cache hit", tool=tc.tool_name)
                        tool_output = cached
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.tool_call_id,
                            "content": tool_output,
                        })
                        continue

                    try:
                        result = await tool_impl.execute(tool_args, ctx)
                        tool_output = result.output
                        is_error = result.is_error
                    except Exception as e:
                        tool_output = f"Error: {e}"
                        is_error = True

                    # Record to loop guard (triggers cache put or invalidation)
                    guard.record_tool_call(tc.tool_name, tool_args, output=tool_output, is_error=is_error)
                else:
                    tool_output = f"Unknown tool: {tc.tool_name}"
                    is_error = True
                    guard.record_tool_call(tc.tool_name, {}, output=tool_output, is_error=True)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.tool_call_id,
                    "content": tool_output,
                })

        output = builder.build()
        return ToolOk(
            output or "No output from sub-agent.",
            title=f"Task: {description[:60]}",
            metadata={"agent": agent_name, "tool_calls": total_tool_calls, "turns": min(turn + 1, MAX_TURNS)},
        )


tool = TaskTool()
