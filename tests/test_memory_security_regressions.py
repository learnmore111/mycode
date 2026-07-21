"""Regression tests for the P0 memory safety and concurrency failures."""
from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.session.memory.memdir import delete_memory, save_memory, update_memory
from mycode.session.memory.memory import SessionMemory


def test_memdir_rejects_absolute_and_traversal_paths(tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("do not change", encoding="utf-8")
    project = tmp_path / "project"

    assert update_memory(str(project), str(outside), content="changed") is None
    assert delete_memory(str(project), str(outside)) is False
    assert update_memory(str(project), "../outside.md", content="changed") is None
    assert delete_memory(str(project), "../outside.md") is False
    assert outside.read_text(encoding="utf-8") == "do not change"

    projected = save_memory(
        str(project),
        "safe",
        "safe",
        "project",
        "content",
        file_id="../../outside",
    )
    assert Path(projected).resolve().is_relative_to((project / ".mycode" / "memory" / "memdir").resolve())
    assert outside.read_text(encoding="utf-8") == "do not change"


def test_unicode_names_do_not_collapse_or_overwrite(tmp_path: Path):
    one = save_memory(str(tmp_path), "回复风格", "a", "feedback", "first")
    two = save_memory(str(tmp_path), "测试习惯", "b", "feedback", "second")
    assert one != two
    assert "回复风格" in one
    assert "测试习惯" in two
    assert len(list((tmp_path / ".mycode" / "memory" / "memdir").glob("feedback_*.md"))) == 2


def test_full_session_ids_and_malformed_jsonl_recovery(tmp_path: Path):
    one = SessionMemory(str(tmp_path), "01SAMEPREFIX_A")
    two = SessionMemory(str(tmp_path), "01SAMEPREFIX_B")
    assert one._get_log_path() != two._get_log_path()

    path = one._get_log_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"turn","turn":1}\n{broken\n{"type":"turn","turn":2}\n', encoding="utf-8")
    assert [row["turn"] for row in one._load_all_turns()] == [1, 2]


async def test_concurrent_turns_are_serialized_and_flushed(tmp_path: Path):
    memory = SessionMemory(str(tmp_path), "concurrent-session")
    memory.schedule_record_turn("one", "done one")
    memory.schedule_record_turn("two", "done two")
    await memory.flush_pending_tasks()

    turns = memory._load_all_turns()
    assert [turn["turn"] for turn in turns] == [1, 2]
    assert not memory._background_tasks


async def test_summary_updates_do_not_overlap(tmp_path: Path):
    memory = SessionMemory(str(tmp_path), "summary-session")
    active = 0
    peak = 0

    async def fake_call(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"summary": "ok", "refined_turns": {}}

    memory._call_llm_combined = fake_call  # type: ignore[method-assign]
    await asyncio.gather(memory._llm_update(), memory._llm_update())
    assert peak == 1
