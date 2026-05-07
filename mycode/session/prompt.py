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

from mycode.agent import agent as agentmod
from mycode.bus.events import SESSION_ERROR
from mycode.permission.permission import PermissionManager
from mycode.permission.schema import Rule
from mycode.provider import provider as providermod
from mycode.session import compaction
from mycode.session import llm as llmmod
from mycode.session import processor as proc
from mycode.session.context import build_context_snapshot
from mycode.session.loop_guard import GuardAction, LoopGuard, LoopGuardConfig
from mycode.session.message import (
    Part,
    ToolPart,
    create_assistant_message,
    create_file_part,
    create_text_part,
    create_user_message,
    get_last_assistant_time,
    persist_turn,
    save_message,
    save_part,
)
from mycode.session.system import build as build_system
from mycode.tool import registry as tool_registry
from mycode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.bus.bus import Bus

logger = logmod.create(service="session.prompt")

# Track busy sessions — use asyncio.Lock per session for TOCTOU safety
import asyncio as _aio  # noqa: E402  # noqa: E402

_session_locks: dict[str, _aio.Lock] = {}
_locks_mutex = _aio.Lock()
# Tracks last-use timestamp for stale lock garbage collection.
_session_lock_last_used: dict[str, float] = {}
# Idle locks older than this are swept from _session_locks.
_SESSION_LOCK_GC_IDLE_SECONDS = 3600.0


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _language_alignment_instruction(user_text: str) -> str | None:
    """Return a lightweight language instruction derived from the user's text.

    We only add this when the user's current turn is clearly Chinese, so
    the base provider prompt can stay stable for other languages.
    """
    if not user_text.strip():
        return None
    if _contains_cjk(user_text):
        return (
            "The user is writing in Chinese. Reply in Chinese. "
            "If you explicitly surface reasoning or thinking content to the user, "
            "write that surfaced content in Chinese as well. "
            "Prefer Simplified Chinese unless the user indicates otherwise."
        )
    return None


def is_session_busy(session_id: str) -> bool:
    """Check if a session is currently being processed."""
    lock = _session_locks.get(session_id)
    return lock is not None and lock.locked()


def _gc_session_locks_locked() -> None:
    """Drop idle, unlocked session-lock entries. Must be called with _locks_mutex held."""
    now = time.time()
    stale: list[str] = []
    for sid, last in list(_session_lock_last_used.items()):
        if now - last < _SESSION_LOCK_GC_IDLE_SECONDS:
            continue
        lock = _session_locks.get(sid)
        if lock is None or not lock.locked():
            stale.append(sid)
    for sid in stale:
        _session_locks.pop(sid, None)
        _session_lock_last_used.pop(sid, None)


async def _acquire_session(session_id: str) -> bool:
    """Try to acquire a session for processing.

    Returns True if acquired, False if already busy.
    Uses asyncio.Lock to prevent TOCTOU race conditions.
    The locked() check and acquire() are both inside _locks_mutex
    to prevent two coroutines from passing the check simultaneously.
    """
    async with _locks_mutex:
        _gc_session_locks_locked()
        if session_id not in _session_locks:
            _session_locks[session_id] = _aio.Lock()
        lock = _session_locks[session_id]

        if lock.locked():
            return False
        await lock.acquire()
        _session_lock_last_used[session_id] = time.time()
        logger.debug("session acquired", session_id=session_id)
        return True


def _release_session(session_id: str) -> None:
    """Release a session after processing. Safe to call multiple times."""
    lock = _session_locks.get(session_id)
    if lock is None:
        return
    if lock.locked():
        try:
            lock.release()
            logger.debug("session released", session_id=session_id)
        except RuntimeError:
            # Lock already released (idempotent guard for cleanup paths).
            logger.debug("session release ignored — already released", session_id=session_id)
    _session_lock_last_used[session_id] = time.time()

@dataclass
class PromptInput:
    session_id: str
    parts: list[dict[str, Any]]
    model: str | None = None  # "provider/model"
    agent: str | None = None
    message_id: str | None = None
    variant: str | None = None
    system: str | None = None
    # Optional abort signal. When set, both the agentic loop and the
    # underlying llm.stream() watch it and bail out within one chunk
    # rather than waiting for the current LLM response to finish.
    abort_event: _aio.Event | None = None

