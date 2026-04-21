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
from collections import namedtuple
from typing import TYPE_CHECKING, Any

from mycode.session import llm as llmmod
from mycode.util import log as logmod

if TYPE_CHECKING:
    from mycode.provider.schema import Model

logger = logmod.create(service="session.compaction")

CompactionMetrics = namedtuple('CompactionMetrics', [
    'old_message_count',     # number of old messages that were summarized
    'old_message_tokens',    # estimated tokens in old messages
    'summary_length',        # length of the generated summary
    'removed_turn_count',    # number of user turns removed
    'old_messages',          # the original old messages (for audit trail)
    'summary',               # the generated summary text
])


PRUNE_MINIMUM = 20_000  # tokens
PRUNE_PROTECT = 40_000  # tokens
OVERFLOW_RATIO = 0.85  # trigger at 85% of context window
COMPACT_KEEP_TURNS = 3  # number of recent user turns to preserve verbatim
SUMMARY_TOOL_OUTPUT_LIMIT = 1000  # chars — default truncate for benign tool outputs

# Error outputs / stack traces usually carry the *most* signal for why the
# agent diverged, so we keep more of them around the compaction prompt. A
# generic successful read/grep result compresses well at the default limit.
SUMMARY_TOOL_OUTPUT_ERROR_LIMIT = 2500

# Heuristic for identifying error-style payloads when the tool did not set
# an explicit `is_error` flag (e.g. raw traces from `bash`).
_ERROR_HINTS = (
    "Traceback",
    "Error:",
    "error:",
    "Exception",
    "FATAL",
    "fatal:",
    "panic:",
    "Exit code:",
    "[error]",
)

# Provider-specific cache TTL (seconds).
# Used to detect whether the API prefix cache has likely expired between turns.
# Conservative values — err on the side of assuming expiry.
_CACHE_TTL: dict[str, int] = {
    "@ai-sdk/anthropic": 300,       # 5 min (default; extended TTL = 1h but not auto)
    "@ai-sdk/openai": 300,          # 5-10 min inactive; use lower bound
    "@ai-sdk/google": 3600,         # 1h default explicit cache
    "@ai-sdk/amazon-bedrock": 300,  # Bedrock Anthropic models — same as Anthropic
    "@ai-sdk/deepinfra": 300,       # conservative default
}
_CACHE_TTL_DEFAULT = 300  # fallback for unknown providers

# Template for wrapping the summary as a user message in the compacted result.
COMPACT_USER_MSG_TEMPLATE = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

{summary}

Recent messages are preserved verbatim. Continue from where we left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened."""

# Fallback prompt if the compaction agent cannot be loaded.
_COMPACTION_PROMPT_FALLBACK = """Provide a detailed summary of the conversation so far.
Focus on: what was done, what is being worked on, which files are relevant,
what needs to be done next, and key user requests or constraints."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate — intentionally conservative (over-estimates).

    Uses byte length divided by a conservative factor to trigger compaction
    earlier rather than later, reducing context overflow risk.

    - English text: ~1 token per 4 bytes (ASCII)
    - Code/JSON: ~1 token per 3-4 bytes (varies heavily)
    - Chinese/Japanese/Korean: ~1 token per 3 bytes (UTF-8, 3 bytes/char)
    - Mixed: byte-based estimate handles all naturally

    We use //3 for the base estimate, then add a 15% safety margin
    to account for code/JSON under-estimation.
    """
    byte_len = len(text.encode("utf-8"))
    base_estimate = byte_len // 3
    # Add 15% safety margin for code-heavy content
    return base_estimate + base_estimate // 7


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


def should_compact(
    *,
    messages: list[dict[str, Any]],
    model_context: int,
    system_tokens: int = 0,
    tools_tokens: int = 0,
) -> bool:
    """Check if the conversation needs compaction based on total context estimate.

    Includes system prompt and tool definitions in the estimate, since they
    occupy context window space alongside the messages.
    """
    if model_context <= 0:
        return False
    est = estimate_messages_tokens(messages) + system_tokens + tools_tokens
    threshold = int(model_context * OVERFLOW_RATIO)
    return est > threshold


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


def get_cache_ttl(model: Model) -> int:
    """Return the cache TTL in seconds for a model's provider."""
    return _CACHE_TTL.get(model.api.npm, _CACHE_TTL_DEFAULT)


