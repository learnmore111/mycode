"""Session compaction — context compression and pruning.

Equivalent to src/session/compaction.ts.
Handles context overflow detection, old tool output pruning, and summary generation.
"""
from __future__ import annotations

from typing import Any

from opencode.util import log as logmod

logger = logmod.create(service="session.compaction")

PRUNE_MINIMUM = 20_000  # tokens
PRUNE_PROTECT = 40_000  # tokens
OVERFLOW_RATIO = 0.85  # trigger at 85% of context window


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


async def compact(
    messages: list[dict[str, Any]],
    *,
    model_name: str,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Compact conversation history by generating a summary.

    Replaces the full conversation with a system summary + last user message.
    """
    import litellm

    # First try pruning tool outputs
    messages, freed = prune_tool_outputs(messages)
    if freed > PRUNE_MINIMUM:
        logger.info("pruning freed enough tokens, skipping full compaction", freed=freed)
        return messages

    # Generate a summary of the conversation
    summary_messages = list(messages)
    summary_messages.append({
        "role": "user",
        "content": COMPACTION_PROMPT,
    })

    try:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": summary_messages,
            "max_tokens": 4096,
            "temperature": 0.0,
        }
        if api_key:
            kwargs["api_key"] = api_key

        response = await litellm.acompletion(**kwargs)
        summary = response.choices[0].message.content or ""
        logger.info("compaction complete", summary_length=len(summary))
    except Exception as e:
        logger.error("compaction LLM call failed", error=str(e))
        # Fallback: just prune and keep going
        return messages

    # Replace messages with compacted version:
    # system summary + last user message
    compacted: list[dict[str, Any]] = [
        {"role": "system", "content": f"[Previous conversation summary]\n\n{summary}"},
    ]
    # Keep the last user message if any
    for msg in reversed(messages):
        if msg.get("role") == "user":
            compacted.append(msg)
            break

    return compacted


COMPACTION_PROMPT = """Provide a detailed prompt for continuing our conversation above.
Focus on information that would be helpful for continuing the conversation, including what we did, what we're doing, which files we're working on, and what we're going to do next.
The summary that you construct will be used so that another agent can read it and continue the work.

When constructing the summary, try to stick to this template:
---
## Goal

[What goal(s) is the user trying to accomplish?]

## Instructions

- [What important instructions did the user give you that are relevant]
- [If there is a plan or spec, include information about it so next agent can continue using it]

## Discoveries

[What notable things were learned during this conversation that would be useful for the next agent to know when continuing the work]

## Accomplished

[What work has been completed, what work is still in progress, and what work is left?]

## Relevant files / directories

[Construct a structured list of relevant files that have been read, edited, or created that pertain to the task at hand.]
---"""
