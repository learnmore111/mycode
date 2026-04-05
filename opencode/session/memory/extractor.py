"""Memory extractor — automatic memory extraction from conversations.

- Analyzes conversation history to extract memorable information
- Classifies into four memory types (user/feedback/project/reference)
- Skips extraction when agent has already written memories
- Falls back to rule-based extraction if LLM unavailable
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from opencode.session.memory.memdir import (
    MEMORY_EXCLUSIONS,
    MemoryEntry,
    MemoryType,
    save_memory,
    scan_memory_files,
)
from opencode.util import log as logmod

logger = logmod.create(service="session.memory.extractor")


@dataclass
class ExtractionResult:
    """Result of memory extraction from a conversation."""
    extracted: list[dict[str, Any]]  # [{name, description, type, content}]
    skipped_reason: str | None = None  # Why extraction was skipped


async def extract_memories(
    project_path: str,
    messages: list[dict[str, Any]],
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    has_memory_writes_since: bool = False,
) -> ExtractionResult:
    """Analyze conversation messages and extract memories to save.

    Args:
        project_path: Project root path
        messages: Conversation messages (OpenAI format)
        model_name: LLM model name for extraction
        api_key: API key for the model
        has_memory_writes_since: If True, the agent already wrote memories this turn — skip

    Returns:
        ExtractionResult with extracted memories
    """
    # Skip if agent already wrote memories
    if has_memory_writes_since:
        return ExtractionResult(extracted=[], skipped_reason="agent already wrote memories this turn")

    # Skip very short conversations
    user_messages = [m for m in messages if m.get("role") == "user"]
    if len(user_messages) < 2:
        return ExtractionResult(extracted=[], skipped_reason="conversation too short")

    # Load existing memories to avoid duplicates
    existing = scan_memory_files(project_path)
    existing_names = {e.name.lower() for e in existing}

    # Try LLM extraction
    if model_name and api_key:
        return await _extract_with_llm(
            project_path, messages, model_name, api_key, existing_names,
        )

    # Fallback: rule-based extraction
    return _extract_with_rules(project_path, messages, existing_names)


async def _extract_with_llm(
    project_path: str,
    messages: list[dict[str, Any]],
    model_name: str,
    api_key: str,
    existing_names: set[str],
) -> ExtractionResult:
    """Extract memories using LLM analysis."""
    # Build conversation summary for extraction
    conversation_text = _format_conversation(messages, max_chars=8000)
    existing_list = ", ".join(sorted(existing_names)[:20]) if existing_names else "(none)"

    prompt = f"""You are a memory extraction system. Analyze this conversation and decide if any information should be saved as long-term memory.

## Memory Types
- **user**: User role, goals, preferences, knowledge level
- **feedback**: User guidance on work style (corrections + confirmations)
- **project**: Ongoing work, goals, events, deadlines
- **reference**: Pointers to information in external systems

## Rules
- ONLY save information that CANNOT be derived from current project state
- DO NOT save: code patterns, file paths, git history, debug solutions, temporary task details
- Existing memories: {existing_list}
- DO NOT create duplicates of existing memories

## Conversation
{conversation_text}

## Output Format
If you find memories to save, output them in this exact format (one per block):
```
MEMORY_START
name: <short name>
description: <one-line description for future relevance matching>
type: <user|feedback|project|reference>
content: <the memory content — for feedback/project, include **Why:** and **How to apply:**>
MEMORY_END
```

If no memories should be saved, output: NO_MEMORIES
"""

    try:
        import litellm
        response = await litellm.acompletion(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.3,
            api_key=api_key,
        )
        raw = response.choices[0].message.content or ""

        if "NO_MEMORIES" in raw:
            return ExtractionResult(extracted=[])

        extracted = _parse_extraction_response(raw, existing_names)
        return ExtractionResult(extracted=extracted)

    except Exception as e:
        logger.error("LLM extraction failed", error=str(e))
        return _extract_with_rules(project_path, messages, existing_names)


def _extract_with_rules(
    project_path: str,
    messages: list[dict[str, Any]],
    existing_names: set[str],
) -> ExtractionResult:
    """Rule-based memory extraction (fallback when LLM unavailable).

    Looks for explicit user instructions about preferences and work style.
    """
    extracted: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        # Detect explicit preference statements
        preference_patterns = [
            r"(?:always|never|prefer|don'?t|please|make sure|remember)\s+(.{10,100})",
            r"(?:I|we)\s+(?:prefer|like|want|need|use)\s+(.{10,100})",
        ]

        for pattern in preference_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                name = _infer_memory_name(match)
                if name.lower() not in existing_names:
                    extracted.append({
                        "name": name,
                        "description": f"User preference: {match[:80]}",
                        "type": "feedback",
                        "content": f"{match}\n\n**Why:** User explicitly stated this preference.\n**How to apply:** Follow this preference in future interactions.",
                    })
                    existing_names.add(name.lower())

    return ExtractionResult(extracted=extracted[:3])  # Cap at 3


def save_extracted_memories(
    project_path: str,
    extraction: ExtractionResult,
) -> list[str]:
    """Save extracted memories to disk. Returns list of saved file paths."""
    saved: list[str] = []
    for mem in extraction.extracted:
        try:
            path = save_memory(
                project_path,
                name=mem["name"],
                description=mem["description"],
                memory_type=mem["type"],
                content=mem["content"],
            )
            saved.append(path)
        except Exception as e:
            logger.error("failed to save extracted memory", name=mem.get("name"), error=str(e))
    return saved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_conversation(messages: list[dict[str, Any]], max_chars: int = 8000) -> str:
    """Format conversation messages for the extraction prompt."""
    lines: list[str] = []
    total = 0
    # Take last N messages to fit in budget
    for msg in reversed(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)[:200]
        text = f"[{role}] {content[:500]}"
        if total + len(text) > max_chars:
            break
        lines.insert(0, text)
        total += len(text)
    return "\n".join(lines)


def _parse_extraction_response(raw: str, existing_names: set[str]) -> list[dict[str, Any]]:
    """Parse the LLM extraction response into memory dicts."""
    blocks = raw.split("MEMORY_START")
    extracted: list[dict[str, Any]] = []

    for block in blocks[1:]:  # Skip text before first MEMORY_START
        end_idx = block.find("MEMORY_END")
        if end_idx < 0:
            continue
        block = block[:end_idx].strip()

        mem: dict[str, Any] = {}
        content_lines: list[str] = []
        in_content = False

        for line in block.split("\n"):
            if in_content:
                content_lines.append(line)
                continue

            for key in ("name", "description", "type"):
                if line.lower().startswith(f"{key}:"):
                    mem[key] = line[len(key) + 1:].strip()
                    break
            else:
                if line.lower().startswith("content:"):
                    in_content = True
                    rest = line[len("content:"):].strip()
                    if rest:
                        content_lines.append(rest)

        if content_lines:
            mem["content"] = "\n".join(content_lines).strip()

        # Validate
        if mem.get("name") and mem.get("type") and mem.get("content"):
            if mem["type"] not in ("user", "feedback", "project", "reference"):
                mem["type"] = "project"
            if mem["name"].lower() not in existing_names:
                extracted.append(mem)

    return extracted


def _infer_memory_name(text: str) -> str:
    """Infer a short memory name from a text snippet."""
    # Take first few meaningful words
    words = re.findall(r"[a-zA-Z]+", text)[:4]
    if words:
        return "_".join(w.lower() for w in words)
    return "user_preference"
