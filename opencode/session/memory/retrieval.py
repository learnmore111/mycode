"""Memory retrieval — find relevant memories for the current context.

- Keyword-based fast matching (no LLM needed)
- LLM-assisted retrieval for complex queries (sideQuery to select from manifest)
- Configurable max results
- Freshness-aware (newer memories preferred)
"""
from __future__ import annotations

import re

from opencode.session.memory.memdir import (
    MemoryEntry,
    format_memory_manifest,
    scan_memory_files,
)
from opencode.util import log as logmod

logger = logmod.create(service="session.memory.retrieval")

MAX_RELEVANT_MEMORIES = 5


def find_relevant_memories(
    project_path: str,
    query: str,
    *,
    max_results: int = MAX_RELEVANT_MEMORIES,
    recent_tools: list[str] | None = None,
) -> list[MemoryEntry]:
    """Find memories relevant to the user's query.

    Uses keyword matching on name + description + content.
    Results are sorted by relevance score (higher = more relevant),
    with ties broken by freshness (newer preferred).

    Args:
        project_path: Path to the project root
        query: User's query text
        max_results: Maximum number of memories to return
        recent_tools: List of recently used tool names (for exclusion signals)
    """
    entries = scan_memory_files(project_path)
    if not entries:
        return []

    # Score each memory against the query
    scored: list[tuple[float, MemoryEntry]] = []
    query_lower = query.lower()
    query_words = set(re.findall(r"\w+", query_lower))

    for entry in entries:
        score = _compute_relevance(entry, query_lower, query_words)
        if score > 0:
            scored.append((score, entry))

    # Sort by score desc, then by freshness (newer first)
    scored.sort(key=lambda x: (-x[0], -x[1].mtime_ms))

    return [entry for _, entry in scored[:max_results]]


async def find_relevant_memories_llm(
    project_path: str,
    query: str,
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    max_results: int = MAX_RELEVANT_MEMORIES,
) -> list[MemoryEntry]:
    """Find relevant memories using LLM-assisted retrieval.

    Flow:
    1. Scan all memory files → build manifest (name + description)
    2. Ask LLM to select relevant memories from manifest
    3. Return selected memory entries with full content

    Falls back to keyword matching if LLM is unavailable.
    """
    entries = scan_memory_files(project_path)
    if not entries:
        return []

    # If no model configured, fall back to keyword matching
    if not model_name or not api_key:
        return find_relevant_memories(project_path, query, max_results=max_results)

    # Build manifest
    manifest = format_memory_manifest(entries)

    # Ask LLM to select
    prompt = f"""You are a memory retrieval system. Given a user query and a list of available memories,
select the most relevant memories (up to {max_results}).

User query: {query}

Available memories:
{manifest}

Reply with ONLY the filenames of relevant memories, one per line.
If no memories are relevant, reply with "NONE".
"""

    try:
        import litellm
        response = await litellm.acompletion(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.0,
            api_key=api_key,
        )
        raw = response.choices[0].message.content or ""

        if "NONE" in raw.upper():
            return []

        # Parse selected filenames
        selected_names = set()
        for line in raw.strip().split("\n"):
            name = line.strip().strip("- ")
            if name:
                selected_names.add(name)

        # Match selected names to entries
        result: list[MemoryEntry] = []
        for entry in entries:
            if entry.filename in selected_names or entry.name in selected_names:
                result.append(entry)
                if len(result) >= max_results:
                    break

        return result

    except Exception as e:
        logger.warn("LLM retrieval failed, falling back to keywords", error=str(e))
        return find_relevant_memories(project_path, query, max_results=max_results)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _compute_relevance(
    entry: MemoryEntry,
    query_lower: str,
    query_words: set[str],
) -> float:
    """Compute relevance score for a memory entry against a query.

    Scoring:
    - Name match: 3.0 per word
    - Description match: 2.0 per word
    - Content match: 1.0 per word (capped)
    - Type boost: user/feedback get +0.5 (more persistent knowledge)
    - Freshness boost: entries from today get +0.3
    """
    score = 0.0

    name_lower = entry.name.lower()
    desc_lower = entry.description.lower()
    content_lower = entry.content.lower()[:2000]  # Cap content search

    for word in query_words:
        if len(word) < 2:  # Skip single-char words
            continue
        if word in name_lower:
            score += 3.0
        if word in desc_lower:
            score += 2.0
        if word in content_lower:
            score += 1.0

    # Exact phrase match bonus
    if query_lower in name_lower:
        score += 5.0
    if query_lower in desc_lower:
        score += 3.0

    # Type boost (user/feedback memories tend to be more universally relevant)
    if entry.memory_type in ("user", "feedback"):
        score += 0.5

    # Freshness boost
    from opencode.session.memory.memory import memory_age_days
    if entry.mtime_ms > 0:
        age = memory_age_days(entry.mtime_ms)
        if age == 0:
            score += 0.3
        elif age == 1:
            score += 0.1

    return score