@dataclass
class PromptEvent:
    """Event yielded to the CLI layer.

    Types:
      - "started":    Session started. data has model/agent info.
      - "reasoning_delta": Incremental thinking text from LLM. data["content"] is the delta.
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
    permission_manager: PermissionManager | None = None,
) -> AsyncGenerator[PromptEvent, None]:
    """Send a message and stream the AI response.

    This is the main entry point for the agentic loop.
    Yields PromptEvent in real-time as the model generates text and executes tools.

    Args:
        permission_manager: External PermissionManager instance. When provided
            (e.g. by the HTTP server), permission "ask" requests are published
            through this manager so the frontend can reply. When None, a local
            instance is created (suitable for CLI or headless mode).
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

        # Memory and skills are now injected as system-reminder messages (not in system prompt)
        # This keeps the system prompt static for prefix cache reuse across sessions

        # Load tools
        tool_registry.register_builtins()
        tools = tool_registry.to_llm_tools()

        # Pre-compute system + tools token estimates for compaction checks
        # and the context bar fallback. The cached variant avoids redoing
        # a ~80KB JSON-to-bytes encode every turn; the payload changes only
        # when the agent prompt or tool registry changes.
        system_tokens_est = compaction.estimate_tokens_cached("\n\n".join(system))
        tools_tokens_est = (
            compaction.estimate_tokens_cached(json.dumps(tools, ensure_ascii=False))
            if tools else 0
        )

        # Build user message content.
        #
        # Backward compatible: if all parts are plain text we keep the
        # legacy string form. If any part is an image / pdf / audio we
        # emit the OpenAI-compatible content-list used by LLM providers
        # that accept multimodal input (litellm normalises the shape
        # downstream for Anthropic/Gemini).
        user_text = ""
        attachment_parts: list[dict[str, Any]] = []
        for part in prompt_input.parts:
            ptype = part.get("type")
            if ptype == "text":
                user_text += part.get("content", "")
            elif ptype == "image":
                if not getattr(model.capabilities.input, "image", False):
                    yield PromptEvent(type="error", data={
                        "message": f"Model {model_id} does not accept image input; drop the attachment or switch model.",
                        "code": "bad_request",
                        "retryable": False,
                    })
                    return
                url = _normalize_image_url(part)
                if url:
                    attachment_parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    logger.warn("dropping image part with no usable content", keys=list(part.keys()))
            elif ptype in ("pdf", "file"):
                if not getattr(model.capabilities.input, "pdf", False):
                    yield PromptEvent(type="error", data={
                        "message": f"Model {model_id} does not accept PDF input.",
                        "code": "bad_request",
                        "retryable": False,
                    })
                    return
                url = _normalize_image_url(part)  # same URL/base64 handling
                if url:
                    attachment_parts.append({"type": "file", "file": {"file_data": url}})
            elif ptype == "audio":
                if not getattr(model.capabilities.input, "audio", False):
                    yield PromptEvent(type="error", data={
                        "message": f"Model {model_id} does not accept audio input.",
                        "code": "bad_request",
                        "retryable": False,
                    })
                    return
                url = _normalize_image_url(part)
                if url:
                    attachment_parts.append({"type": "input_audio", "input_audio": {"data": url}})

        user_content: Any
        if attachment_parts:
            content_list: list[dict[str, Any]] = []
            if user_text:
                content_list.append({"type": "text", "text": user_text})
            content_list.extend(attachment_parts)
            user_content = content_list
        else:
            user_content = user_text

        language_instruction = _language_alignment_instruction(user_text)
        if language_instruction:
            system.append(language_instruction)

        # Persist the exact system prompt used for this turn so history views
        # can reconstruct the context window without depending on frontend guesses.
        assistant_system = list(system)

        # Build conversation messages
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_content})

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
        # Tag this turn so the rollback API can identify truncation points.
        # ``next_turn_number`` is a DB lookup, not free — but it only runs
        # once per prompt() call.
        try:
            from mycode.session.message import next_turn_number
            assistant_msg.turn_number = next_turn_number(session_id)  # type: ignore[attr-defined]
        except Exception:
            logger.debug("turn_number lookup failed; continuing without tag", session_id=session_id)

        yield PromptEvent(type="started", data={
            "session_id": session_id,
            "model": f"{provider_id}/{model_id}",
            "agent": agent_name,
        })

        # Initialize loop guard with three-layer protection
        max_iterations = agent.steps or 50
        guard_config = LoopGuardConfig(max_iterations=max_iterations)
        guard = LoopGuard(config=guard_config)

        # Build permission manager and agent permission ruleset
        perm_manager = permission_manager or PermissionManager(bus, project_id=session_id)
        agent_ruleset: list[Rule] = [
            Rule(
                permission=r.get("permission", "*"),
                pattern=r.get("pattern", "*"),
                action=r.get("action", "ask"),
            )
            for r in (agent.permission or [])
        ]

        # Agentic loop with loop guard
        ctx = proc.ProcessorContext(
            session_id=session_id,
            model=model,
            assistant_message=assistant_msg,
            bus=bus,
            loop_guard=guard,
            permission_manager=perm_manager,
            agent_permission=agent_ruleset,
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
        prev_iter_usage: dict[str, int | float] | None = None  # Per-iteration usage from previous iteration
        _reminder_user_messages: list[dict[str, Any]] = []  # system-reminder dicts to persist

        # Initialize incremental reminder state from history so we don't
        # re-send the full skills/date reminder on every new prompt() call
        # when the session already contains previous reminders.
        prev_skills, prev_date = _extract_reminder_state_from_history(history)

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
                from mycode.session.message import save_compaction_event
                def _save_compact_event(
                    _sid: str = session_id,
                    _iter: int = iteration,
                    _metrics: object = compact_metrics,
                ) -> None:
                    save_compaction_event(
                        session_id=_sid,
                        iteration=_iter,
                        metrics={
                            'old_message_count': _metrics.old_message_count,  # type: ignore[attr-defined]
                            'old_message_tokens': _metrics.old_message_tokens,  # type: ignore[attr-defined]
                            'summary_length': _metrics.summary_length,  # type: ignore[attr-defined]
                            'removed_turn_count': _metrics.removed_turn_count,  # type: ignore[attr-defined]
                        },
                        old_messages=_metrics.old_messages,  # type: ignore[attr-defined]
                        summary=_metrics.summary,  # type: ignore[attr-defined]
                    )
                await _aio.to_thread(_save_compact_event)

            # Build system-reminder (skills + memory + date) and attach it
            # to the current user message instead of injecting a separate message.
            # This keeps ContextViewer clean and avoids visual separation.
            # Still track the reminder dict for DB persistence as a meta message.
            reminder_text, prev_skills, prev_date = _build_system_reminders(prompt_input, prev_skills, prev_date)
            if reminder_text:
                _attach_reminder_to_last_user_message(messages, reminder_text)
                _reminder_user_messages.append({"content": reminder_text})
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
                abort_event=prompt_input.abort_event,
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
                if event.type == "reasoning_delta":
                    yield PromptEvent(type="reasoning_delta", data=event.data)

                elif event.type == "text_delta":
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

            # Token estimation telemetry — compare heuristic vs actual API usage
            if iter_input_tokens > 0:
                est = (
                    compaction.estimate_messages_tokens(iter_messages)
                    + system_tokens_est
                    + tools_tokens_est
                )
                compaction.log_token_accuracy(est, iter_input_tokens, f"{provider_id}/{model_id}")

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
            "raw_usage": getattr(assistant_msg, "raw_usage", None),
        })

        # Persist after yielding done (user sees result immediately)

        # Save user message + text part + attachment file parts, then assistant turn
        user_text_part = create_text_part(session_id, user_msg.id)
        user_text_part.content = user_text

        # Create FileParts for each attachment so they survive rebuild_history
        user_file_parts: list[Part] = []
        for part in prompt_input.parts:
            ptype = part.get("type")
            if ptype in ("image", "pdf", "file", "audio"):
                mime = part.get("mime") or part.get("mime_type") or ""
                content_val = part.get("content") or part.get("url") or ""
                fname = part.get("filename") or part.get("name") or ""
                fp = create_file_part(
                    session_id,
                    user_msg.id,
                    mime_type=mime,
                    content=content_val,
                    filename=fname,
                )
                user_file_parts.append(fp)

        # Prepare system-reminder user messages for persistence
        reminder_persist: list[tuple[Any, Any]] = []
        for rmd in _reminder_user_messages:
            r_msg = create_user_message(session_id, is_meta=True, origin="system")
            r_part = create_text_part(session_id, r_msg.id)
            r_part.content = rmd["content"]
            reminder_persist.append((r_msg, r_part))

        def _persist_all() -> None:
            save_message(user_msg)
            save_part(user_text_part)
            for fp in user_file_parts:
                save_part(fp)
            for r_msg, r_part in reminder_persist:
                save_message(r_msg)
                save_part(r_part)
            persist_turn(session_id, assistant_msg, all_parts)

        await _aio.to_thread(_persist_all)

    except Exception as e:
        logger.error("prompt failed", error=str(e))
        yield PromptEvent(type="error", data={"message": str(e)})
        await bus.publish(SESSION_ERROR, {"session_id": session_id, "error": {"message": str(e)}})
    finally:
        _release_session(session_id)


