"""Tests for structured memory retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mycode.session.memory.memdir import (
    MAX_MEMORY_INDEX_LINES,
    MAX_MEMORY_INDEX_SIZE,
    load_memory_index,
    save_memory,
)
from mycode.session.memory.retrieval import build_memory_context, find_relevant_memories
from mycode.session.prompt import _build_memory_reminder

if TYPE_CHECKING:
    from pathlib import Path


def test_find_relevant_memories_requires_query_match(tmp_path: Path):
    save_memory(
        str(tmp_path),
        name="Work style",
        description="User prefers concise answers",
        memory_type="feedback",
        content="Keep responses short.",
    )

    memories = find_relevant_memories(str(tmp_path), "database migration")

    assert memories == []


def test_find_relevant_memories_matches_chinese_bigrams(tmp_path: Path):
    save_memory(
        str(tmp_path),
        name="记忆策略",
        description="关于结构化记忆注入的用户偏好",
        memory_type="feedback",
        content="回答记忆相关问题时说明读取时机。",
    )

    memories = find_relevant_memories(str(tmp_path), "记忆怎么注入")

    assert len(memories) == 1
    assert memories[0].name == "记忆策略"


def test_load_memory_index_uses_claude_style_limits(tmp_path: Path):
    memdir = tmp_path / ".mycode" / "memory" / "memdir"
    memdir.mkdir(parents=True)
    line = "x" * 200
    (memdir / "MEMORY.md").write_text("\n".join([line] * 300), encoding="utf-8")

    index = load_memory_index(str(tmp_path))

    assert len(index.splitlines()) <= MAX_MEMORY_INDEX_LINES + 2
    assert len(index.encode("utf-8")) <= MAX_MEMORY_INDEX_SIZE + 220
    assert "WARNING: MEMORY.md" in index


def test_build_memory_context_includes_relevant_details_only(tmp_path: Path):
    save_memory(
        str(tmp_path),
        name="Testing preference",
        description="User wants pytest verification",
        memory_type="feedback",
        content="Run focused pytest tests after memory retrieval changes.",
    )

    context, memories = build_memory_context(str(tmp_path), "pytest verification")

    assert "<relevant_memories>" in context
    assert "Testing preference" in context
    assert len(memories) == 1


def test_find_relevant_memories_skips_already_surfaced(tmp_path: Path):
    save_memory(
        str(tmp_path),
        name="Testing preference",
        description="User wants pytest verification",
        memory_type="feedback",
        content="Run focused pytest tests after memory retrieval changes.",
    )

    memories = find_relevant_memories(
        str(tmp_path),
        "pytest verification",
        already_surfaced={"Testing preference"},
    )

    assert memories == []


def test_memory_reminder_injects_index_once_then_only_on_change(tmp_path: Path):
    import mycode.project.instance as inst

    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path),
        worktree=str(tmp_path),
        project=inst.ProjectInfo(id="test", worktree=str(tmp_path)),
    ))
    try:
        save_memory(
            str(tmp_path),
            name="Testing preference",
            description="User wants pytest verification",
            memory_type="feedback",
            content="Run focused pytest tests after memory retrieval changes.",
        )

        first, index_hash = _build_memory_reminder(None)
        assert "<memory_index" in first
        assert "<memory_tool_guidance>" in first
        assert index_hash is not None

        second, same_hash = _build_memory_reminder(index_hash)
        assert second == ""
        assert same_hash == index_hash

        save_memory(
            str(tmp_path),
            name="Review preference",
            description="User wants findings first during code review",
            memory_type="feedback",
            content="Lead review responses with findings.",
        )

        updated, updated_hash = _build_memory_reminder(index_hash)
        assert 'status="updated"' in updated
        assert updated_hash != index_hash
    finally:
        token.reset()
