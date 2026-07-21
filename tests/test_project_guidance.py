"""Canonical mycode.md and compatibility guidance behavior."""
from __future__ import annotations

from typing import TYPE_CHECKING

import mycode.project.instance as inst
from mycode.session.system import build, find_project_guidance

if TYPE_CHECKING:
    from pathlib import Path


def test_mycode_guidance_precedence_and_compatibility_fallback(tmp_path: Path):
    (tmp_path / "mycode.md").write_text("native guidance", encoding="utf-8")
    (tmp_path / "codebuddy.md").write_text("compat guidance", encoding="utf-8")
    guidance = find_project_guidance(str(tmp_path))
    assert guidance and guidance.source_name == "mycode.md"
    assert guidance.content == "native guidance"

    (tmp_path / "mycode.md").write_text("", encoding="utf-8")
    guidance = find_project_guidance(str(tmp_path))
    assert guidance and guidance.source_name == "codebuddy.md"


def test_omit_project_guidance_alias_controls_injection(tmp_path: Path):
    (tmp_path / "mycode.md").write_text("must run tests", encoding="utf-8")
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path),
        worktree=str(tmp_path),
        project=inst.ProjectInfo(id="p1", worktree=str(tmp_path)),
    ))
    try:
        assert "must run tests" in "\n".join(build())
        assert "must run tests" not in "\n".join(build(omit_project_guidance=True))
    finally:
        token.reset()
