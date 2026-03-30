"""Batch tool — application-level explicit parallel tool execution.

Equivalent to src/tool/batch.ts. Allows LLM to submit up to 25 tool calls
in a single batch, which are then executed in parallel via asyncio.gather.
This is an experimental feature, enabled via `experimental.batch_tool: true`.

Does not depend on native parallel tool-call support from the model.
Limitations:
  - Only built-in tools can be batched (no MCP or external tools)
  - Batch calls cannot be nested (no batch-inside-batch)
  - Maximum 25 calls per batch
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from opencode.tool import registry as tool_registry
from opencode.tool.base import ToolContext, ToolInfo, ToolResult
from opencode.util import log as logmod

logger = logmod.create(service="tool.batch")

MAX_BATCH_SIZE = 25

# Tools that cannot appear inside a batch
_EXCLUDED_TOOLS = frozenset({"batch", "task", "todo", "question"})


class BatchTool(ToolInfo):
    id = "batch"
    description = (
        "Execute multiple tool calls in parallel within a single request. "
        "Use this when you need to run several independent operations simultaneously "
        "(e.g. reading multiple files, running multiple searches). "
        "Each call in the array is executed concurrently via asyncio.gather. "
        "Maximum 25 calls per batch. Only built-in tools are supported (no nested batch or task)."
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A brief description of what this batch accomplishes",
                },
                "calls": {
                    "type": "array",
                    "description": (
                        "Array of tool calls to execute in parallel. "
                        "Each item has 'tool' (tool name) and 'args' (tool arguments object)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "The name of the tool to call",
                            },
                            "args": {
                                "type": "object",
                                "description": "Arguments to pass to the tool",
                            },
                        },
                        "required": ["tool", "args"],
                    },
                    "maxItems": MAX_BATCH_SIZE,
                },
            },
            "required": ["calls"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        calls: list[dict[str, Any]] = args.get("calls", [])
        description = args.get("description", "batch execution")

        if not calls:
            return ToolResult(
                title=f"Batch: {description}",
                output="No calls provided.",
                metadata={"total": 0, "succeeded": 0, "failed": 0},
            )

        if len(calls) > MAX_BATCH_SIZE:
            return ToolResult(
                title=f"Batch: {description}",
                output=f"Too many calls: {len(calls)} exceeds maximum of {MAX_BATCH_SIZE}.",
                metadata={"total": len(calls), "succeeded": 0, "failed": 0},
            )

        # Validate all calls before executing any
        validated: list[tuple[dict[str, Any], Any]] = []  # (call_spec, tool_impl)
        errors: list[str] = []
        for i, call in enumerate(calls):
            tool_name = call.get("tool", "")
            if tool_name in _EXCLUDED_TOOLS:
                errors.append(f"[{i}] Tool '{tool_name}' is not allowed in batch")
                continue
            tool_impl = tool_registry.get(tool_name)
            if not tool_impl:
                errors.append(f"[{i}] Unknown tool: {tool_name}")
                continue
            validated.append((call, tool_impl))

        if errors and not validated:
            return ToolResult(
                title=f"Batch: {description}",
                output="All calls failed validation:\n" + "\n".join(errors),
                metadata={"total": len(calls), "succeeded": 0, "failed": len(errors)},
            )

        # Execute all validated calls in parallel
        async def _execute_one(idx: int, call: dict[str, Any], tool_impl: Any) -> str:
            tool_name = call.get("tool", "")
            tool_args = call.get("args", {})
            try:
                result = await tool_impl.execute(tool_args, ctx)
                return f"[{idx}:{tool_name}] {result.output}"
            except Exception as e:
                return f"[{idx}:{tool_name}] Error: {e}"

        tasks = [
            _execute_one(i, call, impl)
            for i, (call, impl) in enumerate(validated)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        succeeded = sum(1 for r in results if "Error:" not in r)
        failed = len(results) - succeeded

        output_parts: list[str] = []
        if errors:
            output_parts.append("--- Validation Errors ---")
            output_parts.extend(errors)
            output_parts.append("")
        output_parts.append("--- Results ---")
        output_parts.extend(results)

        return ToolResult(
            title=f"Batch: {description} ({succeeded}/{len(validated)} succeeded)",
            output="\n".join(output_parts),
            metadata={
                "total": len(calls),
                "succeeded": succeeded,
                "failed": failed + len(errors),
            },
        )


tool = BatchTool()