def _normalize_image_url(part: dict[str, Any]) -> str | None:
    """Coerce a client-supplied image part into an ``image_url`` URL.

    Accepts three input shapes:
      1) ``{"type": "image", "url": "https://…"}``              — passthrough
      2) ``{"type": "image", "content": "data:image/…;base64,…"}`` — passthrough
      3) ``{"type": "image", "content": "<raw-b64>", "mime": "image/png"}``
         — wrap as ``data:<mime>;base64,<raw>`` so providers accept it.
    Returns None if no usable payload is present. We deliberately do not
    validate the base64 — litellm / the provider will reject bad data
    with a specific error that is more actionable than ours.
    """
    url = part.get("url")
    if isinstance(url, str) and url:
        return url
    content = part.get("content")
    if not isinstance(content, str) or not content:
        return None
    if content.startswith("data:") or content.startswith(("http://", "https://")):
        return content
    mime = part.get("mime") or part.get("mime_type") or "image/png"
    return f"data:{mime};base64,{content}"


def _debug_dump(session_id: str, iteration: int, phase: str, **data: Any) -> str:
    """Write debug data to .mycode/debug/ as JSON files.

    Returns the file path for display.
    """
    import json
    from pathlib import Path

    debug_dir = Path(".mycode") / "debug" / session_id[:12]
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


