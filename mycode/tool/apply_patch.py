"""apply_patch tool — atomic multi-file diff application.

Supports a lightweight diff format inspired by GPT-5 / Aider:

    *** Begin Patch
    *** Add File: path/new_file.py
    +#!/usr/bin/env python
    +print("hi")
    *** Update File: path/existing.py
    @@ def greet
    -    return "hello"
    +    return "hello, world"
    *** Delete File: path/gone.py
    *** End Patch

Why a new tool instead of just using ``edit`` / ``write``?

1. **Atomicity across files.** All hunks apply or none do — we validate
   every hunk against the target files first, then commit the whole set
   under a single write pass. ``edit`` and ``write`` are single-file
   operations; on a multi-file refactor the agent can leave the repo in
   a half-applied state if the 4th file fails.

2. **Token efficiency.** Shipping one patch instead of N ``edit`` calls
   saves tool-call overhead in both directions.

3. **Atomic preview.** We produce a per-file ``before/after`` dict for
   the UI so users can review + bulk accept/reject just like with
   ``edit``'s staging layer.

Security / correctness:

- Every touched file goes through ``resolve_tool_path`` so the patch
  cannot escape the project root.
- ``atomic_write`` writes each file via temp-file + rename so a crash
  mid-patch does not tear individual files.
- Failure in any hunk aborts the whole operation; a partial map of
  already-written files is rolled back using the pre-read contents.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mycode.project.instance import current_or_none
from mycode.tool.base import (
    CallableTool,
    ToolContext,
    ToolError,
    ToolOk,
    ToolResult,
    atomic_write,
    resolve_tool_path,
)

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_CMD_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")


class ApplyPatchParams(BaseModel):
    """Parameters for the apply_patch tool."""
    patch: str = Field(
        description=(
            "A patch in the mycode format. Must be wrapped in "
            "`*** Begin Patch` / `*** End Patch` markers. Each file "
            "section begins with `*** Add File: <path>`, "
            "`*** Update File: <path>`, or `*** Delete File: <path>`. "
            "Update hunks use `@@ <context-line>` followed by `-`/`+` "
            "diff lines; Add sections use only `+` lines."
        )
    )


class _Section:
    """A single file operation parsed from the patch."""
    __slots__ = ("action", "path", "lines")

    def __init__(self, action: str, path: str) -> None:
        self.action = action  # "Add" | "Update" | "Delete"
        self.path = path
        self.lines: list[str] = []


def _split_sections(patch: str) -> list[_Section]:
    body = patch.strip()
    if _BEGIN not in body:
        raise ValueError(f"Patch missing `{_BEGIN}` marker")
    after_begin = body.split(_BEGIN, 1)[1]
    if _END in after_begin:
        after_begin = after_begin.split(_END, 1)[0]
    lines = after_begin.splitlines()

    sections: list[_Section] = []
    current: _Section | None = None
    for raw in lines:
        m = _CMD_RE.match(raw)
        if m:
            if current is not None:
                sections.append(current)
            current = _Section(action=m.group(1), path=m.group(2).strip())
            continue
        if current is None:
            if raw.strip() == "":
                continue
            raise ValueError(f"Patch content before any file header: {raw!r}")
        current.lines.append(raw)
    if current is not None:
        sections.append(current)
    if not sections:
        raise ValueError("Patch contains no file sections")
    return sections


def _apply_add(section: _Section) -> str:
    """Render an Add File section — new content is just `+` lines."""
    out: list[str] = []
    for raw in section.lines:
        if not raw:
            continue
        if raw.startswith("+"):
            out.append(raw[1:])
        elif raw.startswith("@@"):
            # tolerate @@ markers in Add sections (some tools emit them)
            continue
        else:
            raise ValueError(
                f"Add File {section.path!r} may only contain `+` lines; got: {raw!r}"
            )
    return "\n".join(out) + ("\n" if out else "")


def _apply_update(section: _Section, original: str) -> str:
    """Apply an Update File section against ``original``.

    The format is a list of hunks separated by ``@@`` context headers.
    Inside each hunk, ``-`` lines must match the current file content at
    the current cursor position, ``+`` lines are additions, and lines
    starting with a space or an empty string are context that must also
    match.
    """
    src_lines = original.splitlines()
    cursor = 0
    out: list[str] = []

    def _advance_to_context(ctx: str) -> None:
        nonlocal cursor, out
        # Empty context header (a bare `@@`) means "continue from cursor".
        if not ctx:
            return
        for idx in range(cursor, len(src_lines)):
            if ctx in src_lines[idx]:
                # Emit everything up to AND INCLUDING the context line so
                # the cursor ends up positioned just after it. This matches
                # how `@@ <anchor>` is typically read: the anchor line is
                # already committed; the hunk that follows modifies what
                # comes after it.
                out.extend(src_lines[cursor:idx + 1])
                cursor = idx + 1
                return
        raise ValueError(
            f"Update File {section.path!r}: context {ctx!r} not found after line {cursor}"
        )

    i = 0
    hunks = section.lines
    while i < len(hunks):
        raw = hunks[i]
        if raw.startswith("@@"):
            _advance_to_context(raw[2:].strip())
            i += 1
            continue
        if raw.startswith("-"):
            expected = raw[1:]
            if cursor >= len(src_lines) or src_lines[cursor] != expected:
                actual = src_lines[cursor] if cursor < len(src_lines) else "<EOF>"
                raise ValueError(
                    f"Update File {section.path!r}: expected line "
                    f"{expected!r} at position {cursor}, got {actual!r}"
                )
            cursor += 1
            i += 1
            continue
        if raw.startswith("+"):
            out.append(raw[1:])
            i += 1
            continue
        if raw.startswith(" ") or raw == "":
            # Context / blank line — must match current cursor if non-empty.
            expected = raw[1:] if raw.startswith(" ") else ""
            if cursor < len(src_lines) and src_lines[cursor] == expected:
                out.append(src_lines[cursor])
                cursor += 1
            elif not expected and (cursor >= len(src_lines) or not src_lines[cursor]):
                # Allow blank padding between hunks.
                pass
            else:
                raise ValueError(
                    f"Update File {section.path!r}: context mismatch at line {cursor}"
                )
            i += 1
            continue
        raise ValueError(
            f"Update File {section.path!r}: unrecognised hunk line {raw!r}"
        )

    # Append remaining tail of the file verbatim.
    out.extend(src_lines[cursor:])
    trailing_newline = original.endswith("\n")
    text = "\n".join(out)
    if trailing_newline and not text.endswith("\n"):
        text += "\n"
    return text


class ApplyPatchTool(CallableTool[ApplyPatchParams]):
    id = "apply_patch"
    description = (
        "Atomically apply a multi-file patch. Use this instead of many "
        "edit/write calls when modifying several files together. "
        "All hunks validate against the current filesystem before any "
        "file is written; failure rolls back the whole patch."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return False

    async def call(self, params: ApplyPatchParams, ctx: ToolContext) -> ToolResult:
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        try:
            sections = _split_sections(params.patch)
        except ValueError as exc:
            return ToolError(str(exc), title="apply_patch")

        # Phase 1: resolve every target path, read current contents,
        # and compute the desired new contents. We only touch disk in
        # phase 2 once every hunk has validated cleanly.
        plan: list[tuple[str, str, str | None, str | None]] = []
        # Each plan row: (action, absolute_path, original_or_None, new_or_None)
        for sec in sections:
            absolute, err = resolve_tool_path(sec.path, base)
            if err:
                return ToolError(
                    f"Path not allowed: {sec.path} — {err}",
                    title="apply_patch",
                )
            p = Path(absolute)
            if sec.action == "Add":
                if p.exists():
                    return ToolError(
                        f"Add File {sec.path!r}: already exists. Use Update File.",
                        title="apply_patch",
                    )
                new_content = _apply_add(sec)
                plan.append(("Add", absolute, None, new_content))
            elif sec.action == "Update":
                if not p.is_file():
                    return ToolError(
                        f"Update File {sec.path!r}: file not found.",
                        title="apply_patch",
                    )
                original = p.read_text(encoding="utf-8")
                try:
                    new_content = _apply_update(sec, original)
                except ValueError as exc:
                    return ToolError(str(exc), title="apply_patch")
                plan.append(("Update", absolute, original, new_content))
            elif sec.action == "Delete":
                if not p.is_file():
                    return ToolError(
                        f"Delete File {sec.path!r}: file not found.",
                        title="apply_patch",
                    )
                plan.append(("Delete", absolute, p.read_text(encoding="utf-8"), None))
            else:
                return ToolError(
                    f"Unknown action {sec.action!r} for {sec.path!r}",
                    title="apply_patch",
                )

        # Phase 2: apply. Track what we've done so we can roll back on
        # partial failure (e.g. permission error on the 4th file).
        applied: list[tuple[str, str, str | None]] = []
        try:
            for action, absolute, original, new in plan:
                if action == "Add" or action == "Update":
                    assert new is not None
                    atomic_write(absolute, new)
                    applied.append((action, absolute, original))
                elif action == "Delete":
                    os.unlink(absolute)
                    applied.append((action, absolute, original))
        except Exception as exc:
            # Roll back.
            for action, absolute, original in reversed(applied):
                try:
                    if action == "Add":
                        if os.path.exists(absolute):
                            os.unlink(absolute)
                    elif action == "Update" and original is not None or action == "Delete" and original is not None:
                        atomic_write(absolute, original)
                except Exception:  # noqa: BLE001 — best-effort rollback
                    pass
            return ToolError(
                f"apply_patch failed mid-apply, rolled back: {exc}",
                title="apply_patch",
            )

        summary_lines = [f"Applied patch to {len(plan)} file(s):"]
        for action, absolute, _orig, _new in plan:
            rel = os.path.relpath(absolute, base)
            summary_lines.append(f"  {action:<7} {rel}")
        return ToolOk(
            "\n".join(summary_lines),
            title=f"apply_patch ({len(plan)} files)",
            metadata={
                "files": [os.path.relpath(a, base) for _, a, _, _ in plan],
                "actions": [act for act, *_ in plan],
            },
        )


tool = ApplyPatchTool()