def is_cache_likely_expired(model: Model, last_llm_time_ms: int | None) -> bool:
    """Check whether the API prefix cache has likely expired.

    Args:
        model: The model being used.
        last_llm_time_ms: Epoch milliseconds of the last LLM completion.
            None means no prior interaction (first turn) — cache is empty.

    Returns:
        True if the cache has likely expired and proactive pruning is advisable.
    """
    if last_llm_time_ms is None:
        return False  # first turn — nothing cached yet, no benefit from pruning

    import time
    elapsed_s = (time.time() * 1000 - last_llm_time_ms) / 1000
    ttl = get_cache_ttl(model)
    expired = elapsed_s > ttl
    if expired:
        logger.info(
            "cache likely expired",
            elapsed_s=int(elapsed_s),
            ttl=ttl,
            provider=model.api.npm,
        )
    return expired


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


def _tool_output_limit(content: str) -> int:
    """Pick an appropriate truncation limit for a tool output string.

    Error-like payloads (stack traces, non-zero exits) get a higher limit
    because the summary LLM needs enough context to reason about *why*
    something failed. Benign outputs compress well at the default.
    """
    if not content:
        return SUMMARY_TOOL_OUTPUT_LIMIT
    head = content[:400]
    if any(hint in head for hint in _ERROR_HINTS):
        return SUMMARY_TOOL_OUTPUT_ERROR_LIMIT
    return SUMMARY_TOOL_OUTPUT_LIMIT


