"""Memory retrieval — find relevant memories for the current context.

- Keyword-based fast matching (no LLM needed)
- LLM-assisted retrieval for complex queries (sideQuery to select from manifest)
- Configurable max results
- Freshness-aware (newer memories preferred)
"""
from __future__ import annotations

import re
from pathlib import Path

from mycode.session.memory.memdir import (
    MemoryEntry,
    format_memory_manifest,
    scan_memory_files,
)
from mycode.util import log as logmod

logger = logmod.create(service="session.memory.retrieval")

MAX_RELEVANT_MEMORIES = 5
MAX_QUERY_TERMS = 80
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def find_relevant_memories(
    project_path: str,
    query: str,
    *,
    max_results: int = MAX_RELEVANT_MEMORIES,
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
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
        already_surfaced: Memory paths/filenames already injected in this session
    """
    entries = scan_memory_files(project_path)
    if not entries:
        return []
    surfaced = already_surfaced or set()
    entries = [entry for entry in entries if not _is_already_surfaced(entry, surfaced)]

    # Score each memory against the query
    scored: list[tuple[float, MemoryEntry]] = []
    query_lower = query.lower()
    query_words = _query_terms(query_lower)

    for entry in entries:
        score = _compute_relevance(entry, query_lower, query_words)
        if score > 0:
            scored.append((score, entry))

    # Sort by score desc, then by freshness (newer first)
    scored.sort(key=lambda x: (-x[0], -x[1].mtime_ms))

    return [entry for _, entry in scored[:max_results]]


def build_memory_context(
    project_path: str,
    query: str,
    *,
    max_results: int = MAX_RELEVANT_MEMORIES,
    already_surfaced: set[str] | None = None,
    recent_tools: list[str] | None = None,
) -> tuple[str, list[MemoryEntry]]:
    """Build user-message memory recall context for selected detailed memories."""
    memories = find_relevant_memories(
        project_path,
        query,
        max_results=max_results,
        recent_tools=recent_tools,
        already_surfaced=already_surfaced,
    )
    if not memories:
        return "", []

    from mycode.session.memory.memdir import format_memories_for_context

    return format_memories_for_context(memories, include_freshness=True), memories


async def find_relevant_memories_llm(
    project_path: str,
    query: str,
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    max_results: int = MAX_RELEVANT_MEMORIES,
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
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
    surfaced = already_surfaced or set()
    entries = [entry for entry in entries if not _is_already_surfaced(entry, surfaced)]
    if not entries:
        return []

    # If no model configured, fall back to keyword matching
    if not model_name or not api_key:
        return find_relevant_memories(
            project_path,
            query,
            max_results=max_results,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
        )

    # Build manifest
    manifest = format_memory_manifest(entries)

    # Ask LLM to select
    tools_section = ""
    if recent_tools:
        tools = ", ".join(sorted(set(recent_tools)))
        tools_section = f"""

Recently used tools: {tools}
Do not select usage reference docs for these tools. Do select warnings, gotchas, or known issues about them.
"""

    prompt = f"""You are selecting memories that will be useful to an AI coding agent as it processes a user's query.
Return a list of filenames for the memories that will clearly be useful (up to {max_results}).
- Be selective and discerning.
- Prefer memories whose descriptions are semantically relevant to the query.
- Consider timestamps: stale project facts may be less useful than recent memories.

User query: {query}

Available memories:
{manifest}{tools_section}

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
        return find_relevant_memories(
            project_path,
            query,
            max_results=max_results,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
        )


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
    matched = False

    for word in query_words:
        if len(word) < 2:  # Skip single-char words
            continue
        if word in name_lower:
            score += 3.0
            matched = True
        if word in desc_lower:
            score += 2.0
            matched = True
        if word in content_lower:
            score += 1.0
            matched = True

    # Exact phrase match bonus
    if query_lower in name_lower:
        score += 5.0
        matched = True
    if query_lower in desc_lower:
        score += 3.0
        matched = True

    if not matched:
        return 0.0

    # Type boost (user/feedback memories tend to be more universally relevant)
    if entry.memory_type in ("user", "feedback"):
        score += 0.5

    # Freshness boost
    from mycode.session.memory.memory import memory_age_days
    if entry.mtime_ms > 0:
        age = memory_age_days(entry.mtime_ms)
        if age == 0:
            score += 0.3
        elif age == 1:
            score += 0.1

    return score


def _query_terms(query_lower: str) -> set[str]:
    """Extract query terms for lightweight multilingual memory matching."""
    terms = {word for word in re.findall(r"\w+", query_lower) if len(word) >= 2}

    # Python's \w+ treats Chinese text as one long token. Add overlapping
    # character bigrams/trigrams so short Chinese queries can still match
    # memory names/descriptions without a full tokenizer dependency.
    cjk_text = "".join(_CJK_RE.findall(query_lower))
    if cjk_text:
        for size in (2, 3):
            for i in range(0, max(len(cjk_text) - size + 1, 0)):
                terms.add(cjk_text[i:i + size])

    return set(list(terms)[:MAX_QUERY_TERMS])


def _is_already_surfaced(entry: MemoryEntry, surfaced: set[str]) -> bool:
    if not surfaced:
        return False
    path = Path(entry.path)
    candidates = {entry.path, entry.name, entry.filename, path.name, path.stem}
    return bool(candidates & surfaced)
