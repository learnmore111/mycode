"""Session prompt — message sending entry point and agentic loop orchestration.

This is the core orchestrator equivalent to src/session/prompt.ts.
Flow: validate → create messages → build system prompt → load tools → run agentic loop

Enhanced with:
- Three-layer loop guard (hard limit, pattern detection, near-limit intelligence)
- Per-step atomic state with checkpoint data
- Result caching for read-only tool deduplication
- Guard verdict events for CLI display
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opencode.agent import agent as agentmod
from opencode.bus.events import SESSION_ERROR
from opencode.provider import provider as providermod
from opencode.session import compaction
from opencode.session import llm as llmmod
from opencode.session import processor as proc
from opencode.session.loop_guard import GuardAction, LoopGuard, LoopGuardConfig
from opencode.session.message import (
    Part,
    TextPart,
    create_assistant_message,
    create_user_message,
    save_message,
    save_parts,
)
from opencode.session.session import touch
from opencode.session.system import build as build_system
from opencode.tool import registry as tool_registry
from opencode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from opencode.bus.bus import Bus

logger = logmod.create(service="session.prompt")

# Track busy sessions
_busy: dict[str, bool] = {}

@dataclass
class PromptInput:
    session_id: str
    parts: list[dict[str, Any]]
    model: str | None = None  # "provider/model"
    agent: str | None = None
    message_id: str | None = None
    variant: str | None = None
    system: str | None = None

@dataclass
class PromptEvent:
    """Event yielded to the CLI layer.

    Types:
      - "started":    Session started. data has model/agent info.
      - "text_delta": Incremental text from LLM. data["content"] is the delta.
      - "tool_start": A tool call identified. data["tool"], data["call_id"].
      - "tool_running": Tool execution started. data["tool"], data["call_id"], data["input"].
      - "tool_done":  Tool finished. data["tool"], data["status"], data["output"], data["input"].
      - "error":      Error occurred. data["message"].
      - "compact":    Context compaction in progress.
      - "guard_warn": Loop guard warning. data["reason"], data["layer"].
      - "guard_stop": Loop guard stopped the loop. data["reason"], data["layer"].
      - "done":       All iterations finished. data has tokens/cost/context stats.
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)


