"""Batch tool — application-level explicit parallel tool execution.

in a single batch, which are then executed in parallel via asyncio.gather.
This is an experimental feature, enabled via `experimental.batch_tool: true`.

Does not depend on native parallel tool-call support from the model.
Limitations:
  - Only built-in tools can be batched (no MCP or external tools)
  - Batch calls cannot be nested (no batch-inside-batch)
  - Maximum 25 calls per batch
  - Permission rules are enforced per-call
"""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from mycode.permission.evaluate import evaluate as eval_permission
from mycode.permission.schema import Rule
from mycode.tool import registry as tool_registry
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder
from mycode.util import log as logmod

logger = logmod.create(service="tool.batch")

MAX_BATCH_SIZE = 25

# Tools that cannot appear inside a batch
_EXCLUDED_TOOLS = frozenset({"batch", "task", "todo", "question"})


class BatchCallItem(BaseModel):
    """A single tool call within a batch."""
    tool: str = Field(description="The name of the tool to call")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments to pass to the tool")


class BatchParams(BaseModel):
    """Parameters for the batch tool."""
    description: str = Field(default="batch execution", description="A brief description of what this batch accomplishes")
    calls: list[BatchCallItem] = Field(description="Array of tool calls to execute in parallel", max_length=MAX_BATCH_SIZE)
    agent_ruleset: list[dict[str, Any]] = Field(default_factory=list, description="Internal: agent permission ruleset (injected by processor)")


class BatchTool(CallableTool[BatchParams]):
    id = "batch"
    description = (
        "Execute multiple tool calls in parallel within a single request. "
        "Use this when you need to run several independent operations simultaneously "
        "(e.g. reading multiple files, running multiple searches). "
        "Each call in the array is executed concurrently via asyncio.gather. "
        "Maximum 25 calls per batch. Only built-in tools are supported (no nested batch or task)."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        """Batch may contain mutating tools, so it's not read-only."""
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        """Batch may contain destructive tools."""
        return False

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        """Batch is not safe to run concurrently with other tools."""
        return False

    async def call(self, params: BatchParams, ctx: ToolContext) -> ToolResult:
        calls = params.calls
        description = params.description

        if not calls:
            return ToolError(
                "No calls provided.",
                title=f"Batch: {description}",
                metadata={"total": 0, "succeeded": 0, "failed": 0},
            )

        if len(calls) > MAX_BATCH_SIZE:
            return ToolError(
                f"Too many calls: {len(calls)} exceeds maximum of {MAX_BATCH_SIZE}.",
                title=f"Batch: {description}",
                metadata={"total": len(calls), "succeeded": 0, "failed": 0},
            )

        # Build permission ruleset from context (if available)
        agent_ruleset: list[Rule] = [
            Rule(
                permission=r.get("permission", "*"),
                pattern=r.get("pattern", "*"),
                action=r.get("action", "ask"),
            )
            for r in params.agent_ruleset
        ] if params.agent_ruleset else []

        # Validate all calls before executing any
        validated: list[tuple[BatchCallItem, object]] = []  # (call_spec, tool_impl)
        errors: list[str] = []
        for i, call_item in enumerate(calls):
            tool_name = call_item.tool
            if tool_name in _EXCLUDED_TOOLS:
                errors.append(f"[{i}] Tool '{tool_name}' is not allowed in batch")
                continue
            tool_impl = tool_registry.get(tool_name)
            if not tool_impl:
                errors.append(f"[{i}] Unknown tool: {tool_name}")
                continue

            # Permission check if ruleset is available
            if agent_ruleset:
                result = eval_permission(tool_name, "*", agent_ruleset)
                if result.action != "allow":
                    errors.append(f"[{i}] Tool '{tool_name}' denied by permission rules (action={result.action})")
                    continue

            validated.append((call_item, tool_impl))

        if errors and not validated:
            return ToolError(
                "All calls failed validation:\n" + "\n".join(errors),
                title=f"Batch: {description}",
                metadata={"total": len(calls), "succeeded": 0, "failed": len(errors)},
            )

        # Execute all validated calls in parallel
        async def _execute_one(idx: int, call_item: BatchCallItem, tool_impl: object) -> tuple[bool, str]:
            tool_name = call_item.tool
            tool_args = call_item.args
            try:
                from mycode.tool.base import ToolInfo
                assert isinstance(tool_impl, ToolInfo)
                result = await tool_impl.execute(tool_args, ctx)
                return (not result.is_error, f"[{idx}:{tool_name}] {result.output}")
            except Exception as e:
                return (False, f"[{idx}:{tool_name}] Error: {e}")

        tasks = [
            _execute_one(i, call_item, impl)
            for i, (call_item, impl) in enumerate(validated)
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Normalize: exceptions surfaced by gather (shouldn't happen given try/except in
        # _execute_one, but be defensive) are treated as failures.
        results: list[tuple[bool, str]] = []
        for i, item in enumerate(raw_results):
            if isinstance(item, BaseException):
                tool_name = validated[i][0].tool
                results.append((False, f"[{i}:{tool_name}] Unexpected error: {item}"))
            else:
                results.append(item)

        succeeded = sum(1 for ok, _ in results if ok)
        failed = len(results) - succeeded

        builder = ToolResultBuilder()
        if errors:
            builder.add_heading("Validation Errors")
            builder.add("\n".join(errors))
            builder.add("\n")
        builder.add_heading("Results")
        builder.add("\n".join(text for _, text in results))

        return ToolOk(
            builder.build(),
            title=f"Batch: {description} ({succeeded}/{len(validated)} succeeded)",
            metadata={
                "total": len(calls),
                "succeeded": succeeded,
                "failed": failed + len(errors),
            },
        )


tool = BatchTool()