def _extract_reminder_state_from_history(
    history: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, str]] | None, str | None]:
    """Scan history for previously persisted system-reminder messages.

    Returns ``(prev_skills, prev_date)`` extracted from the **last** reminder
    that contained each section.  If the history has no reminders at all,
    returns ``(None, None)`` so that ``_build_system_reminders`` treats the
    next call as the initial (full) send.
    """
    if not history:
        return None, None

    import re

    prev_skills: list[dict[str, str]] | None = None
    prev_date: str | None = None

    # Walk history in order; later reminders overwrite earlier ones.
    for msg in history:
        if msg.get("role") != "user":
            continue
        content: str = msg.get("content") or ""
        if "<system-reminder>" not in content:
            continue

        # --- Extract skills ---
        # Full list format: "The following skills are available ..."
        # Incremental format: "New skills available:"
        # Both list skills as "- name: description" lines.
        # We look for the *full list* pattern to capture the complete state.
        full_match = re.search(
            r"<system-reminder>\s*The following skills are available[^\n]*\n(.*?)</system-reminder>",
            content,
            re.DOTALL,
        )
        if full_match:
            skills: list[dict[str, str]] = []
            for line in full_match.group(1).strip().splitlines():
                line = line.strip()
                if line.startswith("- "):
                    line = line[2:]
                    if ": " in line:
                        name, desc = line.split(": ", 1)
                        skills.append({"name": name.strip(), "description": desc.strip()})
                    else:
                        skills.append({"name": line.strip(), "description": ""})
            if skills:
                prev_skills = skills
        else:
            # Check for incremental "New skills available:" — means prev_skills
            # existed and was extended; we can't reconstruct the full list from
            # just an incremental update, but we know skills *were* sent before.
            # Build a merged view by adding new skills to existing prev_skills.
            inc_match = re.search(
                r"<system-reminder>\s*New skills available:\s*\n(.*?)</system-reminder>",
                content,
                re.DOTALL,
            )
            if inc_match and prev_skills is not None:
                existing_names = {s["name"] for s in prev_skills}
                for line in inc_match.group(1).strip().splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        line = line[2:]
                        if ": " in line:
                            name, desc = line.split(": ", 1)
                            name, desc = name.strip(), desc.strip()
                        else:
                            name, desc = line.strip(), ""
                        if name not in existing_names:
                            prev_skills.append({"name": name, "description": desc})
                            existing_names.add(name)

        # --- Extract date ---
        date_match = re.search(
            r"<system-reminder>\s*(?:Today's date|Date has changed[^:]*?):\s*(.+?)\s*</system-reminder>",
            content,
        )
        if date_match:
            prev_date = date_match.group(1).strip()

    return prev_skills, prev_date


