"""Edit file tool — string replacement based editing.

Features:
- Path safety validation (prevent editing outside project directory)
- Atomic write (temp file + rename to prevent corruption)
- Uniqueness check shows all match locations (line numbers) on failure
- No-op detection (old_string == new_string)
- Richer post-edit snippet with clear change markers
- insert_after_line for line-based insertion
- File not found suggests using write tool
- Capability declarations (is_concurrency_safe=False)
- Multi-layer fuzzy match fallback (line-trimmed, block-anchor, whitespace-normalized,
  indentation-flexible, escape-normalized, trimmed-boundary, context-aware)
- Read-before-edit guard (must read file before editing)
- Post-edit LSP diagnostic feedback
"""
from __future__ import annotations

import os
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
    _assert_file_read,
)

_CONTEXT_LINES = 4
_MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10 MB limit for edit operations
_MAX_DIAGNOSTICS_PER_FILE = 20


# ---------------------------------------------------------------------------
# Fuzzy-match replacers (inspired by opencode's edit.ts)
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Levenshtein distance between two strings."""
    if a == "" or b == "":
        return max(len(a), len(b))
    matrix = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        matrix[i][0] = i
    for j in range(len(b) + 1):
        matrix[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            matrix[i][j] = min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j - 1] + cost)
    return matrix[len(a)][len(b)]


def _simple_replacer(content: str, find: str) -> str | None:
    if content.count(find) == 1:
        return find
    return None


def _line_trimmed_replacer(content: str, find: str) -> str | None:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if not search_lines:
        return None
    for i in range(len(original_lines) - len(search_lines) + 1):
        matches = True
        for j in range(len(search_lines)):
            if original_lines[i + j].strip() != search_lines[j].strip():
                matches = False
                break
        if matches:
            start = 0
            for k in range(i):
                start += len(original_lines[k]) + 1
            end = start
            for k in range(len(search_lines)):
                end += len(original_lines[i + k])
                if k < len(search_lines) - 1:
                    end += 1
            return content[start:end]
    return None


def _block_anchor_replacer(content: str, find: str) -> str | None:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if len(search_lines) < 3:
        return None
    first = search_lines[0].strip()
    last = search_lines[-1].strip()
    candidates: list[tuple[int, int]] = []
    for i in range(len(original_lines)):
        if original_lines[i].strip() != first:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last:
                candidates.append((i, j))
                break
    if not candidates:
        return None
    best = None
    best_sim = -1.0
    for start, end in candidates:
        actual_size = end - start + 1
        lines_to_check = min(len(search_lines) - 2, actual_size - 2)
        sim = 0.0
        if lines_to_check > 0:
            for k in range(1, min(len(search_lines) - 1, actual_size - 1)):
                ol = original_lines[start + k].strip()
                sl = search_lines[k].strip()
                max_len = max(len(ol), len(sl))
                if max_len == 0:
                    continue
                sim += (1 - _levenshtein(ol, sl) / max_len) / lines_to_check
        else:
            sim = 1.0
        if sim > best_sim:
            best_sim = sim
            best = (start, end)
    if best is None:
        return None
    start, end = best
    s_idx = sum(len(original_lines[k]) + 1 for k in range(start))
    e_idx = s_idx + sum(len(original_lines[k]) + (1 if k < end else 0) for k in range(start, end + 1))
    return content[s_idx:e_idx]


def _whitespace_normalized_replacer(content: str, find: str) -> str | None:
    import re
    norm = lambda t: re.sub(r"\s+", " ", t).strip()
    nfind = norm(find)
    if not nfind:
        return None
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if norm(line) == nfind:
            return line
        nline = norm(line)
        if nfind in nline:
            words = find.strip().split()
            if words:
                pattern = r"\s+".join(re.escape(w) for w in words)
                match = re.search(pattern, line)
                if match:
                    return match.group(0)
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if norm(block) == nfind:
                return block
    return None


def _indentation_flexible_replacer(content: str, find: str) -> str | None:
    def remove_indent(text: str) -> str:
        lines = text.split("\n")
        non_empty = [ln for ln in lines if ln.strip()]
        if not non_empty:
            return text
        min_indent = min(len(ln) - len(ln.lstrip()) for ln in non_empty)
        return "\n".join(ln if not ln.strip() else ln[min_indent:] for ln in lines)
    nfind = remove_indent(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i:i + len(find_lines)])
        if remove_indent(block) == nfind:
            return block
    return None


def _escape_normalized_replacer(content: str, find: str) -> str | None:
    import re
    def unescape(s: str) -> str:
        return re.sub(r"\\(n|t|r|'|\"|\`|\\|\n|\$)", lambda m: {
            "n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"',
            "`": "`", "\\": "\\", "\n": "\n", "$": "$",
        }.get(m.group(1), m.group(0)), s)
    ufind = unescape(find)
    if ufind in content:
        return ufind
    lines = content.split("\n")
    ufind_lines = ufind.split("\n")
    for i in range(len(lines) - len(ufind_lines) + 1):
        block = "\n".join(lines[i:i + len(ufind_lines)])
        if unescape(block) == ufind:
            return block
    return None


def _trimmed_boundary_replacer(content: str, find: str) -> str | None:
    tfind = find.strip()
    if tfind == find:
        return None
    if tfind in content:
        return tfind
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if block.strip() == tfind:
            return block
    return None


def _context_aware_replacer(content: str, find: str) -> str | None:
    find_lines = find.split("\n")
    if find_lines and find_lines[-1] == "":
        find_lines.pop()
    if len(find_lines) < 3:
        return None
    first = find_lines[0].strip()
    last = find_lines[-1].strip()
    content_lines = content.split("\n")
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() == last:
                block_lines = content_lines[i:j + 1]
                if len(block_lines) == len(find_lines):
                    matching = 0
                    total = 0
                    for k in range(1, len(block_lines) - 1):
                        bl = block_lines[k].strip()
                        fl = find_lines[k].strip()
                        if bl or fl:
                            total += 1
                            if bl == fl:
                                matching += 1
                    if total == 0 or matching / total >= 0.5:
                        return "\n".join(block_lines)
                break
    return None


_REPLACERS = [
    _simple_replacer,
    _line_trimmed_replacer,
    _block_anchor_replacer,
    _whitespace_normalized_replacer,
    _indentation_flexible_replacer,
    _escape_normalized_replacer,
    _trimmed_boundary_replacer,
    _context_aware_replacer,
]


def _fuzzy_replace(content: str, old_string: str, new_string: str) -> tuple[str, str | None]:
    """Try multiple replacer strategies. Returns (new_content, error_message)."""
    for replacer in _REPLACERS:
        match = replacer(content, old_string)
        if match is not None:
            idx = content.find(match)
            if idx == -1:
                continue
            last = content.rfind(match)
            if idx != last:
                return "", f"Found multiple matches for oldString. Provide more surrounding context to make the match unique."
            return content[:idx] + new_string + content[idx + len(match):], None
    return "", "Could not find oldString in the file. It must match exactly, including whitespace, indentation, and line endings."


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

def _snippet_around(lines: list[str], start: int, end: int, context: int = _CONTEXT_LINES) -> str:
    """Return a numbered snippet around the [start, end) line range."""
    lo = max(0, start - context)
    hi = min(len(lines), end + context)
    parts: list[str] = []
    for i in range(lo, hi):
        marker = " " if i < start or i >= end else "|"
        parts.append(f"{i + 1:6d}{marker}{lines[i]}")
    return "\n".join(parts)


def _find_all_occurrences(content: str, needle: str) -> list[int]:
    """Return 1-based line numbers of all occurrences of needle in content."""
    positions: list[int] = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            break
        line_no = content[:idx].count("\n") + 1
        positions.append(line_no)
        start = idx + 1
    return positions


class EditParams(BaseModel):
    """Parameters for the edit tool."""
    file_path: str = Field(description="Path to the file to edit")
    old_string: str = Field(default="", description="Exact string to find and replace. Empty string means append to end of file.")
    new_string: str = Field(default="", description="Replacement string. Empty string with non-empty old_string means deletion.")
    insert_after_line: int | None = Field(default=None, description="Insert new_string after this line number (1-based). Ignores old_string when set.")


class EditTool(CallableTool[EditParams]):
    id = "edit"
    description = (
        "Edit a file by replacing an exact string match, inserting at a line, or appending. "
        "Returns the edited region with surrounding context so you can verify the change."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        return False  # Edits are reversible (can be undone with another edit)

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return False  # File edits are not concurrency-safe

    async def call(self, params: EditParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        old_string = params.old_string
        new_string = params.new_string
        insert_after_line = params.insert_after_line

        # Size limit to prevent DoS
        if len(new_string) > _MAX_CONTENT_SIZE:
            return ToolError(
                f"new_string too large: {len(new_string)} bytes exceeds {_MAX_CONTENT_SIZE // (1024 * 1024)}MB limit.",
                title=f"Edit {file_path}",
                metadata={"success": False},
            )

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        full, path_error = resolve_tool_path(file_path, base)
        if path_error:
            return ToolError(path_error, title=f"Edit {file_path}", metadata={"success": False})

        if not os.path.exists(full):
            return ToolError(
                f"File not found: {file_path}. Use the write tool to create new files.",
                title=f"Edit {file_path}",
                metadata={"success": False},
            )

        # --- Read-before-edit guard ---
        read_err = _assert_file_read(ctx.session_id, full)
        if read_err:
            return ToolError(read_err, title=f"Edit {file_path}", metadata={"success": False})

        try:
            content = Path(full).read_text(encoding="utf-8")
            lines = content.split("\n")
            total_before = len(lines)

            # --- Mode 1: Insert after line number ---
            if insert_after_line is not None:
                if insert_after_line < 0 or insert_after_line > total_before:
                    return ToolError(
                        f"insert_after_line={insert_after_line} out of range (file has {total_before} lines). "
                        f"Valid range: 0..{total_before}. Use 0 to insert at the beginning.",
                        title=f"Edit {file_path}",
                        metadata={"success": False, "total_lines": total_before},
                    )
                insert_lines = new_string.split("\n")
                insert_pos = insert_after_line
                new_lines = lines[:insert_pos] + insert_lines + lines[insert_pos:]
                new_content = "\n".join(new_lines)
                atomic_write(full, new_content)
                snippet = _snippet_around(new_lines, insert_pos, insert_pos + len(insert_lines))
                result = ToolOk(
                    f"Inserted {len(insert_lines)} line(s) after line {insert_after_line} in {file_path} "
                    f"({total_before} → {len(new_lines)} lines)\n\n{snippet}",
                    title=f"Edit {file_path}",
                    metadata={"success": True, "lines_added": len(insert_lines), "total_lines": len(new_lines)},
                )
                await _append_lsp_diagnostics(full, result)
                return result

            # --- Mode 2: Append (empty old_string) ---
            if not old_string:
                if not new_string:
                    return ToolError(
                        "Both old_string and new_string are empty. Nothing to do.",
                        title=f"Edit {file_path}",
                        metadata={"success": False},
                    )
                new_content = content + new_string
                atomic_write(full, new_content)
                new_lines = new_content.split("\n")
                appended_count = len(new_string.split("\n"))
                snippet = _snippet_around(new_lines, max(0, len(new_lines) - appended_count), len(new_lines))
                result = ToolOk(
                    f"Appended to {file_path} ({total_before} → {len(new_lines)} lines)\n\n{snippet}",
                    title=f"Edit {file_path}",
                    metadata={"success": True, "total_lines": len(new_lines)},
                )
                await _append_lsp_diagnostics(full, result)
                return result

            # --- Mode 3: String replacement (with fuzzy fallback) ---
            if old_string == new_string:
                return ToolError(
                    "old_string and new_string are identical. No changes needed.",
                    title=f"Edit {file_path}",
                    metadata={"success": False},
                )

            new_content, error = _fuzzy_replace(content, old_string, new_string)
            if error:
                # Provide hints for common mistakes
                hint = ""
                stripped_old = old_string.strip()
                if stripped_old and stripped_old != old_string:
                    stripped_count = content.count(stripped_old)
                    if stripped_count > 0:
                        locs = _find_all_occurrences(content, stripped_old)
                        hint = (f"\nHint: A stripped version was found {stripped_count} time(s) at line(s) "
                                f"{', '.join(str(ln) for ln in locs[:10])}. "
                                f"Check leading/trailing whitespace.")
                if not hint and old_string.lower() in content.lower():
                    hint = "\nHint: A case-insensitive match exists. Check exact casing."
                return ToolError(
                    f"{error}{hint}",
                    title=f"Edit {file_path}",
                    metadata={"success": False, "total_lines": total_before},
                )

            atomic_write(full, new_content)
            new_lines = new_content.split("\n")
            # Find the changed region for snippet
            pos = content.find(old_string) if old_string in content else 0
            start_line = content[:pos].count("\n") if pos > 0 else 0
            old_line_count = old_string.count("\n") + 1
            new_line_count = new_string.count("\n") + 1
            end_line = start_line + new_line_count
            snippet = _snippet_around(new_lines, start_line, end_line)

            delta = new_line_count - old_line_count
            delta_str = f" ({'+' if delta > 0 else ''}{delta} lines)" if delta != 0 else ""
            result = ToolOk(
                f"Edited {file_path}{delta_str} ({total_before} → {len(new_lines)} lines)\n\n{snippet}",
                title=f"Edit {file_path}",
                metadata={
                    "success": True,
                    "total_lines": len(new_lines),
                    "changed_range": [start_line + 1, end_line],
                },
            )
            await _append_lsp_diagnostics(full, result)
            return result
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Edit {file_path}", metadata={"success": False})


tool = EditTool()


async def _append_lsp_diagnostics(file_path: str, result: ToolResult) -> None:
    """Touch file with LSP and append any diagnostics to the result output."""
    try:
        from mycode.lsp.lsp import get_lsp_manager
        lsp = get_lsp_manager()
        await lsp.touch_file(file_path)
        # Small delay to let LSP compute diagnostics
        import asyncio
        await asyncio.sleep(0.3)
        diagnostics = await lsp.diagnostics()
        normalized = os.path.normpath(file_path)
        issues = diagnostics.get(normalized, [])
        errors = [d for d in issues if d.get("severity") == 1]
        if errors:
            limited = errors[:_MAX_DIAGNOSTICS_PER_FILE]
            suffix = f"\n... and {len(errors) - len(limited)} more" if len(errors) > len(limited) else ""
            diag_lines = []
            for d in limited:
                line = d.get("range", {}).get("start", {}).get("line", 0) + 1
                col = d.get("range", {}).get("start", {}).get("character", 0) + 1
                msg = d.get("message", "")
                diag_lines.append(f"ERROR [{line}:{col}] {msg}")
            result.output += (
                f"\n\nLSP errors detected in this file, please fix:\n"
                f"<diagnostics file=\"{file_path}\">\n"
                f"{chr(10).join(diag_lines)}{suffix}\n"
                f"</diagnostics>"
            )
    except Exception:
        pass  # LSP diagnostics are best-effort