async def prompt(
    prompt_input: PromptInput,
    bus: Bus,
    *,
    history: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[PromptEvent, None]:
    """Send a message and stream the AI response.

    This is the main entry point for the agentic loop.
    Yields PromptEvent in real-time as the model generates text and executes tools.
    """
    session_id = prompt_input.session_id

    if _busy.get(session_id):
        yield PromptEvent(type="error", data={"message": f"Session {session_id} is busy"})
        return

    _busy[session_id] = True
    try:
        # Resolve model
        if prompt_input.model:
            provider_id, model_id = providermod.parse_model(prompt_input.model)
        else:
            provider_id, model_id = await providermod.default_model()

        try:
            model = await providermod.get_model(provider_id, model_id)
        except Exception as e:
            yield PromptEvent(type="error", data={"message": f"Model not found: {e}"})
            return

        # Resolve agent
        agent_name = prompt_input.agent or await agentmod.default_agent()
        agent = await agentmod.get(agent_name)
        if not agent:
            yield PromptEvent(type="error", data={"message": f"Agent not found: {agent_name}"})
            return

        # Build system prompt
        system = build_system(model=model, agent_prompt=agent.prompt, instructions=None)
        if prompt_input.system:
            system.append(prompt_input.system)

        # Load tools
        tool_registry.register_builtins()
        tools = tool_registry.to_llm_tools()

        # Build user message content
        user_text = ""
        for part in prompt_input.parts:
            if part.get("type") == "text":
                user_text += part.get("content", "")

        # Build conversation messages
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_text})

        # Create assistant message
        user_msg = create_user_message(session_id, prompt_input.message_id)
        assistant_msg = create_assistant_message(
            session_id, user_msg.id, provider_id, model_id, agent_name,
        )

        yield PromptEvent(type="started", data={
            "session_id": session_id,
            "model": f"{provider_id}/{model_id}",
            "agent": agent_name,
        })

        # Initialize loop guard with three-layer protection
        max_iterations = agent.steps or 50
        guard_config = LoopGuardConfig(max_iterations=max_iterations)
        guard = LoopGuard(config=guard_config)

        # Agentic loop with loop guard
        ctx = proc.ProcessorContext(
            session_id=session_id,
            model=model,
            assistant_message=assistant_msg,
            bus=bus,
            loop_guard=guard,
        )

        all_parts: list[Part] = []
        iterations_done = 0
        stop_reason = ""

        for iteration in range(max_iterations):
            iterations_done = iteration + 1

            # === Loop Guard Check (BEFORE each iteration) ===
            verdict = guard.check(iteration)

            if verdict.action == GuardAction.FORCE_STOP:
                logger.warn("guard force stop", reason=verdict.reason, layer=verdict.layer)
                yield PromptEvent(type="guard_stop", data={
                    "reason": verdict.reason, "layer": verdict.layer,
                })
                stop_reason = verdict.reason
                break

            if verdict.action == GuardAction.STOP:
                logger.info("guard stop", reason=verdict.reason, layer=verdict.layer)
                yield PromptEvent(type="guard_stop", data={
                    "reason": verdict.reason, "layer": verdict.layer,
                })
                stop_reason = verdict.reason
                break

            if verdict.action == GuardAction.WARN:
                logger.info("guard warn", reason=verdict.reason, layer=verdict.layer)
                yield PromptEvent(type="guard_warn", data={
                    "reason": verdict.reason, "layer": verdict.layer,
                })

            # === Step begin (atomic state) ===
            step = guard.begin_step(iteration)

            # Context compaction check
            context_limit = model.limit.context if model.limit.context > 0 else 0
            if context_limit > 0 and compaction.should_compact(
                messages=messages, model_context=context_limit
            ):
                logger.info("context overflow detected, compacting")
                api_key = await providermod.get_api_key(provider_id)
                model_name = providermod.litellm_model_name(model)
                messages = await compaction.compact(
                    messages, model_name=model_name, api_key=api_key,
                )
                yield PromptEvent(type="compact", data={"session_id": session_id})

            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                top_p=agent.top_p,
                api_key=await providermod.get_api_key(provider_id),
                api_base=model.api.url or None,
            )

            # Stream events from processor
            result: proc.Result = "stop"
            iteration_parts: list[Part] = []
            iter_text_length = 0

            async for event in proc.process_stream(ctx, stream_input):
                if event.type == "text_delta":
                    yield PromptEvent(type="text_delta", data=event.data)

                elif event.type == "tool_start":
                    yield PromptEvent(type="tool_start", data=event.data)

                elif event.type == "tool_running":
                    yield PromptEvent(type="tool_running", data=event.data)

                elif event.type == "tool_done":
                    yield PromptEvent(type="tool_done", data=event.data)

                elif event.type == "error":
                    yield PromptEvent(type="error", data=event.data)
                    step.fail(event.data.get("message", "unknown"))

                elif event.type == "finish":
                    result = event.data.get("result", "stop")
                    iteration_parts = event.data.get("parts", [])
                    iter_text_length = event.data.get("text_length", 0)

            # === Step complete ===
            if step.status.value != "failed":
                guard.complete_step(step, text_length=iter_text_length)
                # Record tool calls for this step
                for p in iteration_parts:
                    if isinstance(p, ToolPart):
                        step.tool_calls.append({
                            "tool": p.tool,
                            "status": p.state.get("status", "?"),
                            "cached": p.state.get("metadata", {}).get("cached", False),
                        })

            all_parts.extend(iteration_parts)

            if result == "stop":
                break

            if result == "continue":
                tool_messages = proc.build_tool_results_messages(iteration_parts)
                messages.extend(tool_messages)
                continue

            if result == "compact":
                logger.info("compaction requested by processor")
                api_key = await providermod.get_api_key(provider_id)
                model_name = providermod.litellm_model_name(model)
                messages = await compaction.compact(
                    messages, model_name=model_name, api_key=api_key,
                )
                yield PromptEvent(type="compact", data={"session_id": session_id})
                continue

        # Finalize
        assistant_msg.time_completed = int(time.time() * 1000)
        save_message(assistant_msg)
        save_parts(all_parts)
        touch(session_id)

        yield PromptEvent(type="done", data={
            "session_id": session_id,
            "tokens": {
                "input": assistant_msg.tokens_input,
                "output": assistant_msg.tokens_output,
                "reasoning": assistant_msg.tokens_reasoning,
                "cache_read": assistant_msg.tokens_cache_read,
                "cache_write": assistant_msg.tokens_cache_write,
            },
            "cost": assistant_msg.cost,
            "context": {
                "used": assistant_msg.tokens_input + assistant_msg.tokens_output,
                "limit": model.limit.context,
            },
            "iterations": iterations_done,
            "parts": len(all_parts),
            "messages": messages,
            "stop_reason": stop_reason,
            "checkpoint": guard.checkpoint,
        })

    except Exception as e:
        logger.error("prompt failed", error=str(e))
        yield PromptEvent(type="error", data={"message": str(e)})
        await bus.publish(SESSION_ERROR, {"session_id": session_id, "error": {"message": str(e)}})
    finally:
        _busy.pop(session_id, None)
