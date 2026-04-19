"""Read file tool.

Features:
- Path safety validation (prevent reading outside project directory)
- Encoding detection (charset-normalizer / chardet fallback)
- Image file support (returns file info + base64 hint)
- PDF basic support (attempts text extraction)
- 1-based line_offset for consistency with editors
- Large file auto-truncation with ToolResultBuilder
- File summary header (total lines, showing range, file size)
- Capability declarations (is_read_only=True)
"""
from __future__ import annotations

import mimetypes
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
    ToolResultBuilder,
    resolve_tool_path,
)

_MAX_LINES = 2000

# Image extensions we handle
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"})

# Binary extensions we can detect
_BINARY_EXTS = frozenset({
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".wasm", ".pyc", ".pyo", ".class",
    ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wav", ".flac",
    ".db", ".sqlite", ".sqlite3",
})


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _detect_encoding(raw: bytes) -> str:
    """Detect encoding of raw bytes, defaulting to utf-8."""
    # Try charset-normalizer first (more accurate), then chardet
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(raw[:8192]).best()
        if result and result.encoding:
            return result.encoding
    except ImportError:
        pass
    try:
        import chardet
        det = chardet.detect(raw[:8192])
        if det and det.get("encoding"):
            return det["encoding"]
    except ImportError:
        pass
    return "utf-8"


def _is_likely_binary(raw: bytes) -> bool:
    """Check if the content is likely binary by looking for null bytes."""
    sample = raw[:8192]
    return b"\x00" in sample


class ReadParams(BaseModel):
    """Parameters for the read tool."""
    file_path: str = Field(description="Path to the file to read (relative to project root or absolute)")
    line_offset: int | None = Field(default=None, description="Starting line number (1-based). Use with line_count for partial reads.")
    line_count: int | None = Field(default=None, description="Number of lines to read from line_offset")


class ReadTool(CallableTool[ReadParams]):
    id = "read"
    description = "Read the contents of a file. Use line_offset and line_count for partial reads of large files."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: ReadParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        full, path_error = resolve_tool_path(file_path, base)
        if path_error:
            return ToolError(path_error, title=f"Read {file_path}")

        if not os.path.exists(full):
            return ToolError(f"File not found: {file_path}", title=f"Read {file_path}")

        if os.path.isdir(full):
            return ToolError(f"Path is a directory, not a file: {file_path}. Use listdir instead.", title=f"Read {file_path}")

        p = Path(full)
        file_size = p.stat().st_size
        ext = p.suffix.lower()

        # --- Handle image files ---
        if ext in _IMAGE_EXTS:
            mime = mimetypes.guess_type(full)[0] or "image/unknown"
            return ToolOk(
                f"Image file: {file_path} ({mime}, {_human_size(file_size)})\n\n"
                f"This is an image file. To process it, use bash with image tools "
                f"or pass the file path to a multimodal model.",
                title=f"Read {file_path}",
                metadata={"type": "image", "mime": mime, "file_size": file_size},
            )

        # --- Handle known binary files ---
        if ext in _BINARY_EXTS:
            return ToolError(
                f"Binary file: {file_path} ({ext}, {_human_size(file_size)}). Cannot display binary content.",
                title=f"Read {file_path}",
                metadata={"type": "binary", "file_size": file_size},
            )

        # --- Handle PDF files ---
        if ext == ".pdf":
            return _read_pdf(full, file_path, file_size)

        # --- Read as text ---
        try:
            raw = p.read_bytes()

            # Binary detection
            if _is_likely_binary(raw):
                return ToolError(
                    f"Cannot read {file_path}: binary file ({_human_size(file_size)}). "
                    f"Use bash with appropriate tools to process binary files.",
                    title=f"Read {file_path}",
                    metadata={"type": "binary", "file_size": file_size},
                )

            # Encoding detection
            encoding = _detect_encoding(raw)
            try:
                content = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                content = raw.decode("utf-8", errors="replace")

            all_lines = content.split("\n")
            # An empty file produces [""] from split — treat it as 0 lines
            if content == "":
                all_lines = []
            total = len(all_lines)

            # Determine the line range to show
            offset_0 = 0
            if params.line_offset is not None:
                if params.line_offset < 1:
                    return ToolError(
                        f"line_offset must be >= 1, got {params.line_offset}.",
                        title=f"Read {file_path}",
                    )
                offset_0 = max(0, params.line_offset - 1)
                if offset_0 >= total:
                    return ToolError(
                        f"line_offset={params.line_offset} is beyond end of file ({total} lines). "
                        f"Use line_offset=1..{total}.",
                        title=f"Read {file_path}",
                        metadata={"total_lines": total},
                    )

            if params.line_count is not None and params.line_count < 1:
                return ToolError(
                    f"line_count must be >= 1, got {params.line_count}.",
                    title=f"Read {file_path}",
                )

            end = min(offset_0 + params.line_count, total) if params.line_count is not None else total

            selected = all_lines[offset_0:end]

            truncated = False
            if len(selected) > _MAX_LINES and params.line_count is None:
                selected = selected[:_MAX_LINES]
                end = offset_0 + _MAX_LINES
                truncated = True

            builder = ToolResultBuilder(max_chars=50_000)

            showing_from = offset_0 + 1
            showing_to = offset_0 + len(selected)
            encoding_note = f", {encoding}" if encoding != "utf-8" else ""
            if showing_from == 1 and showing_to == total and not truncated:
                header = f"File: {file_path} ({total} lines, {_human_size(file_size)}{encoding_note})"
            else:
                header = f"File: {file_path} (showing lines {showing_from}-{showing_to} of {total}, {_human_size(file_size)}{encoding_note})"
            builder.add(header + "\n\n")

            numbered = "\n".join(
                f"{i + offset_0 + 1:6d}:{line}" for i, line in enumerate(selected)
            )
            builder.add(numbered)

            if truncated:
                builder.add(f"\n\n... truncated (showing {_MAX_LINES} of {total} lines). "
                            f"Use line_offset and line_count to view specific ranges.")

            return ToolOk(
                builder.build(),
                title=f"Read {file_path}",
                metadata={
                    "lines_shown": len(selected),
                    "total_lines": total,
                    "from_line": showing_from,
                    "to_line": showing_to,
                    "truncated": truncated or builder.truncated,
                    "file_size": file_size,
                    "encoding": encoding,
                },
            )
        except Exception as e:
            return ToolError(f"Error reading file: {e}", title=f"Read {file_path}")


