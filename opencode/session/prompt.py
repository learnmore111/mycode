"""Session prompt — message sending entry point and agentic loop orchestration.

Flow: validate → create messages → build system prompt → load tools → run agentic loop

Features:
- Three-layer loop guard (hard limit, pattern detection, near-limit intelligence)
- Per-step atomic state with checkpoint data
- Result caching for read-only tool deduplication
- Guard verdict events for CLI display
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opencode.agent import agent as agentmod
from opencode.bus.events import SESSION_ERROR
from opencode.provider import provider as providermod
from opencode.session import compaction
from opencode.session import llm as llmmod
from opencode.session import processor as proc
from opencode.session.context import build_context_snapshot
from opencode.session.loop_guard import GuardAction, LoopGuard, LoopGuardConfig
from opencode.session.message import (
    Part,
    ToolPart,
    create_assistant_message,
    create_text_part,
    create_user_message,
    get_last_assistant_time,
    persist_turn,
    save_message,
    save_part,
)
from opencode.session.system import build as build_system
from opencode.tool import registry as tool_registry
from opencode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from opencode.bus.bus import Bus

logger = logmod.create(service="session.prompt")

# Track busy sessions — use asyncio.Lock per session for TOCTOU safety
import asyncio as _aio  # noqa: E402  # noqa: E402

_session_locks: dict[str, _aio.Lock] = {}
_locks_mutex = _aio.Lock()


def is_session_busy(session_id: str) -> bool:
    """Check if a session is currently being processed."""
    lock = _session_locks.get(session_id)
    return lock is not None and lock.locked()


async def _acquire_session(session_id: str) -> bool:
    """Try to acquire a session for processing.

    Returns True if acquired, False if already busy.
    Uses asyncio.Lock to prevent TOCTOU race conditions.
    The locked() check and acquire() are both inside _locks_mutex
    to prevent two coroutines from passing the check simultaneously.
    """
    async with _locks_mutex:
        if session_id not in _session_locks:
            _session_locks[session_id] = _aio.Lock()
        lock = _session_locks[session_id]

        if lock.locked():
            return False
        await lock.acquire()
        logger.debug("session acquired", session_id=session_id)
        return True


def _release_session(session_id: str) -> None:
    """Release a session after processing."""
    lock = _session_locks.get(session_id)
    if lock and lock.locked():
        lock.release()
        logger.debug("session released", session_id=session_id)

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
      - "context_snapshot": Full context snapshot before LLM call. data has system/tools/messages/summary.
      - "done":       All iterations finished. data has tokens/cost/context stats.
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)


async def prompt(
    prompt_input: PromptInput,
    bus: Bus,
    *,
    history: list[dict[str, Any]] | None = None,
    debug: bool = False,
) -> AsyncGenerator[PromptEvent, None]:
    """Send a message and stream the AI response.

    This is the main entry point for the agentic loop.
    Yields PromptEvent in real-time as the model generates text and executes tools.
    """
    session_id = prompt_input.session_id

    if not await _acquire_session(session_id):
        yield PromptEvent(type="error", data={"message": f"Session {session_id} is busy"})
        return

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

        # Persist the exact system prompt used for this turn so history views
        # can reconstruct the context window without depending on frontend guesses.
        assistant_system = list(system)

        # Memory and skills are now injected as system-reminder messages (not in system prompt)
        # This keeps the system prompt static for prefix cache reuse across sessions

        # Load tools
        tool_registry.register_builtins()
        tools = tool_registry.to_llm_tools()

        # Pre-compute system + tools token estimates for compaction checks and context bar fallback
        system_tokens_est = compaction.estimate_tokens("\n\n".join(system))
        tools_tokens_est = compaction.estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0

        # Build user message content
        user_text = ""
        for part in prompt_input.parts:
            if part.get("type") == "text":
                user_text += part.get("content", "")

        # Build conversation messages
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_text})

        # Proactive pruning: if cache likely expired since last interaction,
        # prune old tool outputs now to reduce re-fill cost on the next LLM call.
        if history:
            last_time = get_last_assistant_time(session_id)
            if compaction.is_cache_likely_expired(model, last_time):
                messages, freed = compaction.prune_tool_outputs(messages)
                if freed > 0:
                    logger.info("proactive cache-expiry prune", tokens_freed=freed)

        # Create assistant message
        user_msg = create_user_message(session_id, prompt_input.message_id)
        assistant_msg = create_assistant_message(
            session_id, user_msg.id, provider_id, model_id, agent_name,
        )
        assistant_msg.system = assistant_system

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

        # Pre-build compaction kwargs so both call sites share the same args
        compact_kwargs: dict[str, Any] = {
            "system": system,
            "tools": tools if model.capabilities.toolcall else None,
            "model": model,
            "api_key": await providermod.get_api_key(provider_id),
            "api_base": model.api.url or None,
        }

        all_parts: list[Part] = []
        iterations_done = 0
        stop_reason = ""
        prev_skills: list[dict[str, str]] | None = None
        prev_date: str | None = None
        prev_iter_usage: dict[str, int | float] | None = None  # Per-iteration usage from previous iteration

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
            # Fallback to 32K if model context limit is not configured (prevents unbounded growth)
            context_limit = model.limit.context if model.limit.context > 0 else 32_000
            if context_limit > 0 and compaction.should_compact(
                messages=messages,
                model_context=context_limit,
                system_tokens=system_tokens_est,
                tools_tokens=tools_tokens_est,
            ):
                logger.info("context overflow detected, compacting")
                messages, compact_metrics = await compaction.compact(messages, **compact_kwargs)
                yield PromptEvent(type="compact", data={
                    "session_id": session_id,
                    "old_message_count": compact_metrics.old_message_count,
                    "old_message_tokens": compact_metrics.old_message_tokens,
                    "summary_length": compact_metrics.summary_length,
                    "removed_turn_count": compact_metrics.removed_turn_count,
                })
                # Save compaction event for audit trail (background)
                from opencode.session.message import save_compaction_event
                def _save_compact_event() -> None:
                    save_compaction_event(
                        session_id=session_id,
                        iteration=iteration,
                        metrics={
                            'old_message_count': compact_metrics.old_message_count,
                            'old_message_tokens': compact_metrics.old_message_tokens,
                            'summary_length': compact_metrics.summary_length,
                            'removed_turn_count': compact_metrics.removed_turn_count,
                        },
                        old_messages=compact_metrics.old_messages,
                        summary=compact_metrics.summary,
                    )
                await _aio.to_thread(_save_compact_event)

            # Build system-reminder messages (skills + memory) — injected temporarily, not persisted
            reminder_text, prev_skills, prev_date = _build_system_reminders(prompt_input, prev_skills, prev_date)
            if reminder_text:
                iter_messages = list(messages)
                iter_messages.append({"role": "user", "content": reminder_text})
            else:
                iter_messages = messages

            stream_input = llmmod.StreamInput(
                model=model,
                messages=iter_messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                top_p=agent.top_p,
                api_key=await providermod.get_api_key(provider_id),
                api_base=model.api.url or None,
            )

            # === Snapshot cumulative tokens BEFORE this iteration ===
            # After process_stream, the delta = current - snapshot gives per-iteration usage.
            tokens_snap_input = assistant_msg.tokens_input
            tokens_snap_output = assistant_msg.tokens_output
            tokens_snap_cache_read = assistant_msg.tokens_cache_read
            tokens_snap_cache_write = assistant_msg.tokens_cache_write
            tokens_snap_reasoning = assistant_msg.tokens_reasoning

            # === Context snapshot for UI context viewer ===
            # actual_usage shows the PREVIOUS iteration's per-iteration values
            # (on iteration 0 there is no previous data, so None)
            snapshot_data = build_context_snapshot(
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                messages=iter_messages,
                model_id=f"{provider_id}/{model_id}",
                context_limit=model.limit.context if model.limit.context > 0 else 0,
                iteration=iteration,
                has_history=bool(history),
                actual_usage=prev_iter_usage,
            )
            yield PromptEvent(type="context_snapshot", data=snapshot_data)

            # === Debug: dump input before LLM call ===
            if debug:
                # Collect cached tool_call_ids from previous iteration's parts
                cached_call_ids = []
                if iteration > 0 and all_parts:
                    for p in all_parts:
                        if isinstance(p, ToolPart) and p.state.get("metadata", {}).get("cached", False):
                            cached_call_ids.append(p.tool_call_id)
                debug_file = _debug_dump(
                    session_id, iteration, "input",
                    messages=messages, system=system,
                    model=f"{provider_id}/{model_id}",
                    tool_count=len(tools) if tools else 0,
                    cached_tool_call_ids=cached_call_ids,
                )
                yield PromptEvent(type="debug_iter", data={
                    "iteration": iteration, "phase": "input",
                    "message_count": len(messages), "file": debug_file,
                })

            # Stream events from processor
            result: proc.Result = "stop"
            iteration_parts: list[Part] = []
            iter_text_length = 0
            iter_text_content = ""

            async for event in proc.process_stream(ctx, stream_input):
                if event.type == "text_delta":
                    iter_text_content += event.data.get("content", "")
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

            # === Debug: dump output after LLM call ===
            if debug:
                tool_outputs = []
                for p in iteration_parts:
                    if isinstance(p, ToolPart):
                        tool_outputs.append({
                            "tool": p.tool, "call_id": p.tool_call_id,
                            "input": p.state.get("input", {}),
                            "output": p.state.get("output", "")[:2000],
                            "status": p.state.get("status", "?"),
                            "cached": p.state.get("metadata", {}).get("cached", False),
                        })
                debug_file = _debug_dump(
                    session_id, iteration, "output",
                    text=iter_text_content, text_length=iter_text_length,
                    tool_calls=tool_outputs, result=result,
                )
                yield PromptEvent(type="debug_iter", data={
                    "iteration": iteration, "phase": "output",
                    "message_count": len(messages), "file": debug_file,
                })

            all_parts.extend(iteration_parts)

            # === Compute per-iteration token deltas ===
            iter_input_tokens = assistant_msg.tokens_input - tokens_snap_input
            prev_iter_usage = {
                "input_tokens": iter_input_tokens,
                "output_tokens": assistant_msg.tokens_output - tokens_snap_output,
                "cache_read_tokens": assistant_msg.tokens_cache_read - tokens_snap_cache_read,
                "cache_write_tokens": assistant_msg.tokens_cache_write - tokens_snap_cache_write,
                "reasoning_tokens": assistant_msg.tokens_reasoning - tokens_snap_reasoning,
                "total_cost": assistant_msg.cost,  # cumulative cost is still useful
            }

            if result == "stop":
                break

            if result == "continue":
                tool_messages = proc.build_tool_results_messages(iteration_parts)
                messages.extend(tool_messages)
                continue

        # Finalize — persist in background (don't block the done event)
        assistant_msg.time_completed = int(time.time() * 1000)

        # context.used = last iteration's input_tokens (= actual context window occupancy)
        # Fallback to heuristic estimate including system+tools if no API data available.
        last_iter_input = prev_iter_usage["input_tokens"] if prev_iter_usage else 0
        fallback_est = (
            compaction.estimate_messages_tokens(messages) + system_tokens_est + tools_tokens_est
        )

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
                "used": last_iter_input if last_iter_input > 0 else fallback_est,
                "limit": model.limit.context,
            },
            "iterations": iterations_done,
            "parts": len(all_parts),
            "messages": messages,
            "stop_reason": stop_reason,
            "checkpoint": guard.checkpoint,
        })

        # Persist after yielding done (user sees result immediately)

        # Save user message + text part, then assistant turn
        user_text_part = create_text_part(session_id, user_msg.id)
        user_text_part.content = user_text

        def _persist_all() -> None:
            save_message(user_msg)
            save_part(user_text_part)
            persist_turn(session_id, assistant_msg, all_parts)

        await _aio.to_thread(_persist_all)

    except Exception as e:
        logger.error("prompt failed", error=str(e))
        yield PromptEvent(type="error", data={"message": str(e)})
        await bus.publish(SESSION_ERROR, {"session_id": session_id, "error": {"message": str(e)}})
    finally:
        _release_session(session_id)


def _debug_dump(session_id: str, iteration: int, phase: str, **data: Any) -> str:
    """Write debug data to .opencode/debug/ as JSON files.

    Returns the file path for display.
    """
    import json
    from pathlib import Path

    debug_dir = Path(".opencode") / "debug" / session_id[:12]
    debug_dir.mkdir(parents=True, exist_ok=True)
    filename = f"iter{iteration:02d}_{phase}.json"
    filepath = debug_dir / filename

    # For messages, truncate long content to keep files readable
    dump_data = {"session_id": session_id, "iteration": iteration, "phase": phase, "timestamp": time.time()}
    for key, value in data.items():
        if key == "messages" and isinstance(value, list):
            # Truncate each message's content for readability
            truncated = []
            for msg in value:
                m = dict(msg)
                if isinstance(m.get("content"), str) and len(m["content"]) > 3000:
                    m["content"] = m["content"][:3000] + f"\n... ({len(msg['content'])} chars total)"
                truncated.append(m)
            dump_data[key] = truncated
        else:
            dump_data[key] = value

    try:
        filepath.write_text(
            json.dumps(dump_data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warn("debug dump failed", error=str(e))

    return str(filepath)


def _build_system_reminders(
    prompt_input: PromptInput,
    prev_skills: list[dict[str, str]] | None,
    prev_date: str | None,
) -> tuple[str, list[dict[str, str]], str]:
    """Build <system-reminder> content for skills, memory, and date.

    Returns (reminder_text, current_skills_snapshot, current_date).

    Skills use an incremental strategy:
    - First call (prev_skills is None) or modifications/deletions → full list
    - Only additions → only the new skills
    - No change → reuse previous text (empty skills section)

    Date uses an incremental strategy:
    - First call (prev_date is None) → full date
    - Date changed → date update reminder
    - No change → omit

    Memory is always included if found.
    """
    sections: list[str] = []

    # --- Skills section ---
    try:
        from opencode.tool.skill import list_skills_with_descriptions

        current_skills = list_skills_with_descriptions()
    except Exception:
        current_skills = []

    skills_text = _build_skills_reminder(current_skills, prev_skills)
    if skills_text:
        sections.append(skills_text)

    # --- Date section ---
    current_date = time.strftime("%A, %b %d, %Y")
    date_text = _build_date_reminder(current_date, prev_date)
    if date_text:
        sections.append(date_text)

    # --- Memory section ---
    memory_text = _build_memory_reminder(prompt_input)
    if memory_text:
        sections.append(memory_text)

    if not sections:
        return "", current_skills, current_date

    reminder = "\n".join(sections)
    return reminder, current_skills, current_date


def _build_skills_reminder(
    current: list[dict[str, str]],
    prev: list[dict[str, str]] | None,
) -> str:
    """Build the skills system-reminder section.

    Returns empty string if no skills exist or nothing changed.
    """
    if not current:
        return ""

    # First call — full list
    if prev is None:
        lines = ["<system-reminder>", "The following skills are available for use with the `skill` tool:", ""]
        for s in current:
            desc = s["description"]
            lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
        lines.append("</system-reminder>")
        return "\n".join(lines)

    # No change
    if current == prev:
        return ""

    # Check if purely additive (current is a superset of prev)
    prev_names = {s["name"] for s in prev}
    prev_map = {s["name"]: s["description"] for s in prev}
    current_map = {s["name"]: s["description"] for s in current}

    # Superset check: all prev items still present and unchanged
    is_superset = all(current_map.get(n) == d for n, d in prev_map.items())

    if is_superset:
        new_skills = [s for s in current if s["name"] not in prev_names]
        if not new_skills:
            return ""
        lines = ["<system-reminder>", "New skills available:", ""]
        for s in new_skills:
            desc = s["description"]
            lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
        lines.append("</system-reminder>")
        return "\n".join(lines)

    # Modification or deletion — full list
    lines = ["<system-reminder>", "The following skills are available for use with the `skill` tool:", ""]
    for s in current:
        desc = s["description"]
        lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
    lines.append("</system-reminder>")
    return "\n".join(lines)


def _build_date_reminder(current: str, prev: str | None) -> str:
    """Build the date system-reminder section.

    Incremental strategy:
    - First call (prev is None) → full date
    - Date changed → date update reminder
    - No change → empty string (omit)
    """
    if prev is None:
        return f"<system-reminder>\nToday's date: {current}\n</system-reminder>"
    if current != prev:
        return f"<system-reminder>\nDate has changed. Today's date is now: {current}\n</system-reminder>"
    return ""


def _build_memory_reminder(prompt_input: PromptInput) -> str:
    """Build the memory system-reminder section.

    Returns empty string if no relevant memories found.
    """
    try:
        from opencode.project.instance import current_or_none
        from opencode.session.memory.memdir import format_memories_for_context
        from opencode.session.memory.retrieval import find_relevant_memories

        inst = current_or_none()
        if not inst:
            return ""

        query = ""
        for part in prompt_input.parts:
            if part.get("type") == "text":
                query += part.get("content", "")

        if not query:
            return ""

        memories = find_relevant_memories(inst.directory, query, max_results=5)
        if not memories:
            return ""

        context = format_memories_for_context(memories, include_freshness=True)
        if context:
            return f"<system-reminder>\n<relevant_memories>\n{context}\n</relevant_memories>\n</system-reminder>"
    except Exception:
        pass  # Memory injection is best-effort
    return ""
