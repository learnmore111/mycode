"""Task tool — spawn a sub-agent for complex multi-step work. Equivalent to src/tool/task.ts."""
from __future__ import annotations

import json
from typing import Any

from opencode.agent import agent as agentmod
from opencode.provider import provider as providermod
from opencode.session import llm as llmmod
from opencode.session.system import build as build_system
from opencode.tool import registry as tool_registry
from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class TaskTool(ToolInfo):
    id = "task"
    description = (
        "Launch a sub-agent to handle a complex task. The sub-agent runs independently with its own context. "
        "Use this when you need to research, explore, or execute multi-step work in parallel."
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A clear description of the task for the sub-agent to accomplish",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent to use (default: 'general'). Options: general, explore",
                },
            },
            "required": ["description"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        description = args["description"]
        agent_name = args.get("agent", "general")

        agent = await agentmod.get(agent_name)
        if not agent:
            return ToolResult(title=f"Task ({agent_name})", output=f"Agent '{agent_name}' not found", metadata={})

        try:
            provider_id, model_id = await providermod.default_model()
            model = await providermod.get_model(provider_id, model_id)
        except Exception as e:
            return ToolResult(title=f"Task ({agent_name})", output=f"Model error: {e}", metadata={})

        system = build_system(agent_prompt=agent.prompt)
        messages = [{"role": "user", "content": description}]
        tools = tool_registry.to_llm_tools()

        # Filter tools for sub-agent (no task recursion, no todo)
        tools = [t for t in tools if t["function"]["name"] not in ("task", "todo", "question")]

        stream_input = llmmod.StreamInput(
            model=model,
            messages=messages,
            system=system,
            tools=tools if model.capabilities.toolcall else None,
            temperature=agent.temperature,
        )

        output_parts: list[str] = []
        tool_results: list[str] = []

        # Run a simple single-pass (no agentic loop for sub-agent to avoid deep recursion)
        async for event in llmmod.stream(stream_input):
            if isinstance(event, llmmod.TextDelta):
                output_parts.append(event.text)
            elif isinstance(event, llmmod.ToolCallDelta):
                # Execute tool calls inline
                tool_impl = tool_registry.get(event.tool_name)
                if tool_impl:
                    try:
                        tool_args = json.loads(event.args) if event.args else {}
                        result = await tool_impl.execute(tool_args, ctx)
                        tool_results.append(f"[{event.tool_name}] {result.output[:500]}")
                    except Exception as e:
                        tool_results.append(f"[{event.tool_name}] Error: {e}")
            elif isinstance(event, llmmod.ErrorEvent):
                output_parts.append(f"\nError: {event.error}")

        output = "".join(output_parts)
        if tool_results:
            output += "\n\n--- Tool Results ---\n" + "\n".join(tool_results)

        return ToolResult(
            title=f"Task: {description[:60]}",
            output=output or "No output from sub-agent.",
            metadata={"agent": agent_name, "tool_calls": len(tool_results)},
        )


tool = TaskTool()