def _read_pdf(full: str, file_path: str, file_size: int) -> ToolResult:
    """Attempt to extract text from a PDF file."""
    try:
        import subprocess
        result = subprocess.run(
            ["pdftotext", full, "-"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            builder = ToolResultBuilder(max_chars=50_000)
            builder.add(f"PDF: {file_path} ({_human_size(file_size)})\n\n")
            builder.add(result.stdout)
            return ToolOk(
                builder.build(),
                title=f"Read {file_path}",
                metadata={"type": "pdf", "file_size": file_size},
            )
        if result.returncode != 0:
            # pdftotext found but failed (encrypted, corrupted, etc.)
            stderr_hint = result.stderr.strip()
            reason = f": {stderr_hint}" if stderr_hint else ""
            return ToolError(
                f"Failed to extract text from PDF: {file_path}{reason}. "
                f"The file may be encrypted or corrupted.",
                title=f"Read {file_path}",
                metadata={"type": "pdf", "file_size": file_size},
            )
        # returncode == 0 but no text content (scanned/image-only PDF)
        return ToolOk(
            f"PDF file: {file_path} ({_human_size(file_size)})\n\n"
            f"No extractable text found. The PDF may contain only scanned images. "
            f"Use an OCR tool (e.g. tesseract) to extract text from image-based PDFs.",
            title=f"Read {file_path}",
            metadata={"type": "pdf", "file_size": file_size},
        )
    except FileNotFoundError:
        pass  # pdftotext not installed — fall through to hint below
    except subprocess.TimeoutExpired:
        return ToolError(
            f"PDF text extraction timed out for: {file_path}. "
            f"The file may be very large or complex.",
            title=f"Read {file_path}",
            metadata={"type": "pdf", "file_size": file_size},
        )
    except (UnicodeDecodeError, OSError) as e:
        return ToolError(
            f"PDF extraction error: {e}",
            title=f"Read {file_path}",
            metadata={"type": "pdf", "file_size": file_size},
        )

    return ToolOk(
        f"PDF file: {file_path} ({_human_size(file_size)})\n\n"
        f"Cannot extract text (pdftotext not available). "
        f"Use bash to install poppler-utils, or process the PDF with specialized tools.",
        title=f"Read {file_path}",
        metadata={"type": "pdf", "file_size": file_size},
    )


tool = ReadTool()
