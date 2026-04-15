"""Session compaction — context compression and pruning.

Handles context overflow detection, old tool output pruning, and summary generation.

Cache-friendly design:
- The compaction LLM call reuses the main agent's system prompt + tools so
  that the API prefix cache is shared (cache hit on system + tools prefix).
- Tool outputs in old messages are truncated before the compaction call to
  reduce cost.
- The compacted result injects the summary as a **user message** (not a system
  message) so the main agent's next call still has an identical system prefix.
"""
from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

from opencode.session import llm as llmmod
from opencode.util import log as logmod

if TYPE_CHECKING:
    from opencode.provider.schema import Model

logger = logmod.create(service="session.compaction")

PRUNE_MINIMUM = 20_000  # tokens
PRUNE_PROTECT = 40_000  # tokens
OVERFLOW_RATIO = 0.85  # trigger at 85% of context window
COMPACT_KEEP_TURNS = 3  # number of recent user turns to preserve verbatim
SUMMARY_TOOL_OUTPUT_LIMIT = 1000  # chars — truncate tool outputs before summary call

# Template for wrapping the summary as a user message in the compacted result.
COMPACT_USER_MSG_TEMPLATE = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

{summary}

Recent messages are preserved verbatim. Continue from where we left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened."""

# Fallback prompt if the compaction agent cannot be loaded.
_COMPACTION_PROMPT_FALLBACK = """Provide a detailed summary of the conversation so far.
Focus on: what was done, what is being worked on, which files are relevant,
what needs to be done next, and key user requests or constraints."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate (1 token ≈ 4 chars for English)."""
    return len(text) // 4


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across all messages."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total += estimate_tokens(content)
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            fn = tc.get("function", {})
            total += estimate_tokens(fn.get("arguments", ""))
            total += estimate_tokens(fn.get("name", ""))
    return total


def should_compact(*, messages: list[dict[str, Any]], model_context: int) -> bool:
    """Check if the conversation needs compaction based on message token estimate."""
    if model_context <= 0:
        return False
    est = estimate_messages_tokens(messages)
    threshold = int(model_context * OVERFLOW_RATIO)
    return est > threshold


def is_overflow(*, tokens: dict[str, int], model_context: int) -> bool:
    """Check if token usage exceeds model context limit."""
    total = tokens.get("input", 0) + tokens.get("output", 0)
    return total > model_context * OVERFLOW_RATIO


def prune_tool_outputs(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Prune old tool outputs to free context space.

    Works with OpenAI message format:
      - {"role": "tool", "tool_call_id": "...", "content": "..."} — tool results
      - {"role": "assistant", "tool_calls": [...]} — tool call requests

    Goes backwards through messages, protecting the most recent PRUNE_PROTECT
    tokens worth of tool outputs. Older outputs are replaced with a placeholder.

    Returns (pruned_messages, tokens_freed).
    """
    # Collect indices of tool-result messages with their token estimates
    tool_indices: list[tuple[int, int]] = []  # (msg_idx, estimated_tokens)
    turns = 0

    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") == "user":
            turns += 1
        if turns < 2:
            continue  # Protect last 2 turns

        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if content and content != "[Old tool result content cleared]":
                est = estimate_tokens(content)
                tool_indices.append((msg_idx, est))

    # Walk from newest to oldest tool output, protect first PRUNE_PROTECT tokens
    protected_tokens = 0
    prunable: list[tuple[int, int]] = []
    for msg_idx, est in tool_indices:
        protected_tokens += est
        if protected_tokens > PRUNE_PROTECT:
            prunable.append((msg_idx, est))

    # Prune
    pruned = 0
    for msg_idx, est in prunable:
        messages[msg_idx]["content"] = "[Old tool result content cleared]"
        pruned += est

    if pruned > PRUNE_MINIMUM:
        logger.info("pruned tool outputs", count=len(prunable), tokens_freed=pruned)

    return messages, pruned


def _split_by_turns(
    messages: list[dict[str, Any]],
    keep_turns: int = COMPACT_KEEP_TURNS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split messages into (old, recent) by user turns.

    A "turn" starts at each ``role=user`` message and includes the
    assistant reply plus any tool call / tool result messages that follow.
    The most recent *keep_turns* turns (and all messages after them) are
    placed in *recent*; everything before goes into *old*.

    Returns ``(old_messages, recent_messages)``.
    """
    # Find indices where user turns start
    turn_starts: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            turn_starts.append(i)

    if len(turn_starts) <= keep_turns:
        # Not enough turns to split – keep everything
        return [], list(messages)

    split_idx = turn_starts[-keep_turns]
    return list(messages[:split_idx]), list(messages[split_idx:])


# ---------------------------------------------------------------------------
# Helpers for the cache-friendly compaction pipeline
# ---------------------------------------------------------------------------


def _truncate_tool_outputs_for_summary(
    messages: list[dict[str, Any]],
    limit: int = SUMMARY_TOOL_OUTPUT_LIMIT,
) -> list[dict[str, Any]]:
    """Create a deep copy of *messages* with large tool outputs truncated.

    This is used **only** for the compaction LLM call so that it sees less
    token volume.  The original messages are never modified.
    """
    truncated = copy.deepcopy(messages)
    for msg in truncated:
        # Truncate tool result content
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > limit:
                msg["content"] = content[:limit] + f"\n... [truncated, {len(content)} chars total]"

        # Truncate tool_call arguments in assistant messages
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if len(args) > limit:
                fn["arguments"] = args[:limit] + "..."
    return truncated


def _extract_summary(text: str, max_length: int = 8000) -> str:
    """Extract the ``<summary>`` content and strip the ``<analysis>`` scratchpad.

    The compaction prompt asks the model to output ``<analysis>...</analysis>``
    followed by ``<summary>...</summary>``.  The analysis block is a drafting
    scratchpad that improves summary quality but should not be kept in the
    final output (it wastes tokens in subsequent calls).

    Enforces a max_length on the summary to prevent unbounded growth.
    """
    # Try to find <summary>...</summary>
    match = re.search(r"<summary>(.*?)</summary>", text, re.DOTALL)
    if match:
        summary = match.group(1).strip()
        if len(summary) > max_length:
            summary = summary[:max_length] + "\n... [summary truncated]"
        return summary

    # Fallback: strip <analysis>...</analysis> and return the rest
    stripped = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL)
    stripped = stripped.strip()
    if stripped and len(stripped) < len(text):
        if len(stripped) > max_length:
            stripped = stripped[:max_length] + "\n... [summary truncated]"
        return stripped

    # Last resort: truncate
    result = text.strip()
    if len(result) > max_length:
        result = result[:max_length] + "\n... [summary truncated]"
        logger.warn("summary extraction fell back to truncated full text", length=len(text))
    return result if result else "[Empty summary generated]"


