"""apply_patch tool + LSP didChange integration tests.

Covers:
1. Patch parsing: Add/Update/Delete sections
2. Apply-patch round-trip: multi-file atomic patch
3. Update hunk matching: context anchors, -/+ lines, trailing content
4. Rollback on hunk failure (phase-1 validation catch)
5. Path safety validation (no escape from project root)
6. Post-write hook fires for each file written by apply_patch
7. LSP notify_changed hook integration (mock-based)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mycode.tool.apply_patch import (
    ApplyPatchTool,
    _apply_add,
    _apply_update,
    _Section,
    _split_sections,
)
from mycode.tool.base import ToolContext, _post_write_hooks, atomic_write

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(session_id: str = "test") -> ToolContext:
    return ToolContext(session_id=session_id, message_id="m1", agent="build")


_TOOL = ApplyPatchTool()


# ---------------------------------------------------------------------------
# 1. Patch parsing
# ---------------------------------------------------------------------------


def test_split_sections_basic() -> None:
    patch = """\
*** Begin Patch
*** Add File: foo.py
+print("hi")
*** Update File: bar.py
@@ def greet
-    return "hello"
+    return "hello, world"
*** Delete File: old.py
*** End Patch
"""
    sections = _split_sections(patch)
    assert len(sections) == 3
    assert sections[0].action == "Add"
    assert sections[0].path == "foo.py"
    assert sections[1].action == "Update"
    assert sections[1].path == "bar.py"
    assert sections[2].action == "Delete"
    assert sections[2].path == "old.py"


def test_split_sections_missing_begin_raises() -> None:
    with pytest.raises(ValueError, match="missing"):
        _split_sections("*** Delete File: x.py\n*** End Patch")


def test_split_sections_empty_raises() -> None:
    with pytest.raises(ValueError, match="no file sections"):
        _split_sections("*** Begin Patch\n*** End Patch")


# ---------------------------------------------------------------------------
# 2. _apply_add
# ---------------------------------------------------------------------------


def test_apply_add_simple() -> None:
    sec = _Section("Add", "new.py")
    sec.lines = ["+#!/usr/bin/env python", "+print('hello')"]
    result = _apply_add(sec)
    assert result == "#!/usr/bin/env python\nprint('hello')\n"


def test_apply_add_rejects_non_plus_lines() -> None:
    sec = _Section("Add", "bad.py")
    sec.lines = ["+ok", "not a plus line"]
    with pytest.raises(ValueError, match="only contain"):
        _apply_add(sec)


# ---------------------------------------------------------------------------
# 3. _apply_update
# ---------------------------------------------------------------------------


def test_apply_update_basic() -> None:
    original = "def greet():\n    return 'hello'\n\nprint(greet())\n"
    sec = _Section("Update", "test.py")
    sec.lines = [
        "@@ def greet",
        "-    return 'hello'",
        "+    return 'hello, world'",
    ]
    result = _apply_update(sec, original)
    assert "hello, world" in result
    assert "hello'" not in result
    assert "print(greet())" in result


def test_apply_update_context_not_found_raises() -> None:
    sec = _Section("Update", "test.py")
    sec.lines = ["@@ nonexistent anchor"]
    with pytest.raises(ValueError, match="not found"):
        _apply_update(sec, "some content\n")


def test_apply_update_minus_mismatch_raises() -> None:
    sec = _Section("Update", "test.py")
    sec.lines = ["-wrong line"]
    with pytest.raises(ValueError, match="expected line"):
        _apply_update(sec, "actual line\n")


def test_apply_update_preserves_trailing_newline() -> None:
    original = "line1\nline2\n"
    sec = _Section("Update", "f.py")
    sec.lines = ["-line1", "+modified"]
    result = _apply_update(sec, original)
    assert result.endswith("\n")


# ---------------------------------------------------------------------------
# 4. Full tool call — Add + Update + Delete
# ---------------------------------------------------------------------------


async def test_apply_patch_full_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Setup project dir
    monkeypatch.setattr(
        "mycode.tool.apply_patch.current_or_none",
        lambda: type("Ctx", (), {"directory": str(tmp_path)})(),
    )
    (tmp_path / "existing.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "to_delete.txt").write_text("bye", encoding="utf-8")

    patch = """\