def _build_system_reminders(
    prompt_input: PromptInput,
    prev_skills: list[dict[str, str]] | None,
    prev_date: str | None,
) -> tuple[str, list[dict[str, str]], str]:
    """Build <system-reminder> content for skills, memory, and date.

    All sections are merged into a **single** <system-reminder> block so
    the UI can display it as one clean unit.

    Returns (reminder_text, current_skills_snapshot, current_date).

    Skills use an incremental strategy:
    - First call (prev_skills is None) or modifications/deletions -> full list
    - Only additions -> only the new skills
    - No change -> reuse previous text (empty skills section)

    Date uses an incremental strategy:
    - First call (prev_date is None) -> full date
    - Date changed -> date update reminder
    - No change -> omit

    Memory is always included if found.
    """
    inner_sections: list[str] = []

    # --- Skills section ---
    try:
        from mycode.tool.skill import list_skills_with_descriptions

        current_skills = list_skills_with_descriptions()
    except Exception:
        current_skills = []

    skills_text = _build_skills_reminder(current_skills, prev_skills)
    if skills_text:
        inner_sections.append(skills_text)

    # --- Date section ---
    current_date = time.strftime("%A, %b %d, %Y")
    date_text = _build_date_reminder(current_date, prev_date)
    if date_text:
        inner_sections.append(date_text)

    # --- Memory section ---
    memory_text = _build_memory_reminder(prompt_input)
    if memory_text:
        inner_sections.append(memory_text)

    if not inner_sections:
        return "", current_skills, current_date

    # Wrap all sections in a single <system-reminder> tag
    body = "\n\n".join(inner_sections)
    reminder = f"<system-reminder>\n{body}\n</system-reminder>"
    return reminder, current_skills, current_date


def _attach_reminder_to_last_user_message(
    messages: list[dict[str, Any]],
    reminder_text: str,
) -> None:
    """Append system-reminder text to the last user message's content.

    Supports both plain-string and multimodal content-list formats.
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = messages[i]["content"]
            if isinstance(content, str):
                messages[i]["content"] = content + "\n\n" + reminder_text
            elif isinstance(content, list):
                # Multimodal (image/pdf/audio): append a text part
                content.append({"type": "text", "text": reminder_text})
            else:
                # Fallback: coerce to string
                messages[i]["content"] = str(content) + "\n\n" + reminder_text
            return
    # No user message found — should not happen in normal flow, but
    # inject as fallback to preserve the reminder information.
    messages.append({"role": "user", "content": reminder_text})


def _build_skills_reminder(
    current: list[dict[str, str]],
    prev: list[dict[str, str]] | None,
) -> str:
    """Build the skills reminder section (plain content, no wrapper tags).

    Returns empty string if no skills exist or nothing changed.
    """
    if not current:
        return ""

    # First call — full list
    if prev is None:
        lines = ["The following skills are available for use with the `skill` tool:", ""]
        for s in current:
            desc = s["description"]
            lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
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
        lines = ["New skills available:", ""]
        for s in new_skills:
            desc = s["description"]
            lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
        return "\n".join(lines)

    # Modification or deletion — full list
    lines = ["The following skills are available for use with the `skill` tool:", ""]
    for s in current:
        desc = s["description"]
        lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
    return "\n".join(lines)


def _build_date_reminder(current: str, prev: str | None) -> str:
    """Build the date reminder section (plain content, no wrapper tags).

    Incremental strategy:
    - First call (prev is None) -> full date
    - Date changed -> date update reminder
    - No change -> empty string (omit)
    """
    if prev is None:
        return f"Today's date: {current}"
    if current != prev:
        return f"Date has changed. Today's date is now: {current}"
    return ""


def _build_memory_reminder(prompt_input: PromptInput) -> str:
    """Build the memory reminder section (plain content, no wrapper tags).

    Returns empty string if no relevant memories found.
    """
    try:
        from mycode.project.instance import current_or_none
        from mycode.session.memory.memdir import format_memories_for_context
        from mycode.session.memory.retrieval import find_relevant_memories

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
            return f"<relevant_memories>\n{context}\n</relevant_memories>"
    except Exception:
        pass  # Memory injection is best-effort
    return ""
