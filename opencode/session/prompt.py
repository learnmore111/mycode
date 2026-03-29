"""Session prompt — message sending entry point and agentic loop orchestration.

This is the core orchestrator equivalent to src/session/prompt.ts.
Flow: validate → create messages → build system prompt → load tools → run agentic loop
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator
from opencode.agent import agent as agentmod
from opencode.bus.bus import Bus
from opencode.bus.events import SESSION_UPDATED, SESSION_ERROR
from opencode.provider import provider as providermod
from opencode.session import llm as llmmod
from opencode.session import processor as proc
from opencode.session.message import (
    AssistantMessage, UserMessage, Part, TextPart, WithParts,
    create_user_message, create_assistant_message, create_text_part,
)
from opencode.session.session import SessionInfo, BusyError, get as get_session, touch
from opencode.session.system import build as build_system
from opencode.tool import registry as tool_registry
from opencode.util import log as logmod

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
    input: PromptInput,
    bus: Bus,
    *,
    history: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[PromptEvent, None]:
    """Send a message and stream the AI response.

    This is the main entry point for the agentic loop.
    Yields PromptEvent as the model generates responses and executes tools.
    """
    session_id = input.session_id

    # 1. Check busy
    if _busy.get(session_id):
        yield PromptEvent(type="error", data={"message": f"Session {session_id} is busy"})
        return

    _busy[session_id] = True
    try:
        # 2. Resolve model
        if input.model:
            provider_id, model_id = providermod.parse_model(input.model)
        else:
            provider_id, model_id = await providermod.default_model()

        try:
            model = await providermod.get_model(provider_id, model_id)
        except Exception as e:
            yield PromptEvent(type="error", data={"message": f"Model not found: {e}"})
            return

        # 3. Resolve agent
        agent_name = input.agent or await agentmod.default_agent()
        agent = await agentmod.get(agent_name)
        if not agent:
            yield PromptEvent(type="error", data={"message": f"Agent not found: {agent_name}"})
            return

        # 4. Build system prompt
        system = build_system(agent_prompt=agent.prompt, instructions=None)
        if input.system:
            system.append(input.system)

        # 5. Load tools
        tool_registry.register_builtins()
        tools = tool_registry.to_llm_tools()

        # 6. Build user message content
        user_text = ""
        for part in input.parts:
            if part.get("type") == "text":
                user_text += part.get("content", "")

        # 7. Build conversation messages
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_text})

        # 8. Create assistant message
        user_msg = create_user_message(session_id, input.message_id)
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

        for iteration in range(max_iterations):
            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                top_p=agent.top_p,
                api_key=providermod._state[provider_id].key if providermod._state and provider_id in providermod._state else None,
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
                logger.info("compaction requested")
                break

        # 10. Finalize
        assistant_msg.time_completed = int(time.time() * 1000)
        touch(session_id)

        yield PromptEvent(type="done", data={
            "session_id": session_id,
            "tokens": {
                "input": assistant_msg.tokens_input,
                "output": assistant_msg.tokens_output,
            },
            "iterations": min(iteration + 1, max_iterations) if 'iteration' in dir() else 1,
            "parts": len(all_parts),
        })

    except Exception as e:
        logger.error("prompt failed", error=str(e))
        yield PromptEvent(type="error", data={"message": str(e)})
        await bus.publish(SESSION_ERROR, {"session_id": session_id, "error": {"message": str(e)}})
    finally:
        _busy.pop(session_id, None)