def _build_compact_result(
    summary: str,
    recent: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Assemble the compacted message list.

    The summary is injected as a **user message** so that the main agent's
    system prompt prefix remains identical → prefix cache hit.
    """
    user_summary_msg: dict[str, Any] = {
        "role": "user",
        "content": COMPACT_USER_MSG_TEMPLATE.format(summary=summary),
    }
    return [user_summary_msg, *recent]


async def _load_compaction_prompt() -> str:
    """Load the compaction agent's prompt text.

    Falls back to a built-in default if the agent cannot be loaded.
    """
    try:
        from opencode.agent import agent as agentmod

        agent = await agentmod.get("compaction")
        if agent and agent.prompt:
            return agent.prompt
    except Exception:
        pass
    return _COMPACTION_PROMPT_FALLBACK


async def compact(
    messages: list[dict[str, Any]],
    *,
    system: list[str],
    tools: list[dict[str, Any]] | None,
    model: Model,
    api_key: str | None = None,
    api_base: str | None = None,
) -> list[dict[str, Any]]:
    """Compact conversation history using sliding-window + summary strategy.

    Cache-friendly pipeline:

    1. Prune old tool outputs first — may be enough on its own.
    2. Split messages into old turns and recent turns (keep last N turns verbatim).
    3. Truncate tool outputs in old messages (deep copy) to reduce summary cost.
    4. Call the LLM with the **same system prompt + tools** as the main agent
       so the API prefix cache is shared.
    5. Extract the ``<summary>`` block, strip ``<analysis>`` scratchpad.
    6. Return: ``[user_summary_msg] + recent_turns``.
    """
    # Step 1: prune tool outputs (in-place on the original messages)
    messages, freed = prune_tool_outputs(messages)
    if freed > PRUNE_MINIMUM:
        logger.info("pruning freed enough tokens, skipping full compaction", freed=freed)
        return messages

    # Step 2: split into old / recent
    old, recent = _split_by_turns(messages, keep_turns=COMPACT_KEEP_TURNS)

    if not old:
        # Nothing old enough to summarise — try pruning harder
        pruned_again, freed_again = prune_tool_outputs(messages)
        if freed_again > 0:
            logger.info("no old turns to compact, but pruned more tool outputs", freed=freed_again)
            return pruned_again
        logger.info("no old turns to compact, returning as-is")
        return messages

    # Step 3: truncate tool outputs in old messages for the summary call
    truncated_old = _truncate_tool_outputs_for_summary(old)

    # Step 4: build the compaction request
    compaction_prompt = await _load_compaction_prompt()

    summary_messages: list[dict[str, Any]] = list(truncated_old)
    summary_messages.append({"role": "user", "content": compaction_prompt})

    stream_input = llmmod.StreamInput(
        model=model,
        messages=summary_messages,
        system=system,  # same system prompt as main agent → cache hit
        tools=tools,  # same tools as main agent → cache key match
        tool_choice="none",  # prevent tool calls at API level
        temperature=0.0,
        max_tokens=4096,
        api_key=api_key,
        api_base=api_base,
    )

    # Step 5: consume stream and collect summary text
    summary_text = ""
    try:
        async for event in llmmod.stream(stream_input):
            if isinstance(event, llmmod.TextDelta):
                summary_text += event.text
            elif isinstance(event, llmmod.ErrorEvent):
                logger.error("compaction LLM stream error", error=event.error)
                return messages  # fallback: return pruned but unsummarised
    except Exception as e:
        logger.error("compaction LLM call failed", error=str(e))
        return messages  # fallback

    if not summary_text.strip():
        logger.warn("compaction produced empty summary")
        return messages

    # Step 6: extract <summary> block, strip <analysis> scratchpad
    summary = _extract_summary(summary_text)
    logger.info(
        "compaction complete",
        summary_len=len(summary),
        old_msgs=len(old),
        kept_msgs=len(recent),
    )

    # Step 7: assemble compacted conversation
    return _build_compact_result(summary, recent)
