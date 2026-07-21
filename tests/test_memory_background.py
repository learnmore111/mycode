"""Shadow extraction policy tests."""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import mycode.project.instance as inst
from mycode.session.memory import background
from mycode.session.memory.background import extract_candidate_specs
from mycode.storage import database as db

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_extracts_multilingual_candidates_and_explicit_activation_signal():
    specs = extract_candidate_specs([
        ("m1", "我希望以后请使用简洁的中文回复"),
        ("m2", "Please remember that our project uses signed release tags."),
        ("m3", "遇到数据库锁的时候，先检查是否有长事务"),
    ])
    assert {spec.memory_type for spec in specs} >= {"user_preference", "project_fact", "procedure_candidate"}
    assert any(spec.explicit for spec in specs)


def test_extractor_ignores_questions_about_past_memory():
    specs = extract_candidate_specs([("m1", "Do you remember what we changed yesterday?")])
    assert specs == []


def test_idle_extraction_is_idempotent_and_uses_pending_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "background.db"))
    db.reset()
    cfg = SimpleNamespace(
        enabled=True,
        use_memories=True,
        generate_memories=True,
        disable_on_external_context=True,
        idle_minutes=0,
        min_user_prompts=2,
        max_results=5,
        project_ttl_days=90,
    )
    monkeypatch.setattr(background.configmod, "get", lambda: SimpleNamespace(memory=cfg))

    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path),
        worktree=str(tmp_path),
        project=inst.ProjectInfo(id="p1", worktree=str(tmp_path)),
    ))
    try:
        from mycode.session.message import create_text_part, create_user_message, save_message, save_part
        from mycode.session.session import create

        session = create(title="idle")
        for content in (
            "I prefer concise technical replies with no repeated summary.",
            "Please remember that our project uses signed release tags.",
        ):
            message = create_user_message(session.id)
            part = create_text_part(session.id, message.id)
            part.content = content
            save_message(message)
            save_part(part)

        first = background.run_eligible_extractions(str(tmp_path), "p1")
        second = background.run_eligible_extractions(str(tmp_path), "p1")
        assert first == {"sessions": 1, "candidates": 1, "active": 1, "skipped_external": 0}
        assert second == {"sessions": 0, "candidates": 0, "active": 0, "skipped_external": 0}
    finally:
        token.reset()
        db.reset()