*** Begin Patch
*** Add File: new_file.py
+# new file
+print("created")
*** Update File: existing.py
@@ def hello
-    return 1
+    return 42
*** Delete File: to_delete.txt
*** End Patch
"""
    result = await _TOOL.execute({"patch": patch}, _ctx())
    assert not result.is_error, result.output
    assert "3 file(s)" in result.output

    # Verify outcomes
    assert (tmp_path / "new_file.py").read_text().strip() == '# new file\nprint("created")'
    assert "return 42" in (tmp_path / "existing.py").read_text()
    assert not (tmp_path / "to_delete.txt").exists()


# ---------------------------------------------------------------------------
# 5. Validation catches errors before writing (atomic abort)
# ---------------------------------------------------------------------------


async def test_apply_patch_aborts_on_update_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "mycode.tool.apply_patch.current_or_none",
        lambda: type("Ctx", (), {"directory": str(tmp_path)})(),
    )
    (tmp_path / "a.py").write_text("original content\n", encoding="utf-8")

    patch = """\
*** Begin Patch
*** Update File: a.py
-WRONG LINE
+something new
*** End Patch
"""
    result = await _TOOL.execute({"patch": patch}, _ctx())
    assert result.is_error
    assert "expected line" in result.output.lower() or "WRONG LINE" in result.output
    # File must be untouched
    assert (tmp_path / "a.py").read_text() == "original content\n"


async def test_apply_patch_add_existing_file_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "mycode.tool.apply_patch.current_or_none",
        lambda: type("Ctx", (), {"directory": str(tmp_path)})(),
    )
    (tmp_path / "exists.py").write_text("already here", encoding="utf-8")

    patch = """\
*** Begin Patch
*** Add File: exists.py
+new content
*** End Patch
"""
    result = await _TOOL.execute({"patch": patch}, _ctx())
    assert result.is_error
    assert "already exists" in result.output


# ---------------------------------------------------------------------------
# 6. Post-write hook fires for each patched file
# ---------------------------------------------------------------------------


async def test_apply_patch_fires_post_write_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "mycode.tool.apply_patch.current_or_none",
        lambda: type("Ctx", (), {"directory": str(tmp_path)})(),
    )
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")

    written_paths: list[str] = []
    _post_write_hooks.clear()

    def _track(path: str, _content: str) -> None:
        written_paths.append(path)

    _post_write_hooks.append(_track)
    try:
        patch = """\
*** Begin Patch
*** Add File: new.txt
+hello
*** Update File: a.txt
-old
+new
*** End Patch
"""
        result = await _TOOL.execute({"patch": patch}, _ctx())
        assert not result.is_error
        # Both new.txt and a.txt should have triggered the hook
        basenames = [p.rsplit("/", 1)[-1] for p in written_paths]
        assert "new.txt" in basenames
        assert "a.txt" in basenames
    finally:
        _post_write_hooks.clear()


# ---------------------------------------------------------------------------
# 7. LSP post-write hook integration (unit level)
# ---------------------------------------------------------------------------


async def test_atomic_write_fires_hook(tmp_path: Path) -> None:
    """atomic_write must call every registered post-write hook."""
    calls: list[tuple[str, str]] = []
    _post_write_hooks.clear()

    def _record(path: str, content: str) -> None:
        calls.append((path, content))

    _post_write_hooks.append(_record)
    try:
        target = str(tmp_path / "test.txt")
        atomic_write(target, "content here")
        assert len(calls) == 1
        assert calls[0] == (target, "content here")
        assert (tmp_path / "test.txt").read_text() == "content here"
    finally:
        _post_write_hooks.clear()


async def test_lsp_manager_registers_hook_on_init() -> None:
    """LspManager.init() must register exactly one post-write hook."""
    _post_write_hooks.clear()
    try:
        from mycode.lsp.lsp import LspManager

        mgr = LspManager()
        await mgr.init(lsp_config=None)  # Default config, hook gets registered
        # The hook should be registered
        assert mgr._hook_registered
        assert len(_post_write_hooks) == 1
        # Calling init again should not double-register
        await mgr.init(lsp_config=None)
        assert len(_post_write_hooks) == 1
    finally:
        _post_write_hooks.clear()


# ---------------------------------------------------------------------------
# 8. Path safety
# ---------------------------------------------------------------------------


async def test_apply_patch_rejects_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "mycode.tool.apply_patch.current_or_none",
        lambda: type("Ctx", (), {"directory": str(tmp_path)})(),
    )
    patch = """\
*** Begin Patch
*** Add File: ../../etc/evil.py
+import os; os.system("rm -rf /")
*** End Patch
"""
    result = await _TOOL.execute({"patch": patch}, _ctx())
    assert result.is_error
    assert "not allowed" in result.output.lower() or "outside" in result.output.lower()
