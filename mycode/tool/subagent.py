"""Sub-agent tool — unified interface for delegate, parallel, and isolated sub-agent modes.

Features:
- **Delegate mode**: Enhanced single sub-agent with context passing and configurable turns
- **Parallel mode**: Multiple sub-agents running concurrently via asyncio.gather
- **Isolated mode**: Sub-agent executing in a git worktree for safe file modifications

Each mode enforces permission rules, loop guard protection, and abort signal support.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from mycode.agent import agent as agentmod
from mycode.provider import provider as providermod
from mycode.session import llm as llmmod
from mycode.session.loop_guard import LoopGuard, LoopGuardConfig
from mycode.session.system import build as build_system
from mycode.tool import registry as tool_registry
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder
from mycode.util import log as logmod
from mycode.util.subagent import build_agent_ruleset, check_tool_permission, is_aborted

logger = logmod.create(service="tool.subagent")

# Tools that sub-agents cannot use (prevent recursion and interactive prompts)
_EXCLUDED_TOOLS = frozenset({"subagent", "todo", "question", "batch"})

# Agents allowed in parallel mode (must be safe for concurrent execution)
_PARALLEL_SAFE_AGENTS = frozenset({"explore", "general"})

# Agents allowed in isolated mode (need write permissions)
_ISOLATED_SAFE_AGENTS = frozenset({"coder", "build", "general"})

MAX_PARALLEL = 10

# Default and max turns per mode
_TURNS_CONFIG: dict[str, tuple[int, int]] = {
    #              (default, max)
    "delegate":   (12, 30),
    "parallel":   (8, 15),
    "isolated":   (20, 30),
}


class SubAgentParams(BaseModel):
    """Parameters for the sub-agent tool."""
    description: str = Field(description="A clear description of the task for the sub-agent")
    agent: str = Field(default="general", description="Agent type: 'general', 'explore', or 'coder'")
    mode: Literal["delegate", "parallel", "isolated"] = Field(
        default="delegate",
        description="Execution mode: 'delegate' (single enhanced sub-agent), 'parallel' (multiple concurrent), 'isolated' (git worktree)",
    )
    # Parallel mode
    tasks: list[str] | None = Field(
        default=None,
        description="(parallel mode) List of task descriptions to execute concurrently",
    )

    @field_validator("tasks", mode="before")
    @classmethod
    def _parse_tasks_string(cls, v: Any) -> list[str] | None:
        """Auto-parse tasks from JSON string — some models pass a stringified array."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed]
                except json.JSONDecodeError:
                    pass
            # Single task string → wrap in list
            return [v]
        return list(v) if isinstance(v, list) else v

    max_concurrency: int = Field(default=5, ge=1, le=MAX_PARALLEL, description="(parallel mode) Max concurrent sub-agents")
    # Delegate/isolated mode
    context: str = Field(default="", description="(delegate/isolated) Context summary to provide to the sub-agent")
    max_turns: int | None = Field(
        default=None,
        ge=1, le=30,
        description="Maximum turns for the sub-agent loop. Defaults vary by mode: delegate=12, parallel=8, isolated=20",
    )
    # Isolated mode
    auto_merge: bool = Field(default=False, description="(isolated) Automatically apply changes to main working directory")

    @model_validator(mode="after")
    def _apply_turns_defaults(self) -> SubAgentParams:
        """Apply mode-specific default and cap for max_turns."""
        default, cap = _TURNS_CONFIG.get(self.mode, (12, 30))
        if self.max_turns is None:
            self.max_turns = default
        elif self.max_turns > cap:
            self.max_turns = cap
        return self


