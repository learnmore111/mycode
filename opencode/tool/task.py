"""Task tool — spawn a sub-agent for complex multi-step work. Equivalent to src/tool/task.ts.

Key improvement: supports a multi-turn agentic loop (up to MAX_TURNS) so the
sub-agent can perform multi-step reasoning (e.g. search → read → analyze)
instead of being limited to a single LLM call.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from opencode.agent import agent as agentmod
from opencode.provider import provider as providermod
from opencode.session import llm as llmmod
from opencode.session.system import build as build_system
from opencode.tool import registry as tool_registry
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder

MAX_TURNS = 8  # Maximum agentic loop iterations to prevent runaway

# Tools excluded from sub-agent to prevent recursion or side effects
_EXCLUDED_TOOLS = frozenset({"task", "todo", "question", "batch"})


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

        system = build_system(agent_prompt=agent.prompt)
        messages: list[dict[str, Any]] = [{"role": "user", "content": description}]
        tools = [t for t in tool_registry.to_llm_tools() if t["function"]["name"] not in _EXCLUDED_TOOLS]

        builder = ToolResultBuilder(max_chars=50_000)
        total_tool_calls = 0

        for turn in range(MAX_TURNS):
            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
            )

            # Accumulate this turn's response
            text_parts: list[str] = []
            pending_tool_calls: list[llmmod.ToolCallDelta] = []
            finish_reason = "stop"

            async for event in llmmod.stream(stream_input):
                if isinstance(event, llmmod.TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, llmmod.ToolCallDelta):
                    pending_tool_calls.append(event)
                elif isinstance(event, llmmod.FinishEvent):
                    finish_reason = event.reason
                elif isinstance(event, llmmod.ErrorEvent):
                    builder.add(f"\nError: {event.error}")
                    return ToolOk(
                        builder.build() or f"Sub-agent error: {event.error}",
                        title=f"Task: {description[:60]}",
                        metadata={"agent": agent_name, "tool_calls": total_tool_calls, "turns": turn + 1},
                    )

            assistant_text = "".join(text_parts)
            if assistant_text:
                builder.add(assistant_text)

            # If no tool calls, the sub-agent is done
            if not pending_tool_calls or finish_reason != "tool-calls":
                break

            # Build the assistant message with tool_calls for the conversation
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

            # Execute tool calls and add results to messages
            for tc in pending_tool_calls:
                total_tool_calls += 1
                tool_impl = tool_registry.get(tc.tool_name)
                tool_output = ""
                if tool_impl:
                    try:
                        tool_args = json.loads(tc.args) if tc.args else {}
                        result = await tool_impl.execute(tool_args, ctx)
                        tool_output = result.output
                    except Exception as e:
                        tool_output = f"Error: {e}"
                else:
                    tool_output = f"Unknown tool: {tc.tool_name}"

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


# Need this import at module level for type annotation in the loop
from typing import Any  # noqa: E402


tool = TaskTool()
