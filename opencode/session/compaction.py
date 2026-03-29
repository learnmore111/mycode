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


def estimate_tokens(text: str) -> int:
    """Rough token estimate (1 token ≈ 4 chars for English)."""
    return len(text) // 4


def is_overflow(*, tokens: dict[str, int], model_context: int) -> bool:
    """Check if token usage exceeds model context limit."""
    total = tokens.get("input", 0) + tokens.get("output", 0)
    return total > model_context * 0.85  # 85% threshold


def prune_tool_outputs(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Prune old tool outputs to free context space.

    Goes backwards through messages, protecting the most recent PRUNE_PROTECT
    tokens worth of tool outputs. Older outputs are replaced with a placeholder.

    Returns (pruned_messages, tokens_freed).
    """
    total_tool_tokens = 0
    prunable: list[tuple[int, int, int]] = []  # (msg_idx, part_idx, estimated_tokens)
    turns = 0

    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") == "user":
            turns += 1
        if turns < 2:
            continue  # Protect last 2 turns

        parts = msg.get("parts", [])
        for part_idx in range(len(parts) - 1, -1, -1):
            part = parts[part_idx]
            if part.get("type") != "tool":
                continue
            state = part.get("state", {})
            if state.get("status") != "completed":
                continue
            if state.get("time", {}).get("compacted"):
                break  # Already pruned from here

            output = state.get("output", "")
            est = estimate_tokens(output)
            total_tool_tokens += est
            if total_tool_tokens > PRUNE_PROTECT:
                prunable.append((msg_idx, part_idx, est))

    pruned = 0
    for msg_idx, part_idx, est in prunable:
        part = messages[msg_idx]["parts"][part_idx]
        part["state"]["output"] = "[Old tool result content cleared]"
        part["state"].setdefault("time", {})["compacted"] = True
        pruned += est

    if pruned > PRUNE_MINIMUM:
        logger.info("pruned tool outputs", count=len(prunable), tokens_freed=pruned)

    return messages, pruned


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