class SubAgentTool(CallableTool[SubAgentParams]):
    id = "subagent"
    description = (
        "Launch sub-agent(s) for complex tasks. Three modes: "
        "'delegate' (single agent with context), 'parallel' (multiple agents concurrently), "
        "'isolated' (agent in git worktree for safe file modifications)."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: SubAgentParams, ctx: ToolContext) -> ToolResult:
        if params.mode == "delegate":
            return await self._run_delegate(params, ctx)
        elif params.mode == "parallel":
            return await self._run_parallel(params, ctx)
        elif params.mode == "isolated":
            return await self._run_isolated(params, ctx)
        else:
            return ToolError(f"Unknown mode: {params.mode}")

    @staticmethod
    def _apply_agent_max_turns(
        agent: Any,
        user_value: int | None,
        mode: str,
    ) -> int:
        """Resolve final max_turns for a sub-agent run.

        Precedence (first non-None wins), capped to mode's upper bound:
          1. ``user_value`` — argument supplied by the LLM in the tool call
          2. ``agent.max_turns`` — author-declared ceiling on the agent itself
          3. mode default from ``_TURNS_CONFIG``
        """
        default, cap = _TURNS_CONFIG.get(mode, (12, 30))
        agent_turns = getattr(agent, "max_turns", None)
        picked = user_value if user_value is not None else agent_turns
        if picked is None:
            picked = default
        return max(1, min(int(picked), cap))

    @staticmethod
    def _filter_tools_for_agent(
        agent: Any,
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply ``agent.tools`` allow-list on top of ``_EXCLUDED_TOOLS``.

        - Always drops tools in ``_EXCLUDED_TOOLS`` (recursion / interactive
          prompts are forbidden for sub-agents).
        - When ``agent.tools`` is a non-empty list, further restricts the
          tool set to names appearing in that list.  A ``None`` value means
          "no allow-list — inherit parent policy".
        """
        allow = getattr(agent, "tools", None)
        allow_set = set(allow) if allow else None

        out: list[dict[str, Any]] = []
        for t in tools:
            name = t["function"]["name"]
            if name in _EXCLUDED_TOOLS:
                continue
            if allow_set is not None and name not in allow_set:
                continue
            out.append(t)
        return out

    # ─── Delegate Mode ────────────────────────────────────────────────────────

    async def _run_delegate(self, params: SubAgentParams, ctx: ToolContext) -> ToolResult:
        """Run a single sub-agent with enhanced context passing."""
        agent = await agentmod.get(params.agent)
        if not agent:
            return ToolError(f"Agent '{params.agent}' not found")

        max_turns = self._apply_agent_max_turns(agent, params.max_turns, "delegate")

        return await self._execute_agent_loop(
            description=params.description,
            context=params.context,
            agent=agent,
            max_turns=max_turns,
            ctx=ctx,
        )

    # ─── Parallel Mode ────────────────────────────────────────────────────────

    async def _run_parallel(self, params: SubAgentParams, ctx: ToolContext) -> ToolResult:
        """Run multiple sub-agents concurrently."""
        tasks = params.tasks
        if not tasks:
            return await self._run_delegate(params, ctx)

        if len(tasks) > MAX_PARALLEL:
            return ToolError(f"Too many parallel tasks: {len(tasks)} exceeds max of {MAX_PARALLEL}")

        if params.agent not in _PARALLEL_SAFE_AGENTS:
            return ToolError(
                f"Agent '{params.agent}' is not safe for parallel mode. "
                f"Use one of: {', '.join(sorted(_PARALLEL_SAFE_AGENTS))}"
            )

        agent = await agentmod.get(params.agent)
        if not agent:
            return ToolError(f"Agent '{params.agent}' not found")

        # Pre-resolve model and tools once for all parallel sub-agents
        try:
            provider_id, model_id = await providermod.default_model()
            model = await providermod.get_model(provider_id, model_id)
            api_key = await providermod.get_api_key(provider_id)
        except Exception as e:
            return ToolError(f"Model error: {e}")

        # `model` is a Pydantic instance and read-only from the agent loop's
        # perspective — share by reference. `tools` is a list[dict]; the
        # agent loop does not mutate it today, but downstream provider
        # transforms sometimes append entries, so hand each sub-agent its
        # own deep copy to prevent accidental cross-contamination.
        import copy as _copy

        shared_tools = self._filter_tools_for_agent(agent, tool_registry.to_llm_tools())

        semaphore = asyncio.Semaphore(params.max_concurrency)

        max_turns = self._apply_agent_max_turns(agent, params.max_turns, "parallel")

        async def _run_one(idx: int, task_desc: str) -> tuple[int, str, bool]:
            async with semaphore:
                if is_aborted(ctx):
                    return idx, "(aborted)", False
                # Per-run ToolContext so that any state one sub-agent writes
                # into its context (for example abort metadata) cannot be
                # observed by a sibling in the gather.
                sub_ctx = ToolContext(
                    session_id=ctx.session_id,
                    message_id=ctx.message_id,
                    agent=ctx.agent,
                    call_id=f"{ctx.call_id}:parallel[{idx}]",
                )
                # Copy tool definitions so sub-agent-specific mutations
                # cannot leak to peers.
                per_agent_tools = _copy.deepcopy(shared_tools)
                result = await self._execute_agent_loop(
                    description=task_desc,
                    context=params.context,
                    agent=agent,
                    max_turns=max_turns,
                    ctx=sub_ctx,
                    _model=model,
                    _tools=per_agent_tools,
                    _api_key=api_key,
                )
                return idx, result.output, not result.is_error

        raw_results = await asyncio.gather(
            *[_run_one(i, t) for i, t in enumerate(tasks)],
            return_exceptions=True,
        )

        builder = ToolResultBuilder(max_chars=80_000)
        succeeded = 0
        failed = 0

        for item in raw_results:
            if isinstance(item, BaseException):
                failed += 1
                builder.add(f"\n--- Task (error) ---\nException: {item}\n")
            else:
                idx, output, ok = item
                if ok:
                    succeeded += 1
                else:
                    failed += 1
                builder.add(f"\n--- Task {idx + 1}: {tasks[idx][:80]} ---\n")
                builder.add(output)
                builder.add("\n")

        return ToolOk(
            builder.build(),
            title=f"Parallel: {succeeded}/{len(tasks)} succeeded",
            metadata={
                "mode": "parallel",
                "agent": params.agent,
                "total": len(tasks),
                "succeeded": succeeded,
                "failed": failed,
            },
        )

    # ─── Isolated Mode ────────────────────────────────────────────────────────

    async def _run_isolated(self, params: SubAgentParams, ctx: ToolContext) -> ToolResult:
        """Run a sub-agent in a git worktree for isolated file modifications."""
        from mycode.project.instance import current_or_none
        from mycode.session.worktree import apply_diff_text, cleanup_worktree, create_worktree, stage_and_collect

        project = current_or_none()
        if not project:
            return ToolError("No active project context — isolated mode requires a project directory")

        project_dir = project.directory

        worktree = await create_worktree(project_dir)
        if not worktree:
            return ToolError("Failed to create git worktree. Is this a git repository?")

        try:
            agent_name = params.agent if params.agent in _ISOLATED_SAFE_AGENTS else "coder"
            agent = await agentmod.get(agent_name)
            if not agent:
                return ToolError(f"Agent '{agent_name}' not found")

            isolated_desc = (
                f"{params.description}\n\n"
                f"IMPORTANT: You are working in an isolated worktree at: {worktree.path}\n"
                f"All file paths should be relative to or within this directory.\n"
                f"The base project is at: {project_dir}"
            )

            max_turns = self._apply_agent_max_turns(agent, params.max_turns, "isolated")

            result = await self._execute_agent_loop(
                description=isolated_desc,
                context=params.context,
                agent=agent,
                max_turns=max_turns,
                ctx=ctx,
                working_dir=worktree.path,
            )

            # Collect diff and changed files in one pass (avoids duplicate git add -A)
            diff, changed_files = await stage_and_collect(worktree)

            if not diff:
                output = result.output + "\n\n(No file changes were made in the worktree)"
                return ToolOk(
                    output,
                    title=f"Isolated: {params.description[:50]} (no changes)",
                    metadata={"mode": "isolated", "agent": agent_name, "changes": False},
                )

            merged = False
            if params.auto_merge:
                merged = await apply_diff_text(diff, project_dir)

            builder = ToolResultBuilder(max_chars=80_000)
            builder.add(result.output)
            builder.add_heading("Changes")
            builder.add(f"Files modified: {len(changed_files)}\n")
            for f in changed_files[:20]:
                builder.add(f"  - {f}\n")
            if len(changed_files) > 20:
                builder.add(f"  ... and {len(changed_files) - 20} more\n")
            builder.add_heading("Diff")
            builder.add(diff[:10000])
            if len(diff) > 10000:
                builder.add(f"\n... (diff truncated, full length: {len(diff)} chars)")
            if merged:
                builder.add_heading("Status")
                builder.add("Changes have been automatically applied to the main working directory.")
            else:
                builder.add_heading("Status")
                builder.add("Changes are available in the worktree. Use auto_merge=true to apply them.")

            return ToolOk(
                builder.build(),
                title=f"Isolated: {params.description[:50]}",
                metadata={
                    "mode": "isolated",
                    "agent": agent_name,
                    "changes": True,
                    "merged": merged,
                    "changed_files": changed_files,
                    "worktree_path": worktree.path,
                },
            )
        finally:
            await cleanup_worktree(worktree)

    # ─── Core Agent Loop ──────────────────────────────────────────────────────

    async def _execute_agent_loop(
        self,
        *,
        description: str,
        context: str,
        agent: Any,
        max_turns: int,
        ctx: ToolContext,
        working_dir: str | None = None,
        _model: Any | None = None,
        _tools: list[dict[str, Any]] | None = None,
        _api_key: str | None = None,
    ) -> ToolResult:
        """Execute the core sub-agent agentic loop.

        Args:
            _model: Pre-resolved model (avoids redundant lookups in parallel mode).
            _tools: Pre-built tool list (avoids redundant registry scans in parallel mode).
            _api_key: Pre-fetched API key (avoids redundant lookups in parallel mode).
        """
        model = _model
        api_key = _api_key
        if model is None:
            try:
                provider_id, model_id = await providermod.default_model()
                model = await providermod.get_model(provider_id, model_id)
                api_key = await providermod.get_api_key(provider_id)
            except Exception as e:
                return ToolError(f"Model error: {e}")

        agent_ruleset = build_agent_ruleset(agent)

        guard_config = LoopGuardConfig(
            max_iterations=max_turns,
            repeat_threshold=3,
            stall_threshold=3,
            cache_enabled=True,
            cache_max_size=50,
            max_retries=1,
        )
        guard = LoopGuard(config=guard_config)

        system = build_system(
            agent_prompt=agent.prompt,
            omit_project_guidance=getattr(agent, "omit_project_guidance", getattr(agent, "omit_claudemd", False)),
        )

        user_content = description
        if context:
            user_content = f"Context:\n{context}\n\nTask:\n{description}"

        from mycode.session.memory.service import recall_for_current_project

        memory_evidence = await asyncio.to_thread(recall_for_current_project, description, agent=agent.name)
        if memory_evidence:
            user_content = f"{user_content}\n\n{memory_evidence}"

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

        tools = _tools
        if tools is None:
            tools = self._filter_tools_for_agent(agent, tool_registry.to_llm_tools())

        builder = ToolResultBuilder(max_chars=50_000)
        total_tool_calls = 0

        for turn in range(max_turns):
            if is_aborted(ctx):
                builder.add("\n\n(Sub-agent aborted)")
                break

            verdict = guard.check(turn)
            if verdict.action.value in ("stop", "force_stop"):
                logger.warn("sub-agent loop guard stop", reason=verdict.reason, agent=agent.name)
                builder.add(f"\n\n(Stopped by loop guard: {verdict.reason})")
                break

            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                api_key=api_key,
                api_base=model.api.url or None,
            )

            text_parts: list[str] = []
            pending_tool_calls: list[llmmod.ToolCallDelta] = []
            finish_reason = "stop"

            async for event in llmmod.stream(stream_input):
                if is_aborted(ctx):
                    builder.add("\n\n(Sub-agent aborted)")
                    return ToolOk(
                        builder.build() or "Sub-agent aborted.",
                        title=f"SubAgent: {description[:60]}",
                        metadata={"agent": agent.name, "tool_calls": total_tool_calls, "turns": turn + 1, "aborted": True},
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
                        title=f"SubAgent: {description[:60]}",
                        metadata={"agent": agent.name, "tool_calls": total_tool_calls, "turns": turn + 1},
                    )

            assistant_text = "".join(text_parts)
            if assistant_text:
                builder.add(assistant_text)

            # Record step for loop guard (begin_step returns the step object)
            step = guard.begin_step(turn)
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

                if is_aborted(ctx):
                    messages.append({"role": "tool", "tool_call_id": tc.tool_call_id, "content": "Aborted"})
                    continue

                perm_error = check_tool_permission(tc.tool_name, agent_ruleset)
                if perm_error:
                    logger.warn("sub-agent tool denied", tool=tc.tool_name, agent=agent.name)
                    guard.record_tool_call(tc.tool_name, {}, output=perm_error, is_error=True)
                    messages.append({"role": "tool", "tool_call_id": tc.tool_call_id, "content": perm_error})
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
                        messages.append({"role": "tool", "tool_call_id": tc.tool_call_id, "content": tool_output})
                        continue

                    # Rewrite file paths for isolated mode (worktree)
                    if working_dir and tool_args:
                        tool_args = _rewrite_paths(tool_args, working_dir)

                    cached = guard.cache.get(tc.tool_name, tool_args)
                    if cached is not None:
                        messages.append({"role": "tool", "tool_call_id": tc.tool_call_id, "content": cached})
                        continue

                    try:
                        result = await tool_impl.execute(tool_args, ctx)
                        tool_output = result.output
                        is_error = result.is_error
                    except Exception as e:
                        tool_output = f"Error: {e}"
                        is_error = True

                    guard.record_tool_call(tc.tool_name, tool_args, output=tool_output, is_error=is_error)
                else:
                    tool_output = f"Unknown tool: {tc.tool_name}"
                    is_error = True
                    guard.record_tool_call(tc.tool_name, {}, output=tool_output, is_error=True)

                messages.append({"role": "tool", "tool_call_id": tc.tool_call_id, "content": tool_output})

        output = builder.build()
        return ToolOk(
            output or "No output from sub-agent.",
            title=f"SubAgent: {description[:60]}",
            metadata={"agent": agent.name, "tool_calls": total_tool_calls, "turns": min(turn + 1, max_turns)},
        )


# Path keys used by file-operating tools
_PATH_KEYS = ("file_path", "path", "directory")


def _rewrite_paths(args: dict[str, Any], working_dir: str) -> dict[str, Any]:
    """Rewrite path arguments to be within the working directory for isolated mode."""
    rewritten = dict(args)
    for key in _PATH_KEYS:
        if key in rewritten and isinstance(rewritten[key], str):
            val = rewritten[key]
            if not os.path.isabs(val):
                rewritten[key] = os.path.join(working_dir, val)
    return rewritten


tool = SubAgentTool()