def _truncate_tool_outputs_for_summary(
    messages: list[dict[str, Any]],
    limit: int = SUMMARY_TOOL_OUTPUT_LIMIT,
) -> list[dict[str, Any]]:
    """Create a copy of *messages* with large tool outputs truncated.

    Uses copy-on-write: only messages that actually need truncation are
    deep-copied.  Messages that fit within *limit* are shared with the
    original list (no allocation).

    The *limit* argument is treated as a floor — individual tool messages
    whose content looks like an error trace are allowed to keep
    ``SUMMARY_TOOL_OUTPUT_ERROR_LIMIT`` chars so post-mortem signal is
    preserved.

    This is used **only** for the compaction LLM call so that it sees less
    token volume.  The original messages are never modified.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        needs_copy = False
        tool_limit = limit

        # Check tool result content
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str):
                tool_limit = max(limit, _tool_output_limit(content))
                if len(content) > tool_limit:
                    needs_copy = True

        # Check tool_call arguments in assistant messages
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            if len(fn.get("arguments", "")) > limit:
                needs_copy = True
                break

        if not needs_copy:
            result.append(msg)
            continue

        # Only deep-copy messages that need truncation
        msg_copy = copy.deepcopy(msg)
        if msg_copy.get("role") == "tool":
            content = msg_copy.get("content", "")
            if isinstance(content, str) and len(content) > tool_limit:
                msg_copy["content"] = content[:tool_limit] + f"\n... [truncated, {len(content)} chars total]"
        for tc in msg_copy.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if len(args) > limit:
                fn["arguments"] = args[:limit] + "..."
        result.append(msg_copy)

    return result


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

    # Last resort: strip common reasoning/scratchpad patterns and truncate
    result = _strip_reasoning_patterns(text)
    if not result:
        return "[Empty summary generated]"

    logger.warn("summary extraction fell back to stripped full text", length=len(text), stripped_length=len(result))
    if len(result) > max_length:
        result = result[:max_length] + "\n... [summary truncated]"
    return result


# Patterns that indicate LLM reasoning/scratchpad (not useful as summary content)
_REASONING_TAG_RE = re.compile(r"<(?:thinking|reasoning|scratchpad)>.*?</(?:thinking|reasoning|scratchpad)>", re.DOTALL)
_REASONING_LINE_RE = re.compile(
    r"^(?:Let me (?:think|analyze|consider|review)|I (?:need to|should|will)|"
    r"First,? |Next,? |Then,? |Finally,? |Step \d|OK,? so ).*$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_reasoning_patterns(text: str) -> str:
    """Strip common LLM reasoning/scratchpad patterns from raw text.

    Used as a last-resort cleanup when the model fails to output proper
    ``<summary>``/``<analysis>`` tags.
    """
    result = _REASONING_TAG_RE.sub("", text)
    result = _REASONING_LINE_RE.sub("", result)
    # Collapse multiple blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


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
        from mycode.agent import agent as agentmod

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
) -> tuple[list[dict[str, Any]], CompactionMetrics]:
    """Compact conversation history using sliding-window + summary strategy.

    Cache-friendly pipeline:

    1. Prune old tool outputs first — may be enough on its own.
    2. Split messages into old turns and recent turns (keep last N turns verbatim).
    3. Truncate tool outputs in old messages (deep copy) to reduce summary cost.
    4. Call the LLM with the **same system prompt + tools** as the main agent
       so the API prefix cache is shared.
    5. Extract the ``<summary>`` block, strip ``<analysis>`` scratchpad.
    6. Return: ``([user_summary_msg] + recent_turns, metrics)``.
    """
    _empty_metrics = CompactionMetrics(
        old_message_count=0, old_message_tokens=0, summary_length=0,
        removed_turn_count=0, old_messages=[], summary="",
    )

    # Step 1: prune tool outputs (in-place on the original messages)
    messages, freed = prune_tool_outputs(messages)
    if freed > PRUNE_MINIMUM:
        logger.info("pruning freed enough tokens, skipping full compaction", freed=freed)
        return messages, _empty_metrics

    # Step 2: split into old / recent
    old, recent = _split_by_turns(messages, keep_turns=COMPACT_KEEP_TURNS)

    if not old:
        # Nothing old enough to summarise — try pruning harder
        pruned_again, freed_again = prune_tool_outputs(messages)
        if freed_again > 0:
            logger.info("no old turns to compact, but pruned more tool outputs", freed=freed_again)
            return pruned_again, _empty_metrics
        logger.info("no old turns to compact, returning as-is")
        return messages, _empty_metrics

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
                return messages, _empty_metrics  # fallback: return pruned but unsummarised
    except Exception as e:
        logger.error("compaction LLM call failed", error=str(e))
        return messages, _empty_metrics  # fallback

    if not summary_text.strip():
        logger.warn("compaction produced empty summary")
        return messages, _empty_metrics

    # Step 6: extract <summary> block, strip <analysis> scratchpad
    summary = _extract_summary(summary_text)
    logger.info(
        "compaction complete",
        summary_len=len(summary),
        old_msgs=len(old),
        kept_msgs=len(recent),
    )

    # Step 7: assemble compacted conversation
    metrics = CompactionMetrics(
        old_message_count=len(old),
        old_message_tokens=estimate_messages_tokens(old),
        summary_length=len(summary),
        removed_turn_count=sum(1 for m in old if m.get("role") == "user"),
        old_messages=list(old),
        summary=summary,
    )
    result = _build_compact_result(summary, recent)

    # Step 8: post-compaction validation — warn if result still overflows
    system_tokens_est = estimate_tokens("\n\n".join(system)) if system else 0
    tools_tokens_est = estimate_tokens(str(tools)) if tools else 0
    result_tokens = estimate_messages_tokens(result)
    total_est = result_tokens + system_tokens_est + tools_tokens_est
    context_limit = model.limit.context if model.limit.context > 0 else 32_000
    threshold = int(context_limit * OVERFLOW_RATIO)
    if total_est > threshold:
        logger.warn(
            "compacted result still exceeds threshold — next iteration will re-compact",
            result_tokens=result_tokens,
            total_est=total_est,
            threshold=threshold,
        )

    return result, metrics


def log_token_accuracy(estimated: int, actual: int, model_id: str) -> None:
    """Log token estimation accuracy for tuning.

    Compares the heuristic estimate against the actual input token count
    reported by the API.  Only logs when divergence is significant (>2× or <0.5×)
    so that normal operation produces no noise.

    Args:
        estimated: Heuristic estimate (from ``estimate_messages_tokens``).
        actual: Real input_tokens from API usage metadata.
        model_id: Model identifier (e.g. ``"anthropic/claude-sonnet"``).
    """
    if actual <= 0 or estimated <= 0:
        return
    ratio = estimated / actual
    if ratio > 2.0 or ratio < 0.5:
        logger.info(
            "token estimate divergence",
            estimated=estimated,
            actual=actual,
            ratio=f"{ratio:.2f}",
            model=model_id,
        )
