"""Context snapshot builder for UI context viewer.

Builds a structured snapshot of the full context sent to the LLM on each
iteration of the agentic loop. This powers the frontend "context viewer"
panel, showing system prompt, tools, messages, estimated token usage,
and prefix-cache hit predictions.
"""

from __future__ import annotations

import json
from typing import Any

from mycode.session.compaction import estimate_tokens

TOOL_OUTPUT_PREVIEW_LIMIT = 500  # chars — preview shown in UI for tool outputs
TOOL_ARGS_PREVIEW_LIMIT = 200  # chars — preview shown for tool call arguments

# Signature used to detect compaction summary messages.
_COMPACTION_MARKER = "continued from a previous conversation"


def build_context_snapshot(
    *,
    system: list[str],
    tools: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    model_id: str,
    context_limit: int,
    iteration: int,
    has_history: bool,
    actual_usage: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    """Build a context snapshot for the UI context viewer.

    Parameters
    ----------
    system:
        System prompt sections (will be joined with ``\\n\\n``).
    tools:
        LLM tool definitions (OpenAI function-calling format), or *None*.
    messages:
        The full message list about to be sent to the LLM.
    model_id:
        Fully-qualified model identifier (``provider/model``).
    context_limit:
        Model context window size in tokens.
    iteration:
        Current agentic-loop iteration (0-based).
    has_history:
        Whether the session was started with pre-existing history.
    actual_usage:
        Real token usage accumulated from previous LLM API responses.
        Contains ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
        ``cache_write_tokens`` etc. from the provider.  When available these
        are **far more accurate** than our heuristic estimates.

    Returns
    -------
    dict
        Structured snapshot matching the ``context_snapshot`` event schema.
    """
    # --- System prompt ---
    system_text = "\n\n".join(system)
    system_tokens = estimate_tokens(system_text)
    system_info: dict[str, Any] = {
        "content": system_text,
        "estimated_tokens": system_tokens,
        "cache_status": "cached",
    }

    # --- Tools ---
    if tools:
        tool_names = [t.get("function", {}).get("name", "?") for t in tools]
        tools_json = json.dumps(tools, ensure_ascii=False)
        tools_tokens = estimate_tokens(tools_json)
    else:
        tool_names = []
        tools_json = ""
        tools_tokens = 0
    tools_info: dict[str, Any] = {
        "count": len(tool_names),
        "names": tool_names,
        "estimated_tokens": tools_tokens,
        "cache_status": "cached",
    }

    # --- Messages ---
    # Cache status heuristic:
    #   iteration == 0 and no history → everything is new
    #   otherwise → all messages except the last one are cached
    all_new = iteration == 0 and not has_history
    msg_count = len(messages)

    message_infos: list[dict[str, Any]] = []
    compaction_boundary_index: int | None = None

    for idx, msg in enumerate(messages):
        role: str = msg.get("role", "unknown")
        content: str = msg.get("content") or ""

        if all_new:
            cache_status = "new"
        elif idx < msg_count - 1:
            cache_status = "cached"
        else:
            cache_status = "new"

        info: dict[str, Any] = {
            "index": idx,
            "role": role,
            "cache_status": cache_status,
        }

        # --- role=tool ---
        if role == "tool":
            full_length = len(content)
            truncated = len(content) > TOOL_OUTPUT_PREVIEW_LIMIT
            preview = content[:TOOL_OUTPUT_PREVIEW_LIMIT] if truncated else content
            info["content"] = preview
            info["content_truncated"] = truncated
            info["full_length"] = full_length
            info["estimated_tokens"] = estimate_tokens(content)
            info["tool_call_id"] = msg.get("tool_call_id", "")
            info["tool_name"] = msg.get("name", "")

        # --- role=assistant ---
        elif role == "assistant":
            info["content"] = content
            info["estimated_tokens"] = estimate_tokens(content)
            # Tool calls summary
            raw_calls = msg.get("tool_calls") or []
            if raw_calls:
                tc_summaries: list[dict[str, Any]] = []
                for tc in raw_calls:
                    fn = tc.get("function", {})
                    args_str = fn.get("arguments", "")
                    tc_summaries.append({
                        "id": tc.get("id", ""),
                        "tool": fn.get("name", ""),
                        "args_preview": args_str[:TOOL_ARGS_PREVIEW_LIMIT],
                    })
                info["tool_calls"] = tc_summaries
                # Add tool call tokens to estimate
                for tc in raw_calls:
                    fn = tc.get("function", {})
                    info["estimated_tokens"] += estimate_tokens(fn.get("arguments", ""))
                    info["estimated_tokens"] += estimate_tokens(fn.get("name", ""))

        # --- role=user / role=system / other ---
        else:
            info["content"] = content
            info["estimated_tokens"] = estimate_tokens(content)

        # Detect compaction summary
        if role == "user" and _COMPACTION_MARKER in content.lower():
            info["is_compaction_summary"] = True
            compaction_boundary_index = idx

        # Detect system-reminder injection
        if "<system-reminder>" in content:
            info["is_system_reminder"] = True

        message_infos.append(info)

    # --- Compaction info ---
    compaction_info: dict[str, Any] = {
        "has_boundary": compaction_boundary_index is not None,
        "boundary_index": compaction_boundary_index,
    }

    # --- Summary ---
    total_tokens = system_tokens + tools_tokens
    cached_tokens = system_tokens + tools_tokens  # system + tools always cached
    new_tokens = 0

    for mi in message_infos:
        t = mi.get("estimated_tokens", 0)
        total_tokens += t
        if mi.get("cache_status") == "cached":
            cached_tokens += t
        else:
            new_tokens += t

    usage_percent = round(100 * total_tokens / context_limit, 1) if context_limit > 0 else 0.0

    summary: dict[str, Any] = {
        "total_estimated_tokens": total_tokens,
        "cached_estimated_tokens": cached_tokens,
        "new_estimated_tokens": new_tokens,
        "context_limit": context_limit,
        "usage_percent": usage_percent,
    }

    # --- Actual API usage from previous iterations ---
    # These are real numbers from the LLM provider, accumulated across
    # all completed iterations so far.  On iteration 0 they will all be 0.
    if actual_usage:
        actual_info: dict[str, Any] | None = {
            "input_tokens": actual_usage.get("input_tokens", 0),
            "output_tokens": actual_usage.get("output_tokens", 0),
            "cache_read_tokens": actual_usage.get("cache_read_tokens", 0),
            "cache_write_tokens": actual_usage.get("cache_write_tokens", 0),
            "reasoning_tokens": actual_usage.get("reasoning_tokens", 0),
            "total_cost": actual_usage.get("total_cost", 0.0),
        }
    else:
        actual_info = None

    return {
        "system": system_info,
        "tools": tools_info,
        "messages": message_infos,
        "compaction": compaction_info,
        "summary": summary,
        "actual_usage": actual_info,
        "iteration": iteration,
        "model": model_id,
    }
