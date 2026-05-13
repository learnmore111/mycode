"""Agent-runner protocol + default litellm-based implementation.

The coordinator runtime must execute individual agents (one per spawn),
but we deliberately do *not* hard-wire it to :mod:`mycode.session.llm` so
that:

1. Tests can inject a deterministic fake runner with no network I/O.
2. A future swarm runtime can share the same abstraction.
3. Runtime code stays decoupled from :mod:`mycode.tool.task`, avoiding
   a cyclic import (tools live inside sessions; orchestration lives
   outside them).

``AgentRunner`` is a ``Protocol`` — any callable/object matching the
signature is accepted.  The default implementation,
:class:`LiteLLMAgentRunner`, mirrors the loop used by the ``task`` tool
but consumes a pre-resolved :class:`AgentInfo` instead of resolving by
name at execution time.  This keeps flow-level ``extends`` / inline
override semantics intact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from mycode.orchestration.runtime.context import SpawnOutput

if TYPE_CHECKING:
    from mycode.agent.agent import AgentInfo
    from mycode.orchestration.runtime.events import OrchestrationEventEmitter


# --- Request / response -----------------------------------------------------


@dataclass
class SpawnRequest:
    """Input to an ``AgentRunner``: one agent, one task, optional context."""

    agent: AgentInfo
    task: str
    # Markdown-rendered "inputs" block (from previous stages) to append
    # after the task body.  Coordinators use this to inject prior spawn
    # outputs.  Empty string when the stage has no ``inputs``.
    inputs_block: str = ""
    # Arbitrary key-value overrides for the spawn (from ``SpawnSpec.vars``).
    # Reserved for future template substitution at runtime; currently
    # unused by the default runner.
    vars: dict[str, Any] | None = None
    timeout_seconds: int | None = None
    stage_id: str | None = None
    spawn_index: int | None = None
    events: OrchestrationEventEmitter | None = None


# --- Protocol ---------------------------------------------------------------


class AgentRunner(Protocol):
    """Callable contract for executing one agent-spawn.

    Implementations must be **async**, and must not raise for
    domain-level failures (e.g. tool errors, LLM 5xx).  Instead, they
    should return a :class:`SpawnOutput` with ``is_error=True``.  Only
    programmer bugs (invalid ``AgentInfo`` shape, etc.) should raise.
    """

    async def __call__(self, req: SpawnRequest) -> SpawnOutput: ...


# --- Default implementation -------------------------------------------------


# Tools excluded from sub-agent tool lists (same policy as mycode.tool.task).
_EXCLUDED_SUBAGENT_TOOLS = frozenset({"subagent", "todo", "question", "batch"})

# Hard ceiling on turns for a single spawn.  Agents with a declared
# ``max_turns`` override this downward; None inherits the ceiling.
DEFAULT_MAX_TURNS = 8


def _compose_user_message(req: SpawnRequest) -> str:
    """Combine the task body and the optional inputs block into one user
    message.  Kept as a top-level helper so tests / custom runners can
    reuse it without instantiating the full LiteLLM runner."""
    if not req.inputs_block:
        return req.task
    return f"{req.task}\n\n---\n\n## Prior stage outputs\n\n{req.inputs_block}"


class LiteLLMAgentRunner:
    """Production ``AgentRunner`` that streams through litellm.

    Parallels :mod:`mycode.tool.task` but accepts a resolved
    :class:`AgentInfo` (already merged with registry extends + inline
    overrides by :mod:`agent_resolver`) and runs standalone — no
    ``ToolContext`` / session required.  That means no tool-loop guard
    cache hits across spawns, which is fine: each spawn is short-lived.

    When the caller does have a :class:`ToolContext` (e.g. embedded in a
    parent session), they can pass it via ``ctx=`` and we will forward
    it to tool execution so abort signals propagate.
    """

    def __init__(
        self,
        *,
        session_id: str = "orchestration",
        message_id: str = "orchestration",
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self._session_id = session_id
        self._message_id = message_id
        self._max_turns_cap = max_turns

    async def __call__(self, req: SpawnRequest) -> SpawnOutput:
        # Late imports to keep this module importable in the absence of
        # a fully bootstrapped session / provider layer (e.g. during
        # unit tests that only exercise the spawn data model).
        from mycode.provider import provider as providermod
        from mycode.session import llm as llmmod
        from mycode.session.loop_guard import LoopGuard, LoopGuardConfig
        from mycode.session.system import build as build_system
        from mycode.tool import registry as tool_registry
        from mycode.tool.base import ToolContext
        from mycode.util.subagent import build_agent_ruleset, check_tool_permission

        agent = req.agent
        user_msg = _compose_user_message(req)

        try:
            provider_id, model_id = await providermod.default_model()
            model = await providermod.get_model(provider_id, model_id)
            api_key = await providermod.get_api_key(provider_id)
        except Exception as exc:  # pragma: no cover — config-layer failure
            return SpawnOutput(
                agent=agent.name,
                task=req.task,
                output=f"Model resolution failed: {exc}",
                is_error=True,
            )

        agent_ruleset = build_agent_ruleset(agent)

        # Respect agent.max_turns (flow or registry) but cap at our limit.
        effective_max_turns = min(
            self._max_turns_cap,
            agent.max_turns or self._max_turns_cap,
        )

        guard = LoopGuard(config=LoopGuardConfig(
            max_iterations=effective_max_turns,
            repeat_threshold=3,
            stall_threshold=3,
            cache_enabled=True,
            cache_max_size=50,
            max_retries=1,
        ))

        system_prompt = build_system(agent_prompt=agent.prompt)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]

        # Orchestration runners may execute without going through the normal
        # session prompt bootstrap, so populate the process-local registry here
        # before deriving the tool schema.
        tool_registry.register_builtins()

        # Build the tool allow-list: honour agent.tools if declared,
        # otherwise full registry minus the subagent-excluded ones.
        all_tools = tool_registry.to_llm_tools()
        if agent.tools is not None:
            allowed = set(agent.tools)
            tools = [t for t in all_tools if t["function"]["name"] in allowed]
        else:
            tools = [t for t in all_tools if t["function"]["name"] not in _EXCLUDED_SUBAGENT_TOOLS]

        ctx = ToolContext(
            session_id=self._session_id,
            message_id=self._message_id,
            agent=agent.name,
        )

        output_parts: list[str] = []
        total_tool_calls = 0
        turn = 0

        for turn in range(effective_max_turns):
            verdict = guard.check(turn)
            if verdict.action.value in ("stop", "force_stop"):
                output_parts.append(f"\n\n(stopped by loop guard: {verdict.reason})")
                break

            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system_prompt,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                api_key=api_key,
                api_base=model.api.url or None,
            )

            text_parts: list[str] = []
            pending: list[llmmod.ToolCallDelta] = []
            finish = "stop"

            async for event in llmmod.stream(stream_input):
                if isinstance(event, llmmod.TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, llmmod.ToolCallDelta):
                    pending.append(event)
                elif isinstance(event, llmmod.FinishEvent):
                    finish = event.reason
                elif isinstance(event, llmmod.ErrorEvent):
                    return SpawnOutput(
                        agent=agent.name,
                        task=req.task,
                        output="".join(output_parts) + f"\n\nError: {event.error}",
                        is_error=True,
                        turns=turn + 1,
                        tool_calls=total_tool_calls,
                    )

            assistant_text = "".join(text_parts)
            if assistant_text:
                output_parts.append(assistant_text)
                if req.events is not None:
                    await req.events.agent_message(
                        stage_id=req.stage_id,
                        spawn_index=req.spawn_index,
                        agent=agent.name,
                        role="assistant",
                        content=assistant_text,
                        turn=turn + 1,
                    )

            step = guard.begin_step(turn)
            guard.complete_step(step, text_length=len(assistant_text))

            if not pending or finish != "tool-calls":
                break

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": [
                    {
                        "id": tc.tool_call_id,
                        "type": "function",
                        "function": {"name": tc.tool_name, "arguments": tc.args},
                    }
                    for tc in pending
                ],
            }
            messages.append(assistant_msg)

            for tc in pending:
                total_tool_calls += 1
                perm_error = check_tool_permission(tc.tool_name, agent_ruleset)
                if perm_error:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "content": perm_error,
                    })
                    continue

                tool_impl = tool_registry.get(tc.tool_name)
                if tool_impl is None:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "content": f"Unknown tool: {tc.tool_name}",
                    })
                    continue

                try:
                    tool_args = json.loads(tc.args) if tc.args and tc.args.strip() else {}
                except json.JSONDecodeError as exc:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "content": f"Invalid JSON arguments: {exc}",
                    })
                    continue

                try:
                    result = await tool_impl.execute(tool_args, ctx)
                    tool_output = result.output
                except Exception as exc:  # noqa: BLE001 — tool errors must not kill the run
                    tool_output = f"Error: {exc}"

                if req.events is not None:
                    await req.events.agent_tool(
                        stage_id=req.stage_id,
                        spawn_index=req.spawn_index,
                        agent=agent.name,
                        tool_name=tc.tool_name,
                        args_preview=tc.args or "",
                        output_preview=tool_output,
                        turn=turn + 1,
                    )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.tool_call_id,
                    "content": tool_output,
                })

        return SpawnOutput(
            agent=agent.name,
            task=req.task,
            output="".join(output_parts) or "(no output)",
            is_error=False,
            turns=min(turn + 1, effective_max_turns),
            tool_calls=total_tool_calls,
        )
