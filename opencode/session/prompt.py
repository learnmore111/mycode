"""Session prompt — message sending entry point and agentic loop orchestration.

This is the core orchestrator equivalent to src/session/prompt.ts.
Flow: validate → create messages → build system prompt → load tools → run agentic loop
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
    type: str  # "text", "tool_start", "tool_end", "done", "error"
    data: dict[str, Any] = field(default_factory=dict)


async def prompt(
    prompt_input: PromptInput,
    bus: Bus,
    *,
    history: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[PromptEvent, None]:
    """Send a message and stream the AI response.

    This is the main entry point for the agentic loop.
    Yields PromptEvent as the model generates responses and executes tools.
    """
    session_id = prompt_input.session_id

    # 1. Check busy
    if _busy.get(session_id):
        yield PromptEvent(type="error", data={"message": f"Session {session_id} is busy"})
        return

    _busy[session_id] = True
    try:
        # 2. Resolve model
        if prompt_input.model:
            provider_id, model_id = providermod.parse_model(prompt_input.model)
        else:
            provider_id, model_id = await providermod.default_model()

        try:
            model = await providermod.get_model(provider_id, model_id)
        except Exception as e:
            yield PromptEvent(type="error", data={"message": f"Model not found: {e}"})
            return

        # 3. Resolve agent
        agent_name = prompt_input.agent or await agentmod.default_agent()
        agent = await agentmod.get(agent_name)
        if not agent:
            yield PromptEvent(type="error", data={"message": f"Agent not found: {agent_name}"})
            return

        # 4. Build system prompt
        system = build_system(model=model, agent_prompt=agent.prompt, instructions=None)
        if prompt_input.system:
            system.append(prompt_input.system)

        # 5. Load tools
        tool_registry.register_builtins()
        tools = tool_registry.to_llm_tools()

        # 6. Build user message content
        user_text = ""
        for part in prompt_input.parts:
            if part.get("type") == "text":
                user_text += part.get("content", "")

        # 7. Build conversation messages
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_text})

        # 8. Create assistant message
        user_msg = create_user_message(session_id, prompt_input.message_id)
        assistant_msg = create_assistant_message(
            session_id, user_msg.id, provider_id, model_id, agent_name,
        )

        yield PromptEvent(type="started", data={
            "session_id": session_id,
            "model": f"{provider_id}/{model_id}",
            "agent": agent_name,
        })

        # 9. Agentic loop
        ctx = proc.ProcessorContext(
            session_id=session_id,
            model=model,
            assistant_message=assistant_msg,
            bus=bus,
        )

        all_parts: list[Part] = []
        max_iterations = agent.steps or 50
        iterations_done = 0

        for iteration in range(max_iterations):
            iterations_done = iteration + 1

            # Check if context needs compaction before sending to LLM
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

            result, parts = await proc.process(ctx, stream_input)
            all_parts.extend(parts)

            # Yield events for each part
            for part in parts:
                if isinstance(part, TextPart):
                    yield PromptEvent(type="text", data={"content": part.content, "part_id": part.id})
                elif hasattr(part, "tool"):
                    yield PromptEvent(type="tool", data={
                        "tool": part.tool, "call_id": part.tool_call_id,
                        "status": part.state.get("status", "unknown"),
                        "input": part.state.get("input", {}),
                        "output": part.state.get("output", "")[:500],
                    })

            if result == "stop":
                break

            if result == "continue":
                # Add tool results to messages for next iteration
                tool_messages = proc.build_tool_results_messages(parts)
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

        # 10. Finalize — persist assistant message and parts
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
        })

    except Exception as e:
        logger.error("prompt failed", error=str(e))
        yield PromptEvent(type="error", data={"message": str(e)})
        await bus.publish(SESSION_ERROR, {"session_id": session_id, "error": {"message": str(e)}})
    finally:
        _busy.pop(session_id, None)
